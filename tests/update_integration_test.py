"""End-to-end update test on a throw-away copy of the app (real restarts, health checks, rollback).
Run: python tests/update_integration_test.py"""
import json, os, shutil, signal, subprocess, sys, tempfile, time, socket
from pathlib import Path
import httpx
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable
TMP = Path(tempfile.mkdtemp(prefix="jiv-upd-int-")); APP = TMP / "app"; DATA = TMP / "data"
shutil.copytree(SRC, APP, ignore=shutil.ignore_patterns(".venv", "__pycache__", "screenshots", "dist", "build"))
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
PORT, GH = free_port(), free_port()
sys.path.insert(0, str(SRC))
os.environ["JIV_DATA_DIR"] = str(DATA)
from jiv import updater as U
res = []
def check(n, c, info=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{info}]"), flush=True)
KEY = "/workspace/jiv-signing/jiv_update_private.pem"

# v1.6.2: signed release built with make_update.py (bumps version, adds a file)
NEW = TMP / "v111"; shutil.copytree(APP, NEW)
(NEW / "jiv/__init__.py").write_text('__version__ = "1.6.3"\n'); (NEW / "jiv/static/whatsnew.txt").write_text("1.6.3 is here")
subprocess.run([PY, str(SRC / "tools/make_update.py"), "--old", str(APP), "--new", str(NEW), "--version", "1.6.3", "--changelog",
                "## 1.6.3\n- Adds whatsnew.txt", "--key", KEY, "-o", str(TMP / "jiv-1.6.3.jivupdate")], check=True, capture_output=True)
# v1.6.3: unsigned, breaks server.py at import -> must auto-roll back
BAD = TMP / "v112"; shutil.copytree(NEW, BAD)
(BAD / "jiv/__init__.py").write_text('__version__ = "1.6.4"\n')
(BAD / "jiv/server.py").write_text((NEW / "jiv/server.py").read_text() + "\nraise RuntimeError('simulated broken update')\n")
subprocess.run([PY, str(SRC / "tools/make_update.py"), "--old", str(NEW), "--new", str(BAD), "--version", "1.6.4", "--changelog", "broken",
                "-o", str(TMP / "jiv-1.6.4.jivupdate")], check=True, capture_output=True)

gh = subprocess.Popen([PY, str(SRC / "tests/mock_github.py"), str(GH), str(TMP / "jiv-1.6.3.jivupdate"), "v1.6.3"])
env = dict(os.environ, JIV_DATA_DIR=str(DATA), JIV_GITHUB_API=f"http://127.0.0.1:{GH}", JIV_HEALTH_TIMEOUT="30",
           JIV_UPDATE_FIRST_CHECK="2", JIV_UPDATE_INTERVAL="3", JIV_IDLE_SECS="3")
