"""Headless Chrome: attach files (button, drag & drop, paste), chips, ask about a PDF, image preview + vision tip.
Starts its own mock LLM + Jiv on a temp data dir. python tests/attach_ui_test.py"""
import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable; OUT = SRC / "screenshots"; FX = SRC / "tests/fixtures/attach"
def fp():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
PORT, LLM = fp(), fp(); DATA = Path(tempfile.mkdtemp(prefix="jiv-att-"))
(DATA / "config.json").write_text(json.dumps({"base_url": f"http://127.0.0.1:{LLM}/v1", "model": "mock-tools", "setup_done": True}))
env = dict(os.environ, JIV_DATA_DIR=str(DATA))
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
        pg.goto(B); pg.wait_for_selector(".empty")
        check("📎 attach button present", pg.is_visible("#attachBtn"))
        with pg.expect_file_chooser() as fc: pg.click("#attachBtn")
        fc.value.set_files([str(FX / "quarterly-report.pdf"), str(FX / "sales.xlsx")])
        pg.wait_for_selector("#attachChips .chip-att.ready >> nth=1")
        chips = pg.inner_text("#attachChips")
        check("two chips shown before sending (multiple files)", "quarterly-report.pdf" in chips and "sales.xlsx" in chips and "chars" in chips, chips)
        pg.fill("#input", "What does this report say?")
        pg.screenshot(path=str(OUT / "29_attach_pdf_chips.png"))
        pg.click(".chip-att:has-text('sales.xlsx') [data-rmatt]")
        check("chip can be removed", pg.locator("#attachChips .chip-att").count() == 1)
        pg.keyboard.press("Enter") if False else pg.click("#send")
        pg.wait_for_selector(".msg.bot:has-text('it says')", timeout=30000); pg.wait_for_selector("#send:not(.hidden)")
        txt = pg.inner_text("#messages")
        check("model received the extracted PDF text", "Revenue grew 18%" in txt and "quarterly-report.pdf" in txt, txt[-500:])
        check("sent message shows the file chip", pg.locator(".msg.user .chip-att:has-text('quarterly-report.pdf')").count() == 1)
        check("chips cleared after sending", pg.locator("#attachChips .chip-att").count() == 0)
        pg.wait_for_timeout(300); pg.screenshot(path=str(OUT / "30_ask_about_pdf.png"))
        # drag & drop (synthetic DataTransfer)
        try:
            dt = pg.evaluate_handle("""() => { const d = new DataTransfer(); d.items.add(new File(["Dropped notes: the meeting is on Friday."], "dropped.txt", {type: "text/plain"})); return d; }""")
            pg.dispatch_event("#messages", "dragenter", {"dataTransfer": dt}); pg.dispatch_event("#messages", "drop", {"dataTransfer": dt})
            pg.wait_for_selector("#attachChips .chip-att.ready:has-text('dropped.txt')", timeout=10000)
            check("drag & drop attaches a file", True)
            pg.evaluate("""() => { const d = new DataTransfer(); d.items.add(new File(["pasted text file"], "clip.md", {type: "text/markdown"}));
                           document.getElementById("input").dispatchEvent(new ClipboardEvent("paste", {clipboardData: d, bubbles: true, cancelable: true})); }""")
            pg.wait_for_selector("#attachChips .chip-att.ready:has-text('clip.md')", timeout=10000)
            check("paste attaches a file", True)
        except Exception as e:
            check("drag & drop / paste (synthetic events)", False, e)
        pg.click("#newChat")
        pg.set_input_files("#attachInput", [str(FX / "photo.png")])
        pg.wait_for_selector("#attachChips .chip-att.ready img")
        check("image chip shows a preview", pg.locator("#attachChips img").count() == 1)
        check("non-vision model → install-a-vision-model tip", "qwen2.5vl:3b" in pg.inner_text("#attachChips"))
        pg.click("#send"); pg.wait_for_selector(".msg.user .img-att img"); pg.wait_for_selector("#send:not(.hidden)")
        nat = pg.eval_on_selector(".msg.user .img-att img", "i => i.naturalWidth") if False else pg.locator(".msg.user .img-att img").bounding_box()
        check("sent image preview rendered", nat and nat["width"] > 50, nat)
        check("model reply says it can't see images", "vision model" in pg.inner_text("#messages"))
        pg.wait_for_timeout(300); pg.screenshot(path=str(OUT / "31_image_no_vision.png"))
        # reload: attachments persist in the conversation
        pg.reload(); pg.wait_for_selector("#convs .item"); pg.click("#convs .item >> nth=1"); pg.wait_for_selector(".msg.user .chip-att")
        check("attachments shown again when reopening a chat", "quarterly-report.pdf" in pg.inner_text("#messages"))
        b.close()
    tok = httpx.get(B).text.split('jiv-token" content="')[1].split('"')[0]
    r = httpx.post(B + "api/uploads", content=b"x", headers={"X-Filename": "a.txt"})
    check("upload needs the session token", r.status_code == 403)
    m = httpx.post(B + "api/uploads", content=b"<script>alert(1)</script>", headers={"X-Filename": "x.html", "X-Jiv-Token": tok}).json()
    rr = httpx.get(B + f"api/uploads/{m['id']}/raw?t={tok}")
    check("non-image raw download is never rendered (octet-stream attachment)", rr.headers["content-type"] == "application/octet-stream" and "attachment" in rr.headers.get("content-disposition", ""))
    check("raw needs token", httpx.get(B + f"api/uploads/{m['id']}/raw").status_code == 403)
    check("stored under <data>/uploads", (DATA / "uploads" / m["id"] / "x.html").exists())
finally:
    app.terminate(); llm.terminate()
errors = [e for e in errors if "Failed to load resource" not in e]
check("no console errors", not errors, errors)
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
