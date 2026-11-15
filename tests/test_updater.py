"""Unit tests for the update system. Works on a throw-away copy of the app + data folder.
Run: python tests/test_updater.py"""
import hashlib, io, json, os, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path
SRC = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="jiv-updtest-"))
APP = TMP / "app"
shutil.copytree(SRC, APP, ignore=shutil.ignore_patterns(".venv", "__pycache__", "screenshots", "dist", "build"))
os.environ["JIV_APP_DIR"] = str(APP); os.environ["JIV_DATA_DIR"] = str(TMP / "data")
sys.path.insert(0, str(APP))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from jiv import updater as U, selfupdate as SU, __version__ as V
_parts = [int(x) for x in V.split('.')]; _parts[-1]+=1; NEXT='.'.join(map(str,_parts))

KEY = Ed25519PrivateKey.generate(); OTHER = Ed25519PrivateKey.generate()
os.environ["JIV_UPDATE_PUBKEY"] = KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
res = []
def check(n, c, info=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{info}]"))
def raises(fn, text):
    try: fn(); return False
    except U.UpdateError as e: return text.lower() in str(e).lower()
def tree_hash(root):
    return {r: hashlib.sha256(p.read_bytes()).hexdigest() for r, p in U.iter_app_files(root)}
P = lambda n: TMP / n

# --- build / verify ---
pkg = U.build_package(P("good.jivupdate"), NEXT, "Fix things", files={"jiv/static/new.txt": b"hi", "README.md": b"# new readme"},
                      deletes=["start_console.bat"], private_key=KEY)
info = U.verify_package(pkg)
check("signed package verifies", info["signed"] and info["signature"] == "valid" and info["label"].startswith("Signed"))
acts = {f["path"]: f["action"] for f in info["manifest"]["files"]}
check("actions add/replace/delete", acts == {"jiv/static/new.txt": "add", "README.md": "replace", "start_console.bat": "delete"}, acts)
un = U.verify_package(U.build_package(P("unsigned.jivupdate"), NEXT, "x", files={"a.txt": b"1"}))
check("unsigned package labelled 'Unsigned – created locally'", not un["signed"] and un["label"] == "Unsigned – created locally")

def rewrite(src, dst, mutate):
    zin = zipfile.ZipFile(src); zout = zipfile.ZipFile(dst, "w")
    for n in zin.namelist():
        d = zin.read(n); d = mutate(n, d); 
        if d is not None: zout.writestr(n, d)
    zout.close(); return dst
t = rewrite(pkg, P("tampered.jivupdate"), lambda n, d: b"EVIL" if n == "files/README.md" else d)
check("tampered file → hash mismatch", raises(lambda: U.verify_package(t), "hash mismatch"))
t2 = rewrite(pkg, P("tampered-man.jivupdate"), lambda n, d: d.replace(b"Fix things", b"Fix thingz") if n == "manifest.json" else d)
check("edited manifest → bad signature", raises(lambda: U.verify_package(t2), "bad signature"))
t3 = U.build_package(P("otherkey.jivupdate"), NEXT, "x", files={"a.txt": b"1"}, private_key=OTHER)
check("signed with a different key → bad signature", raises(lambda: U.verify_package(t3), "bad signature"))
t4 = rewrite(pkg, P("extra.jivupdate"), lambda n, d: d)
with zipfile.ZipFile(t4, "a") as z: z.writestr("files/sneaky.py", "print(1)")
check("unlisted extra file rejected", raises(lambda: U.verify_package(t4), "unlisted"))

# --- paths ---
for bad, why in [("../evil.py", "traversal"), ("/etc/passwd", "absolute"), ("C:/Windows/x.dll", "absolute"), ("jiv/../../x", "traversal"),
                 (".venv/lib/x.py", "not allowed"), ("data/jiv.db", "user data"), ("config.json", "user data"), ("~/x", "absolute")]:
    check(f"path blocked: {bad}", raises(lambda: U.build_package(P("x.jivupdate"), NEXT, "", files={bad: b"x"}), why))
def handmade(path):
    man = {"format": 1, "version": NEXT, "files": [{"path": path, "action": "add", "sha256": hashlib.sha256(b"x").hexdigest(), "size": 1}]}
    b = io.BytesIO(); z = zipfile.ZipFile(b, "w"); z.writestr("manifest.json", json.dumps(man)); z.writestr("files/" + path, b"x"); z.close()
    p = P("hand.jivupdate"); p.write_bytes(b.getvalue()); return p
check("hand-made manifest with ../ rejected", raises(lambda: U.verify_package(handmade("../../outside.txt")), "traversal"))
b = io.BytesIO(); z = zipfile.ZipFile(b, "w"); z.writestr("manifest.json", json.dumps({"format": 1, "version": "9.0", "files": []})); z.writestr("../slip.txt", "x"); z.close()
P("slip.jivupdate").write_bytes(b.getvalue())
check("zip-slip entry rejected", raises(lambda: U.verify_package(P("slip.jivupdate")), "traversal"))

