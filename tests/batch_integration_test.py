"""Batch over HTTP with a real Jiv server: upload, permission required, start, kill -9 the server mid-job, restart,
check it resumes and finishes, then download result + review files with the token link. python tests/batch_integration_test.py"""
import json, os, re, signal, socket, subprocess, sys, tempfile, time, zipfile, io
from pathlib import Path
import httpx
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable; F = SRC / "tests/fixtures/batch"
def fp():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
PORT, LLM = fp(), fp(); DATA = Path(tempfile.mkdtemp(prefix="jiv-bint-")); B = f"http://127.0.0.1:{PORT}"
(DATA / "config.json").write_text(json.dumps({"base_url": f"http://127.0.0.1:{LLM}/v1", "model": "qwen2.5:3b", "setup_done": True}))
res = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:400]}]"), flush=True)
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)], env=dict(os.environ, MOCK_DELAY="0.03"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def start_app():
    p = subprocess.Popen([PY, str(SRC / "run.py"), "--no-browser", "--port", str(PORT)], cwd=SRC, env=dict(os.environ, JIV_DATA_DIR=str(DATA)), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(80):
        try: httpx.get(B + "/health", timeout=1); break
        except Exception: time.sleep(0.25)
    tok = re.search(r'jiv-token" content="([^"]+)"', httpx.get(B + "/").text).group(1)
    return p, tok, httpx.Client(base_url=B, headers={"X-Jiv-Token": tok}, timeout=60)
app, tok, c = start_app()
try:
    up = c.post("/api/batch/upload", content=(F / "mcq_200.json").read_bytes(), headers={"X-Filename": "mcq_200.json"}).json()
    check("upload input file", up["kind"] == "file" and up["files"] == 1, up)
    spec = {"source": "set:" + up["set"], "template": "mcq"}
    pv = c.post("/api/batch/preview", json=spec).json()
    check("preview over HTTP", pv["selected"] == 200 and pv["split"] == "json", pv)
    r = c.post("/api/batch/jobs", json={"spec": spec})
    check("job refused without approval", r.status_code == 400 and "permission" in r.text)
    check("bad token refused", httpx.post(B + "/api/batch/jobs", json={"spec": spec, "approved": True}, headers={"X-Jiv-Token": "nope"}).status_code == 403)
    j = c.post("/api/batch/jobs", json={"spec": spec, "approved": True, "job_id": pv["job_id"]}).json()
    check("job created with the id shown on the permission card", j["id"] == pv["job_id"] and j["output_dir"] == pv["output_dir"])
    check("job start is in the audit log", any(a["tool"] == "start_batch" for a in c.get("/api/audit").json()))
    while c.get(f"/api/batch/jobs/{j['id']}").json()["done"] < 30: time.sleep(0.2)
    before = c.get(f"/api/batch/jobs/{j['id']}").json()
    check("running with ETA + tok/s", before["status"] == "running" and before["eta_secs"] and before["tok_per_s"], before)
    os.kill(app.pid, signal.SIGKILL); app.wait(); time.sleep(0.5)
    app, tok, c = start_app()
    after = c.get(f"/api/batch/jobs/{j['id']}?log=500").json()
    check("after kill -9 + restart the job is back and resuming", after["status"] in ("running", "queued") and after["done"] >= before["done"] and any("resuming" in l["text"] for l in after["log"]), after["status"])
    t0 = time.time()
    while c.get(f"/api/batch/jobs/{j['id']}").json()["status"] != "done" and time.time() - t0 < 180: time.sleep(0.5)
    fin = c.get(f"/api/batch/jobs/{j['id']}?log=2000").json()
    done_idx = [l["idx"] for l in fin["log"] if l["idx"] is not None and ("✓ ok" in l["text"] or "⚠ flagged" in l["text"])]
    check("finished all 200; each item completed exactly once", fin["done"] == 200 and len(done_idx) == 200 == len(set(done_idx)), (fin["done"], len(done_idx), len(set(done_idx))))
    check("4 items flagged for review", fin["flagged"] == 4)
    fl = c.get(f"/api/batch/jobs/{j['id']}/items?status=flagged").json()
    check("flagged items API", fl["count"] == 4 and all(i["problems"] for i in fl["items"]))
    r = httpx.get(B + f"/api/batch/jobs/{j['id']}/download/mcq_200_rewritten.json?t={tok}")
    check("download result via token link", r.status_code == 200 and len(r.json()) == 200 and "attachment" in r.headers.get("content-disposition", ""))
    r = httpx.get(B + f"/api/batch/jobs/{j['id']}/download/review.xlsx?t={tok}")
    from openpyxl import load_workbook
    check("download review.xlsx", r.status_code == 200 and load_workbook(io.BytesIO(r.content))["Flagged for review"].max_row == 9)
    check("download without token refused", httpx.get(B + f"/api/batch/jobs/{j['id']}/download/review.xlsx").status_code == 403)
    check("no path tricks in download names", httpx.get(B + f"/api/batch/jobs/{j['id']}/download/..%2F..%2Fjiv.db?t={tok}").status_code == 404)
    # zip of HTML pages
    zb = (F / "site.zip").read_bytes()
    up = c.post("/api/batch/upload", content=zb, headers={"X-Filename": "site.zip"}).json()
    j2 = c.post("/api/batch/jobs", json={"spec": {"source": "set:" + up["set"], "template": "adsense"}, "approved": True}).json()
    while c.get(f"/api/batch/jobs/{j2['id']}").json()["status"] != "done": time.sleep(0.3)
    r = httpx.get(B + f"/api/batch/jobs/{j2['id']}/download/site_rewritten.zip?t={tok}")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    check("zip of HTML pages in → zip out with the same files", sorted(z.namelist()) == sorted(zipfile.ZipFile(F / "site.zip").namelist()), z.namelist())
    check("auto-update waits while a batch runs (idle check)", "batch.busy()" in (SRC / "jiv/server.py").read_text())
finally:
    app.terminate(); llm.terminate()
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