app = subprocess.Popen([PY, str(APP / "run.py"), "--no-browser", "--port", str(PORT)], cwd=APP, env=env,
                       stdout=open(TMP / "app.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
B = f"http://127.0.0.1:{PORT}"
def health(timeout=60, want=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            h = httpx.get(B + "/health", timeout=2).json()
            if want is None or h["version"] == want: return h
        except Exception: pass
        time.sleep(0.5)
    return None
def client():
    import re
    tok = re.search(r'jiv-token" content="([^"]+)"', httpx.get(B + "/").text).group(1)
    return httpx.Client(base_url=B, headers={"X-Jiv-Token": tok}, timeout=60)
try:
    check("app 1.6.2 starts", (health(30) or {}).get("version") == "1.6.2")
    c = client()
    check("check without repo → not configured", c.post("/api/updates/check").json()["configured"] is False)
    c.post("/api/updates/settings", json={"update_repo": "me/empty"})
    check("repo without releases handled", "No releases" in c.post("/api/updates/check").json().get("message", ""))
    c.post("/api/updates/settings", json={"update_repo": "https://github.com/me/jiv-desktop"})
    r = c.post("/api/updates/check").json(); st = r.get("staged") or {}
    check("GitHub check finds 1.6.3 and downloads + verifies it", r.get("available") and r["latest"] == "1.6.3" and st.get("ok") and st.get("signed"), r)
    check("diff preview included", any(f["path"] == "jiv/static/whatsnew.txt" and f["action"] == "add" for f in st.get("diff", [])))
    check("apply refused without approval", c.post(f"/api/updates/apply/{st['id']}", json={}).status_code == 400)
    a = c.post(f"/api/updates/apply/{st['id']}", json={"approved": True}).json()
    check("apply → restarting", a.get("restarting") and a["version"] == "1.6.3", a)
    h = health(60, "1.6.3"); check("restarted as 1.6.3 and healthy", h and h["version"] == "1.6.3")
    time.sleep(1); c = client()
    u = c.get("/api/updates").json()
    check("state ok after restart", u["state"].get("status") == "ok", u["state"])
    check("backup of 1.6.2 exists", u["backups"] and u["backups"][0]["version"] == "1.6.2")
    check("new file present in app folder", (APP / "jiv/static/whatsnew.txt").read_text() == "1.6.3 is here")
    check("user data untouched (config kept repo)", c.get("/api/updates").json()["repo"] == "https://github.com/me/jiv-desktop")

    # broken unsigned update -> health check fails -> automatic rollback
    up = c.post("/api/updates/upload", content=(TMP / "jiv-1.6.4.jivupdate").read_bytes(), headers={"X-Filename": "jiv-1.6.4.jivupdate"}).json()
    check("uploaded unsigned update shows as created locally", up["ok"] and not up["signed"] and up["label"] == "Unsigned – created locally" and up["touches_core"] == ["jiv/server.py"], up)
    check("core change refused without advanced", c.post(f"/api/updates/apply/{up['id']}", json={"approved": True}).status_code == 400)
    t0 = time.time()
    a = c.post(f"/api/updates/apply/{up['id']}", json={"approved": True, "advanced": True}).json()
    check("broken update applied → restarting", a.get("restarting"), a)
    time.sleep(3)
    rb = None
    while time.time() - t0 < 90:
        s = json.loads((DATA / "updates/state.json").read_text())
        if s.get("status") in ("rolled_back", "ok", "failed"): rb = s; break
        time.sleep(1)
    h = health(30, "1.6.3")
    check("failed health check → auto-rolled back to 1.6.3", rb and rb["status"] == "rolled_back" and h and h["version"] == "1.6.3", rb)
    check("rollback happened within ~45s", time.time() - t0 < 60, time.time() - t0)
    check("server.py restored (no broken code)", "simulated broken update" not in (APP / "jiv/server.py").read_text())
    time.sleep(1); c = client()
    check("UI state reports the rollback reason", "rolled back" in c.get("/api/updates").json()["state"]["message"])

    # manual rollback to 1.6.2
    b110 = next(b for b in c.get("/api/updates").json()["backups"] if b["version"] == "1.6.2")
    check("rollback refused without approval", c.post("/api/updates/rollback", json={"backup": b110["name"]}).status_code == 400)
    r = c.post("/api/updates/rollback", json={"approved": True, "backup": b110["name"]}).json()
    h = health(60, "1.6.2"); check("manual rollback → back on 1.6.2", r.get("restarting") and h and h["version"] == "1.6.2", r)
    check("added file removed by rollback", not (APP / "jiv/static/whatsnew.txt").exists())
    time.sleep(1); c = client()
    audit = [(x["tool"], x["decision"]) for x in c.get("/api/audit").json()]
    check("audit has apply + rollback entries", ("apply_update", "allowed") in audit and ("rollback", "allowed") in audit and ("apply_update", "denied") in audit, audit[:8])

    # auto-install signed updates when idle
    c.post("/api/updates/settings", json={"auto_install_signed": True})
    h = health(90, "1.6.3"); check("auto-install picked up signed 1.6.3 and restarted when idle", h and h["version"] == "1.6.3")
    time.sleep(1); c = client()
    check("auto-update recorded in audit as auto-update", any(x["agent"] == "auto-update" for x in c.get("/api/audit").json()))
finally:
    try:
        port_pid = subprocess.run(["bash", "-c", f"ss -ltnp | grep ':{PORT} ' | grep -o 'pid=[0-9]*' | cut -d= -f2"], capture_output=True, text=True).stdout.split()
        for p in port_pid: os.kill(int(p), signal.SIGTERM)
    except Exception: pass
    for p in (app, gh):
        try: p.terminate()
        except Exception: pass
    lg = DATA / "updates/relaunch.log.app"
    if lg.exists(): print("--- app log ---\n" + lg.read_text()[-3000:])
    print((DATA / "updates/relaunch.log").read_text() if (DATA / "updates/relaunch.log").exists() else "")
    shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
