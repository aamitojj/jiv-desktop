"""Headless Chrome (1.4.1): refusal guard in chat, Batch suggestion for many MCQs → NCLEX generator, Settings auto-approve toggle
with warning, Uncensored-models note. Screenshots 55-61. python tests/refusal_ui_test.py"""
import json, os, re, socket, subprocess, sys, tempfile, time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable; OUT = SRC / "screenshots"
def fp():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
PORT, LLM = fp(), fp(); DATA = Path(tempfile.mkdtemp(prefix="jiv-rui-"))
(DATA / "config.json").write_text(json.dumps({"base_url": f"http://127.0.0.1:{LLM}/v1", "ollama_url": f"http://127.0.0.1:{LLM}", "model": "qwen2.5:3b", "setup_done": True}))
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)], env=dict(os.environ, MOCK_DELAY="0.01"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
app = subprocess.Popen([PY, str(SRC / "run.py"), "--no-browser", "--port", str(PORT)], cwd=SRC, env=dict(os.environ, JIV_DATA_DIR=str(DATA)), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
B = f"http://127.0.0.1:{PORT}/"; res = []; errors = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f" [{str(i)[:400]}]"), flush=True)
for _ in range(60):
    try: httpx.get(B + "health"); break
    except Exception: time.sleep(0.5)
def send(pg, text):
    n = pg.locator(".msg.assistant").count()
    pg.fill("#input", text); pg.keyboard.press("Enter")
    pg.wait_for_function if False else None
    for _ in range(300):
        if pg.locator(".msg.assistant").count() > n and not pg.is_visible("#stop"): break
        time.sleep(0.1)
    pg.wait_for_timeout(400)
