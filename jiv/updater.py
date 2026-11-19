"""Jiv update system: .jivupdate packages (zip: manifest.json + files/ + optional signature.sig),
verification, backup, staged atomic apply, rollback, GitHub Releases checks.

Package manifest:
{ "format": 1, "name": "...", "version": "1.1.1", "min_version": "1.0.0", "changelog": "markdown",
  "created": "ISO time", "source": "release|local|self",
  "files": [ {"path": "jiv/x.py", "action": "add|replace|delete", "sha256": "...", "size": 123}, ... ] }
signature.sig = raw ed25519 signature over the exact manifest.json bytes (which pin every file's sha256)."""
import difflib, hashlib, io, json, os, re, shutil, sys, tempfile, time, uuid, zipfile
from pathlib import Path, PurePosixPath
from . import __version__
from .config import data_dir

APP_DIR = Path(os.environ.get("JIV_APP_DIR") or Path(__file__).resolve().parent.parent)
EXCLUDE_DIRS = {".venv", "venv", "__pycache__", ".git", "build", "dist", "node_modules", ".pytest_cache"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".jivnew", ".jivold")
# updater core + permission system: self-made (unsigned) updates may only touch these with the 'advanced' box ticked
CORE_FILES = {"jiv/updater.py", "jiv/relaunch.py", "jiv/update_key.py", "jiv/agent.py", "jiv/server.py", "run.py"}
MAX_PACKAGE = 60 * 1024 * 1024

class UpdateError(Exception): pass

# ---------------- install layout ----------------
def layout():
    """Installed by JivSetup.exe:  <root>/JivDesktop.exe, <root>/python/(pythonw.exe, Lib/site-packages), <root>/app (= APP_DIR).
    Otherwise a script/venv install (APP_DIR has run.py + .venv) - both update the same way: .py files in APP_DIR."""
    root = APP_DIR.parent
    exe, pyd = root / "JivDesktop.exe", root / "python"
    installed = exe.exists() and pyd.is_dir() and APP_DIR.name.lower() == "app"
    return {"installed": installed, "root": str(root) if installed else "", "launcher": str(exe) if installed else "",
            "python_dir": str(pyd) if installed else "", "site_packages": str(pyd / "Lib" / "site-packages") if installed else "",
            "app_dir": str(APP_DIR)}

def console_python():
    """python.exe that can run pip (pythonw.exe has no console streams)."""
    lay = layout()
    if lay["installed"]:
        for n in ("python.exe", "python"):
            if (Path(lay["python_dir"]) / n).exists(): return str(Path(lay["python_dir"]) / n)
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists(): return str(exe.parent / "python.exe")
    return sys.executable

# a requirement is a plain PyPI name + optional extras + version specifiers. No URLs, paths, options or markers.
_REQ = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?(\s*(==|>=|<=|~=|!=|>|<)\s*[A-Za-z0-9.*+!-]+(\s*,\s*(==|>=|<=|~=|!=|>|<)\s*[A-Za-z0-9.*+!-]+)*)?$")

def check_requirements(reqs):
    if reqs is None: return []
    if not isinstance(reqs, list) or len(reqs) > 30: raise UpdateError("bad requirements list")
    out = []
    for r in reqs:
        r = str(r).strip()
        if not _REQ.match(r): raise UpdateError(f"requirement not allowed (plain PyPI names and versions only): {r!r}")
        out.append(r)
    return out

