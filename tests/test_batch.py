"""Jiv 1.4 Batch unit + engine tests (starts its own mock model). Run: python tests/test_batch.py"""
import asyncio, csv, io, json, os, re, socket, subprocess, sys, tempfile, time, zipfile
from pathlib import Path
os.environ["JIV_DATA_DIR"] = tempfile.mkdtemp(prefix="jiv-batch-")
SRC = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(SRC)); F = SRC / "tests/fixtures/batch"
from jiv.db import DB
from jiv import batch as B
res = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:500]}]"), flush=True)
def fp():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
PORT = fp(); FAILFLAG = Path(tempfile.mkdtemp()) / "fail"
mock = subprocess.Popen([sys.executable, str(SRC / "tests/mock_llm.py"), str(PORT)], env=dict(os.environ, MOCK_DELAY="0", MOCK_FAIL=str(FAILFLAG)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
class Cfg(dict):
    def get(self, k, d=None): return super().get(k, d)
cfg = Cfg(base_url=f"http://127.0.0.1:{PORT}/v1", model="qwen2.5:3b")
try:
    # ---------------- pure functions
    src = (F / "site/index.html").read_text()
    parts, segs, starts = B.html_parts(src)
    check("HTML split is lossless (parts join back to the exact page)", "".join(parts) == src)
    texts = [" ".join(t.split()) for _, t in segs]
    check("visible text found, incl. <title>", "Green Leaf Tea Shop - Fresh tea online" in texts and any("We sell the best green tea" in t for t in texts), texts)
    check("script / <pre><code> / attributes are not text segments", not any("do not touch" in t or "coupon" in t or "cup of green tea" in t for t in texts))
    check("sections start at headings / <section>", len(starts) >= 3)
    check("&amp; entity kept raw in the segment", any("&amp;" in t for _, t in segs))
    items, lay = B.split_items(F, ["mcq_200.json"], "json")
    check("JSON array → 200 items", len(items) == 200 and items[0]["input"]["data"]["id"] == 1)
    items, lay = B.split_items(F, ["mcq_wrapped.json"], "json")
    check("JSON object wrapping a list → its elements (wrapper kept)", len(items) == 12 and lay["wrapper"] == "questions")
    items, lay = B.split_items(F, ["mcq_30.csv"], "csv")
    check("CSV rows with header", len(items) == 30 and lay["fields"][:3] == ["id", "question", "option_a"] and lay["delimiter"] == ",")
    items, _ = B.split_items(F, ["mcq_20.jsonl"], "jsonl"); check("JSONL lines", len(items) == 20)
    items, _ = B.split_items(F, ["lines.txt"], "lines"); check("TXT lines (blank lines skipped)", len(items) == 3, [i["label"] for i in items])
    items, _ = B.split_items(F, ["guide.md"], "md_sections"); check("MD sections by heading", len(items) == 3 and items[1]["input"]["text"].startswith("## Green tea"))
    items, _ = B.split_items(F / "site", B.list_files(F / "site"), "html_pages")
    check("HTML folder → one item per page (CSS/PNG skipped)", sorted(i["label"] for i in items) == ["about.html", "blog/post1.html", "index.html"])
    items, _ = B.split_items(F / "site", ["index.html"], "html_sections")
    check("HTML page → items per section", len(items) >= 3 and any("Our weekly offers" in i["label"] for i in items), [i["label"] for i in items])
    check("auto-detect", [B.detect_split([f], F, "file") for f in ("mcq_200.json", "mcq_30.csv", "mcq_20.jsonl", "lines.txt", "guide.md", "site/index.html")] ==
          ["json", "csv", "jsonl", "lines", "md_sections", "html_pages"])
    long = "\n\n".join(f"Paragraph {i}. " + "Tea is nice. " * 40 for i in range(12))
    ch = B.text_chunks(long, 1500)
    check("long text chunked to fit the context, nothing lost", all(len(c) <= 1500 for c in ch) and "".join(ch).replace("\n", "") == long.replace("\n", ""), [len(c) for c in ch])
    u = B.units_of({"type": "html", "segs": {str(i): "word " * 300 for i in range(10)}}, {"chunk_chars": 3500})
    check("long HTML page → several context-sized units", len(u) >= 3 and all(sum(len(t) for t in x["segs"].values()) <= 3600 for x in u), len(u))
    check("short labels (<3 words) are not sent to the model", B.units_of({"type": "html", "segs": {"1": "Home", "2": "About us page text here"}}, {})[0]["segs"] == {"2": "About us page text here"})
    q = {"id": 3, "question": "What is 2 + 2?", "options": ["3", "4", "5", "6"], "answer": "B"}
    f = B.mcq_fields(q); opts = B.options_of(q, f)
    check("MCQ fields + options detected", f["q"] == "question" and f["a"] == "answer" and [l for l, _ in opts] == list("ABCD"))
    check("answer keys: letter / 'b)' / text / number / 'Option B'", [B.resolve_answer(a, opts) for a in ("B", "b)", "4", 2, "Option B", "B) 4", "Z")] == [1, 1, 1, 1, 1, 1, None])
    check("number answers can be 0-based", B.resolve_answer(1, opts, zero_based=True) == 1)
    qc = {"id": 1, "question": "Capital of France?", "option_a": "Paris", "option_b": "Rome", "answer": "A"}
    check("CSV-style option columns", [t for _, t in B.options_of(qc, B.mcq_fields(qc))] == ["Paris", "Rome"])
    good = {**q, "question": "Add two and two. What do you get?", "answer": "B", "explanation": "Two plus two equals four, option B."}
    p, n, _ = B.mcq_check(q, good); check("valid rewrite passes MCQ checks", p == [] and n == [], p)
    p, n, _ = B.mcq_check(q, {**good, "answer": "E"}); check("answer key not matching an option is caught", any("does not match" in x for x in p))
    p, n, _ = B.mcq_check(q, {**good, "question": "What is 2 + 2 ?"}); check("unchanged wording is caught", any("too close" in x for x in p))
    p, n, _ = B.mcq_check(q, {**good, "options": ["3", "4", "4", "6"]}); check("duplicate options caught", any("same" in x for x in p))
    p, n, _ = B.mcq_check(q, {**good, "answer": "C"}); check("changed answer key → note for review", n and "B → C" in n[0])
    p, n, _ = B.mcq_check(q, {**good, "explanation": ""}); check("missing explanation caught", any("explanation" in x for x in p))
    sch = B.auto_mcq_schema(q, ["explanation"])
    check("auto MCQ schema: same fields + explanation required", sch["required"] == ["id", "question", "options", "answer", "explanation"])
    check("schema: valid", B.validate_schema(good, sch) == [])
    errs = B.validate_schema({**good, "options": "4"}, sch) + B.validate_schema({k: v for k, v in good.items() if k != "id"}, sch)
    check("schema: wrong type + missing field reported", any("should be array" in e for e in errs) and any("missing field 'id'" in e for e in errs), errs)
    check("schema: enum/minimum/additionalProperties", B.validate_schema({"a": 5, "x": 1}, {"type": "object", "properties": {"a": {"enum": [1, 2]}}, "additionalProperties": False}) == ["$.a must be one of [1, 2]", "unexpected field 'x'"])
    check("JSON parsed from fenced / chatty replies", B.parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1} and B.parse_json('Here: {"a": {"b": 2}} done') == {"a": {"b": 2}})
    check("'Here is the rewritten text:' preface stripped", B.clean_text("Here is the rewritten text:\nHello") == "Hello")
    s = B.save_input("", "../../evil/../x.json", b"[1]"); d = B.root() / "inputs" / s["set"] / "files"
    check("upload path traversal neutralised", (d / "evil/x.json").exists() and not (B.root().parent / "evil").exists(), B.list_files(d))
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as z: z.writestr("../../pwn.html", "<p>x y z</p>"); z.writestr("site/a.html", "<p>hello there friend</p>"); z.writestr("__MACOSX/._a.html", "junk")
    s = B.save_input("", "pages.zip", zb.getvalue())
    check("zip extracted safely (no ../, no __MACOSX)", s["kind"] == "folder" and B.list_files(B.root() / "inputs" / s["set"] / "files") == ["pwn.html", "site/a.html"], s)

    # ---------------- engine against the mock model
    for _ in range(50):
        try: socket.create_connection(("127.0.0.1", PORT), 0.2).close(); break
        except OSError: time.sleep(0.2)
    db = DB(); m = B.BatchManager(db, cfg)
    names = [t["name"] for t in m.templates()]
    check("built-in templates shipped", any(n.startswith("Rewrite MCQ in original wording") for n in names) and any("AdSense" in n for n in names))
    try: m.save_template({"name": names[0], "prompt": "x"}); check("built-in templates can't be overwritten", False)
    except ValueError: check("built-in templates can't be overwritten", True)
    t = m.save_template({"name": "My quiz", "prompt": "Do {item}", "verify": True}); check("save + list custom template", any(x["name"] == "My quiz" for x in m.templates()) and t["verify"])
    m.delete_template(t["id"]); check("delete custom template", not any(x["name"] == "My quiz" for x in m.templates()))
    pv = m.preview({"source": str(F / "mcq_200.json"), "template": "mcq", "start": 11, "limit": 25})
    check("preview: counts, start/limit selection, fields, output folder", pv["total_items"] == 200 and pv["selected"] == 25 and pv["first_item"] == 11 and "answer" in pv["fields"] and pv["output_dir"].endswith("output"), pv)
    for bad, msg in [({"mode": "repeat", "count": 0, "prompt": "x"}, "between 1 and 10000"), ({"mode": "repeat", "count": 10001, "prompt": "x"}, "between"),
                     ({"source": str(F / "mcq_200.json")}, "Write a prompt"), ({"source": "/nope/x.json", "prompt": "x"}, "not found"),
                     ({"source": str(F / "mcq_200.json"), "prompt": "x", "schema": "{bad"}, "not valid JSON")]:
        try: m.preview(bad); check(f"rejects: {msg}", False)
        except ValueError as e: check(f"rejects: {msg}", msg in str(e), e)
    async def run_all():
        m.start()
        j1 = m.create({"source": str(F / "mcq_200.json"), "template": "mcq"})
        j2 = m.create({"source": str(F / "mcq_30.csv"), "template": "mcq"})
        j3 = m.create({"source": str(F / "site"), "template": "adsense", "verify": True})
        j4 = m.create({"mode": "repeat", "count": 6, "prompt": "Write fact number {i} of {total}.", "chain": True})
        j5 = m.create({"source": str(F / "mcq_200.json"), "template": "mcq", "start": 101, "limit": 5, "include_unprocessed": False, "verify": False})
        j6 = m.create({"source": str(F / "guide.md"), "template": "rewrite text"})
        t0 = time.time()
        while m.busy() and time.time() - t0 < 120: await asyncio.sleep(0.1)
        return [m.job(j["id"], log=1000) for j in (j1, j2, j3, j4, j5, j6)]
    j1, j2, j3, j4, j5, j6 = asyncio.run(run_all())
    check("200-MCQ job done", j1["status"] == "done" and j1["done"] == 200, j1["error"])
    fl = m.items(j1["id"], "flagged", 0, 100)["items"]
    check("always-wrong answer keys flagged after 2 retries (ids 50,100,150,200)", sorted(int(i["label"].split()[0][1:]) for i in fl) == [50, 100, 150, 200] and all(i["attempts"] == 3 for i in fl), [(i["label"], i["attempts"]) for i in fl])
    check("flag reason says what the verifier found", "reviewer thinks the answer is" in fl[0]["problems"], fl[0]["problems"])
    log = "\n".join(l["text"] for l in j1["log"])
    check("wrong key on 1st try fixed by retry (#20, #40…)", re.search(r"#20 .*✓ ok \(2 attempts", log) and re.search(r"#40 .*✓ ok \(2 attempts", log), log[:2000])
    check("missing field caught by schema/MCQ check then fixed (#7, #37)", re.search(r"#37 .*✓ ok \(2 attempts", log) and "missing field(s): explanation" in log)
    check("wrong key in the source data is fixed and noted (#45)", re.search(r"#45 .*answer key changed", log), log[:300])
    out = Path(j1["output_dir"]); files = B.output_files(j1["id"])
    check("result + review files written", files == ["mcq_200_rewritten.json", "review.xlsx", "review_flagged.csv"], files)
    data = json.loads((out / "mcq_200_rewritten.json").read_text())
    check("result JSON: 200 items, same fields + explanation, new wording", len(data) == 200 and all(set(x) == {"id", "question", "options", "answer", "topic", "explanation"} for x in data)
          and data[0]["question"].startswith("Find the total") and data[0]["options"] == json.loads((F / "mcq_200.json").read_text())[0]["options"])
    ok_keys = sum(1 for x in data if x["options"]["ABCD".index(x["answer"])] == str(sum(map(int, re.findall(r"\d+", x["question"])))))
    check("answer keys correct for all non-flagged items", ok_keys == 196, ok_keys)
    from openpyxl import load_workbook
    wb = load_workbook(out / "review.xlsx"); ws = wb["Flagged for review"]; rows = list(ws.iter_rows(values_only=True))
    check("review.xlsx: flagged sheet with original vs new side by side", rows[0][-2:] == ("Original", "New") and len(rows) == 1 + 4 + 4 and "What is" in rows[1][6] and "Find the total" in rows[1][7], len(rows))
    check("review.xlsx: 'All items' sheet", len(list(wb["All items"].iter_rows())) == 201)
    rc = list(csv.reader(io.StringIO((out / "review_flagged.csv").read_text("utf-8-sig"))))
    check("review_flagged.csv", rc[0][0] == "#" and len(rc) == 9)
    csvout = list(csv.DictReader(io.StringIO((Path(j2["output_dir"]) / "mcq_30_rewritten.csv").read_text("utf-8-sig"))))
    check("CSV in → CSV out with the same columns + explanation", len(csvout) == 30 and list(csvout[0]) == ["id", "question", "option_a", "option_b", "option_c", "option_d", "answer", "explanation"], list(csvout[0]))
    check("HTML folder job done; prohibited text caught by verify then fixed", j3["status"] == "done" and j3["flagged"] == 0 and "prohibited content" in "\n".join(l["text"] for l in j3["log"]))
    zp = Path(j3["output_dir"]) / "site_rewritten.zip"; z = zipfile.ZipFile(zp)
    check("HTML zip has every file (pages, CSS, image)", sorted(z.namelist()) == sorted(B.list_files(F / "site")), z.namelist())
    new = z.read("index.html").decode(); tags = lambda h: re.findall(r"<[^>]+>", h)
    check("markup and tags kept intact (identical tag sequence)", tags(new) == tags(src))
    check("script, <pre><code> and attributes untouched", "do not touch this script text" in new and 'coupon = "TEA2026"' in new and 'alt="A cup of green tea"' in new)
    check("visible text rewritten; prohibited sentence removed", "Our team offer the finest green tea" in new and "cracked" not in new and "Click the ads" not in new)
    check("entities stay valid (&amp;)", "&amp; comes in a recyclable box" in new, re.findall(r"Each.*?box", new))
    check("binary files copied byte for byte", z.read("img/cup.png") == (F / "site/img/cup.png").read_bytes())
    md = (Path(j4["output_dir"]) / "results.md").read_text()
    check("repeat ×6 with {i}/{total} and previous output chained", md.count("## Run") == 6 and "Result of run 6." in md and "(built on: Result of run 5." in md, md[-300:])
    d5 = json.loads((Path(j5["output_dir"]) / "mcq_200_rewritten.json").read_text())
    check("start=101 limit=5 → exactly items 101–105", [x["id"] for x in d5] == [101, 102, 103, 104, 105])
    mdout = (Path(j6["output_dir"]) / "guide_rewritten.md").read_text()
    check("Markdown sections rewritten, headings kept", [l.split()[0] for l in mdout.splitlines() if l.startswith("#")] == ["#", "##", "##"] and "Camellia sinensis" in mdout and "made using a single plant" in mdout, mdout)
    # pause / resume / cancel / restart
    async def lifecycle():
        m2 = B.BatchManager(db, cfg); m2.start()
        os.environ["MOCK_DELAY"] = "0"
        j = m2.create({"source": str(F / "mcq_200.json"), "template": "mcq", "verify": False})
        while m2.job(j["id"])["done"] < 20: await asyncio.sleep(0.02)
        check("pause", m2.pause(j["id"])); await asyncio.sleep(0.4); d1 = m2.job(j["id"])["done"]; await asyncio.sleep(0.4)
        check("paused job does not advance", m2.job(j["id"])["done"] == d1 and m2.job(j["id"])["status"] == "paused")
        check("resume", m2.resume(j["id"]))
        while m2.job(j["id"])["done"] < d1 + 20: await asyncio.sleep(0.02)
        # simulate a crash: stop the worker mid-item, then a new manager (= restarted Jiv) picks the job up
        m2.task.cancel(); await asyncio.sleep(0.1)
        db.x("UPDATE batch_jobs SET status='running' WHERE id=?", (j["id"],))
        mid = m2.job(j["id"])["done"]
        m3 = B.BatchManager(db, cfg); m3.start()
        while m3.job(j["id"])["status"] not in ("done", "failed"): await asyncio.sleep(0.05)
        jj = m3.job(j["id"], log=2000)
        per = {}
        for l in jj["log"]:
            if l["idx"] is not None and ("✓ ok" in l["text"] or "⚠ flagged" in l["text"]): per[l["idx"]] = per.get(l["idx"], 0) + 1
        check("restart resumes where it left off and finishes", jj["status"] == "done" and jj["done"] == 200 and mid < 200 and any("resuming" in l["text"] for l in jj["log"]))
        check("every item processed exactly once across pause + restart", len(per) == 200 and set(per.values()) == {1}, {k: v for k, v in per.items() if v != 1})
        j2 = m3.create({"source": str(F / "mcq_200.json"), "template": "mcq", "verify": False})
        while m3.job(j2["id"])["done"] < 5: await asyncio.sleep(0.02)
        check("cancel", m3.cancel(j2["id"])); await asyncio.sleep(0.3)
        jc = m3.job(j2["id"])
        part = json.loads((Path(jc["output_dir"]) / "mcq_200_rewritten.json").read_text())
        check("cancelled job keeps finished items as a partial result", jc["status"] == "cancelled" and 5 <= len(part) == jc["done"] < 200, (len(part), jc["done"]))
        FAILFLAG.write_text("x")
        j3 = m3.create({"mode": "repeat", "count": 3, "prompt": "x {i}"})
        while m3.job(j3["id"])["status"] not in ("failed", "done"): await asyncio.sleep(0.1)
        check("model server down → job stops as 'failed' after 3 errors (not a crash loop)", m3.job(j3["id"])["status"] == "failed" and "3 times" in m3.job(j3["id"])["error"])
        FAILFLAG.unlink(); m3.resume(j3["id"])
        while m3.job(j3["id"])["status"] not in ("failed", "done"): await asyncio.sleep(0.1)
        check("…and Resume finishes it once the model is back", m3.job(j3["id"])["status"] == "done")
        e = await m3.estimate({"source": str(F / "mcq_200.json"), "template": "mcq"})
        check("estimate from 1 test item: tok/s, ETA, sample output", e["items"] == 200 and e["tok_per_s"] and e["eta_secs"] >= 0 and "Find the total" in e["sample_output"] and "verify: passed" in e["notes"], e)
        m3.delete(j2["id"]); check("delete job removes rows + files", not m3.job(j2["id"]) and not B.job_dir(j2["id"]).exists())
    asyncio.run(lifecycle())
    # chat tool
    from jiv import agent as AG, tools as T
    AG.BATCH_MGR["m"] = m
    d = AG.describe("start_batch", {"source": str(F / "mcq_200.json"), "template": "mcq", "limit": 50})
    check("start_batch permission card: prompt, item count, output folder", "50 item(s)" in d and "Prompt: Rewrite this multiple-choice" in d and "Output folder:" in d and "no per-item" in d, d)
    check("start_batch needs permission and is in the small-model tool set", T.needs_permission("start_batch") and "start_batch" in [s["function"]["name"] for s in T.schemas(["*"], small=True)])
    check("template aliases (mcq / adsense)", AG.batch_spec({"template": "mcq"})["template"] == "Rewrite MCQ" and AG.batch_spec({"template": "adsense"})["template"].startswith("Rewrite page"))
finally:
    mock.terminate()
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
