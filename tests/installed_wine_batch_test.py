"""Wine: install JivSetup-1.2.0.exe, apply 1.2.2 (pip from PyPI) and 1.3.0, then 1.3.0 → 1.4.0 (Batch). In the installed app:
run a 200-MCQ batch job with verify, restart Jiv mid-job (wineserver -k + relaunch) and check it resumes, download the review
Excel (openpyxl in the embedded Python), run an HTML zip job. python tests/installed_wine_batch_test.py <setup.exe> <1.2.2> <1.3.0> <1.4.0> [prefix]"""
import json, os, re, socket, subprocess, sys, time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable
SETUP, PKG, PKG13, PKG14 = (Path(a).resolve() for a in sys.argv[1:5])
PREFIX = Path(sys.argv[5] if len(sys.argv) > 5 else os.environ.get("WINEPREFIX", Path.home() / ".wine"))
USER = os.environ.get("USER", "box")
LOCAL = PREFIX / f"drive_c/users/{USER}/AppData/Local/Programs/JivDesktop"; DATA = PREFIX / f"drive_c/users/{USER}/AppData/Roaming/JivDesktop"
PORT, LLM = 18765, 18766; B = f"http://127.0.0.1:{PORT}"; OUT = SRC / "screenshots"; FX = SRC / "tests/fixtures/attach"
ENV = {k: v for k, v in os.environ.items() if not k.startswith("JIV_")}; ENV.update(WINEPREFIX=str(PREFIX), WINEDEBUG="-all", JIV_HEALTH_TIMEOUT="60", JIV_WINGET="Z:" + str(SRC / "tests/fixtures/bin/winget.cmd").replace("/", "\\"))
res = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:500]}]"), flush=True)
def wine(*a, wait=True):
    if wait: return subprocess.run(["wine", *map(str, a)], env=ENV, capture_output=True, text=True, timeout=300)
    return subprocess.Popen(["wine", *map(str, a)], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
def health(t=120, want=None):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            h = httpx.get(B + "/health", timeout=2).json()
            if not want or h["version"] == want: return h
        except Exception: pass
        time.sleep(1)
def client():
    tok = re.search(r'jiv-token" content="([^"]+)"', httpx.get(B + "/").text).group(1)
    return httpx.Client(base_url=B, headers={"X-Jiv-Token": tok}, timeout=600), tok

F = SRC / "tests/fixtures/batch"
import io, zipfile
def apply(c, pkg, ver, t=240):
    st = c.post("/api/updates/upload", content=pkg.read_bytes(), headers={"X-Filename": pkg.name}).json()
    a = c.post(f"/api/updates/apply/{st['id']}", json={"approved": True}) if st.get("ok") else None
    return st, a, health(t, ver)
def wait_job(c, jid, cond, t=600):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            j = c.get(f"/api/batch/jobs/{jid}?log=1000").json()
            if cond(j): return j
        except Exception: pass
        time.sleep(1)
    return c.get(f"/api/batch/jobs/{jid}?log=1000").json()
subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA"); time.sleep(3)
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)], env=dict(os.environ, MOCK_DELAY="0.03"))
try:
    check("install 1.2.0", wine(SETUP, "/S").returncode == 0); time.sleep(2)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "config.json").write_text(json.dumps({"port": PORT, "setup_done": True, "base_url": f"http://127.0.0.1:{LLM}/v1", "model": "qwen2.5:3b"}))
    wine(LOCAL / "JivDesktop.exe", "--no-browser", wait=False)
    check("1.2.0 running", (health() or {}).get("version") == "1.2.0")
    c, _ = client()
    st, a, h = apply(c, PKG, "1.2.2"); check("1.2.0 → 1.2.2", h and h["version"] == "1.2.2", st.get("error")); time.sleep(2); c, _ = client()
    st, a, h = apply(c, PKG13, "1.3.0"); check("1.2.2 → 1.3.0", h and h["version"] == "1.3.0", st.get("error")); time.sleep(2); c, _ = client()
    check("1.3.0 has no Batch API", c.get("/api/batch/jobs").status_code == 404)
    st, a, h = apply(c, PKG14, "1.4.0")
    check("1.4.0 package signed, min-version 1.3.0, no pip requirements", st.get("ok") and st.get("signed") and st["manifest"].get("min_version") == "1.3.0" and not st["manifest"].get("requirements"), st)
    check("1.3.0 → 1.4.0 applied and healthy", h and h["version"] == "1.4.0", h)
    time.sleep(2); c, tok = client()
    check("backup of 1.3.0 kept for rollback", any(b["version"] == "1.3.0" for b in c.get("/api/updates").json()["backups"]))
    check("built-in templates present", sum(t["builtin"] for t in c.get("/api/batch/templates").json()["templates"]) == 4)
    up = c.post("/api/batch/upload", content=(F / "mcq_200.json").read_bytes(), headers={"X-Filename": "mcq_200.json"}).json()
    spec = {"source": "set:" + up["set"], "template": "mcq"}
    e = c.post("/api/batch/estimate", json=spec).json()
    check("estimate from a test item in the installed app", e.get("tok_per_s") and e["items"] == 200, e)
    check("start without approval refused", c.post("/api/batch/jobs", json={"spec": spec}).status_code == 400)
    j = c.post("/api/batch/jobs", json={"spec": spec, "approved": True, "estimate": e}).json()
    check("job output under %APPDATA%\\JivDesktop\\batch", "jivdesktop\\batch\\jobs" in j["output_dir"].lower(), j["output_dir"])
    jj = wait_job(c, j["id"], lambda x: x["done"] >= 40, 300)
    check("job running in the installed app", jj["done"] >= 40, jj.get("done"))
    # simulate a PC restart: kill everything, relaunch Jiv
    subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True); time.sleep(2)
    wine(LOCAL / "JivDesktop.exe", "--no-browser", wait=False)
    check("Jiv back after the hard stop", (health(120, "1.4.0") or {}).get("version") == "1.4.0")
    c, tok = client()
    fin = wait_job(c, j["id"], lambda x: x["status"] == "done", 600)
    idx = [l["idx"] for l in fin["log"] if l["idx"] is not None and ("✓ ok" in l["text"] or "⚠ flagged" in l["text"])]
    check("resumed after restart and finished; each item once", fin["status"] == "done" and fin["done"] == 200 and len(idx) == 200 == len(set(idx))
          and any("resuming" in l["text"] for l in fin["log"]), (fin["status"], fin["done"], len(idx), len(set(idx))))
    check("4 items flagged for review", fin["flagged"] == 4, fin["flagged"])
    r = httpx.get(B + f"/api/batch/jobs/{j['id']}/download/mcq_200_rewritten.json?t={tok}", timeout=60)
    check("result JSON downloads (200 items + explanation)", r.status_code == 200 and len(r.json()) == 200 and all("explanation" in x for x in r.json()))
    r = httpx.get(B + f"/api/batch/jobs/{j['id']}/download/review.xlsx?t={tok}", timeout=60)
    from openpyxl import load_workbook
    check("review.xlsx built by the embedded Python (openpyxl)", r.status_code == 200 and load_workbook(io.BytesIO(r.content))["Flagged for review"].max_row == 9)
    up = c.post("/api/batch/upload", content=(F / "site.zip").read_bytes(), headers={"X-Filename": "site.zip"}).json()
    j2 = c.post("/api/batch/jobs", json={"spec": {"source": "set:" + up["set"], "template": "adsense"}, "approved": True}).json()
    f2 = wait_job(c, j2["id"], lambda x: x["status"] == "done", 300)
    r = httpx.get(B + f"/api/batch/jobs/{j2['id']}/download/site_rewritten.zip?t={tok}", timeout=60)
    z = zipfile.ZipFile(io.BytesIO(r.content)); new = z.read("site/index.html").decode("utf-8"); old = (F / "site/index.html").read_text()
    check("HTML zip job on Windows: tags intact, text rewritten", f2["status"] == "done" and __import__("re").findall(r"<[^>]+>", new) == __import__("re").findall(r"<[^>]+>", old) and "finest green tea" in new)
    check("job data is in %APPDATA%\\JivDesktop\\batch", (DATA / "batch/jobs" / j["id"] / "output/review.xlsx").exists())
finally:
    llm.terminate(); subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
    if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA")
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
