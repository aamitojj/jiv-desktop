"""Wine: install JivSetup-1.2.0.exe, apply 1.2.2 → 1.3.0 → 1.4.0 → 1.4.1, then the 1.5.0 quality update. In the installed app:
answer stats, think mode, regenerate, edit & resend, memories used, the Settings model bug fix, /api/connection, quick switch,
batch verify/parallel defaults, and the 1.4.1 refusal guard still working.
python tests/installed_wine_quality_test.py <setup.exe> <1.2.2> <1.3.0> <1.4.0> <1.4.1> <1.5.0> [prefix]"""
import json, os, re, subprocess, sys, time
from pathlib import Path
import httpx
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable
SETUP, PKG, PKG13, PKG14, PKG141, PKG15 = (Path(a).resolve() for a in sys.argv[1:7])
PREFIX = Path(sys.argv[7] if len(sys.argv) > 7 else os.environ.get("WINEPREFIX", Path.home() / ".wine"))
USER = os.environ.get("USER", "box")
LOCAL = PREFIX / f"drive_c/users/{USER}/AppData/Local/Programs/JivDesktop"; DATA = PREFIX / f"drive_c/users/{USER}/AppData/Roaming/JivDesktop"
PORT, LLM = 18765, 18766; B = f"http://127.0.0.1:{PORT}"
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
def chat(c, msg, **kw):
    evs = []
    with c.stream("POST", "/api/chat", json={"message": msg, **kw}) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                ev = json.loads(line[5:]); evs.append(ev)
                if ev["type"] == "permission": c.post(f"/api/permission/{ev['id']}", json={"decision": "deny"})
    txt = ""
    for e in evs:
        if e["type"] == "text": txt += e["delta"]
        elif e["type"] == "replace_text": txt = e["text"]
    cid = next((e.get("conv_id") for e in evs if e["type"] == "start"), None)
    return txt, evs, cid
subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA"); time.sleep(3)
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)], env=dict(os.environ, MOCK_DELAY="0.02", MOCK_MODELS="nemotron-mini:4b,qwen2.5:3b"))
try:
    check("install 1.2.0", wine(SETUP, "/S").returncode == 0); time.sleep(2)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "config.json").write_text(json.dumps({"port": PORT, "setup_done": True, "base_url": f"http://127.0.0.1:{LLM}/v1", "ollama_url": f"http://127.0.0.1:{LLM}", "model": "nemotron-mini:4b"}))
    wine(LOCAL / "JivDesktop.exe", "--no-browser", wait=False)
    check("1.2.0 running", (health() or {}).get("version") == "1.2.0")
    c, _ = client()
    for pkg, ver in ((PKG, "1.2.2"), (PKG13, "1.3.0"), (PKG14, "1.4.0"), (PKG141, "1.4.1")):
        st, a, h = apply(c, pkg, ver); check(f"→ {ver}", h and h["version"] == ver, st.get("error")); time.sleep(2); c, _ = client()
    txt, evs, _ = chat(c, "hello from 1.4.1")
    c.post("/api/settings", json={"temperature": 0.55})
    st, a, h = apply(c, PKG15, "1.5.0")
    check("1.5.0 package signed, min-version 1.4.1, no pip requirements", st.get("ok") and st.get("signed") and st["manifest"].get("min_version") == "1.4.1" and not st["manifest"].get("requirements"), st)
    check("1.4.1 → 1.5.0 applied and healthy", h and h["version"] == "1.5.0", h)
    time.sleep(2); c, tok = client()
    check("backup of 1.4.1 kept for rollback", any(b["version"] == "1.4.1" for b in c.get("/api/updates").json()["backups"]))
    stt = c.get("/api/state").json(); cfg = stt["config"]
    check("chat from 1.4.1 kept after the update", len(stt["conversations"]) >= 1)
    check("settings kept: model nemotron-mini:4b, temperature 0.55", cfg["model"] == "nemotron-mini:4b" and abs(cfg.get("temperature", 0) - 0.55) < 1e-6, (cfg["model"], cfg.get("temperature")))
    check("new defaults: quality guard on, think auto, permission cards still on", cfg.get("quality_guard") is True and cfg.get("think_mode") == "auto" and cfg.get("auto_approve_all") is False)
    txt, evs, _ = chat(c, "calculate 15% of 240")
    sts = [e for e in evs if e["type"] == "stats"]
    check("answer with think part + stats event (tokens, tok/s)", "36" in txt and "<think>" in txt and sts and sts[0]["tokens"] > 0 and "tps" in sts[0], (txt[:120], sts))
    txt, evs, cid = chat(c, "regen give me a tip")
    t2, evs, _ = chat(c, "", conv_id=cid, regenerate=True)
    msgs = c.get(f"/api/conversations/{cid}").json()["messages"]
    check("regenerate replaces the answer (1 question + 1 answer)", t2 != txt and len(msgs) == 2 and msgs[1]["content"] == t2, [m["content"][:40] for m in msgs])
    t3, evs, _ = chat(c, "regen give me two tips", conv_id=cid, edit=True)
    msgs = c.get(f"/api/conversations/{cid}").json()["messages"]
    check("edit & resend replaces the last question and answer", len(msgs) == 2 and msgs[0]["content"] == "regen give me two tips" and "two tips" in t3, [m["content"][:40] for m in msgs])
    c.post("/api/memory", json={"text": "User is preparing for the NCLEX exam", "kind": "fact"})
    txt, evs, _ = chat(c, "give me NCLEX study tips for the exam")
    mu = [e for e in evs if e["type"] == "memories_used"]
    check("memories_used lists the NCLEX memory", mu and any("NCLEX" in m["text"] for m in mu[0]["items"]), mu)
    check("memory merge endpoint works", "merged" in c.post("/api/memory/merge", json={}).json())
    c.post("/api/settings", json={"model": "", "temperature": 0.6})
    check("Settings bug fixed: an empty model doesn't switch/clear the model", c.get("/api/state").json()["config"]["model"] == "nemotron-mini:4b")
    cn = c.get("/api/connection").json()
    check("/api/connection ok and model listed", cn["ok"] is True and cn.get("model_listed") is True, cn)
    c.post("/api/setup/finish", json={"model": "qwen2.5:3b"})
    check("quick switch to qwen2.5:3b", c.get("/api/state").json()["config"]["model"] == "qwen2.5:3b")
    tp = {t["name"]: t for t in c.get("/api/batch/templates").json()["templates"]}
    art = next((t for n, t in tp.items() if "AdSense" in n or "article" in n.lower()), None)
    check("Batch: article template verify off, NCLEX template verify on", art and not art["verify"] and next(t for n, t in tp.items() if n.startswith("Generate NCLEX"))["verify"])
    j = c.post("/api/batch/jobs", json={"spec": {"template": "Generate NCLEX", "count": 6, "topic": "heart failure", "parallel": 2}, "approved": True}).json()
    for _ in range(300):
        jj = c.get(f"/api/batch/jobs/{j['id']}").json()
        if jj["status"] in ("done", "failed"): break
        time.sleep(1)
    check("NCLEX job with 2 at a time finishes on Windows", jj["status"] == "done" and jj["done"] == 6 and jj["spec"].get("parallel") == 2, (jj["status"], jj["done"], jj.get("spec", {}).get("parallel")))
    txt, evs, _ = chat(c, "Create 3 NCLEX practice MCQs on heart failure for my Red Nurses site.")
    check("1.4.1 refusal guard still works", "Answer: B" in txt and "unable to assist" not in txt, txt[:200])
    js = httpx.get(B + "/static/app.js", timeout=30).text; wz = httpx.get(B + "/static/wizard.js", timeout=30).text
    check("UI has Regenerate, Edit, meter, reconnect banner; Models has Quick switch + Uncensored list",
          all(k in js for k in ("Regenerate", "startEdit", "checkConn", "Reconnected")) and "Quick switch" in wz and "huihui_ai/qwen2.5-abliterate:3b" in wz)
finally:
    llm.terminate(); subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
    if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA")
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