try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_context(viewport={"width": 1360, "height": 900}).new_page()
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text)); pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(B); pg.wait_for_selector(".empty")
        send(pg, "Create 3 NCLEX practice MCQs on heart failure for my Red Nurses site.")
        body = pg.inner_text("#messages")
        check("chat: refusal replaced by the MCQs after one automatic retry", "Answer: B" in body and "unable to assist" not in body, body[-600:])
        check("chat: notice explains the retry", "asking it again with a clearer instruction" in body)
        pg.screenshot(path=str(OUT / "55_refusal_guard.png"))
        send(pg, "Create 60 NCLEX MCQs on sepsis")
        pg.wait_for_selector(".notice.batch:has-text('Batch MCQ generator')")
        check("chat: 60 questions → Batch suggestion with a button", "60 questions" in pg.inner_text(".notice.batch >> nth=-1"))
        pg.screenshot(path=str(OUT / "56_batch_suggestion.png"))
        pg.click(".notice.batch >> nth=-1 >> button"); pg.wait_for_selector("#bCount")
        tpl = pg.eval_on_selector("#bTpl", "s => s.options[s.selectedIndex].textContent")
        check("Batch opens in Repeat mode with the NCLEX generator, count 60, topic sepsis", "Generate NCLEX" in tpl and pg.input_value("#bCount") == "60" and pg.input_value("#bTopic") == "sepsis" and pg.is_checked("#bVerify"), (tpl, pg.input_value("#bTopic")))
        check("template prompt asks for options, answer and rationale", all(k in pg.input_value("#bPrompt") for k in ("{topic}", "rationale", "4 plausible options")))
        pg.screenshot(path=str(OUT / "57_batch_nclex_template.png"))
        pg.fill("#bCount", "15"); pg.click("#bGo"); pg.wait_for_selector("#bPerm .perm")
        perm = pg.inner_text("#bPerm")
        check("one permission card for the job, showing the topic", "15 time(s)" in perm and "topic: sepsis" in perm, perm)
        pg.click("#bPerm [data-a='1']"); pg.wait_for_selector(".bjob")
        for _ in range(300):
            if "done" in pg.inner_text(".bjob").lower() and pg.locator(".bjob [data-dl]").count(): break
            time.sleep(0.2)
        jt = pg.inner_text(".bjob")
        check("NCLEX job finished with 3 flagged (Q5/10/15 have a wrong key)", "3 flagged" in jt or "flagged 3" in jt or re.search(r"3\s*⚠|⚠\s*3", jt), jt)
        hrefs = pg.eval_on_selector_all(".bjob [data-dl], .bjob a", "as => as.map(a => a.getAttribute('href') || a.dataset.dl || '')")
        check("download questions.json offered", any("questions.json" in h for h in hrefs), hrefs)
        js = [h for h in hrefs if "questions.json" in h][0]
        qs = httpx.get(B.rstrip("/") + js).json()
        check("questions.json: 15 MCQs with 4 options, answer and rationale", len(qs) == 15 and all(len(q["options"]) == 4 and q["answer"] and q["rationale"] for q in qs))
        pg.screenshot(path=str(OUT / "58_batch_nclex_done.png"))
        pg.click("[data-close]") if pg.is_visible("[data-close]") else None
        # settings toggle
        pg.click("#openSettings"); pg.wait_for_selector("#s_autoall")
        check("Settings: 'Auto-approve all actions (no permission prompts)' off by default, no warning", not pg.is_checked("#s_autoall") and not pg.is_visible("#s_autowarn")
              and "Auto-approve all actions (no permission prompts)" in pg.inner_text("#modalCard"))
        pg.check("#s_autoall")
        check("switching it on shows a clear warning", pg.is_visible("#s_autowarn") and "without asking you" in pg.inner_text("#s_autowarn"))
        pg.locator("#s_autowarn").scroll_into_view_if_needed(); pg.screenshot(path=str(OUT / "59_settings_auto_approve.png"))
        pg.click("#s_save"); pg.wait_for_timeout(500)
        tok = re.search(r'jiv-token" content="([^"]+)"', httpx.get(B).text).group(1)
        check("setting saved", httpx.get(B + "api/state", headers={"X-Jiv-Token": tok}).json()["config"]["auto_approve_all"] is True)
        pg.click("#newChat") if pg.is_visible("#newChat") else None
        send(pg, "make file please")
        check("with auto-approve: write_file runs with no permission card", pg.locator(".perm:not(.done)").count() == 0 and (Path.home() / "jiv_test/hello.txt").exists())
        audit = httpx.get(B + "api/audit", headers={"X-Jiv-Token": tok}).json()
        rows = audit if isinstance(audit, list) else audit.get("rows") or audit.get("audit") or []
        check("auto-approved action is still in the audit log", any(r.get("tool") == "write_file" and r.get("decision") == "auto" for r in rows), str(rows)[:300])
        pg.click("#openBatch"); pg.wait_for_selector("#bTabs"); pg.click("[data-bmode='repeat']"); pg.wait_for_selector("#bCount")
        pg.fill("#bPrompt", "Write one fact about the heart ({i})."); pg.fill("#bCount", "2"); pg.click("#bGo")
        pg.wait_for_selector(".bjob")
        check("with auto-approve: batch job starts without a prompt", pg.locator(".bjob").count() >= 1)
        pg.click("[data-close]")
        pg.click("#openSettings"); pg.wait_for_selector("#s_autoall"); pg.uncheck("#s_autoall"); pg.click("#s_save"); pg.wait_for_timeout(400)
        check("turned off again", httpx.get(B + "api/state", headers={"X-Jiv-Token": tok}).json()["config"]["auto_approve_all"] is False)
        # models page note
        pg.click("#openModels"); pg.wait_for_selector("#mUncensored")
        pg.click("#mUncensored summary"); pg.wait_for_timeout(300)
        t = pg.inner_text("#mUncensored")
        check("Models page: Uncensored note lists verified Ollama names that fit 4 GB, defaults stay qwen/nemotron",
              all(k in t for k in ("dolphin-phi:2.7b", "huihui_ai/qwen2.5-abliterate:3b", "huihui_ai/llama3.2-abliterate:3b", "huihui_ai/qwen3-abliterated:4b", "qwen2.5:3b or nemotron-mini:4b")), t)
        pg.click("#mUncFill"); check("fill-in button puts the name in the Pull box", pg.input_value("#mName") == "dolphin-phi:2.7b")
        pg.locator("#mUncensored").scroll_into_view_if_needed(); pg.screenshot(path=str(OUT / "60_models_uncensored.png"))
        check("no console errors", not errors, errors)
        b.close()
finally:
    app.terminate(); llm.terminate()
    try: (Path.home() / "jiv_test/hello.txt").unlink()
    except Exception: pass
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
