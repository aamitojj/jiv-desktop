"""Jiv 1.6.0 Builder: deterministic, no-AI project generation from local templates.

No model, Ollama or network calls on this path. File writes, compiles, runs and
packages stay inside the chosen project folder and require an explicit permission.
"""
from __future__ import annotations
import json, os, re, shutil, socket, tempfile, threading, time, uuid, zipfile
import http.server, socketserver
from pathlib import Path
from .config import data_dir

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BUILTIN_DIR = Path(__file__).resolve().parent / "templates"
USER_TPL_DIR = lambda: data_dir() / "templates"
PROJECTS_FILE = lambda: data_dir() / "builder" / "projects.json"
DEFAULT_PROJECTS = lambda: data_dir() / "projects"

TOKEN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\s*\}\}")
BLOCK = re.compile(r"\{%\s*(.*?)\s*%\}", re.S)
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\- ]{0,59}$")
JAVA_ERR = re.compile(
    r"^(?P<file>\S.*?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<kind>error|warning):\s*(?P<msg>.*)$", re.M)
# also "File.java:12: error:" without column
SKIP_DIR = {".git", "__pycache__", "node_modules", "target", "out", "build", ".idea", ".gradle"}
SKIP_FILE = {".class", ".jar", ".exe", ".zip", ".log"}

class BuilderError(ValueError):
    pass


def user_templates_dir():
    p = USER_TPL_DIR(); p.mkdir(parents=True, exist_ok=True); return p

def projects_db_path():
    p = PROJECTS_FILE(); p.parent.mkdir(parents=True, exist_ok=True); return p

# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------
_SYSTEM = (
    "/etc", "/usr", "/bin", "/sbin", "/boot", "/dev", "/proc", "/sys", "/root",
    "/Windows", "/Winnt", "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
)
def _is_system(p: Path) -> bool:
    s = str(p.resolve()).replace("\\", "/")
    low = s.lower()
    for x in _SYSTEM:
        n = x.replace("\\", "/").lower()
        if low == n or low.startswith(n + "/"): return True
    return False

def safe_rel(rel: str) -> str:
    r = (rel or "").replace("\\", "/").strip().lstrip("/")
    if not r or r.startswith("~") or ":" in r.split("/")[0]:
        raise BuilderError("invalid relative path")
    parts = [p for p in r.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise BuilderError("path traversal is not allowed")
    if any(p.startswith(".") and p not in (".gitignore", ".gitattributes") for p in parts if p not in (".gitignore",)):
        # allow normal files; hidden dirs like .git are skipped elsewhere. Allow .gitignore.
        pass
    return "/".join(parts)

def safe_join(root: Path, rel: str) -> Path:
    r = safe_rel(rel)
    root = Path(root).resolve()
    p = (root / r).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise BuilderError("path escapes the project folder")
    return p

def sanitize_name(name: str) -> str:
    n = (name or "").strip()
    if not SAFE_NAME.match(n):
        raise BuilderError("project name: letters, numbers, spaces, . _ - (max 60, must start with a letter or digit)")
    return n

def slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip()).strip("_").lower()
    if not s or s[0].isdigit(): s = "app_" + (s or "project")
    return s[:40]

def java_package(name: str) -> str:
    return "com.jiv." + slugify(name)

def validate_folder(folder: str, *, must_exist=False) -> Path:
    raw = (folder or "").strip()
    if not raw: raise BuilderError("choose a project folder")
    if ".." in Path(raw).parts:
        raise BuilderError("path traversal is not allowed")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p)
    p = p if p.exists() or not must_exist else p
    # resolve existing parents
    try:
        if p.exists(): p = p.resolve()
        else:
            parent = p.parent if p.parent.exists() else p.parent
            # resolve existing prefix
            cur = Path(p.anchor) if p.anchor else Path("/")
            rest = []
            for part in p.parts[1:] if p.is_absolute() else p.parts:
                nxt = cur / part
                if nxt.exists(): cur = nxt.resolve(); 
                else: rest.append(part)
            p = cur.joinpath(*rest) if rest else cur
    except Exception as e:
        raise BuilderError(f"bad folder: {e}")
    if must_exist and not (p.exists() and p.is_dir()):
        raise BuilderError("project folder does not exist")
    if _is_system(p if p.exists() else p.parent if p.parent.exists() else p):
        raise BuilderError("refusing to write under a system directory")
    app_root = Path(__file__).resolve().parents[1]
    probe = (p if p.exists() else p.parent).resolve()
    try:
        probe.relative_to(app_root)
        inside = True
    except ValueError:
        inside = False
    if inside:
        raise BuilderError("refusing to write inside the Jiv app folder")
    return p

