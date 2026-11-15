"""Headless Chrome: the Batch page on a 200-MCQ JSON file and an HTML folder, estimate, single permission card, progress/ETA,
pause/resume, downloads, 'repeat this 50 times' from chat, and start_batch from chat. Screenshots 44-53. python tests/batch_ui_test.py"""
import json, os, re, socket, subprocess, sys, tempfile, time, zipfile
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable; OUT = SRC / "screenshots"; F = SRC / "tests/fixtures/batch"
def fp():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
PORT, LLM = fp(), fp(); DATA = Path(tempfile.mkdtemp(prefix="jiv-bui-")); DL = Path(tempfile.mkdtemp(prefix="jiv-dl-"))
(DATA / "config.json").write_text(json.dumps({"base_url": f"http://127.0.0.1:{LLM}/v1", "model": "qwen2.5:3b", "setup_done": True}))
llm = subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)], env=dict(os.environ, MOCK_DELAY="0.04"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
app = subprocess.Popen([PY, str(SRC / "run.py"), "--no-browser", "--port", str(PORT)], cwd=SRC, env=dict(os.environ, JIV_DATA_DIR=str(DATA)), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
B = f"http://127.0.0.1:{PORT}/"; res = []; errors = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f" [{str(i)[:400]}]"), flush=True)
for _ in range(60):
    try: httpx.get(B + "health"); break
    except Exception: time.sleep(0.5)
def wait_until(fn, t=60):
    t0 = time.time()
    while time.time() - t0 < t:
        try:
            if fn(): return True
        except Exception: pass
        time.sleep(0.2)
    return False
