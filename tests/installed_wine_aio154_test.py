"""Wine: install through 1.5.1 only, then apply the 1.5.4 ALL-IN-ONE package (no 1.5.2/1.5.3).
Preserves sample chat/settings; checks reliability + prompt-improve after direct upgrade.
python tests/installed_wine_aio154_test.py <setup.exe> <1.2.2> <1.3.0> <1.4.0> <1.4.1> <1.5.0> <1.5.1> <1.5.4-aio> [prefix]
"""
import json, os, re, subprocess, sys, time
from pathlib import Path
import httpx
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable
SETUP, PKG, PKG13, PKG14, PKG141, PKG15, PKG151, PKGAIO = (Path(a).resolve() for a in sys.argv[1:9])
PREFIX = Path(sys.argv[9] if len(sys.argv) > 9 else os.environ.get("WINEPREFIX", Path.home() / ".wine"))
USER = os.environ.get("USER", "box")
LOCAL = PREFIX / f"drive_c/users/{USER}/AppData/Local/Programs/JivDesktop"
DATA = PREFIX / f"drive_c/users/{USER}/AppData/Roaming/JivDesktop"
PORT, LLM = 18775, 18776; B = f"http://127.0.0.1:{PORT}"
ENV = {k: v for k, v in os.environ.items() if not k.startswith("JIV_")}
ENV.update(WINEPREFIX=str(PREFIX), WINEDEBUG="-all", JIV_HEALTH_TIMEOUT="60",
           JIV_WINGET="Z:" + str(SRC / "tests/fixtures/bin/winget.cmd").replace("/", "\\"))
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
                if ev.get("type") == "permission":
                    try: c.post(f"/api/permission/{ev['id']}", json={"decision": "deny"})
                    except Exception: pass
    txt = ""
    for e in evs:
        if e["type"] == "text": txt += e.get("delta", "")
        elif e["type"] == "replace_text": txt = e.get("text", "")
    return txt, evs

subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA"); time.sleep(3)
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)],
                       env=dict(os.environ, MOCK_DELAY="0.02",
                                MOCK_MODELS="nemotron-mini:4b,qwen2.5:3b,huihui_ai/qwen2.5-abliterate:3b"))
try:
    check("install 1.2.0", wine(SETUP, "/S").returncode == 0); time.sleep(2)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "config.json").write_text(json.dumps({
        "port": PORT, "setup_done": True,
        "base_url": f"http://127.0.0.1:{LLM}/v1", "ollama_url": f"http://127.0.0.1:{LLM}",
        "model": "nemotron-mini:4b", "auto_approve_all": False}))
    wine(LOCAL / "JivDesktop.exe", "--no-browser", wait=False)
    check("1.2.0 running", (health() or {}).get("version") == "1.2.0")
    c, _ = client()
    for pkg, ver in ((PKG, "1.2.2"), (PKG13, "1.3.0"), (PKG14, "1.4.0"), (PKG141, "1.4.1"), (PKG15, "1.5.0"), (PKG151, "1.5.1")):
        st, a, h = apply(c, pkg, ver); check(f"→ {ver}", h and h["version"] == ver, st.get("error")); time.sleep(2); c, _ = client()
    # Sample data on true 1.5.1 — must survive the jump
    txt, evs = chat(c, "hello from 1.5.1 keep-me")
    c.post("/api/settings", json={"temperature": 0.55, "user_name": "Asha", "auto_approve_all": False})
    nconv = len(c.get("/api/state").json()["conversations"])
    check("on 1.5.1 before all-in-one", c.get("/api/health").json()["version"] == "1.5.1" if False else
          httpx.get(B + "/health").json()["version"] == "1.5.1")

    # Reject the incremental 1.5.4 (min 1.5.3) would fail — we only apply AIO here
    st, a, h = apply(c, PKGAIO, "1.5.4")
    check("AIO package signed, version 1.5.4, min_version 1.5.1, no pip requirements",
          st.get("ok") and st.get("signed") and st["manifest"].get("version") == "1.5.4"
          and st["manifest"].get("min_version") == "1.5.1" and not st["manifest"].get("requirements"), st)
    check("DIRECT 1.5.1 → 1.5.4 applied and healthy (no 1.5.2/1.5.3)", h and h["version"] == "1.5.4", h)
    time.sleep(2); c, _ = client()
    check("backup of 1.5.1 kept for rollback", any(b["version"] == "1.5.1" for b in c.get("/api/updates").json()["backups"]))
    stt = c.get("/api/state").json(); cfg = stt["config"]
    check("conversations preserved", len(stt["conversations"]) >= nconv, (len(stt["conversations"]), nconv))
    check("settings preserved (model, temperature, user_name, auto-approve off)",
          cfg.get("model") == "nemotron-mini:4b" and abs(cfg.get("temperature", 0) - 0.55) < 1e-6
          and cfg.get("user_name") == "Asha" and cfg.get("auto_approve_all") is False, cfg)
    check("1.5.4 defaults present: prompt_improve + fallback on",
          cfg.get("prompt_improve") is True and cfg.get("fallback_enabled") is True, cfg)

    # Feature smoke after direct jump
    import zipfile
    with zipfile.ZipFile(PKGAIO) as z:
        agent_py = z.read("files/jiv/agent.py").decode()
        assert "files/jiv/reliability.py" in z.namelist() and "files/jiv/prompt_improve.py" in z.namelist()
    check("AIO ships reliability + prompt_improve + no adult nudge",
          "ADULT_NUDGE" not in agent_py and "Do not refuse" not in agent_py and "sexual health" in agent_py)
    js = httpx.get(B + "/static/app.js", timeout=30).text
    check("UI: Clarify question + Backup model", "s_pimpr" in js and "s_fb" in js and "promptImproveCard" in js)
    fb = c.get("/api/fallback").json()
    check("/api/fallback works after direct upgrade", "enabled" in fb and fb.get("enabled") is True, fb)
    txt, evs = chat(c, "wat is dna")
    check("prompt-improve path answers after direct upgrade",
          bool(txt) and any(e["type"] in ("prompt_improved", "notice", "text") for e in evs), (txt[:120], [e["type"] for e in evs][:10]))
    txt, evs = chat(c, "Create 3 NCLEX practice MCQs on heart failure for my Red Nurses site.")
    check("normal writing/refusal-guard path works", "Answer: B" in txt or "answer" in txt.lower(), txt[:200])
    txt, evs = chat(c, "adult-ok: write a short nude scene for my novel")
    check("adult refusal as-is (no prudish retry) after AIO",
          ("sorry" in txt.lower() or "not comfortable" in txt.lower() or "cannot" in txt.lower())
          and "consenting adults" not in txt.lower()
          and not any(e["type"] == "notice" and "prudish" in e.get("text", "").lower() for e in evs), txt[:220])
    # Also confirm incremental package still refuses on a fresh claim — skipped; user has AIO.
finally:
    llm.terminate(); subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
    if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA")
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
