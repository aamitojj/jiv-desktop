"""Headless Chrome: 1.3 memory, run_code + skills, feedback lessons, Languages page (mock winget).
Starts its own mock LLM + Jiv on a temp data dir. python tests/attach_ui_test.py"""
import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable; OUT = SRC / "screenshots"; FX = SRC / "tests/fixtures/attach"
def fp():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
PORT, LLM = fp(), fp(); DATA = Path(tempfile.mkdtemp(prefix="jiv-learnui-"))
(DATA / "config.json").write_text(json.dumps({"base_url": f"http://127.0.0.1:{LLM}/v1", "model": "mock-tools", "setup_done": True}))
FB = tempfile.mkdtemp(prefix="fakewinget-")
env = dict(os.environ, JIV_DATA_DIR=str(DATA), JIV_WINGET=str(SRC / "tests/fixtures/bin/winget"), JIV_FORCE_INSTALLER="winget", FAKE_WINGET_BIN=FB, PATH=FB + os.pathsep + os.environ["PATH"])
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)])
app = subprocess.Popen([PY, str(SRC / "run.py"), "--no-browser", "--port", str(PORT)], cwd=SRC, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
B = f"http://127.0.0.1:{PORT}/"; res = []; errors = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f" [{str(i)[:300]}]"), flush=True)
for _ in range(60):
    try: httpx.get(B + "health"); break
    except Exception: time.sleep(0.5)
try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1360, "height": 860})
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text)); pg.on("pageerror", lambda e: errors.append(str(e)))
        def ask(text, done, idle=True):
            pg.fill("#input", text); pg.click("#send")
            pg.wait_for_selector(done, timeout=30000)
            if idle: pg.wait_for_selector("#send:not(.hidden)", timeout=30000)
        pg.goto(B); pg.wait_for_selector(".empty")
        check("Memory / Skills / Languages buttons present", all(pg.is_visible(s) for s in ("#openMemory", "#openSkills", "#openLanguages")))
        # 1) automatic memory
        ask("My name is Ravi and I prefer short answers.", ".msg.bot:has-text('Nice to meet you')")
        pg.wait_for_selector(".notice.mem")
        notes = pg.inner_text("#messages")
        check("auto-memory notice shown", "Remembered: The user's name is Ravi." in notes and "prefers short answers" in notes, notes[-400:])
        pg.screenshot(path=str(OUT / "34_auto_memory.png"))
        pg.click("#newChat") if pg.is_visible("#newChat") else None
        ask("What's my name?", ".msg.bot:has-text('Your name is')")
        check("new chat recalls the name from memory", pg.locator(".msg.bot:has-text('Your name is Ravi')").count() >= 1, pg.inner_text("#messages")[-300:])
        # 2) run_code with permission + skill offer
        ask("Write a program that prints the first 10 squares", ".perm", idle=False)
        pg.screenshot(path=str(OUT / "35_run_code_permission.png"))
        check("run_code asks permission with Always allow", "Always allow run_code" in pg.inner_text(".perm"))
        pg.click(".perm [data-perm='always']")
        pg.wait_for_selector(".skill-offer", timeout=30000); pg.wait_for_selector("#send:not(.hidden)")
        txt = pg.inner_text("#messages")
        check("code ran and output shown (1 … 100)", "100" in txt and "81" in txt, txt[-500:])
        pg.screenshot(path=str(OUT / "36_skill_offer.png"))
        pg.click(".skill-offer [data-sksave]"); pg.wait_for_selector(".skill-offer:has-text('Saved skill')")
        check("skill saved from the offer card", True)
        ask("Run my squares skill", ".msg.bot >> nth=-1")
        time.sleep(1); pg.wait_for_selector("#send:not(.hidden)")
        check("run_skill ran without asking again (always allow)", pg.locator(".perm:not(.done)").count() == 0 and "100" in pg.inner_text(".msg.bot >> nth=-1"), pg.inner_text("#messages")[-400:])
        # buggy -> fix loop
        ask("Run some buggy code", ".msg.bot:has-text('total 6')")
        t = pg.inner_text("#messages")
        check("auto-fix loop: failed attempt then fixed run", "Attempt 1 of 3 failed" in t or pg.locator(".tool").count() >= 2, t[-600:])
        pg.click("#openSkills"); pg.wait_for_selector("#skList")
        pg.click("#skList details >> nth=0"); time.sleep(0.3)
        sk = pg.inner_text("#skList")
        check("Skills page lists the saved skill with usage", "squares" in sk and "used 1×" in sk, sk)
        pg.screenshot(path=str(OUT / "37_skills_page.png")); pg.click("[data-close]")
        # 3) feedback -> lesson
        ask("What is the capital of Australia?", ".msg.bot:has-text('Sydney')")
        pg.locator("[data-fb='-1']").last.click()
        pg.fill(".fb-fix textarea", "Wrong: the capital of Australia is Canberra, not Sydney.")
        pg.screenshot(path=str(OUT / "38_feedback_correction.png"))
        pg.click("[data-fbsend]"); pg.wait_for_selector(".fb-msg:has-text('lesson')")
        check("👎 + correction saved as a lesson", True)
        pg.click("#openMemory"); pg.wait_for_selector(".mem-table")
        facts = pg.inner_text("#modalCard")
        check("Memory page shows facts", "Ravi" in facts and "short answers" in facts, facts)
        pg.screenshot(path=str(OUT / "39_memory_page.png"))
        pg.click("[data-tab='lessons']"); pg.wait_for_selector("[data-tab='lessons'].on")
        check("Lessons tab shows the correction", "Canberra" in pg.inner_text(".mem-table"))
        pg.screenshot(path=str(OUT / "40_memory_lessons.png"))
        pg.click("[data-tab='memory']"); pg.wait_for_selector("[data-tab='memory'].on")
        n = pg.locator(".mem-table tr").count(); pg.click(".mem-table tr:has-text('short answers') [data-mdel]")
        for _ in range(50):
            if pg.locator(".mem-table tr").count() == n - 1: break
            time.sleep(0.1)
        check("delete a memory from the page", "short answers" not in pg.inner_text(".mem-table")); pg.click("[data-close]")
        # 4) Languages page + mocked winget install
        pg.click("#openLanguages"); pg.wait_for_selector(".lang-table")
        lt = pg.inner_text(".lang-table")
        check("Languages page lists runtimes", all(k in lt for k in ("Python", "JavaScript", "Java", "Go", "Rust", "C#")), lt)
        pg.screenshot(path=str(OUT / "41_languages_page.png"))
        row = ".lang-table tr[data-lid='csharp']"
        if pg.locator(row + " [data-lginst]").count():
            pg.click(row + " [data-lginst]"); pg.wait_for_selector("#lgPerm .perm")
            check("install asks permission with exact winget command", "Microsoft.DotNet.SDK.8" in pg.inner_text("#lgPerm"))
            pg.screenshot(path=str(OUT / "42_language_install_permission.png"))
            pg.click("#lgPerm [data-a='1']"); pg.wait_for_selector(row + " .mini-log", timeout=15000)
            pg.wait_for_selector(row + " button:has-text('Done')", timeout=20000)
            check("install log streamed into the row", "Successfully installed" in pg.inner_text(row))
            pg.screenshot(path=str(OUT / "43_language_installed.png"))
        else: check("csharp row offers install", False, lt)
        check("no JS errors", not errors, errors)
        b.close()
finally:
    app.terminate(); llm.terminate()
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