# --- versions ---
check("downgrade blocked", raises(lambda: U.verify_package(U.build_package(P("old.jivupdate"), "1.0.5", "", files={"a.txt": b"1"})), "downgrade"))
check("same version blocked", raises(lambda: U.verify_package(U.build_package(P("same.jivupdate"), V, "", files={"a.txt": b"1"})), "not newer"))
check("min_version enforced", raises(lambda: U.verify_package(U.build_package(P("min.jivupdate"), "2.0.0", "", files={"a.txt": b"1"}, min_version="9.0.0")), "needs jiv 9.0.0"))
check("not a zip rejected", raises(lambda: (P("junk.jivupdate").write_bytes(b"hello"), U.verify_package(P("junk.jivupdate"))), "not a valid"))

# --- apply / backup / rollback ---
before = tree_hash(APP)
r = U.apply_package(pkg)
after = tree_hash(APP)
check("apply: new file added", (APP / "jiv/static/new.txt").read_bytes() == b"hi")
check("apply: file replaced", (APP / "README.md").read_bytes() == b"# new readme")
check("apply: file deleted", not (APP / "start_console.bat").exists())
check("apply: no .jivnew leftovers", not list(APP.rglob("*.jivnew")))
check("apply: state pending with backup", U.read_state()["status"] == "pending" and U.read_state()["backup"] == r["backup"]["name"])
bl = U.list_backups(); check("backup listed with version", bl and bl[0]["version"] == V and bl[0]["files"] == len(before))
data_db = TMP / "data" / "keep.db"; data_db.write_text("user data")
U.restore_backup(r["backup"]["name"])
check("rollback restores the exact previous tree", tree_hash(APP) == before)
check("rollback leaves user data alone", data_db.read_text() == "user data")
# failure during staging leaves app untouched
bad_hash = rewrite(pkg, P("bad2.jivupdate"), lambda n, d: d)
orig = U.verify_package
U.verify_package = lambda p, **k: orig(pkg)            # pretend verification passed, then staging finds a bad hash
z = zipfile.ZipFile(bad_hash); data = {n: z.read(n) for n in z.namelist()}; z.close()
data["files/README.md"] = b"corrupt"; zz = zipfile.ZipFile(bad_hash, "w"); [zz.writestr(n, d) for n, d in data.items()]; zz.close()
try: U.apply_package(bad_hash); ok = False
except U.UpdateError: ok = True
U.verify_package = orig
check("hash mismatch during staging → nothing changed", ok and tree_hash(APP) == before)

# --- core protection ---
core = U.build_package(P("core.jivupdate"), NEXT, "", files={"jiv/agent.py": (APP / "jiv/agent.py").read_bytes() + b"\n# tweak\n"})
check("unsigned core change refused without advanced", raises(lambda: U.apply_package(core), "advanced"))
U.apply_package(core, advanced=True); check("unsigned core change allowed with advanced", (APP / "jiv/agent.py").read_text().endswith("# tweak\n"))
U.restore_backup(U.list_backups()[0]["name"])
core_s = U.build_package(P("core_s.jivupdate"), NEXT, "", files={"jiv/agent.py": b"# signed core\n"}, private_key=KEY)
check("signed core update does not need advanced", U.verify_package(core_s)["touches_core"] == ["jiv/agent.py"] and U.apply_package(core_s))
U.restore_backup(U.list_backups()[0]["name"]); check("restored after core tests", tree_hash(APP) == before)