# ---------------------------------------------------------------------------
# Tiny template engine ({{ var }}, {% if %}, {% for %} ) — no Jinja, no eval
# ---------------------------------------------------------------------------
def expand(text: str, ctx: dict) -> str:
    return _render(text or "", dict(ctx))

def _lookup(ctx, name):
    cur = ctx
    for part in (name or "").split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return ""
            cur = cur[part]
        else:
            return getattr(cur, part, "") if hasattr(cur, part) and not part.startswith("_") else ""
    return cur

def _truthy(v):
    if isinstance(v, (list, tuple, dict, str)): return bool(v)
    return bool(v)

def _render(src: str, ctx: dict) -> str:
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("{{", i):
            m = TOKEN.match(src, i)
            if m:
                out.append("" if _lookup(ctx, m.group(1)) is None else str(_lookup(ctx, m.group(1))))
                i = m.end(); continue
        if src.startswith("{%", i):
            m = BLOCK.match(src, i)
            if m:
                inner = m.group(1).strip(); i = m.end()
                if inner.startswith("if "):
                    name = inner[3:].strip()
                    body, i2, else_b = _collect_if(src, i)
                    i = i2
                    out.append(_render(body if _truthy(_lookup(ctx, name)) else (else_b or ""), ctx))
                    continue
                if inner.startswith("for "):
                    mm = re.match(r"for\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+in\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*$", inner)
                    if not mm: raise BuilderError("bad for-tag: " + inner)
                    body, i2 = _collect_end(src, i, "endfor")
                    i = i2
                    seq = _lookup(ctx, mm.group(2)) or []
                    if not isinstance(seq, (list, tuple)): seq = list(seq) if seq else []
                    for item in seq:
                        c2 = dict(ctx); c2[mm.group(1)] = item
                        if isinstance(item, dict): c2.update(item)
                        out.append(_render(body, c2))
                    continue
                if inner in ("endif", "else", "endfor"):
                    raise BuilderError("unexpected {%% %s %%}" % inner)
                raise BuilderError("unknown tag: " + inner)
        out.append(src[i]); i += 1
    return "".join(out)

def _collect_end(src, i, end):
    depth, start, n = 1, i, len(src)
    kind = "if" if end == "endif" else "for"
    while i < n:
        if src.startswith("{%", i):
            m = BLOCK.match(src, i)
            if m:
                tag = m.group(1).strip()
                if tag.startswith(kind + " ") or tag == kind:
                    depth += 1
                elif tag == end:
                    depth -= 1
                    if depth == 0: return src[start:i], m.end()
                i = m.end(); continue
        i += 1
    raise BuilderError("unclosed {%% %s %%}" % kind)

def _collect_if(src, i):
    depth, start, else_at, body_end, n = 1, i, None, None, len(src)
    while i < n:
        if src.startswith("{%", i):
            m = BLOCK.match(src, i)
            if m:
                tag = m.group(1).strip()
                if tag.startswith("if "):
                    depth += 1
                elif tag == "endif":
                    depth -= 1
                    if depth == 0:
                        if else_at is not None:
                            return src[start:body_end], m.end(), src[else_at:i]
                        return src[start:i], m.end(), ""
                elif tag == "else" and depth == 1:
                    if else_at is not None:
                        raise BuilderError("multiple else in one if")
                    body_end = i
                    else_at = m.end()
                i = m.end(); continue
        i += 1
    raise BuilderError("unclosed {% if %}")

# ---------------------------------------------------------------------------
# Templates & snippets
# ---------------------------------------------------------------------------
def _read_manifest(folder: Path):
    man = folder / "manifest.json"
    if not man.is_file(): return None
    try: d = json.loads(man.read_text("utf-8"))
    except Exception: return None
    if not d.get("id") or not d.get("name"): return None
    d["_root"] = folder
    d["builtin"] = not str(folder).startswith(str(data_dir()))
    return d

def list_templates():
    found, seen = [], set()
    for root, builtin in ((user_templates_dir(), False), (BUILTIN_DIR, True)):
        if not root.is_dir(): continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name == "snippets": continue
            m = _read_manifest(child)
            if not m or m["id"] in seen: continue
            seen.add(m["id"]); m["builtin"] = builtin; found.append(m)
    return found

