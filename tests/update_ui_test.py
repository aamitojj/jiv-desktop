"""Headless Chrome: propose_update end to end (chat card -> permission -> apply -> restart -> new version) + screenshots.
Runs on a temporary copy of the app. python tests/update_ui_test.py"""
import json, os, shutil, signal, socket, subprocess, sys, tempfile, time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable; OUT = SRC / "screenshots"
TMP = Path(tempfile.mkdtemp(prefix="jiv-upd-ui-")); APP = TMP / "app"; DATA = TMP / "data"; DATA.mkdir()
shutil.copytree(SRC, APP, ignore=shutil.ignore_patterns(".venv", "__pycache__", "screenshots", "dist", "build"))
def fp():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
PORT, LLM = fp(), fp()
(DATA / "config.json").write_text(json.dumps({"base_url": f"http://127.0.0.1:{LLM}/v1", "model": "mock-tools", "setup_done": True}))
# a backup to show in the list: pretend an earlier 1.0.x install was backed up
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)])
env = dict(os.environ, JIV_DATA_DIR=str(DATA), JIV_HEALTH_TIMEOUT="30")
app = subprocess.Popen([PY, str(APP / "run.py"), "--no-browser", "--port", str(PORT)], cwd=APP, env=env, start_new_session=True,
                       stdout=open(TMP / "app.log", "w"), stderr=subprocess.STDOUT)
B = f"http://127.0.0.1:{PORT}/"; res = []; errors = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f" [{i}]"), flush=True)
for _ in range(60):
    try: httpx.get(B + "health"); break
    except Exception: time.sleep(0.5)
try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1360, "height": 900})
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text)); pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(B); pg.wait_for_selector(".empty")
        pg.click("#openUpdates"); pg.wait_for_selector("#uCheck")
        check("Updates page shows current version 1.6.2", "1.6.2" in pg.inner_text("#modalCard"))
        pg.wait_for_timeout(200)
        pg.screenshot(path=str(OUT / "22_updates_page.png")); pg.click("[data-close]")
        pg.fill("#input", "Please add a greeting feature to yourself"); pg.keyboard.press("Enter")
        pg.wait_for_selector(".perm button[data-perm=once]")
        check("propose_update asks permission first", "propose_update" in pg.inner_text(".perm"))
        pg.click(".perm button[data-perm=once]")
        pg.wait_for_selector(".upd-card [data-upd-apply]", timeout=60000); pg.wait_for_selector("#send:not(.hidden)")
        card = pg.inner_text(".upd-card")
        check("self-update card: unsigned – created locally", "Unsigned" in card and "created locally" in card, card[:200])
        check("card lists checks passed", pg.locator(".upd-card .chk.ok").count() >= 2 and pg.locator(".upd-card .chk.bad").count() == 0)
        check("card lists new file", "jiv/greetings.py" in card)
        pg.click(".upd-card .fdiff summary"); pg.wait_for_timeout(200)
        pg.locator(".upd-card").scroll_into_view_if_needed()
        pg.screenshot(path=str(OUT / "23_self_update_card.png"))
        pg.click(".upd-card [data-upd-apply]"); pg.wait_for_selector(".upd-card .upd-perm button[data-a='1']")
        pg.screenshot(path=str(OUT / "24_self_update_permission.png"))
        pg.click(".upd-card .upd-perm button[data-a='1']")
        pg.wait_for_selector("text=Updating to 1.6.3", timeout=15000)
        pg.screenshot(path=str(OUT / "25_restarting.png"))
        h = None
        for _ in range(90):
            try:
                h = httpx.get(B + "health", timeout=2).json()
                if h["version"] == "1.6.3": break
            except Exception: pass
            time.sleep(0.5)
        check("app restarted as 1.6.3", h and h["version"] == "1.6.3", h)
        pg.wait_for_selector(".empty, .msg", timeout=30000); pg.wait_for_timeout(1500)
        pg.click("#openUpdates"); pg.wait_for_selector("#uCheck")
        txt = pg.inner_text("#modalCard")
        check("page reloaded and shows 1.6.3 + backup of 1.6.2", "1.6.3" in txt and "1.6.2" in txt, txt[:300])
        check("new file really installed", (APP / "jiv/greetings.py").exists())
        pg.screenshot(path=str(OUT / "26_updates_after_apply.png"))
        b.close()
finally:
    try:
        for pid in subprocess.run(["bash", "-c", f"ss -ltnp | grep ':{PORT} ' | grep -o 'pid=[0-9]*' | cut -d= -f2"], capture_output=True, text=True).stdout.split():
            os.kill(int(pid), signal.SIGTERM)
    except Exception: pass
    for x in (app, llm): x.terminate()
    shutil.rmtree(TMP, ignore_errors=True)
errors = [e for e in errors if "Failed to load resource" not in e and "fetch" not in e.lower()]
check("no console errors", not errors, errors)
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