try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1360, "height": 900}, accept_downloads=True); pg = ctx.new_page()
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text)); pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(B); pg.wait_for_selector(".empty")
        check("⚡ Batch button in the sidebar", pg.is_visible("#openBatch"))
        pg.click("#openBatch"); pg.wait_for_selector("#bTabs")
        check("two modes + jobs tab", pg.inner_text("#bTabs").count("Repeat N times") == 1 and "Run over a file" in pg.inner_text("#bTabs"))
        with pg.expect_file_chooser() as fc: pg.click("#bPickFile")
        fc.value.set_files(str(F / "mcq_200.json"))
        pg.wait_for_selector("#bPreview:has-text('200 items')")
        pg.select_option("#bTpl", label=next(o for o in pg.eval_on_selector_all("#bTpl option", "os => os.map(o => o.textContent)") if "Rewrite MCQ" in o))
        pg.wait_for_timeout(600)
        pv = pg.inner_text("#bPreview")
        check("200-MCQ file auto-split into JSON elements, fields shown", "200 items" in pv and "JSON array elements" in pv and "answer" in pv and "What is" in pv, pv)
        check("built-in MCQ template fills prompt, verify, MCQ checks, schema", "original wording" in pg.input_value("#bPrompt") and pg.is_checked("#bVerify") and pg.is_checked("#bMcq") and pg.input_value("#bSchema") == "auto-mcq")
        pg.screenshot(path=str(OUT / "44_batch_file_mcq.png"))
        pg.fill("#bLimit", "120"); pg.dispatch_event("#bLimit", "change"); pg.wait_for_selector("#bSelCount:has-text('120')")
        check("limit → 120 selected", pg.inner_text("#bSelCount") == "120")
        pg.click("#bEstimate"); pg.wait_for_selector("#bEst:has-text('Estimated time')", timeout=30000)
        est = pg.inner_text("#bEst")
        check("estimate from a measured test item (tokens/s + ETA)", "tokens/s" in est and "Measured on 1 test item" in est and "passed the verify pass" in est, est)
        pg.screenshot(path=str(OUT / "45_batch_estimate.png"))
        pg.click("#bGo"); pg.wait_for_selector("#bPerm .perm")
        perm = pg.inner_text("#bPerm")
        check("ONE permission card with prompt, item count and output folder", "120 item(s)" in perm and "Prompt:" in perm and "Output folder:" in perm and "verify pass" in perm, perm)
        pg.screenshot(path=str(OUT / "46_batch_permission.png"))
        pg.click("#bPerm [data-a='1']"); pg.wait_for_selector(".bjob")
        check("job started → Jobs tab with progress", pg.locator(".bjob").count() == 1)
        wait_until(lambda: int(re.search(r"(\d+) / 120", pg.inner_text(".bjob")).group(1)) >= 15)
        pg.wait_for_selector(".bjob:has-text('ETA')")
        pg.screenshot(path=str(OUT / "47_batch_running.png"))
        check("progress bar, ETA and tok/s shown", "ETA" in pg.inner_text(".bjob") and "tok/s" in pg.inner_text(".bjob") and pg.locator(".bjob .bar span").count() == 1)
        check("no per-item permission cards appeared", pg.locator("#bPerm .perm:not(.done)").count() == 0)
        pg.click(".bjob [data-bact='pause']"); pg.wait_for_selector(".bjob:has-text('paused')")
        n1 = pg.inner_text(".bjob .bj-stats"); time.sleep(1.5); n2 = pg.inner_text(".bjob .bj-stats")
        check("pause stops progress", n1.split("(")[0] == n2.split("(")[0], (n1, n2))
        check("log opens automatically for the new job", pg.inner_text(".bjob [data-blog]") == "Hide log")
        pg.wait_for_selector(".bj-log:not(.hidden) pre")
        pg.screenshot(path=str(OUT / "48_batch_paused_log.png"))
        check("per-item log shown", "✓ ok" in pg.inner_text(".bj-log") and "Paused" in pg.inner_text(".bj-log"))
        pg.click(".bjob [data-bact='resume']")
        check("resume → finishes all 120", wait_until(lambda: "done" in pg.inner_text(".bjob .tag"), 90))
        if not wait_until(lambda: pg.locator(".bj-log h4").count() > 0, 15): pg.screenshot(path="/tmp/fail.png"); print(pg.inner_text(".bjob"))
        check("flagged items listed (ids 50, 100)", "Flagged for review (2)" in pg.inner_text(".bj-log"), pg.inner_text(".bj-log")[-400:])
        pg.screenshot(path=str(OUT / "49_batch_done_flagged.png"))
        with pg.expect_download() as d: pg.click(".bjob [data-dl='result']")
        rp = DL / d.value.suggested_filename; d.value.save_as(rp); data = json.loads(rp.read_text())
        check("download result JSON (same format as input)", rp.name == "mcq_200_rewritten.json" and len(data) == 120 and all("explanation" in x for x in data))
        with pg.expect_download() as d: pg.click(".bjob [data-dl='xlsx']")
        xp = DL / d.value.suggested_filename; d.value.save_as(xp)
        from openpyxl import load_workbook
        ws = load_workbook(xp)["Flagged for review"]
        check("download review.xlsx with original vs new", xp.name == "review.xlsx" and ws.max_row == 1 + 2 + 2 and ws["G1"].value == "Original" and ws["H1"].value == "New")
        # HTML folder
        pg.click("[data-bmode='file']"); pg.wait_for_selector("#bPickFolder")
        pg.set_input_files("#bFolder", str(F / "site"))
        pg.wait_for_selector("#bPreview:has-text('3 items')", timeout=15000)
        pg.select_option("#bTpl", label=next(o for o in pg.eval_on_selector_all("#bTpl option", "os => os.map(o => o.textContent)") if "AdSense" in o))
        pg.wait_for_timeout(600)
        pv = pg.inner_text("#bPreview")
        check("HTML folder → one item per page", "3 items" in pv and "One item per HTML page" in pv and "site" in pg.inner_text("#bSrc"), pv)
        pg.screenshot(path=str(OUT / "50_batch_html_folder.png"))
        pg.click("#bGo"); pg.wait_for_selector("#bPerm .perm"); pg.click("#bPerm [data-a='1']")
        pg.wait_for_selector(".bjob >> nth=1")
        check("HTML job done", wait_until(lambda: "done" in pg.inner_text(".bjob >> nth=0 >> .tag"), 60))
        pg.wait_for_selector(".bjob >> nth=0 >> [data-dl='result']")
        with pg.expect_download() as d: pg.click(".bjob >> nth=0 >> [data-dl='result']")
        zp = DL / d.value.suggested_filename; d.value.save_as(zp); z = zipfile.ZipFile(zp)
        idx = z.read("site/index.html").decode(); orig = (F / "site/index.html").read_text()
        check("HTML zip: tags identical, text rewritten", re.findall(r"<[^>]+>", idx) == re.findall(r"<[^>]+>", orig) and "finest green tea" in idx and sorted(z.namelist()) == sorted("site/" + str(x.relative_to(F / "site")) for x in (F / "site").rglob("*") if x.is_file()))
        pg.screenshot(path=str(OUT / "51_batch_jobs_list.png"))
        pg.click("[data-close]")
        # chat: "repeat this 50 times"
        pg.fill("#input", "Write one surprising tea fact, number {i}. Repeat this 50 times"); pg.click("#send")
        pg.wait_for_selector("#bCount")
        check("'repeat this 50 times' opens a prefilled Batch page", pg.input_value("#bCount") == "50" and pg.input_value("#bPrompt").startswith("Write one surprising tea fact, number {i}") and "on" in pg.get_attribute("[data-bmode='repeat']", "class"))
        check("…and nothing was sent to the chat", pg.locator(".msg.user").count() == 0)
        pg.check("#bChain"); pg.screenshot(path=str(OUT / "52_batch_repeat_prefilled.png"))
        pg.click("#bGo"); pg.wait_for_selector("#bPerm .perm")
        check("repeat permission card shows 50 runs", "50 time(s)" in pg.inner_text("#bPerm"))
        pg.click("#bPerm [data-a='1']"); pg.wait_for_selector(".bjob")
        check("repeat job finishes", wait_until(lambda: "done" in pg.inner_text(".bjob >> nth=0 >> .tag"), 60))
        r = httpx.get(B + f"api/batch/jobs", headers={"X-Jiv-Token": re.search(r'jiv-token" content="([^"]+)"', httpx.get(B).text).group(1)}).json()["jobs"][0]
        pg.click("[data-close]")
        # chat: start_batch tool with an attachment
        with pg.expect_file_chooser() as fc: pg.click("#attachBtn")
        fc.value.set_files(str(F / "mcq_200.json")); pg.wait_for_selector("#attachChips .chip-att.ready")
        pg.fill("#input", "Please rewrite all these MCQs for AdSense"); pg.click("#send")
        pg.wait_for_selector(".perm:has-text('start_batch')", timeout=30000)
        card = pg.inner_text(".perm:has-text('start_batch')")
        check("chat start_batch: one permission card with items, prompt, output folder", "40 item(s)" in card and "Prompt:" in card and "Output folder" in card and "no per-item" in card, card)
        pg.screenshot(path=str(OUT / "53_chat_start_batch_permission.png"))
        pg.click(".perm [data-perm='once']"); pg.wait_for_selector(".notice.batch", timeout=30000); pg.wait_for_selector("#send:not(.hidden)")
        pg.click(".notice.batch [data-openbatch]"); pg.wait_for_selector(".bjob")
        check("batch_started notice opens the Jobs tab", pg.locator(".bjob").count() == 4 and "mcq_200.json" in pg.inner_text(".bjob >> nth=0"))
        wait_until(lambda: "done" in pg.inner_text(".bjob >> nth=0 >> .tag"), 60)
        pg.screenshot(path=str(OUT / "54_chat_batch_job.png"))
        check("no JS errors", not errors, errors)
        b.close()
finally:
    app.terminate(); llm.terminate()
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
