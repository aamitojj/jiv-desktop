"""Wine: install JivSetup-1.2.0.exe, apply 1.2.2 (pip from PyPI), then apply 1.3.0 with winget mocked (tests/fixtures/bin/winget.cmd).
Checks memory, Languages API, mocked winget install + PATH refresh, run_code with the embedded Python, fix loop, skills, feedback.
python tests/installed_wine_learning_test.py <setup.exe> <pkg-1.2.2> <pkg-1.3.0> [prefix]"""
import json, os, re, socket, subprocess, sys, time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable
SETUP, PKG, PKG13 = (Path(a).resolve() for a in sys.argv[1:4])
PREFIX = Path(sys.argv[4] if len(sys.argv) > 4 else os.environ.get("WINEPREFIX", Path.home() / ".wine"))
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

def chat(c, msg, conv=None):
    evs = []
    with c.stream("POST", "/api/chat", json={"message": msg, "conv_id": conv}) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                ev = json.loads(line[5:]); evs.append(ev)
                if ev["type"] == "permission": c.post(f"/api/permission/{ev['id']}", json={"decision": "always"})
    return evs
def apply(c, pkg, ver, t=240):
    st = c.post("/api/updates/upload", content=pkg.read_bytes(), headers={"X-Filename": pkg.name}).json()
    a = c.post(f"/api/updates/apply/{st['id']}", json={"approved": True}) if st.get("ok") else None
    return st, a, health(t, ver)
subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA"); time.sleep(3)
subprocess.run(["wine", "reg", "delete", r"HKCU\Environment", "/v", "Path", "/f"], env=ENV, capture_output=True)
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)])
try:
    check("install 1.2.0", wine(SETUP, "/S").returncode == 0); time.sleep(2)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "config.json").write_text(json.dumps({"port": PORT, "setup_done": True, "base_url": f"http://127.0.0.1:{LLM}/v1", "model": "nemotron-mini:4b"}))
    wine(LOCAL / "JivDesktop.exe", "--no-browser", wait=False)
    check("1.2.0 running", (health() or {}).get("version") == "1.2.0")
    c, _ = client()
    st, a, h = apply(c, PKG, "1.2.2"); check("1.2.0 → 1.2.2 (pip from PyPI)", h and h["version"] == "1.2.2", st.get("error"))
    time.sleep(2); c, _ = client()
    check("1.2.2 has no Languages API", c.get("/api/languages").status_code == 404)
    st, a, h = apply(c, PKG13, "1.3.0")
    check("1.3.0 package signed, no pip requirements", st.get("ok") and st.get("signed") and not st["manifest"].get("requirements"), st)
    check("1.2.2 → 1.3.0 applied and healthy", h and h["version"] == "1.3.0", h)
    time.sleep(2); c, _ = client()
    u = c.get("/api/updates").json(); check("backup of 1.2.2 kept for rollback", any(b["version"] == "1.2.2" for b in u["backups"]))
    L = c.get("/api/languages?force=1").json(); rt = {r["id"]: r for r in L["runtimes"]}
    check("Languages API: installer is winget (mock found)", L["installer"] == "winget" and L["installer_found"], L)
    check("Python available via the embedded Python", rt["python"]["installed"] and "JivDesktop" in rt["python"]["path"], rt["python"])
    check("workspace is %APPDATA%\\JivDesktop\\workspace", L["workspace"].lower().endswith("appdata\\roaming\\jivdesktop\\workspace"), L["workspace"])
    print("     node before:", rt["javascript"]["installed"], rt["javascript"].get("path"))
    j = c.post("/api/languages/install", json={"id": "javascript", "approved": True}).json()
    check("install refused without approval", c.post("/api/languages/install", json={"id": "go"}).status_code in (400, 403))
    for _ in range(120):
        jj = c.get(f"/api/languages/jobs/{j['job']}").json()
        if jj["status"] != "running": break
        time.sleep(1)
    check("mock winget install of Node.js finished", jj["status"] == "done" and any("Successfully installed" in l for l in jj["log"]), jj)
    check("PATH refreshed from registry → node detected without restart", jj.get("detected", {}).get("installed") and "22.11.0-fake" in jj["detected"].get("version", ""), jj.get("detected"))
    # memory
    evs = chat(c, "My name is Asha and I prefer short answers.")
    check("auto-memory saved name + preference", sum(e["type"] == "memory_saved" for e in evs) == 2, [e for e in evs if e["type"] == "memory_saved"])
    evs = chat(c, "What's my name?")
    check("recall in a new chat (nemotron-mini prompt)", "Your name is Asha" in "".join(e.get("delta", "") for e in evs if e["type"] == "text"), [e for e in evs if e["type"] in ("text", "error")][-5:])
    # run_code with embedded python
    evs = chat(c, "Write a program that prints the first 10 squares")
    res_ = [e for e in evs if e["type"] == "tool_result"]
    check("run_code ran with the embedded Python on Windows", res_ and "10 100" in json.dumps(res_), [e for e in evs if e["type"] in ("tool_result", "error")])
    offer = next((e for e in evs if e["type"] == "skill_offer"), None); check("skill offered", offer)
    check("code written under %APPDATA%\\JivDesktop\\workspace", any((DATA / "workspace").glob("*-python-*/main.py")))
    s = c.post("/api/skills", json={"name": offer["name"], "description": "squares", "language": "python", "script": offer["code"]}).json()
    evs = chat(c, "Run some buggy code")
    tr = [e for e in evs if e["type"] == "tool_result"]
    check("fix loop: NameError then corrected run", len(tr) == 2 and "Attempt 1 of 3 failed" in json.dumps(tr[0]) and "total 6" in json.dumps(tr[1]), json.dumps(tr)[:600])
    evs = chat(c, "Run my squares skill")
    check("run_skill works", "10 100" in json.dumps([e for e in evs if e["type"] == "tool_result"]))
    evs = chat(c, "What is the capital of Australia?"); ms = next(e for e in evs if e["type"] == "msg_saved")
    f = c.post("/api/feedback", json={"conv_id": ms["conv_id"], "msg_id": ms["id"], "rating": -1, "correction": "It is Canberra."}).json()
    mem = c.get("/api/memory").json()["memories"]
    check("👎 correction stored as a lesson", f.get("lesson_id") and any(m["kind"] == "lesson" and "Canberra" in m["text"] for m in mem), mem)
    check("memory DB lives in %APPDATA%\\JivDesktop", (DATA / "jiv.db").exists() or any(DATA.glob("*.db")))
finally:
    llm.terminate(); subprocess.run(["wineserver", "-k"], env=ENV, capture_output=True)
    if (LOCAL / "Uninstall.exe").exists(): wine(LOCAL / "Uninstall.exe", "/S", "/DELETEDATA")
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