def get_template(tid: str):
    t = next((x for x in list_templates() if x["id"] == tid), None)
    if not t: raise BuilderError("unknown template: " + (tid or ""))
    return t

def public_tpl(t):
    return {k: v for k, v in t.items() if k != "_root"}

def list_snippets():
    out = []
    for root, builtin in ((user_templates_dir() / "snippets", False), (BUILTIN_DIR / "snippets", True)):
        if not root.is_dir(): continue
        for f in sorted(root.iterdir()):
            if not f.is_file() or f.suffix not in {".html", ".java", ".js", ".css", ".json"}: continue
            sid = f.stem
            if any(s["id"] == sid for s in out): continue
            head = f.read_text("utf-8")[:400]
            desc = ""
            for line in head.splitlines()[:4]:
                s = line.strip()
                if s.startswith(("<!--", "//", "/*")):
                    desc = re.sub(r"^<!--\s*|\s*-->$|^//\s*|^/\*\s*|\s*\*/$", "", s).strip(); break
            out.append({"id": sid, "name": sid.replace("_", " "), "file": f.name, "kind": f.suffix[1:],
                        "description": desc, "builtin": builtin, "size": f.stat().st_size})
    return out

def snippet_body(sid: str) -> tuple[str, Path]:
    for root in (user_templates_dir() / "snippets", BUILTIN_DIR / "snippets"):
        for f in root.glob(sid + ".*") if root.is_dir() else []:
            return f.read_text("utf-8"), f
    raise BuilderError("unknown snippet: " + sid)

# ---------------------------------------------------------------------------
# Context + generation
# ---------------------------------------------------------------------------
def build_context(name, options, tpl):
    options = dict(options or {})
    pages_raw = options.get("pages") or "Home, About"
    if isinstance(pages_raw, str):
        pages = []
        for i, part in enumerate([p.strip() for p in pages_raw.split(",") if p.strip()] or ["Home"]):
            slug = "index" if i == 0 or part.lower() == "home" else slugify(part)
            pages.append({"name": part, "slug": slug, "file": ("index" if slug == "index" else slug) + ".html", "is_home": slug == "index"})
        # unique slugs
        seen = set()
        for p in pages:
            base = p["slug"]; k = 2
            while p["slug"] in seen:
                p["slug"] = f"{base}{k}"; p["file"] = p["slug"] + ".html"; k += 1
            seen.add(p["slug"])
        options["pages"] = pages
    try:
        screens = int(options.get("screens") or 3)
    except (TypeError, ValueError):
        screens = 3
    screens = max(1, min(12, screens))
    options["screens"] = screens
    try:
        port = int(options.get("port") or 8080)
    except (TypeError, ValueError):
        port = 8080
    options["port"] = max(1, min(65535, port))
    options["screen_list"] = [{"n": i + 1, "title": options.get(f"screen_{i+1}_title") or f"Screen {i+1}"} for i in range(screens)]
    ctx = {
        "project_name": name,
        "slug": slugify(name),
        "package": java_package(name),
        "package_path": java_package(name).replace(".", "/"),
        "year": time.strftime("%Y"),
        "contact": bool(options.get("contact")),
        "maven": bool(options.get("maven")),
        "nav": bool(options.get("nav", True)),
        **{k: v for k, v in options.items()},
    }
    return ctx

def _iter_template_files(tpl, ctx=None):
    """Yield (dest_rel, src_path). Skip manifest. Honours optional manifest["files"] maps."""
    root = Path(tpl["_root"])
    files_dir = root / "files" if (root / "files").is_dir() else root
    mapped = tpl.get("files")
    if mapped:
        for item in mapped:
            when = item.get("when")
            if when and ctx is not None and not _truthy(_lookup(ctx, when)):
                continue
            src = files_dir / item["src"]
            if not src.is_file(): raise BuilderError("template file missing: " + item["src"])
            dest = item.get("dest") or item["src"]
            yield dest, src
        return
    for p in files_dir.rglob("*"):
        if not p.is_file(): continue
        if p.name == "manifest.json": continue
        rel = p.relative_to(files_dir).as_posix()
        yield rel, p