# --- make_update.py CLI ---
new = TMP / "newver"; shutil.copytree(APP, new)
(new / "jiv/__init__.py").write_text("__version__ = %s\n" % NEXT); (new / "jiv/static/extra.css").write_text("body{}"); (new / "install.sh").unlink()
kp = TMP / "k.pem"; kp.write_bytes(KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
out = subprocess.run([sys.executable, str(APP / "tools/make_update.py"), "--old", str(APP), "--new", str(new), "--version", NEXT,
                      "--changelog", "cli", "--key", str(kp), "-o", str(P("cli.jivupdate"))], capture_output=True, text=True, env=os.environ)
ci = U.verify_package(P("cli.jivupdate")) if out.returncode == 0 else None
check("make_update.py builds a signed diff package", ci and ci["signed"] and {f["path"]: f["action"] for f in ci["manifest"]["files"]} ==
      {"jiv/__init__.py": "replace", "jiv/static/extra.css": "add", "install.sh": "delete"}, out.stdout + out.stderr)
shutil.make_archive(str(TMP / "oldzip"), "zip", TMP, "app")
out2 = subprocess.run([sys.executable, str(APP / "tools/make_update.py"), "--old", str(TMP / "oldzip.zip"), "--new", str(new), "--version", NEXT,
                       "-o", str(P("cli2.jivupdate"))], capture_output=True, text=True, env=os.environ)
check("make_update.py accepts the old version as a .zip", out2.returncode == 0 and "UNSIGNED" in out2.stdout, out2.stdout + out2.stderr)

# --- self-update (propose_update) ---
m = SU.propose("Add greeter", "Adds a greeting helper", [{"path": "jiv/greet.py", "content": "def hello(n):\n    return f'Hello {n}'\n"}])
check("propose: good change builds + checks pass", m["ok"] and m["checks_ok"] and m["origin"] == "self" and not m["signed"] and m["manifest"]["version"] == U.bump_patch(V), m.get("checks"))
check("propose: ran syntax check and tests", any("py_compile" in c["name"] for c in m["checks"]) and any(c["name"].startswith("tests") and c["ok"] for c in m["checks"]))
m2 = SU.propose("Broken", "", [{"path": "jiv/broken.py", "content": "def x(:\n"}])
check("propose: syntax error is caught, tests skipped", not m2["checks_ok"] and m2["checks"][0]["ok"] is False and "skipped" in m2["checks"][-1]["output"])
m3 = SU.propose("Breaks tests", "", [{"path": "jiv/models_catalog.py", "content": "CATALOG = []\ndef recommend(h): return {}\n"}])
check("propose: failing tests reported", not m3["checks_ok"] and not m3["checks"][-1]["ok"])
check("propose: path outside app refused", raises(lambda: SU.propose("x", "", [{"path": "../../etc/x", "content": "1"}]), "traversal"))
m4 = SU.propose("Core", "", [{"path": "jiv/updater.py", "content": (APP / "jiv/updater.py").read_text() + "\n# x\n"}])
check("propose: core edit flagged", m4["touches_core"] == ["jiv/updater.py"])
check("app tree untouched by proposals", tree_hash(APP) == before)
check("repo parsing", U.parse_repo("https://github.com/me/jiv-desktop.git") == ("me", "jiv-desktop") and U.parse_repo("me/jiv") == ("me", "jiv"))

# --- 1.2: pip requirements + installed (JivSetup) layout ---
for good in ["rich", "rich>=13", "rich[jupyter]>=13,<15", "python-dateutil==2.9.0"]:
    check(f"requirement allowed: {good}", U.check_requirements([good]) == [good])
for bad in ["--index-url http://evil", "-e .", "git+https://x/y", "pkg @ https://x/y.whl", "../local", "rich; os_name=='nt'", "a b"]:
    check(f"requirement refused: {bad}", raises(lambda: U.check_requirements([bad]), "not allowed"))
rq = U.build_package(P("req.jivupdate"), NEXT, "", files={"a.txt": b"1"}, extra={"requirements": ["rich>=13"]}, private_key=KEY)
check("requirements are part of the signed manifest", U.verify_package(rq)["requirements"] == ["rich>=13"])
z = zipfile.ZipFile(rq); m = json.loads(z.read("manifest.json")); m["requirements"] = ["evilpkg"]
buf = io.BytesIO(); zo = zipfile.ZipFile(buf, "w")
for n in z.namelist(): zo.writestr(n, json.dumps(m, indent=2, sort_keys=True) if n == "manifest.json" else z.read(n))
zo.close(); P("req_t.jivupdate").write_bytes(buf.getvalue())
check("tampered requirements break the signature", raises(lambda: U.verify_package(P("req_t.jivupdate")), "signature"))
bad_rq = U.build_package(P("reqbad.jivupdate"), NEXT, "", files={"a.txt": b"1"}, extra={"requirements": ["--extra-index-url=http://x"]})
check("package with option-style requirement refused", raises(lambda: U.verify_package(bad_rq), "not allowed"))
check("dev checkout is not 'installed'", U.layout()["installed"] is False)
inst = TMP / "Programs" / "JivDesktop"; (inst / "python" / "Lib" / "site-packages").mkdir(parents=True)
(inst / "JivDesktop.exe").write_bytes(b"MZ"); (inst / "python" / "python.exe").write_bytes(b"")
old_app = U.APP_DIR; U.APP_DIR = inst / "app"; U.APP_DIR.mkdir()
lay = U.layout()
check("installed layout detected", lay["installed"] and lay["launcher"].endswith("JivDesktop.exe") and lay["site_packages"].endswith(os.path.join("Lib", "site-packages")))
check("pip uses the embedded python.exe", U.console_python() == str(inst / "python" / "python.exe"))
U.APP_DIR = old_app
for f in ("make_update.py",):
    nd = TMP / "reqnew"; shutil.rmtree(nd, ignore_errors=True); shutil.copytree(APP, nd)
    (nd / "requirements.txt").write_text((APP / "requirements.txt").read_text() + "rich>=13\n")
    o = subprocess.run([sys.executable, str(APP / "tools/make_update.py"), "--old", str(APP), "--new", str(nd), "--version", "1.2.7", "-o", str(P("mk_req.jivupdate"))], capture_output=True, text=True)
    check("make_update picks up new requirements.txt lines", o.returncode == 0 and json.loads(zipfile.ZipFile(P("mk_req.jivupdate")).read("manifest.json"))["requirements"] == ["rich>=13"], o.stdout + o.stderr)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