def install_requirements(reqs, timeout=900):
    """pip-install new packages into the app's own Python (embedded site-packages when installed). Returns (ok, output)."""
    import subprocess
    reqs = check_requirements(reqs)
    if not reqs: return True, ""
    cmd = [console_python(), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-warn-script-location", "--prefer-binary"]
    # installed layout: the embedded python's own scheme already points at python\Lib\site-packages (pip is bundled there)
    if os.environ.get("JIV_PIP_FIND_LINKS"): cmd += ["--no-index", "--find-links", os.environ["JIV_PIP_FIND_LINKS"]]
    cmd += reqs
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return r.returncode == 0, (r.stdout + r.stderr)[-4000:]
    except Exception as e: return False, str(e)

# ---------------- versions ----------------
def vtuple(v):
    nums = re.findall(r"\d+", str(v or "0"))[:4]
    return tuple(int(x) for x in nums) + (0,) * (4 - len(nums))

def bump_patch(v):
    a = list(vtuple(v)[:3]); a[2] += 1; return ".".join(map(str, a))

# ---------------- paths ----------------
def safe_relpath(p):
    """Normalise a package path; reject anything that could escape the app folder or touch user data."""
    if not isinstance(p, str) or not p.strip(): raise UpdateError("empty path")
    s = p.replace("\\", "/").strip()
    if s.startswith("/") or re.match(r"^[A-Za-z]:", s) or s.startswith("~"): raise UpdateError(f"absolute path not allowed: {p}")
    parts = PurePosixPath(s).parts
    if any(x in ("..", "") for x in parts) or "\x00" in s: raise UpdateError(f"path traversal not allowed: {p}")
    norm = "/".join(x for x in parts if x != ".")
    if not norm: raise UpdateError(f"bad path: {p}")
    if parts[0] in EXCLUDE_DIRS or any(x in EXCLUDE_DIRS for x in parts): raise UpdateError(f"path not allowed (environment/build folder): {p}")
    if norm.lower().endswith((".db", ".sqlite", ".sqlite3")) or PurePosixPath(norm).name in ("config.json",):
        raise UpdateError(f"updates may not touch user data: {p}")
    target = (APP_DIR / norm).resolve()
    if APP_DIR.resolve() not in target.parents: raise UpdateError(f"path escapes the app folder: {p}")
    if data_dir().resolve() in target.parents or target == data_dir().resolve(): raise UpdateError(f"updates may not touch the data folder: {p}")
    return norm

def sha256(b): return hashlib.sha256(b).hexdigest()

def iter_app_files(root=None):
    root = Path(root or APP_DIR)
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in EXCLUDE_DIRS and not d.startswith(".jiv")]
        for f in fns:
            if f.endswith(EXCLUDE_SUFFIX): continue
            full = Path(dp) / f
            yield full.relative_to(root).as_posix(), full

# ---------------- signing ----------------
def _pubkey():
    from .update_key import PUBLIC_KEY_HEX
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(os.environ.get("JIV_UPDATE_PUBKEY") or PUBLIC_KEY_HEX))

def load_private_key(path):
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)

# ---------------- build ----------------
VERSION_FILE = "jiv/__init__.py"

def with_version_file(files, version, as_bytes=True):
    """The restarted app must report the new version or the health check rolls it back, so every package sets jiv/__init__.py."""
    files = dict(files or {}); txt = f'__version__ = "{version}"\n'
    files[VERSION_FILE] = txt.encode() if as_bytes else txt
    return files

def build_package(out_path, version, changelog, files=None, deletes=(), min_version="1.0.0", name=None,
                  source="local", private_key=None, extra=None):
    """files: {relpath: bytes}. deletes: [relpath]. private_key: path or key object (optional)."""
    files = files or {}
    entries, seen = [], set()
    for p, data in files.items():
        rp = safe_relpath(p)
        if rp in seen: raise UpdateError(f"duplicate path {rp}")
        seen.add(rp)
        if isinstance(data, str): data = data.encode("utf-8")
        entries.append({"path": rp, "action": "replace" if (APP_DIR / rp).exists() else "add", "sha256": sha256(data), "size": len(data), "_data": data})
    for p in deletes:
        rp = safe_relpath(p)
        if rp in seen: raise UpdateError(f"duplicate path {rp}")
        seen.add(rp); entries.append({"path": rp, "action": "delete", "sha256": "", "size": 0})
    man = {"format": 1, "name": name or f"Jiv {version}", "version": str(version), "min_version": str(min_version),
           "changelog": changelog or "", "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": source,
           "files": [{k: v for k, v in e.items() if k != "_data"} for e in entries], **(extra or {})}
    mbytes = json.dumps(man, indent=2, sort_keys=True).encode("utf-8")
    out_path = Path(out_path); out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", mbytes)
        for e in entries:
            if e["action"] != "delete": z.writestr("files/" + e["path"], e["_data"])
        if private_key is not None:
            key = load_private_key(private_key) if isinstance(private_key, (str, Path)) else private_key
            z.writestr("signature.sig", key.sign(mbytes))
    return out_path

def diff_trees(old_root, new_root):
    """(files_to_write {rel: bytes}, deletes [rel]) to turn old_root into new_root."""
    old = {r: p for r, p in iter_app_files(old_root)}; new = {r: p for r, p in iter_app_files(new_root)}
    files = {r: p.read_bytes() for r, p in new.items() if r not in old or old[r].read_bytes() != p.read_bytes()}
    return files, sorted(set(old) - set(new))