def preview_files(tid, name, options, folder):
    tpl = get_template(tid)
    name = sanitize_name(name)
    dest = validate_folder(folder)
    ctx = build_context(name, options, tpl)
    planned = []
    for rel, src in _iter_template_files(tpl, ctx):
        dest_rel = safe_rel(expand(rel, ctx))
        planned.append({"path": dest_rel, "bytes": src.stat().st_size})
    # extra pages for static_website
    if tpl["id"] == "static_website":
        page_tpl = Path(tpl["_root"]) / "files" / "page.html"
        have = {p["path"] for p in planned}
        if page_tpl.is_file():
            for pg in ctx["pages"]:
                if pg["file"] not in have and pg["file"] != "page.html":
                    planned.append({"path": pg["file"], "bytes": page_tpl.stat().st_size})
        planned = [p for p in planned if p["path"] != "page.html"]
    return {"folder": str(dest), "files": planned, "count": len(planned), "template": tpl["id"], "name": name, "context": {k: ctx[k] for k in ("slug", "package")}}

def generate(tid, name, options, folder):
    """Write template files into folder. Caller must have permission. Returns project dict."""
    preview = preview_files(tid, name, options, folder)
    tpl = get_template(tid)
    name = sanitize_name(name)
    dest = validate_folder(folder)
    ctx = build_context(name, options, tpl)
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for rel, src in _iter_template_files(tpl, ctx):
        dest_rel = safe_rel(expand(rel, ctx))
        if dest_rel == "page.html" and tpl["id"] == "static_website":
            continue
        text = src.read_text("utf-8", errors="replace")
        body = expand(text, ctx)
        out = safe_join(dest, dest_rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, "utf-8")
        written.append(dest_rel)
    if tpl["id"] == "static_website":
        page_tpl = Path(tpl["_root"]) / "files" / "page.html"
        if page_tpl.is_file():
            raw = page_tpl.read_text("utf-8")
            for pg in ctx["pages"]:
                if pg["file"] in written: continue
                c2 = dict(ctx); c2["page"] = pg; c2["page_name"] = pg["name"]
                out = safe_join(dest, pg["file"])
                out.write_text(expand(raw, c2), "utf-8")
                written.append(pg["file"])
    proj = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "folder": str(dest),
        "template": tid,
        "options": options or {},
        "created": time.time(),
        "files": written,
        "package": ctx.get("package", ""),
        "slug": ctx["slug"],
    }
    _save_project(proj)
    return proj

# ---------------------------------------------------------------------------
# Project registry + files
# ---------------------------------------------------------------------------
def _load_projects():
    p = projects_db_path()
    if not p.exists(): return []
    try: return json.loads(p.read_text("utf-8"))
    except Exception: return []

def _save_all(rows):
    projects_db_path().write_text(json.dumps(rows, indent=2), "utf-8")

def _save_project(proj):
    rows = [r for r in _load_projects() if r["id"] != proj["id"]]
    rows.insert(0, proj); _save_all(rows)

def list_projects():
    return _load_projects()

def get_project(pid):
    p = next((x for x in _load_projects() if x["id"] == pid), None)
    if not p: raise BuilderError("unknown project")
    return p

def forget_project(pid):
    """Remove from the list; does not delete files."""
    rows = [r for r in _load_projects() if r["id"] != pid]
    if len(rows) == len(_load_projects()): raise BuilderError("unknown project")
    _save_all(rows)

def project_root(pid) -> Path:
    p = get_project(pid)
    return validate_folder(p["folder"], must_exist=True)

def tree(pid):
    root = project_root(pid)
    items = []
    def walk(d, prefix=""):
        try: kids = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception: return
        for c in kids:
            if c.name in SKIP_DIR or c.name.startswith(".") and c.name not in (".gitignore",):
                continue
            rel = (prefix + "/" + c.name).lstrip("/") if prefix else c.name
            if c.is_dir():
                items.append({"path": rel.replace("\\", "/"), "kind": "dir"})
                walk(c, rel)
            else:
                items.append({"path": rel.replace("\\", "/"), "kind": "file", "size": c.stat().st_size})
    walk(root)
    return items

def read_file(pid, rel, max_chars=400_000):
    p = safe_join(project_root(pid), rel)
    if not p.is_file(): raise BuilderError("file not found")
    data = p.read_bytes()
    if b"\x00" in data[:4096]: raise BuilderError("binary file")
    text = data.decode("utf-8", "replace")
    return text[:max_chars], p.stat().st_mtime

def write_file(pid, rel, content):
    p = safe_join(project_root(pid), rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content if isinstance(content, str) else content.decode("utf-8"), "utf-8")
    return {"path": safe_rel(rel), "bytes": p.stat().st_size}

