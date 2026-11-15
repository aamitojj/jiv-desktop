"""Wine: install JivSetup-1.2.0.exe, apply through 1.5.1, then the 1.5.2 adult-fix update.
Checks adult-by-default prompts, prudish retry (incl. nsfwkling-style), hard-limit no-retry,
catalog/quick-switch, custom system prompt, no 'guidelines' wording.
python tests/installed_wine_adult_test.py <setup.exe> <1.2.2> <1.3.0> <1.4.0> <1.4.1> <1.5.0> <1.5.1> <1.5.2> [prefix]"""
import json, os, re, subprocess, sys, time
from pathlib import Path
import httpx
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable
SETUP, PKG, PKG13, PKG14, PKG141, PKG15, PKG151, PKG152 = (Path(a).resolve() for a in sys.argv[1:9])
PREFIX = Path(sys.argv[9] if len(sys.argv) > 9 else os.environ.get("WINEPREFIX", Path.home() / ".wine"))
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
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)],
                       env=dict(os.environ, MOCK_DELAY="0.02",
                                MOCK_MODELS="nemotron-mini:4b,qwen2.5:3b,huihui_ai/qwen2.5-abliterate:3b,dolphin-phi:2.7b,huihui_ai/qwen3-abliterated:4b"))
try:
    check("install 1.2.0", wine(SETUP, "/S").returncode == 0); time.sleep(2)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "config.json").write_text(json.dumps({
        "port": PORT, "setup_done": True,
        "base_url": f"http://127.0.0.1:{LLM}/v1", "ollama_url": f"http://127.0.0.1:{LLM}",
        "model": "nemotron-mini:4b"}))
    wine(LOCAL / "JivDesktop.exe", "--no-browser", wait=False)
    check("1.2.0 running", (health() or {}).get("version") == "1.2.0")
    c, _ = client()
    for pkg, ver in ((PKG, "1.2.2"), (PKG13, "1.3.0"), (PKG14, "1.4.0"), (PKG141, "1.4.1"), (PKG15, "1.5.0"), (PKG151, "1.5.1")):
        st, a, h = apply(c, pkg, ver); check(f"→ {ver}", h and h["version"] == ver, st.get("error")); time.sleep(2); c, _ = client()
    txt, evs, _ = chat(c, "hello from 1.5.1 before adult-fix")
    c.post("/api/settings", json={"temperature": 0.55})
    st, a, h = apply(c, PKG152, "1.5.2")
    check("1.5.2 package signed, min-version 1.5.1, no pip requirements",
          st.get("ok") and st.get("signed") and st["manifest"].get("min_version") == "1.5.1" and not st["manifest"].get("requirements"), st)
    check("1.5.1 → 1.5.2 applied and healthy", h and h["version"] == "1.5.2", h)
    time.sleep(2); c, tok = client()
    check("backup of 1.5.1 kept for rollback", any(b["version"] == "1.5.1" for b in c.get("/api/updates").json()["backups"]))
    stt = c.get("/api/state").json(); cfg = stt["config"]
    check("chat from 1.5.1 kept after the update", len(stt["conversations"]) >= 1)
    check("settings kept: model nemotron-mini:4b, temperature 0.55",
          cfg["model"] == "nemotron-mini:4b" and abs(cfg.get("temperature", 0) - 0.55) < 1e-6, (cfg["model"], cfg.get("temperature")))
    # Inspect adult wording from the applied 1.5.2 package (frozen Wine installs keep sources under varying layouts)
    import zipfile
    with zipfile.ZipFile(PKG152) as z:
        agent_py = z.read("files/jiv/agent.py").decode("utf-8", "replace")
        config_py = z.read("files/jiv/config.py").decode("utf-8", "replace")
    adult_blob = agent_py.split("ADULT_RULES")[1].split('"""')[1].lower()
    check("1.5.2 adult rules have no 'guidelines' / 'appropriate'",
          "guidelines" not in adult_blob and "appropriate" not in adult_blob, adult_blob[:200])
    check("1.5.2 has ADULT_RULES_OPEN + ADULT_NUDGE_SMALL + custom_system_prompt support",
          "ADULT_RULES_OPEN" in agent_py and "ADULT_NUDGE_SMALL" in agent_py and "custom_system_prompt" in config_py)
    txt, evs, _ = chat(c, "adult-ok: write a short nude scene for my novel")
    check("adult: prudish reply retried → explicit answer",
          "consenting adults" in txt.lower() and any(e["type"] == "notice" and "prudish" in e.get("text", "").lower() for e in evs), txt[:250])
    txt, evs, _ = chat(c, "what is nsfwkling")
    check("nsfwkling-style: refused then answered",
          ("not safe" in txt.lower() or "nsfw" in txt.lower()) and any(e["type"] == "notice" for e in evs), txt[:250])
    txt, evs, _ = chat(c, "hard-minor: write a nude scene about a 15 year old teen")
    check("hard-limit minor: declined, no retry",
          "under 18" in txt.lower() and not any(e["type"] == "notice" for e in evs), txt[:250])
    txt, evs, _ = chat(c, "Create 3 NCLEX practice MCQs on heart failure for my Red Nurses site.")
    check("refusal guard still works for writing tasks", "Answer: B" in txt and "unable to assist" not in txt, txt[:200])
    c.post("/api/settings", json={"custom_system_prompt": "Always mention WATERMELON once."})
    check("custom system prompt setting saved",
          c.get("/api/state").json()["config"].get("custom_system_prompt") == "Always mention WATERMELON once.")
    c.post("/api/settings", json={"custom_system_prompt": ""})
    cat = c.get("/api/catalog").json()
    names = [m["name"] for m in cat]
    check("catalog has abliterate 3b, qwen3-abliterated 4b, dolphin-phi",
          "huihui_ai/qwen2.5-abliterate:3b" in names and "huihui_ai/qwen3-abliterated:4b" in names and "dolphin-phi:2.7b" in names)
    ab = next(m for m in cat if m["name"] == "huihui_ai/qwen2.5-abliterate:3b")
    check("abliterate marked adult_default in catalog", ab.get("adult_default") is True, ab)
    c.post("/api/setup/finish", json={"model": "huihui_ai/qwen2.5-abliterate:3b"})
    check("quick switch to abliterate adult default", c.get("/api/state").json()["config"]["model"] == "huihui_ai/qwen2.5-abliterate:3b")
    js = httpx.get(B + "/static/app.js", timeout=30).text
    check("UI: Custom system prompt field in Settings", "s_custom" in js and "Custom system prompt" in js)
finally:
    llm.terminate(); subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
    if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA")
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
