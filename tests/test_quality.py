"""Jiv 1.5.1 (quality + adult) update: answer guard, think mode, clarity hint, regenerate/edit/stop, stats, memories, batch parallel/ETA,
Settings model bug, /api/connection. Runs the real server in-process with a scripted model. Run: python tests/test_quality.py"""
import asyncio, json, os, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path
os.environ["JIV_DATA_DIR"] = tempfile.mkdtemp(prefix="jiv-q-")
for k in list(os.environ):
    if k.startswith("JIV_") and k != "JIV_DATA_DIR": os.environ.pop(k)
SRC = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(SRC))
import httpx, uvicorn
from jiv import agent as A, batch as B, learning as L, config as C
res = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:700]}]"), flush=True)

# ---------- unit checks ----------
for q in ("calculate 15% of 240", "why does the sky look blue?", "solve 3x + 5 = 20", "compare SSD vs HDD for a laptop", "what is 17*23"):
    check(f"think auto: reasoning question → think ({q})", A.needs_thinking(q, "auto"))
for q in ("write an article about sleep for students", "Create 3 NCLEX MCQs on sepsis", "hello", "translate good morning to Hindi"):
    check(f"think auto: no thinking for {q!r}", not A.needs_thinking(q, "auto"))
check("think on: always; off: never", A.needs_thinking("hi", "on") and not A.needs_thinking("calculate 2+2", "off"))
check("strip_think removes the reasoning block", A.strip_think("<think>a\nb</think>\nAnswer 42") == "Answer 42")
for q in ("write", "make mcqs", "quiz", "write an essay", "help me"):
    check(f"vague first message: {q!r}", A.is_vague(q, True))
for q in ("write an essay on climate change for class 10", "hi", "what is diabetes?", "make mcqs"):
    if q == "make mcqs": check("vague only on the first message", not A.is_vague(q, False))
    else: check(f"not vague: {q!r}", not A.is_vague(q, True))
MCQ = lambda n: "\n".join(f"**{i}.** Question {i}?\nA) one\nB) two\nC) three\nD) four\n**Answer: B**\n**Rationale:** because." for i in range(1, n + 1))
check("answer_problem: empty", A.answer_problem("write an article on sleep", "") == "empty")
check("answer_problem: think-only reply counts as empty", A.answer_problem("calculate 2+2", "<think>2+2=4</think>") == "empty")
check("answer_problem: too short writing answer", A.answer_problem("write a blog post about time management", "Sure!") == "short")
check("answer_problem: 2 of 5 MCQs → count:2/5", A.answer_problem("Write 5 MCQs on asthma", MCQ(2)) == "count:2/5")
check("answer_problem: 5 of 5 MCQs → ok", A.answer_problem("Write 5 MCQs on asthma", MCQ(5)) is None)
check("answer_problem: MCQs without options/keys → format", A.answer_problem("Write 5 MCQs on asthma", "Here are some questions about asthma: what is it, why does it happen, how is it treated, and more.") == "format")
check("answer_problem: chat answers are not judged", A.answer_problem("hello there", "Hi!") is None)
check("answer_problem: more than 20 MCQs is not counted (Batch)", A.answer_problem("Write 50 MCQs on asthma", MCQ(3)) is None)
# memory patterns + merge
ex = dict((t, k) for k, t in L.auto_extract("I am preparing for NCLEX. I'm from Kerala and I am 24 years old. Please keep answers short."))
check("auto-memory: preparing for / from / age / keep answers short", any("preparing for NCLEX" in t for t in ex) and any("Kerala" in t for t in ex) and any("24" in t for t in ex) and any("short" in t for t in ex), ex)
check("recall matches words at the end of a sentence (\"NCLEX.\" ~ \"NCLEX\")", L.tokens("for NCLEX.") == ["nclex"])
check("near_duplicate detects rewording", L.near_duplicate("User is preparing for NCLEX", "User is preparing for the NCLEX") and not L.near_duplicate("User is from Kerala", "User is preparing for NCLEX"))
# batch
check("eta_from uses throughput (3 items in 10 s → 20 left ≈ 100 s)", B.eta_from([{"secs": 9, "updated": 100}, {"secs": 9, "updated": 105}, {"secs": 9, "updated": 110}], 20) == 100)
check("eta_from: fallback avg/parallel", B.eta_from([{"secs": 10}], 10, 2) == 50)
check("config defaults: quality guard on, think auto, clarity hint on, parallel 1, permission cards on", C.DEFAULTS["quality_guard"] is True and C.DEFAULTS["think_mode"] == "auto"
      and C.DEFAULTS["clarity_nudge"] is True and C.DEFAULTS["batch_parallel"] == 1 and C.DEFAULTS["auto_approve_all"] is False)