def insert_snippet(pid, rel, sid):
    body, src = snippet_body(sid)
    root = project_root(pid)
    p = safe_join(root, rel)
    marker = f"snippet:{sid}"
    # A complete Java type must live in its own file (javac forbids a second public class
    # and import statements in the middle of another class).
    mcls = re.search(r"\b((?:public\s+)?(?:class|interface|enum)\s+)(\w+)", body) if src.suffix == ".java" else None
    if src.suffix == ".java" and mcls:
        dest_name = mcls.group(2) + ".java"
        dest = p.parent / dest_name if p.suffix == ".java" and p.is_file() else (p if p.suffix == ".java" else p.parent / dest_name)
        dest = safe_join(root, dest.relative_to(root).as_posix())
        if dest.is_file() and marker in dest.read_text("utf-8", errors="replace"):
            return {"path": dest.relative_to(root).as_posix(), "inserted": False, "reason": "already present"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"// {marker}\n{body}\n" if marker not in body else body, "utf-8")
        return {"path": dest.relative_to(root).as_posix(), "inserted": True}
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", "utf-8")
    cur = p.read_text("utf-8", errors="replace")
    if marker in cur:
        return {"path": safe_rel(rel), "inserted": False, "reason": "already present"}
    block = f"\n<!-- {marker} -->\n{body}\n" if src.suffix == ".html" else f"\n// {marker}\n{body}\n"
    if src.suffix == ".html" and re.search(r"</body>", cur, re.I):
        new = re.sub(r"</body>", block + "</body>", cur, count=1, flags=re.I)
    elif src.suffix == ".java" and cur.rstrip().endswith("}"):
        idx = cur.rfind("}")
        new = cur[:idx] + block + cur[idx:]
    else:
        new = cur + ("\n" if cur and not cur.endswith("\n") else "") + block
    p.write_text(new, "utf-8")
    return {"path": safe_rel(rel), "inserted": True}

def save_as_template(pid, tpl_id, name, description=""):
    tid = slugify(tpl_id or name)
    if not tid: raise BuilderError("template id required")
    dest = user_templates_dir() / tid
    if dest.exists(): raise BuilderError("a user template with that id already exists")
    root = project_root(pid)
    dest.mkdir(parents=True)
    files = dest / "files"; files.mkdir()
    copied = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR and not d.startswith(".")]
        for fn in filenames:
            if any(fn.endswith(ext) for ext in SKIP_FILE): continue
            src = Path(dirpath) / fn
            rel = src.relative_to(root).as_posix()
            out = files / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            copied.append(rel)
    man = {"id": tid, "name": name or tid, "kind": get_project(pid).get("template", "custom"),
           "description": description or f"Saved from project {get_project(pid)['name']}",
           "options": []}
    (dest / "manifest.json").write_text(json.dumps(man, indent=2), "utf-8")
    return {"id": tid, "files": len(copied)}

# ---------------------------------------------------------------------------
# Tool detection (local only)
# ---------------------------------------------------------------------------
def _which(names):
    for n in names:
        p = shutil.which(n)
        if p: return p
        if os.name == "nt":
            p = shutil.which(n + ".exe")
            if p: return p
    if "java" in names:
        jh = os.environ.get("JAVA_HOME")
        if jh:
            exe = Path(jh) / "bin" / ("java.exe" if os.name == "nt" else "java")
            if exe.exists(): return str(exe)
    return None

def detect_tools():
    java = _which(["java"])
    javac = _which(["javac"])
    jar = _which(["jar"])
    mvn = _which(["mvn", "mvn.cmd"])
    gradle = _which(["gradle", "gradle.bat"])
    jpackage = _which(["jpackage"])
    # javac next to java
    if java and not javac:
        cand = Path(java).parent / ("javac.exe" if os.name == "nt" else "javac")
        if cand.exists(): javac = str(cand)
    if java and not jar:
        cand = Path(java).parent / ("jar.exe" if os.name == "nt" else "jar")
        if cand.exists(): jar = str(cand)
    if java and not jpackage:
        cand = Path(java).parent / ("jpackage.exe" if os.name == "nt" else "jpackage")
        if cand.exists(): jpackage = str(cand)
    def ver(exe, args=("-version",)):
        if not exe: return ""
        try:
            import subprocess
            r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=8)
            return (r.stdout or r.stderr or "").strip().splitlines()[:2]
        except Exception:
            return ""
    return {
        "java": java, "javac": javac, "jar": jar, "mvn": mvn, "gradle": gradle, "jpackage": jpackage,
        "java_version": ver(java), "javac_version": ver(javac),
        "mvn_version": ver(mvn, ("-v",)) if mvn else "",
        "has_jdk": bool(javac),
    }

