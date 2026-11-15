"""Stand-alone relauncher + health watchdog (no jiv imports, so a broken update can't break it).
Run as: python relaunch.py job.json   (the app copies this file to the data folder first).
1. wait for the old app to exit (its port becomes free)  2. start the new version
3. poll http://127.0.0.1:<port>/health for up to <timeout> s, expecting the new version
4. on failure: kill it, restore the backup, start the previous version again, record why."""
import json, os, shutil, socket, subprocess, sys, time, urllib.request
from pathlib import Path

EXCLUDE_DIRS = {".venv", "venv", "__pycache__", ".git", "build", "dist", "node_modules", ".pytest_cache"}

def log(job, msg):
    with open(job["log"], "a", encoding="utf-8") as f: f.write(time.strftime("%H:%M:%S ") + msg + "\n")

def port_free(port):
    with socket.socket() as s:
        if os.name != "nt": s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # ignore TIME_WAIT like uvicorn does
        try: s.bind(("127.0.0.1", port)); return True
        except OSError: return False

def files(root):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in EXCLUDE_DIRS and not d.startswith(".jiv")]
        for f in fns:
            if not f.endswith((".pyc", ".pyo", ".jivnew")): yield Path(dp, f).relative_to(root).as_posix()

def restore(backup_app, app_dir):
    want = set(files(backup_app))
    for rel in list(files(app_dir)):
        if rel not in want: Path(app_dir, rel).unlink()
    for rel in want:
        t = Path(app_dir, rel); t.parent.mkdir(parents=True, exist_ok=True)
        tmp = t.with_name(t.name + ".jivnew"); shutil.copy2(Path(backup_app, rel), tmp); os.replace(tmp, t)
    for dp, dns, _ in os.walk(app_dir):   # drop stale bytecode so old .py files are re-read
        for d in dns:
            if d == "__pycache__": shutil.rmtree(Path(dp, d), ignore_errors=True)

def start(job):
    kw = dict(cwd=job["app_dir"], stdin=subprocess.DEVNULL, stdout=open(job["log"] + ".app", "a"), stderr=subprocess.STDOUT)
    if os.name == "nt": kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
    else: kw["start_new_session"] = True
    env = dict(os.environ); env.update(job.get("env") or {}); kw["env"] = env
    return subprocess.Popen(job["cmd"], **kw)

def healthy(job, proc, expect):
    deadline = time.time() + job.get("timeout", 30)
    while time.time() < deadline:
        if proc.poll() is not None: return False, f"process exited with code {proc.returncode}"
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{job['port']}/health", timeout=2) as r:
                d = json.loads(r.read())
                if d.get("ok") and (expect is None or d.get("version") == expect): return True, "ok"
                if d.get("version") != expect: return False, f"health reports version {d.get('version')}, expected {expect}"
        except Exception: pass
        time.sleep(0.5)
    return False, f"no healthy response within {job.get('timeout', 30)}s"

def set_state(job, **kw):
    p = Path(job["state_file"])
    try: s = json.loads(p.read_text())
    except Exception: s = {}
    s.update(kw, updated=time.time()); p.write_text(json.dumps(s, indent=2))

def main():
    job = json.loads(Path(sys.argv[1]).read_text())
    log(job, f"relauncher started; waiting for port {job['port']}")
    t0 = time.time()
    while not port_free(job["port"]) and time.time() - t0 < 30: time.sleep(0.2)
    proc = start(job); log(job, f"started new version pid {proc.pid}: {job['cmd']}")
    ok, why = healthy(job, proc, job.get("expect"))
    if ok:
        log(job, "health check passed"); set_state(job, status="ok", message=f"Updated to {job.get('expect')}"); return
    log(job, "HEALTH CHECK FAILED: " + why)
    try: proc.kill(); proc.wait(10)
    except Exception: pass
    if not job.get("backup_app"):
        set_state(job, status="failed", message=why); return
    restore(job["backup_app"], job["app_dir"]); log(job, "backup restored")
    t0 = time.time()
    while not port_free(job["port"]) and time.time() - t0 < 15: time.sleep(0.2)
    proc = start(job); ok2, why2 = healthy(job, proc, job.get("previous"))
    log(job, f"previous version restarted: {ok2} {why2}")
    set_state(job, status="rolled_back", message=f"The update failed its health check ({why}) and was rolled back automatically.",
              rollback_ok=ok2)

if __name__ == "__main__": main()
