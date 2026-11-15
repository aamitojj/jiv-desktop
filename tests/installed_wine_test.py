"""Installed-layout test under Wine: silent install of JivSetup.exe, launch via JivDesktop.exe, /health,
signed .jivupdate (feature), auto-rollback of a broken update, pip requirements into the embedded Python,
manual rollback, big update via a JivSetup.exe from (mock) GitHub, upgrade keeps data, uninstall.
Usage: python tests/installed_wine_test.py <JivSetup.exe> [wineprefix]     (Linux with wine)"""
import json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path
import httpx
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable
SETUP = Path(sys.argv[1]).resolve()
PREFIX = Path(sys.argv[2] if len(sys.argv) > 2 else os.environ.get("WINEPREFIX", Path.home() / ".wine"))
USER = os.environ.get("USER", "box")
LOCAL = PREFIX / f"drive_c/users/{USER}/AppData/Local/Programs/JivDesktop"
DATA = PREFIX / f"drive_c/users/{USER}/AppData/Roaming/JivDesktop"
KEY = os.environ.get("JIV_SIGNING_KEY", "/workspace/jiv-signing/jiv_update_private.pem")
SAMPLE = Path(os.environ.get("JIV_SAMPLE_UPDATE", "/workspace/jiv-updates/jiv-1.2.1-export-chat.jivupdate"))
WHEELS = os.environ.get("JIV_TEST_WHEELS", "/workspace/winbuild/pipwheels")  # contains six-1.17.0 wheel
TMP = Path(tempfile.mkdtemp(prefix="jiv-wine-"))
PORT = 18765; B = f"http://127.0.0.1:{PORT}"
ENV = {k: v for k, v in os.environ.items() if not k.startswith("JIV_")}
ENV.update(WINEPREFIX=str(PREFIX), WINEDEBUG="-all", JIV_HEALTH_TIMEOUT="60",
           JIV_PIP_FIND_LINKS="Z:" + WHEELS.replace("/", "\\"),
           JIV_GITHUB_API="http://127.0.0.1:18799", JIV_INSTALLER_ARGS='["/S", "/RELAUNCH"]')
res = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:600]}]"), flush=True)
def wine(*a, wait=True, timeout=300):
    if wait: return subprocess.run(["wine", *map(str, a)], env=ENV, capture_output=True, text=True, timeout=timeout)
    return subprocess.Popen(["wine", *map(str, a)], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
def health(timeout=90, want=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            h = httpx.get(B + "/health", timeout=2).json()
            if want is None or h["version"] == want: return h
        except Exception: pass
        time.sleep(1)
    return None
def down(timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try: httpx.get(B + "/health", timeout=1)
        except Exception: return True
        time.sleep(0.5)
    return False
def client():
    tok = re.search(r'jiv-token" content="([^"]+)"', httpx.get(B + "/").text).group(1)
    return httpx.Client(base_url=B, headers={"X-Jiv-Token": tok}, timeout=300)
def upload(c, path):
    return c.post("/api/updates/upload", content=Path(path).read_bytes(), headers={"X-Filename": Path(path).name}).json()
def state(): return json.loads((DATA / "updates/state.json").read_text())
def wait_state(statuses, t=150):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            s = state()
            if s.get("status") in statuses: return s
        except Exception: pass
        time.sleep(1)
    return None
def make(old, ver, mutate, signed=True):
    new = TMP / f"v{ver}"; shutil.rmtree(new, ignore_errors=True); shutil.copytree(old, new); mutate(new)
    out = TMP / f"jiv-{ver}.jivupdate"
    cmd = [PY, str(SRC / "tools/make_update.py"), "--old", str(old), "--new", str(new), "--version", ver, "--changelog", f"test {ver}", "-o", str(out)]
    if signed: cmd += ["--key", KEY]
    p = subprocess.run(cmd, capture_output=True, text=True); assert p.returncode == 0, p.stderr
    return out

subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA"); time.sleep(3)
shutil.rmtree(DATA, ignore_errors=True)
gh = None
try:
    # ---------- install ----------
    r = wine(SETUP, "/S"); time.sleep(2)
    check("silent install exits 0", r.returncode == 0, r.stderr)
    check("installed to %LOCALAPPDATA%\\Programs\\JivDesktop", (LOCAL / "JivDesktop.exe").exists() and (LOCAL / "python/pythonw.exe").exists() and (LOCAL / "app/run.py").exists())
    check("app source shipped as plain .py, no private key", (LOCAL / "app/jiv/updater.py").exists() and not list((LOCAL / "app").rglob("*.pem")))
    reg = wine("reg", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\JivDesktop").stdout
    check("per-user uninstall entry, version 1.2.0", "DisplayVersion" in reg and "1.2.0" in reg and "Uninstall.exe" in reg, reg)
    sm = PREFIX / f"drive_c/users/{USER}/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Jiv Desktop.lnk"
    dl = PREFIX / f"drive_c/users/{USER}/Desktop/Jiv Desktop.lnk"
    check("Start menu + desktop shortcuts", sm.exists() and dl.exists())
    DATA.mkdir(parents=True, exist_ok=True); (DATA / "config.json").write_text(json.dumps({"port": PORT, "setup_done": True}))
    # ---------- launch ----------
    wine(LOCAL / "JivDesktop.exe", "--no-browser", wait=False)
    h = health(120); check("JivDesktop.exe starts the embedded Python; /health ok", h and h["version"] == "1.2.0", h)
    c = client(); u = c.get("/api/updates").json()
    check("updater sees the installed layout", u["layout"]["installed"] and u["layout"]["app_dir"].lower().endswith("jivdesktop\\app") and u["restart_supported"], u["layout"])
    check("not treated as a frozen exe", u["frozen"] is False)
    r2 = wine(LOCAL / "JivDesktop.exe", "--no-browser", timeout=60)
    check("second launch exits quickly (already running)", r2.returncode == 0)
    base = TMP / "base"; shutil.copytree(LOCAL / "app", base, ignore=shutil.ignore_patterns("__pycache__"))

    # ---------- 1. signed feature update (the sample package) ----------
    st = upload(c, SAMPLE)
    check("sample 1.2.1 feature package verified + signed", st.get("ok") and st.get("signed"), st)
    a = c.post(f"/api/updates/apply/{st['id']}", json={"approved": True}).json()
    check("apply → restarting", a.get("restarting"), a)
    job = json.loads((DATA / "updates/relaunch-job.json").read_text())
    check("restart goes through JivDesktop.exe", job["cmd"][0].lower().endswith("jivdesktop.exe"), job["cmd"])
    h = health(150, "1.2.1"); check("restarted as 1.2.1 under the installed layout", h and h["version"] == "1.2.1", h)
    check("feature file installed into {app}\\app", (LOCAL / "app/jiv/static/export.js").exists())
    check("state ok", (wait_state({"ok", "rolled_back", "failed"}, 30) or {}).get("status") == "ok", state())
    time.sleep(2); c = client()
    check("export button served", "exportChat" in c.get("/").text)
    v121 = TMP / "v121"; shutil.copytree(LOCAL / "app", v121, ignore=shutil.ignore_patterns("__pycache__"))

    # ---------- 2. broken update → automatic rollback ----------
    bad = make(v121, "1.2.2", lambda n: (n / "jiv/server.py").write_text((n / "jiv/server.py").read_text() + "\nraise RuntimeError('broken update')\n"), signed=False)
    st = upload(c, bad); t0 = time.time()
    a = c.post(f"/api/updates/apply/{st['id']}", json={"approved": True, "advanced": True}).json()
    check("broken 1.2.2 applied → restarting", a.get("restarting"), a)
    time.sleep(3)
    s = wait_state({"rolled_back", "failed"}, 200)
    h = health(120, "1.2.1")
    check("health check failed → rolled back to 1.2.1 automatically", s and s["status"] == "rolled_back" and h and h["version"] == "1.2.1", s)
    check("server.py restored", "broken update" not in (LOCAL / "app/jiv/server.py").read_text())
    print(f"     (broken update → healthy 1.2.1 again in {time.time() - t0:.0f}s)")
    time.sleep(2); c = client()

    # ---------- 3. update with a new pip requirement ----------
    def add_req(n):
        (n / "requirements.txt").write_text((n / "requirements.txt").read_text() + "six==1.17.0\n")
        (n / "jiv/sixcheck.py").write_text("import six\nVERSION = six.__version__\n")
    rq = make(v121, "1.2.3", add_req)
    st = upload(c, rq)
    check("requirement listed in the signed package", st.get("manifest", {}).get("requirements") == ["six==1.17.0"], st)
    a = c.post(f"/api/updates/apply/{st['id']}", json={"approved": True})
    check("apply with pip install → restarting", a.status_code == 200 and a.json().get("restarting"), a.text)
    h = health(150, "1.2.3"); check("restarted as 1.2.3", h and h["version"] == "1.2.3", h)
    check("six installed into the embedded python\\Lib\\site-packages", (LOCAL / "python/Lib/site-packages/six.py").exists())
    time.sleep(2); c = client()
    check("pip install audited", any(x["tool"] == "pip_install" and x["decision"] == "allowed" for x in c.get("/api/audit").json()))

    # ---------- 4. manual rollback ----------
    bk = c.get("/api/updates").json()["backups"]
    b121 = next(b for b in bk if b["version"] == "1.2.1")
    r = c.post("/api/updates/rollback", json={"approved": True, "backup": b121["name"]}).json()
    h = health(150, "1.2.1"); check("Roll back → 1.2.1", r.get("restarting") and h and h["version"] == "1.2.1", r)
    check("rollback removed the 1.2.3 file", not (LOCAL / "app/jiv/sixcheck.py").exists())
    time.sleep(2); c = client()

    # ---------- 5. big update: JivSetup.exe from GitHub releases ----------
    gh = subprocess.Popen([PY, str(SRC / "tests/mock_github.py"), "18799", "-", "v1.3.0", str(SETUP)]); time.sleep(2)
    c.post("/api/updates/settings", json={"update_repo": "me/jiv"})
    r = c.post("/api/updates/check").json()
    check("check offers the full installer", (r.get("installer") or {}).get("name") == "JivSetup-1.3.0.exe" and "installer" in r.get("message", ""), r)
    check("installer refused without permission", c.post("/api/updates/installer", json={}).status_code == 400)
    (DATA / "keepme.txt").write_text("user data")
    r = c.post("/api/updates/installer", json={"approved": True}).json()
    check("installer downloaded + started", r.get("started"), r)
    check("Jiv closed for the installer", down(30))
    h = health(240)
    check("installer ran and relaunched Jiv (/RELAUNCH)", h is not None, h)
    check("fresh program files after reinstall", not (LOCAL / "app/jiv/static/export.js").exists())
    check("user data kept across the upgrade", (DATA / "keepme.txt").exists() and json.loads((DATA / "config.json").read_text()).get("update_repo") == "me/jiv")
    if h:
        time.sleep(2); c = client()
        check("run_installer audited", any(x["tool"] == "run_installer" for x in c.get("/api/audit").json()))
    # ---------- 6. uninstall ----------
    subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True); time.sleep(2)
    wine(LOCAL / "Uninstall.exe", "/S"); time.sleep(5)
    check("uninstall removes program files", not (LOCAL / "app").exists() and not (LOCAL / "JivDesktop.exe").exists())
    check("uninstall keeps user data by default", (DATA / "keepme.txt").exists())
    check("uninstall entry + shortcuts removed", "DisplayVersion" not in wine("reg", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\JivDesktop").stdout and not sm.exists() and not dl.exists())
    wine(SETUP, "/S"); time.sleep(2); wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA"); time.sleep(5)
    check("reinstall, then uninstall /DELETEDATA removes data", not DATA.exists())
finally:
    if gh: gh.terminate()
    subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
    lg = DATA / "updates/relaunch.log"
    if lg.exists(): print("--- relaunch.log (tail) ---\n" + lg.read_text()[-1500:])
    shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