# ---------------------------------------------------------------------------
# Error parsing, build, run, package, serve
# ---------------------------------------------------------------------------
def parse_errors(text: str):
    out = []
    for m in JAVA_ERR.finditer(text or ""):
        out.append({"file": m.group("file"), "line": int(m.group("line")),
                    "col": int(m.group("col") or 0), "kind": m.group("kind"), "msg": m.group("msg")})
    return out

JOBS = {}
_JOB_LOCK = threading.Lock()
_PROCESSES = {}  # pid-key -> Popen
_SERVERS = {}

def _job(kind, proj):
    jid = uuid.uuid4().hex[:10]
    row = {"id": jid, "kind": kind, "project": proj["id"], "status": "running",
           "log": [], "errors": [], "started": time.time(), "code": None, "extra": {}}
    with _JOB_LOCK: JOBS[jid] = row
    return row

def job_view(jid):
    with _JOB_LOCK: j = JOBS.get(jid)
    return dict(j) if j else None

def _log(job, line):
    with _JOB_LOCK:
        job["log"].append(line)
        if len(job["log"]) > 4000: job["log"] = job["log"][-3000:]

def _run_cmd(job, cmd, cwd, timeout=180, env=None):
    import subprocess
    _log(job, "$ " + " ".join(cmd))
    try:
        p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=env, bufsize=1)
    except FileNotFoundError as e:
        _log(job, str(e)); return 127, ""
    buf = []
    try:
        t0 = time.time()
        while True:
            if timeout and time.time() - t0 > timeout:
                p.kill(); _log(job, "(timed out)"); return 124, "".join(buf)
            line = p.stdout.readline() if p.stdout else ""
            if line:
                buf.append(line); _log(job, line.rstrip())
            elif p.poll() is not None:
                rest = p.stdout.read() if p.stdout else ""
                if rest:
                    buf.append(rest)
                    for ln in rest.splitlines(): _log(job, ln)
                break
            else:
                time.sleep(0.05)
        return p.returncode, "".join(buf)
    finally:
        try:
            if p.poll() is None: p.kill()
        except Exception:
            pass

def _java_sources(root: Path):
    return [p for p in root.rglob("*.java") if "target" not in p.parts and "out" not in p.parts]

def _main_class(proj, root: Path):
    pkg = proj.get("package") or java_package(proj["name"])
    # find public class with main
    for p in _java_sources(root):
        try: t = p.read_text("utf-8", errors="replace")
        except Exception: continue
        if re.search(r"void\s+main\s*\(\s*String", t):
            m = re.search(r"package\s+([\w.]+)\s*;", t)
            c = re.search(r"public\s+class\s+(\w+)", t)
            if c: return (m.group(1) + "." if m else "") + c.group(1)
    return pkg + ".Main"

def _out_dir(root: Path) -> Path:
    d = root / "out"; d.mkdir(exist_ok=True); return d

def build_project(pid):
    proj = get_project(pid); root = project_root(pid)
    tools = detect_tools()
    job = _job("build", proj)
    def work():
        try:
            if (root / "pom.xml").is_file() and tools["mvn"]:
                code, out = _run_cmd(job, [tools["mvn"], "-q", "-DskipTests", "package"], root, timeout=240)
            elif (root / "build.gradle").is_file() and (tools["gradle"] or (root / "gradlew").exists() or (root / "gradlew.bat").exists()):
                g = tools["gradle"] or str(root / ("gradlew.bat" if os.name == "nt" else "gradlew"))
                code, out = _run_cmd(job, [g, "-q", "build", "-x", "test"], root, timeout=240)
            elif tools["javac"]:
                srcs = _java_sources(root)
                if not srcs:
                    # website — nothing to compile
                    if any(root.glob("*.html")) or (root / "index.html").exists() or (root / "frontend" / "index.html").exists():
                        _log(job, "Static website: no compile step.")
                        job["status"] = "ok"; job["code"] = 0; return
                    _log(job, "No .java files found."); job["status"] = "error"; job["code"] = 1; return
                outd = _out_dir(root)
                cmd = [tools["javac"], "-encoding", "UTF-8", "-d", str(outd), *[str(s) for s in srcs]]
                code, out = _run_cmd(job, cmd, root, timeout=180)
            else:
                _log(job, "No JDK (javac) or Maven found. Open the Languages page to install a JDK (Temurin / OpenJDK), then retry.")
                job["status"] = "error"; job["code"] = 1; return
            job["errors"] = parse_errors("\n".join(job["log"]))
            job["code"] = code
            job["status"] = "ok" if code == 0 else "error"
        except Exception as e:
            _log(job, f"{type(e).__name__}: {e}"); job["status"] = "error"; job["code"] = 1
    threading.Thread(target=work, daemon=True).start()
    return job