# ---------------- verify ----------------
def read_package(path):
    path = Path(path)
    if path.stat().st_size > MAX_PACKAGE: raise UpdateError("package too large")
    try: z = zipfile.ZipFile(path)
    except zipfile.BadZipFile: raise UpdateError("not a valid .jivupdate (zip) file")
    return z

def verify_package(path, current_version=None, allow_downgrade=False):
    """Returns info dict. Raises UpdateError for anything unsafe (bad hash, bad signature, traversal, downgrade…)."""
    current_version = current_version or __version__
    z = read_package(path)
    names = set(z.namelist())
    for n in names:   # zip-slip style names are rejected outright
        if n.startswith("/") or ".." in PurePosixPath(n.replace("\\", "/")).parts: raise UpdateError(f"path traversal in archive: {n}")
    if "manifest.json" not in names: raise UpdateError("manifest.json missing")
    mbytes = z.read("manifest.json")
    try: man = json.loads(mbytes)
    except Exception: raise UpdateError("manifest.json is not valid JSON")
    if man.get("format") != 1: raise UpdateError("unsupported package format")
    for k in ("version", "files"):
        if k not in man: raise UpdateError(f"manifest missing '{k}'")
    # signature
    signed, sig_status = False, "unsigned"
    if "signature.sig" in names:
        from cryptography.exceptions import InvalidSignature
        try: _pubkey().verify(z.read("signature.sig"), mbytes); signed, sig_status = True, "valid"
        except InvalidSignature: raise UpdateError("BAD SIGNATURE — this package was modified or signed with a different key")
    # files
    listed = set()
    for f in man["files"]:
        rp = safe_relpath(f.get("path", ""))
        if rp != f["path"]: raise UpdateError(f"non-normalised path {f['path']}")
        if rp in listed: raise UpdateError(f"duplicate path {rp}")
        listed.add(rp)
        act = f.get("action")
        if act not in ("add", "replace", "delete"): raise UpdateError(f"bad action for {rp}")
        if act != "delete":
            if "files/" + rp not in names: raise UpdateError(f"file missing from package: {rp}")
            data = z.read("files/" + rp)
            if sha256(data) != f.get("sha256") or len(data) != f.get("size", len(data)):
                raise UpdateError(f"HASH MISMATCH for {rp} — package is corrupted or tampered with")
    extra = {n[6:] for n in names if n.startswith("files/") and not n.endswith("/")} - listed
    if extra: raise UpdateError(f"unlisted files in package: {sorted(extra)[:3]}")
    if vtuple(man["version"]) <= vtuple(current_version) and not allow_downgrade:
        raise UpdateError(f"version {man['version']} is not newer than the installed {current_version} (downgrade blocked)")
    if vtuple(current_version) < vtuple(man.get("min_version", "0")):
        raise UpdateError(f"this update needs Jiv {man['min_version']} or newer (installed: {current_version})")
    core = sorted(p for p in listed if p in CORE_FILES)
    reqs = check_requirements(man.get("requirements"))
    return {"manifest": man, "signed": signed, "signature": sig_status, "touches_core": core, "requirements": reqs,
            "label": "Signed by the Jiv update key ✓" if signed else "Unsigned – created locally"}

def diff_preview(path, max_lines=400):
    z = read_package(path); man = json.loads(z.read("manifest.json")); out = []
    for f in man["files"]:
        rp, act = f["path"], f["action"]; cur = APP_DIR / rp
        old = cur.read_bytes() if cur.exists() else b""
        new = z.read("files/" + rp) if act != "delete" else b""
        try:
            a, b = old.decode("utf-8").splitlines(), new.decode("utf-8").splitlines()
            d = list(difflib.unified_diff(a, b, "a/" + rp, "b/" + rp, lineterm="", n=2))
            text = "\n".join(d[:max_lines]) + (f"\n… {len(d) - max_lines} more lines" if len(d) > max_lines else "")
        except UnicodeDecodeError:
            text = f"(binary file, {len(old)} → {len(new)} bytes)"
        plus = sum(1 for l in text.splitlines() if l.startswith("+") and not l.startswith("+++"))
        minus = sum(1 for l in text.splitlines() if l.startswith("-") and not l.startswith("---"))
        out.append({"path": rp, "action": act, "size": f.get("size", 0), "diff": text, "added": plus, "removed": minus,
                    "core": rp in CORE_FILES})
    return out

# ---------------- staging registry ----------------
def staging_dir(): d = data_dir() / "updates" / "staged"; d.mkdir(parents=True, exist_ok=True); return d

