"""Wine: install JivSetup-1.2.0.exe, apply 1.2.2 → 1.3.0 → 1.4.0, then the 1.4.1 hotfix (refusals). In the installed app: the default
persona is upgraded, the refusal guard turns the user's reported refusal into MCQs, Jiv-change requests are not retried, auto-approve
is off by default, and the NCLEX generator template runs. python tests/installed_wine_refusal_test.py <setup.exe> <1.2.2> <1.3.0> <1.4.0> <1.4.1> [prefix]"""
import json, os, re, socket, subprocess, sys, time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable
SETUP, PKG, PKG13, PKG14, PKG141 = (Path(a).resolve() for a in sys.argv[1:6])
PREFIX = Path(sys.argv[6] if len(sys.argv) > 6 else os.environ.get("WINEPREFIX", Path.home() / ".wine"))
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

def apply(c, pkg, ver, t=240):
    st = c.post("/api/updates/upload", content=pkg.read_bytes(), headers={"X-Filename": pkg.name}).json()
    a = c.post(f"/api/updates/apply/{st['id']}", json={"approved": True}) if st.get("ok") else None
    return st, a, health(t, ver)
def chat(c, msg):
    evs = []
    with c.stream("POST", "/api/chat", json={"message": msg}) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                ev = json.loads(line[5:]); evs.append(ev)
                if ev["type"] == "permission": c.post(f"/api/permission/{ev['id']}", json={"decision": "deny"})
    txt = ""
    for e in evs:
        if e["type"] == "text": txt += e["delta"]
        elif e["type"] == "replace_text": txt = e["text"]
    return txt, evs
subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA"); time.sleep(3)
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)], env=dict(os.environ, MOCK_DELAY="0.02"))
try:
    check("install 1.2.0", wine(SETUP, "/S").returncode == 0); time.sleep(2)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "config.json").write_text(json.dumps({"port": PORT, "setup_done": True, "base_url": f"http://127.0.0.1:{LLM}/v1", "model": "nemotron-mini:4b"}))
    wine(LOCAL / "JivDesktop.exe", "--no-browser", wait=False)
    check("1.2.0 running", (health() or {}).get("version") == "1.2.0")
    c, _ = client()
    for pkg, ver in ((PKG, "1.2.2"), (PKG13, "1.3.0"), (PKG14, "1.4.0")):
        st, a, h = apply(c, pkg, ver); check(f"→ {ver}", h and h["version"] == ver, st.get("error")); time.sleep(2); c, _ = client()
    txt, evs = chat(c, "Create 3 NCLEX practice MCQs on heart failure for my Red Nurses site.")
    check("1.4.0 reproduces the reported refusal (mock replays it)", "unable to assist" in txt, txt[:200])
    st, a, h = apply(c, PKG141, "1.4.1")
    check("1.4.1 package signed, min-version 1.4.0, no pip requirements", st.get("ok") and st.get("signed") and st["manifest"].get("min_version") == "1.4.0" and not st["manifest"].get("requirements"), st)
    check("1.4.0 → 1.4.1 applied and healthy", h and h["version"] == "1.4.1", h)
    time.sleep(2); c, tok = client()
    check("backup of 1.4.0 kept for rollback", any(b["version"] == "1.4.0" for b in c.get("/api/updates").json()["backups"]))
    stt = c.get("/api/state").json()
    main = next(x for x in stt["agents"] if x.get("is_main"))
    check("untouched default persona upgraded to the general-assistant one", "general-purpose" in main["persona"])
    check("chat from 1.4.0 kept after the update", len(stt["conversations"]) >= 1, stt["conversations"])
    check("auto-approve all actions is OFF by default after the update", stt["config"].get("auto_approve_all") is False)
    txt, evs = chat(c, "Create 3 NCLEX practice MCQs on heart failure for my Red Nurses site.")
    check("same request in 1.4.1: guard retries once and shows the MCQs", "Answer: B" in txt and "unable to assist" not in txt and any(e["type"] == "notice" and "declined" in e.get("text", "") for e in evs), txt[:200])
    txt, evs = chat(c, "Add a dark mode button to Jiv")
    check("Jiv self-change reply is not retried", "can't safely" in txt and not any(e["type"] == "notice" for e in evs))
    txt, evs = chat(c, "Create 60 NCLEX MCQs on sepsis")
    check("60 MCQs → Batch NCLEX generator suggested", any(e["type"] == "batch_suggest" and e["count"] == 60 for e in evs))
    tp = c.get("/api/batch/templates").json()["templates"]
    check("5 built-in templates incl. 'Generate NCLEX-style MCQs (topic, count)'", sum(t["builtin"] for t in tp) == 5 and any(t["name"].startswith("Generate NCLEX") for t in tp))
    j = c.post("/api/batch/jobs", json={"spec": {"template": "Generate NCLEX", "count": 10, "topic": "heart failure"}, "approved": True}).json()
    for _ in range(300):
        jj = c.get(f"/api/batch/jobs/{j['id']}").json()
        if jj["status"] in ("done", "failed"): break
        time.sleep(1)
    check("NCLEX job on Windows: 10 done, 2 flagged (planned wrong keys)", jj["status"] == "done" and jj["done"] == 10 and jj["flagged"] == 2, (jj["status"], jj["done"], jj["flagged"]))
    r = httpx.get(B + f"/api/batch/jobs/{j['id']}/download/questions.json?t={tok}", timeout=60)
    check("questions.json downloads (10 MCQs with rationale)", r.status_code == 200 and len(r.json()) == 10 and all(q.get("rationale") for q in r.json()))
    r = httpx.get(B + "/static/wizard.js", timeout=30)
    check("Models page has the Uncensored note", "huihui_ai/qwen2.5-abliterate:3b" in r.text)
finally:
    llm.terminate(); subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
    if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA")
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