def run_project(pid, timeout=0):
    """Start the project. timeout=0 means until stop_run()."""
    proj = get_project(pid); root = project_root(pid)
    tools = detect_tools()
    job = _job("run", proj)
    def work():
        import subprocess
        try:
            # prefer built jar
            jars = list((root / "dist").glob("*.jar")) + list((root / "target").glob("*-jar-with-dependencies.jar")) + list((root / "target").glob("*.jar"))
            jars = [j for j in jars if "sources" not in j.name and "javadoc" not in j.name]
            env = os.environ.copy()
            if tools["java"] and jars:
                cmd = [tools["java"], "-jar", str(jars[0])]
            elif tools["java"] and (root / "out").is_dir():
                main = _main_class(proj, root)
                cmd = [tools["java"], "-cp", str(root / "out"), main]
            elif tools["javac"] and _java_sources(root):
                # compile then run
                build_project(pid)  # async — wait?
                # do a sync compile here
                srcs = _java_sources(root); outd = _out_dir(root)
                code, _ = _run_cmd(job, [tools["javac"], "-encoding", "UTF-8", "-d", str(outd), *[str(s) for s in srcs]], root)
                if code != 0:
                    job["errors"] = parse_errors("\n".join(job["log"]))
                    job["status"] = "error"; job["code"] = code; return
                main = _main_class(proj, root)
                cmd = [tools["java"], "-cp", str(outd), main]
            else:
                _log(job, "Nothing to run. Build a Java project first, or use Open site for a website.")
                job["status"] = "error"; job["code"] = 1; return
            _log(job, "$ " + " ".join(cmd))
            p = subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            _PROCESSES[job["id"]] = p
            t0 = time.time()
            while True:
                if timeout and time.time() - t0 > timeout:
                    p.kill(); _log(job, "(timed out)"); job["code"] = 124; break
                if job.get("stop"):
                    p.terminate(); 
                    try: p.wait(timeout=3)
                    except Exception: p.kill()
                    _log(job, "(stopped)"); job["code"] = 0; break
                line = p.stdout.readline() if p.stdout else ""
                if line: _log(job, line.rstrip())
                elif p.poll() is not None:
                    rest = p.stdout.read() if p.stdout else ""
                    for ln in (rest or "").splitlines(): _log(job, ln)
                    job["code"] = p.returncode; break
                else:
                    time.sleep(0.05)
            job["status"] = "ok" if (job["code"] or 0) == 0 else "error"
        except Exception as e:
            _log(job, f"{type(e).__name__}: {e}"); job["status"] = "error"; job["code"] = 1
        finally:
            _PROCESSES.pop(job["id"], None)
    threading.Thread(target=work, daemon=True).start()
    return job

def stop_run(jid):
    with _JOB_LOCK:
        j = JOBS.get(jid)
        if j: j["stop"] = True
    p = _PROCESSES.get(jid)
    if p:
        try: p.terminate()
        except Exception: pass
    return bool(j or p)