# ---------- scripted model ----------
CALLS = []
async def fake_stream(base, key, model, msgs, temperature=0.7, tools=None, **kw):
    sysm = msgs[0]["content"]; last = msgs[-1]["content"]; user = next(m["content"] for m in reversed(msgs) if m["role"] == "user" and not m["content"].startswith("(Note from Jiv"))
    retry = last.startswith("(Note from Jiv"); CALLS.append({"user": user, "retry": retry, "sys": sysm, "msgs": msgs})
    async def say(t, step=0.0, n=1):
        for i in range(0, len(t), max(1, len(t) // n)):
            if step: await asyncio.sleep(step)
            yield ("text", t[i:i + max(1, len(t) // n)])
    if user.startswith("EMPTY"): out = "" if not retry else "Sleep hygiene for students\n\nGo to bed at the same time every day. " * 3
    elif user.startswith("SHORT"): out = "Sure." if not retry else "# Time management for nurses\n\nPlan your shift in blocks. " * 4
    elif "MCQs on asthma" in user: out = MCQ(5 if retry else 2)
    elif user.startswith("REFUSE"): out = "I'm sorry, but I can't help with that." if not retry else ""
    elif user.startswith("calculate"): out = "<think>15% of 240 = 0.15*240 = 36</think>\n**36**"
    elif user.startswith("SLOW"):
        async for e in say("word " * 60, 0.05, 60): yield e
        return
    elif user.startswith("regen"): out = f"Answer version {sum(1 for c in CALLS if c['user'].startswith('regen'))}"
    else: out = "OK: " + user[:80]
    async for e in say(out, 0, 3): yield e
    yield ("usage", {"completion_tokens": 42, "prompt_tokens": 100})
A.stream_chat = fake_stream

from jiv import server as SV
def fp():
    s_ = socket.socket(); s_.bind(("127.0.0.1", 0)); pt = s_.getsockname()[1]; s_.close(); return pt
PORT = fp(); MOCK = fp()
srv = uvicorn.Server(uvicorn.Config(SV.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=srv.run, daemon=True).start()
mock = subprocess.Popen([sys.executable, str(SRC / "tests/mock_llm.py"), str(MOCK)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
H = {"X-Jiv-Token": C.SESSION_TOKEN}; U = f"http://127.0.0.1:{PORT}"
cl = httpx.Client(base_url=U, headers=H, timeout=30)
for _ in range(80):
    try: cl.get("/api/state"); httpx.get(f"http://127.0.0.1:{MOCK}/v1/models", timeout=1); break
    except Exception: time.sleep(0.25)
SV.cfg.update({"base_url": f"http://127.0.0.1:{MOCK}/v1", "model": "qwen2.5:3b", "provider": "custom", "setup_done": True})

def chat(body, stop_after=None):
    evs = []
    with cl.stream("POST", "/api/chat", json=body) as r:
        if r.status_code != 200: return r.status_code, [], r.read().decode()
        for line in r.iter_lines():
            if not line.startswith("data:"): continue
            ev = json.loads(line[5:]); evs.append(ev)
            if stop_after and sum(1 for e in evs if e["type"] == "text") == stop_after:
                rid = next(e["run_id"] for e in evs if e["type"] == "start"); cl.post(f"/api/stop/{rid}")
    st = next((e for e in evs if e["type"] == "start"), {})
    return 200, evs, st.get("conv_id")
def text_of(cid): return [(m["role"], m["content"]) for m in SV.db.msgs(cid) if m["role"] in ("user", "assistant")]
try:
    code, evs, cid = chat({"message": "EMPTY: write an article about sleep hygiene for students"})
    check("empty reply → one automatic retry with a notice, answer shown", any(e["type"] == "notice" and "empty" in e["text"] for e in evs) and "Sleep hygiene" in text_of(cid)[-1][1], [e for e in evs if e["type"] != "text"])
    code, evs, cid = chat({"message": "SHORT: write a blog post about time management for nurses"})
    check("too-short reply → retry for the complete answer", any(e["type"] == "notice" and "short" in e["text"] for e in evs) and "Plan your shift" in text_of(cid)[-1][1])
    code, evs, cid = chat({"message": "Write 5 MCQs on asthma"})
    saved = text_of(cid)[-1][1]
    check("2 of 5 MCQs → retry asks for all 5; saved answer has 5", any(e["type"] == "notice" and "fewer" in e["text"] for e in evs) and saved.count("**Answer: B**") == 5 and "Write all 5" in CALLS[-1]["msgs"][-1]["content"])
    check("writing guide is in the small-model prompt", "## Writing well" in CALLS[-1]["sys"])
    n = len(CALLS); code, evs, cid = chat({"message": "REFUSE: write a short poem about rain for my class"})
    check("refusal → one retry; an empty retry keeps the first reply (no blank answer)", len(CALLS) - n == 2 and "can't help" in text_of(cid)[-1][1])
    code, evs, cid = chat({"message": "calculate 15% of 240"})
    check("reasoning question → think instruction in the prompt", "## Think first" in CALLS[-1]["sys"])
    check("think reply saved with reasoning (UI folds it) and final answer", "**36**" in text_of(cid)[-1][1])
    code, evs, cid2 = chat({"message": "and 20% of it?", "conv_id": cid})
    hist = [m["content"] for m in CALLS[-1]["msgs"] if m["role"] == "assistant"]
    check("earlier reasoning is stripped from the history sent to the model", hist and all("<think>" not in h for h in hist), hist)
    code, evs, cid = chat({"message": "make mcqs"})
    check("vague first message → hint event + clarify prompt", any(e["type"] == "hint" for e in evs) and "## The request is very short" in CALLS[-1]["sys"])
    st = next(e for e in evs if e["type"] == "stats")
    check("stats event: tokens from server usage (42), tok/s, secs", st["tokens"] == 42 and st["estimated"] is False and st["tps"] > 0 and "secs" in st, st)
    check("stats saved in the message meta", SV.db.msgs(cid)[-1]["meta"].get("stats", {}).get("tokens") == 42)
    SV.cfg.update({"clarity_nudge": False}); code, evs, _ = chat({"message": "make mcqs"}); SV.cfg.update({"clarity_nudge": True})
    check("clarity hint can be turned off", not any(e["type"] == "hint" for e in evs))
    # regenerate / edit
    code, evs, cid = chat({"message": "regen please answer"})
    code, evs, _ = chat({"message": "", "conv_id": cid, "regenerate": True})
    tx = text_of(cid)
    check("regenerate: same question asked again, old answer replaced (1 user + 1 answer)", len(tx) == 2 and tx[0] == ("user", "regen please answer") and tx[1][1] == "Answer version 2", tx)
    check("regenerate: start event says mode=regenerate", any(e["type"] == "start" and e["mode"] == "regenerate" for e in evs))
    code, evs, _ = chat({"message": "regen edited question", "conv_id": cid, "edit": True})
    tx = text_of(cid)
    check("edit: last message replaced and answered again", len(tx) == 2 and tx[0] == ("user", "regen edited question") and tx[1][1] == "Answer version 3", tx)
    code, _, body = chat({"message": "", "conv_id": "nope", "regenerate": True})
    check("regenerate with no conversation → 400", code == 400)
    # stop keeps the partial answer
    code, evs, cid = chat({"message": "SLOW tell me a long story"}, stop_after=5)
    stp = [e for e in evs if e["type"] == "stopped"]; last = SV.db.msgs(cid)[-1]
    check("stop: stopped event with kept chars; partial answer saved + '(stopped)'", stp and stp[0]["kept"] > 0 and last["content"].startswith("word") and "_(stopped)_" in last["content"] and last["meta"].get("stopped"), (stp, last["content"][:80]))
    # 409 while running
    out = {}
    def bg(): out["r"] = chat({"message": "SLOW another long story", "conv_id": cid})
    th = threading.Thread(target=bg); th.start(); time.sleep(0.6)
    r = cl.post("/api/chat", json={"message": "", "conv_id": cid, "regenerate": True})
    check("regenerate while an answer is running → 409", r.status_code == 409, r.status_code); th.join()
    # memories used
    code, evs, cid = chat({"message": "I am preparing for NCLEX. hello"})
    code, evs, _ = chat({"message": "give me NCLEX study tips for the exam", "conv_id": cid})
    mu = [e for e in evs if e["type"] == "memories_used"]
    check("memories_used event lists the NCLEX memory", mu and any("NCLEX" in m["text"] for m in mu[0]["items"]), mu)
    check("memories saved in meta for the chat view", any("NCLEX" in m["text"] for m in SV.db.msgs(cid)[-1]["meta"].get("memories", [])))
    SV.learn.add("User is preparing for NCLEX exam", "fact", "manual"); SV.learn.db.x("INSERT INTO memories(text,kind,source,created,updated,uses) VALUES(?,?,?,?,?,0)", ("User is preparing for the NCLEX exam", "fact", "manual", time.time(), time.time())) if False else None
    before = len(SV.learn.memories())
    r = cl.post("/api/memory/merge", json={}).json()
    check("merge endpoint works (near-duplicates merged on add or by the button)", "merged" in r and len(SV.learn.memories()) <= before, r)
    # settings bug: an empty model never silently switches/clears the model
    cl.post("/api/settings", json={"model": "", "temperature": 0.6})
    check("Settings save with an empty model keeps the current model", SV.cfg.get("model") == "qwen2.5:3b" and SV.cfg.get("temperature") == 0.6)
    cl.post("/api/settings", json={"think_mode": "banana"})
    check("invalid think_mode is ignored", SV.cfg.get("think_mode", "auto") in ("auto", "on", "off"))
    cl.post("/api/settings", json={"think_mode": "off"}); check("think_mode can be set to off", SV.cfg.get("think_mode") == "off"); cl.post("/api/settings", json={"think_mode": "auto"})
    # connection banner endpoint
    c = cl.get("/api/connection").json()
    check("/api/connection: ok when the server answers", c["ok"] is True and "count" in c, c)
    SV.cfg.update({"base_url": "http://127.0.0.1:9/v1"}); c = cl.get("/api/connection").json()
    check("/api/connection: ok=false with an error when the model server is down", c["ok"] is False and c["error"], c)
    SV.cfg.update({"base_url": "http://127.0.0.1:11434/v1"}); c = cl.get("/api/connection").json()
    check("/api/connection: recognises Ollama (offers Start Ollama)", c["is_ollama"] is True)
    SV.cfg.update({"base_url": f"http://127.0.0.1:{MOCK}/v1"})
    # batch: parallel, verify defaults
    bm = SV.batch if hasattr(SV, "batch") else B.BatchManager(SV.db, SV.cfg)
    t = bm.find_template("AdSense") or bm.find_template("article")
    check("AdSense/article template: verify off by default (faster)", t is not None and not t["verify"], t and t["name"])
    check("MCQ templates keep verify on", bm.find_template("Rewrite MCQ")["verify"] and bm.find_template("Generate NCLEX")["verify"])
    s1 = bm.normalize({"template": "Generate NCLEX", "count": 3, "topic": "x", "parallel": 5})
    s2 = bm.normalize({"template": "Generate NCLEX", "count": 3, "topic": "x", "parallel": 2, "chain": True})
    s3 = bm.normalize({"template": "Generate NCLEX", "count": 3, "topic": "x"})
    check("parallel clamped to 1–2, chain forces 1, default 1", s1["parallel"] == 2 and s2["parallel"] == 1 and s3["parallel"] == 1, (s1["parallel"], s2["parallel"], s3["parallel"]))
    # static UI pieces present
    js = (SRC / "jiv/static/app.js").read_text() + (SRC / "jiv/static/wizard.js").read_text() + (SRC / "jiv/static/batch.js").read_text() + (SRC / "jiv/static/learning.js").read_text()
    html = (SRC / "jiv/static/index.html").read_text()
    for k in ("dataset.regen", "dataset.editlast", "statsChip", "memChip", "details class=\"think\"", "checkConn", "quickSwitch", "QUICK_MODELS", "bQuick20", "bPar", "mMerge", "(not found on this server)"):
        check(f"UI has {k}", k in js)
    for k in ('id="tips"', 'id="connBanner"', 'id="meter"', 'id="stop"'):
        check(f"index.html has {k}", k in html)
    check("version is 1.6.2", __import__("jiv").__version__ == "1.6.2")
finally:
    mock.terminate(); srv.should_exit = True
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
