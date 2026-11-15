"""Headless Chrome (1.5.0): tips strip, live meter + stats chip, think box, regenerate, edit & resend, stop keeps the partial
answer, memories-used chip, quick model switch, Batch speed/Quick 20, Memory merge, Ollama-down banner + reconnect.
Screenshots 62-69. python tests/quality_ui_test.py"""
import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
SRC = Path(__file__).resolve().parent.parent; PY = sys.executable; OUT = SRC / "screenshots"; OUT.mkdir(exist_ok=True)
def fp():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
PORT, LLM = fp(), fp(); DATA = Path(tempfile.mkdtemp(prefix="jiv-qui-"))
(DATA / "config.json").write_text(json.dumps({"base_url": f"http://127.0.0.1:{LLM}/v1", "ollama_url": f"http://127.0.0.1:{LLM}", "model": "qwen2.5:3b", "setup_done": True, "adult_model_suggested": True}))
ENV = {k: v for k, v in os.environ.items() if not k.startswith("JIV_")}
def start_llm(): return subprocess.Popen([PY, str(SRC / "tests/mock_llm.py"), str(LLM)], env=dict(ENV, MOCK_DELAY="0.01", MOCK_MODELS="qwen2.5:3b,nemotron-mini:4b"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
llm = start_llm()
app = subprocess.Popen([PY, str(SRC / "run.py"), "--no-browser", "--port", str(PORT)], cwd=SRC, env=dict(ENV, JIV_DATA_DIR=str(DATA)), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
B = f"http://127.0.0.1:{PORT}/"; res = []; errors = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f" [{str(i)[:400]}]"), flush=True)
for _ in range(60):
    try: httpx.get(B + "health"); break
    except Exception: time.sleep(0.5)
for _ in range(40):
    try: httpx.get(f"http://127.0.0.1:{LLM}/api/version"); break
    except Exception: time.sleep(0.25)
def wait_idle(pg, n, t=30):
    for _ in range(t * 10):
        if pg.locator("#messages > .msg.bot").count() > n and not pg.is_visible("#stop"): break
        time.sleep(0.1)
    pg.wait_for_timeout(400)
def send(pg, text):
    n = pg.locator("#messages > .msg.bot").count(); pg.fill("#input", text); pg.keyboard.press("Enter"); wait_idle(pg, n)
def until(fn, t=20):
    for _ in range(t * 10):
        try:
            if fn(): return True
        except Exception: pass
        time.sleep(0.1)
    return False
try:
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_context(viewport={"width": 1360, "height": 900}).new_page()
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text)); pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(B); pg.wait_for_selector(".empty")
        check("tips strip shown for a small model (qwen2.5:3b)", pg.is_visible("#tips") and "Tips for small models" in pg.inner_text("#tips"))
        check("no connection banner while the server is up and the model is listed", until(lambda: not pg.is_visible("#connBanner"), 5))
        pg.screenshot(path=str(OUT / "62_tips_strip.png"))
        # live meter + stats
        pg.fill("#input", "slow story about a nurse"); pg.keyboard.press("Enter")
        check("live token meter visible while answering", until(lambda: pg.is_visible("#meter") and "tok" in pg.inner_text("#meter"), 10), pg.inner_text("#meter") if pg.is_visible("#meter") else "")
        pg.wait_for_timeout(1200); pg.click("#stop"); wait_idle(pg, 0)
        last = pg.inner_text("#messages > .msg.bot >> nth=-1")
        check("stop keeps the partial answer and says so", "Once upon a time" in last and "partial answer is kept" in pg.inner_text("#messages"), last[-200:])
        pg.screenshot(path=str(OUT / "63_stop_partial.png"))
        send(pg, "calculate 15% of 240")
        last = pg.locator("#messages > .msg.bot >> nth=-1")
        check("think part folded into a 'thinking' box, answer shown", last.locator("details.think").count() == 1 and "36" in last.inner_text(), last.inner_html()[:300])
        check("stats chip: tokens · tok/s · seconds", until(lambda: "tok/s" in last.locator(".stats").inner_text(), 5))
        check("think box is collapsed by default", not last.locator("details.think").evaluate("d => d.open"))
        pg.screenshot(path=str(OUT / "64_think_stats.png"))
        # regenerate
        send(pg, "regen tell me one tip")
        t1 = pg.inner_text("#messages > .msg.bot >> nth=-1")
        check("↻ Regenerate on the last answer only, ✎ Edit on the last message only", pg.locator("[data-regen]").count() == 1 and pg.locator("[data-editlast]").count() == 1)
        nb = pg.locator("#messages > .msg.bot").count()
        pg.click("[data-regen]"); wait_idle(pg, nb - 1)
        t2 = pg.inner_text("#messages > .msg.bot >> nth=-1")
        check("regenerate replaces the last answer with a new one", pg.locator("#messages > .msg.bot").count() == nb and t1 != t2 and "Answer version" in t2, (t1[:80], t2[:80]))
        # edit & resend
        pg.click("[data-editlast]"); pg.wait_for_selector("#editText")
        check("edit box prefilled with the last message", pg.input_value("#editText") == "regen tell me one tip", pg.input_value("#editText"))
        pg.screenshot(path=str(OUT / "65_edit_resend.png"))
        pg.fill("#editText", "regen tell me two tips"); pg.click("#editSend"); wait_idle(pg, nb - 1)
        users = pg.locator("#messages > .msg.user"); t3 = pg.inner_text("#messages > .msg.bot >> nth=-1")
        check("edited message replaced the old one and got a fresh answer", "two tips" in users.nth(users.count() - 1).inner_text() and "one tip" not in pg.inner_text("#messages") and "two tips" in t3, t3[:100])
        cid = pg.evaluate("S.convId")
        if cid:
            msgs = pg.evaluate("api('/api/conversations/' + S.convId)")["messages"]
            check("saved chat matches the screen after edit (no stale turns)", not any("one tip" in str(m.get("content")) for m in msgs), [m.get("content", "")[:40] for m in msgs])
        pg.reload(); pg.wait_for_selector("#convs .item"); pg.click("#convs .item >> nth=0"); pg.wait_for_selector("#messages > .msg.bot"); pg.wait_for_timeout(300)
        check("after reload: stats chip and actions still shown", pg.locator(".stats").count() >= 1 and pg.locator("[data-regen]").count() == 1)
        # memories used
        pg.evaluate("api('/api/memory', {method: 'POST', body: {text: 'User is preparing for the NCLEX exam', kind: 'fact'}})")
        pg.click("#newChat"); send(pg, "give me NCLEX study tips for the exam")
        mu = pg.locator("#messages > .msg.bot >> nth=-1").locator(".memused")
        check("🧠 'Used N memories' chip on the answer", mu.count() == 1 and "Used" in mu.inner_text(), pg.inner_html("#messages > .msg.bot >> nth=-1")[:300])
        mu.locator("summary").click(); pg.wait_for_timeout(200)
        check("chip expands to show the NCLEX memory", "NCLEX" in mu.inner_text())
        pg.screenshot(path=str(OUT / "66_memories_used.png"))
        # settings quick switch
        pg.click("#openSettings"); pg.wait_for_selector("#s_quick [data-quick]")
        check("Settings: quick switch has ✓ qwen2.5:3b (current), ★ abliterate, and nemotron",
              "✓ qwen2.5:3b" in pg.inner_text("#s_quick") and "huihui_ai/qwen2.5-abliterate:3b" in pg.inner_text("#s_quick") and "nemotron-mini:4b" in pg.inner_text("#s_quick"))
        check("Settings keeps the current model selected (no silent switch)", pg.input_value("#s_model") == "qwen2.5:3b", pg.input_value("#s_model"))
        pg.screenshot(path=str(OUT / "67_settings_quick_switch.png"))
        pg.click("[data-close]")
        # models quick switch
        pg.click("#openModels"); pg.wait_for_selector("#mQuick")
        check("Models: ⚡ Quick switch section with recommended adult default tag", "recommended adult default" in pg.inner_text("#mQuick"))
        check("Models: Adult / fewer-refusal list still there", pg.locator("#mUncensored").count() == 1 and "consenting adult" in pg.inner_text("#mUncensored"))
        pg.click("[data-close]")
        # batch
        pg.click("#openBatch"); pg.wait_for_selector("#bPar")
        check("Batch: Speed defaults to 1 at a time", pg.input_value("#bPar") == "1")
        pg.click("[data-bmode='repeat']"); pg.wait_for_selector("#bCount"); pg.click("#bQuick20")
        check("Batch: 🧪 Quick 20 sets count 20", pg.input_value("#bCount") == "20", pg.input_value("#bCount"))
        pg.screenshot(path=str(OUT / "68_batch_speed_quick20.png"))
        pg.click("[data-close]")
        # memory merge
        pg.click("#openMemory"); pg.wait_for_selector("#mMerge"); pg.click("#mMerge")
        check("Memory: 🧹 Merge duplicates reports a result", until(lambda: pg.inner_text("#mMergeMsg").strip() != "", 5))
        pg.click("[data-close]")
        # connection banner
        llm.terminate(); llm.wait()
        pg.evaluate("checkConn()")
        check("banner when Ollama is down, with Retry / Start Ollama", until(lambda: pg.is_visible("#connBanner") and "Can't reach" in pg.inner_text("#connBanner"), 10) and pg.locator("[data-reconnect]").count() == 1, pg.inner_text("#connBanner") if pg.is_visible("#connBanner") else "")
        pg.screenshot(path=str(OUT / "69_ollama_down_banner.png"))
        globals()['llm'] = start_llm(); until(lambda: httpx.get(f"http://127.0.0.1:{LLM}/api/version").status_code == 200, 10)
        pg.click("[data-reconnect]")
        check("✓ Reconnected after Retry", until(lambda: "Reconnected" in pg.inner_text("#connBanner"), 10))
        check("banner hides itself after reconnecting", until(lambda: not pg.is_visible("#connBanner"), 8))
        pg.click("[data-closetips]"); pg.wait_for_timeout(300)
        check("tips strip dismissed and remembered", not pg.is_visible("#tips") and pg.evaluate("api('/api/state')")["config"].get("tips_dismissed") is True)
        check("no JS errors", not [e for e in errors if "Failed to load resource" not in e], errors)
        b.close()
finally:
    app.terminate(); llm.terminate()
print(f"{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