def stage_file(src_bytes_or_path, origin="upload", extra=None):
    sid = uuid.uuid4().hex[:10]; d = staging_dir() / sid; d.mkdir()
    pkg = d / "package.jivupdate"
    if isinstance(src_bytes_or_path, (bytes, bytearray)): pkg.write_bytes(src_bytes_or_path)
    else: shutil.copyfile(src_bytes_or_path, pkg)
    meta = {"id": sid, "origin": origin, "staged": time.time(), **(extra or {})}
    try:
        info = verify_package(pkg); meta.update(ok=True, **info)
    except UpdateError as e:
        meta.update(ok=False, error=str(e))
        try: meta["manifest"] = json.loads(zipfile.ZipFile(pkg).read("manifest.json"))
        except Exception: pass
    (d / "info.json").write_text(json.dumps(meta, indent=2))
    return meta

def staged_list():
    out = []
    for d in sorted(staging_dir().iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        try: out.append(json.loads((d / "info.json").read_text()))
        except Exception: continue
    return out

def staged(sid):
    if not re.fullmatch(r"[0-9a-f]{10}", sid or ""): raise UpdateError("bad id")
    d = staging_dir() / sid
    if not d.exists(): raise UpdateError("no such staged update")
    return d, json.loads((d / "info.json").read_text())

def update_staged_meta(sid, **kw):
    d, m = staged(sid); m.update(kw); (d / "info.json").write_text(json.dumps(m, indent=2)); return m

def discard(sid):
    d, _ = staged(sid); shutil.rmtree(d)

# ---------------- backup / restore ----------------
def backups_dir(): d = data_dir() / "backups"; d.mkdir(parents=True, exist_ok=True); return d

def make_backup(reason="before update", to_version=None):
    name = f"{__version__}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    dest = backups_dir() / name / "app"
    for rel, full in iter_app_files():
        t = dest / rel; t.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(full, t)
    meta = {"name": name, "version": __version__, "created": time.time(), "reason": reason, "to_version": to_version,
            "files": sum(1 for _ in iter_app_files(dest))}
    (backups_dir() / name / "backup.json").write_text(json.dumps(meta, indent=2))
    prune_backups(); return meta

def list_backups():
    out = []
    for d in backups_dir().iterdir():
        try: out.append(json.loads((d / "backup.json").read_text()))
        except Exception: continue
    return sorted(out, key=lambda b: b["created"], reverse=True)

def prune_backups(keep=5):
    for b in list_backups()[keep:]: shutil.rmtree(backups_dir() / b["name"], ignore_errors=True)

def restore_backup(name, app_dir=None):
    """Make the app folder identical to the backup (user data lives elsewhere and is untouched)."""
    app_dir = Path(app_dir or APP_DIR)
    if not re.fullmatch(r"[\w.\-]+", name or ""): raise UpdateError("bad backup name")
    src = backups_dir() / name / "app"
    if not src.exists(): raise UpdateError("backup not found")
    want = {r for r, _ in iter_app_files(src)}
    for rel, full in list(iter_app_files(app_dir)):
        if rel not in want: full.unlink()
    for rel, full in iter_app_files(src):
        t = app_dir / rel; t.parent.mkdir(parents=True, exist_ok=True)
        tmp = t.with_name(t.name + ".jivnew"); shutil.copy2(full, tmp); os.replace(tmp, t)
    return json.loads((backups_dir() / name / "backup.json").read_text())

# ---------------- apply ----------------
def state_path(): return data_dir() / "updates" / "state.json"
def read_state():
    try: return json.loads(state_path().read_text())
    except Exception: return {}
def write_state(**kw):
    state_path().parent.mkdir(parents=True, exist_ok=True); s = {**read_state(), **kw, "updated": time.time()}
    state_path().write_text(json.dumps(s, indent=2)); return s

def apply_package(pkg, advanced=False, allow_unsigned=True):
    """Backup → verify → stage every new file next to its target → swap (os.replace) → record pending state.
    Any failure before the swap leaves the app untouched; a failure during the swap restores the backup."""
    info = verify_package(pkg)
    if not info["signed"] and not allow_unsigned: raise UpdateError("unsigned updates are not allowed here")
    if info["touches_core"] and not info["signed"] and not advanced:
        raise UpdateError("this unsigned update changes the updater/permission core: " + ", ".join(info["touches_core"]) +
                          ". Tick 'advanced' to allow it.")
    man = info["manifest"]; z = read_package(pkg)
    backup = make_backup(f"before update to {man['version']}", man["version"])
    staged_new = []
    try:   # phase 1: stage (no visible change yet)
        for f in man["files"]:
            if f["action"] == "delete": continue
            data = z.read("files/" + f["path"])
            if sha256(data) != f["sha256"]: raise UpdateError(f"hash mismatch while staging {f['path']}")
            t = APP_DIR / f["path"]; t.parent.mkdir(parents=True, exist_ok=True)
            tmp = t.with_name(t.name + ".jivnew"); tmp.write_bytes(data); staged_new.append((tmp, t))
    except Exception:
        for tmp, _ in staged_new: tmp.unlink(missing_ok=True)
        raise
    try:   # phase 2: swap
        for tmp, t in staged_new: os.replace(tmp, t)
        for f in man["files"]:
            if f["action"] == "delete": (APP_DIR / f["path"]).unlink(missing_ok=True)
    except Exception as e:
        restore_backup(backup["name"]); raise UpdateError(f"apply failed and was rolled back: {e}")
    write_state(status="pending", from_version=__version__, to_version=man["version"], backup=backup["name"],
                changelog=man.get("changelog", ""), signed=info["signed"], applied=time.time())
    return {"backup": backup, "version": man["version"], "info": info}

# ---------------- GitHub releases ----------------
# Official public repo: used by "Check for updates" when the user has not set their own (1.6.3).
DEFAULT_REPO = "aamitojj/jiv-desktop"

def effective_repo(configured):
    return (configured or "").strip() or DEFAULT_REPO

def _pick_package(assets):
    """Choose the .jivupdate asset: prefer an all-in-one (cumulative) package, then the newest-looking name."""
    pk = [a for a in assets if a.get("name", "").lower().endswith(".jivupdate")]
    if not pk: return None
    pk.sort(key=lambda a: ("all-in-one" in a["name"].lower(), a.get("size", 0)), reverse=True)
    return pk[0]

def parse_repo(s):
    s = (s or "").strip().rstrip("/")
    m = re.search(r"(?:github\.com[/:])?([\w.-]+)/([\w.-]+?)(?:\.git)?$", s)
    if not m: raise UpdateError("Repository must look like owner/repo or https://github.com/owner/repo")
    return m.group(1), m.group(2)

async def check_github(repo, download=True):
    import httpx
    owner, name = parse_repo(repo)
    api = os.environ.get("JIV_GITHUB_API", "https://api.github.com").rstrip("/")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"Accept": "application/vnd.github+json", "User-Agent": "JivDesktop"}) as c:
        r = await c.get(f"{api}/repos/{owner}/{name}/releases/latest")
        if r.status_code == 404: return {"available": False, "repo": f"{owner}/{name}",
                                         "message": f"No releases published yet (or {owner}/{name} is private/not found)."}
        if r.status_code in (403, 429) and "rate limit" in r.text.lower():
            return {"available": False, "repo": f"{owner}/{name}", "error": "GitHub rate limit reached (60 checks/hour without sign-in). Try again later."}
        r.raise_for_status(); rel = r.json()
        latest = (rel.get("tag_name") or "").lstrip("vV")
        res = {"repo": f"{owner}/{name}", "latest": latest, "current": __version__, "notes": rel.get("body") or "", "url": rel.get("html_url"),
               "available": vtuple(latest) > vtuple(__version__)}
        asset = _pick_package(rel.get("assets", []))
        inst = next((a for a in rel.get("assets", []) if re.match(r"(?i)^JivSetup.*\.exe$", a.get("name", ""))), None)
        if inst: res["installer"] = {"name": inst["name"], "url": inst["browser_download_url"], "size": inst.get("size", 0), "version": latest}
        if not res["available"]: res["message"] = f"You're up to date ({__version__})."; return res
        if not asset:
            res["message"] = (f"Version {latest} is a full reinstall: download and run its installer (JivSetup.exe)." if inst
                              else f"Version {latest} is out, but the release has no .jivupdate file."); return res
        res["package"] = {"name": asset["name"], "url": asset["browser_download_url"], "size": asset.get("size", 0)}
        if not download: return res
        b = await c.get(asset["browser_download_url"]); b.raise_for_status()
        if len(b.content) > MAX_PACKAGE: raise UpdateError("release asset too large")
        res["staged"] = stage_file(b.content, origin="github", extra={"release": latest, "asset": asset["name"]})
        return res