def package_project(pid, kind="auto"):
    proj = get_project(pid); root = project_root(pid)
    tools = detect_tools()
    job = _job("package", proj)
    def work():
        try:
            is_web = (root / "index.html").exists() or (root / "frontend" / "index.html").exists()
            want = kind
            if want == "auto":
                want = "zip" if is_web and not _java_sources(root) else "jar" if _java_sources(root) else "zip"
            dist = root / "dist"; dist.mkdir(exist_ok=True)
            if want == "zip":
                src = root / "frontend" if (root / "frontend" / "index.html").exists() else root
                zpath = dist / (proj["slug"] + "-site.zip")
                with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                    for p in src.rglob("*"):
                        if not p.is_file(): continue
                        try:
                            p.resolve().relative_to(dist.resolve()); continue
                        except ValueError:
                            pass
                        if p.suffix in SKIP_FILE and p.suffix != ".zip":
                            if p.suffix in {".class", ".jar", ".exe"}: continue
                        if any(x in p.parts for x in ("out", "target", "dist", ".git")): continue
                        z.write(p, p.relative_to(src).as_posix())
                _log(job, f"Wrote {zpath} (Netlify-ready: unzip and drag the folder, or publish the zip).")
                job["extra"] = {"file": str(zpath), "download": Path(zpath).name}
                job["status"] = "ok"; job["code"] = 0; return
            if want in ("jar", "exe"):
                if not tools["javac"] and not tools["jar"]:
                    _log(job, "A JDK (javac/jar) is required to package a Java app. Open Languages to install one.")
                    job["status"] = "error"; job["code"] = 1; return
                # ensure compiled
                srcs = _java_sources(root)
                outd = _out_dir(root)
                if srcs and tools["javac"]:
                    code, _ = _run_cmd(job, [tools["javac"], "-encoding", "UTF-8", "-d", str(outd), *[str(s) for s in srcs]], root)
                    if code != 0:
                        job["errors"] = parse_errors("\n".join(job["log"]))
                        job["status"] = "error"; job["code"] = code; return
                main = _main_class(proj, root)
                jar_path = dist / (proj["slug"] + ".jar")
                if tools["jar"]:
                    mf = outd / "META-INF"; mf.mkdir(exist_ok=True)
                    (mf / "MANIFEST.MF").write_text(f"Manifest-Version: 1.0\nMain-Class: {main}\n", "utf-8")
                    code, _ = _run_cmd(job, [tools["jar"], "cfm", str(jar_path), str(mf / "MANIFEST.MF"), "-C", str(outd), "."], root)
                    if code != 0:
                        job["status"] = "error"; job["code"] = code; return
                else:
                    _log(job, "jar tool not found."); job["status"] = "error"; job["code"] = 1; return
                _log(job, f"Runnable jar: {jar_path}  (java -jar {jar_path.name})")
                job["extra"] = {"file": str(jar_path), "download": jar_path.name}
                if want == "exe":
                    if tools["jpackage"]:
                        appd = dist / "app"
                        if appd.exists(): shutil.rmtree(appd)
                        cmd = [tools["jpackage"], "--type", "app-image" if os.name != "nt" else "exe",
                               "--name", proj["slug"], "--input", str(dist), "--main-jar", jar_path.name,
                               "--main-class", main, "--dest", str(dist)]
                        # exe type may need WiX on Windows; catch failure
                        code, _ = _run_cmd(job, cmd, root, timeout=180)
                        if code != 0:
                            _log(job, "jpackage could not build an .exe (on Windows it often needs the WiX toolset). The .jar is ready: java -jar " + jar_path.name)
                        else:
                            _log(job, "jpackage finished. Look in " + str(dist))
                    else:
                        _log(job, "jpackage is not in this JDK. The runnable .jar is ready: java -jar " + jar_path.name
                             + "  (jpackage ships with a full JDK, not a JRE.)")
                job["status"] = "ok"; job["code"] = 0; return
            _log(job, "unknown package kind"); job["status"] = "error"; job["code"] = 1
        except Exception as e:
            _log(job, f"{type(e).__name__}: {e}"); job["status"] = "error"; job["code"] = 1
    threading.Thread(target=work, daemon=True).start()
    return job

def start_site(pid):
    proj = get_project(pid); root = project_root(pid)
    site = root / "frontend" if (root / "frontend" / "index.html").exists() else root
    if not (site / "index.html").exists():
        raise BuilderError("no index.html in this project (or frontend/)")
    stop_site(pid)
    handler = lambda *a, **k: _QuietHandler(*a, directory=str(site), **k)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    _SERVERS[pid] = httpd
    url = f"http://127.0.0.1:{port}/"
    return {"url": url, "port": port, "root": str(site)}

def stop_site(pid):
    s = _SERVERS.pop(pid, None)
    if s:
        try: s.shutdown()
        except Exception: pass
        try: s.server_close()
        except Exception: pass
    return bool(s)

class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass
    def translate_path(self, path):
        # SimpleHTTPRequestHandler already confines to directory= when passed
        return super().translate_path(path)

def default_folder(name):
    return str(DEFAULT_PROJECTS() / slugify(name or "project"))
