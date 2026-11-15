"""Attachments: text extraction, context truncation, read_file on uploads, vision handling, small-model honesty prompt.
Run: python tests/test_uploads.py  (needs pypdf + openpyxl)"""
import asyncio, json, os, sys, tempfile
from pathlib import Path
os.environ["JIV_DATA_DIR"] = tempfile.mkdtemp(prefix="jiv-upl-")
SRC = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(SRC))
from jiv import uploads as U, tools as T
from jiv.agent import Runner, is_small_model
FX = SRC / "tests/fixtures/attach"
res = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:300]}]"))
def up(name): return U.save(name, (FX / name).read_bytes())

pdf = up("quarterly-report.pdf"); t = U.text(pdf)
check("PDF text extracted (pypdf)", "Revenue grew 18%" in t and "--- Page 2 ---" in t and "headcount is 46" in t, t)
check("DOCX text extracted", "move the launch to 12 November" in U.text(up("notes.docx")))
x = U.text(up("sales.xlsx")); check("XLSX extracted (openpyxl)", "Sheet: Sales" in x and "North,120" in x, x)
p = U.text(up("deck.pptx")); check("PPTX extracted", "Slide 1" in p and "Ship R2 in Europe" in p, p)
h = U.text(up("page.html")); check("HTML -> visible text only", "Hello page" in h and "Visible & text" in h and "alert" not in h, h)
img = up("photo.png"); check("image stored, no text", img["kind"] == "image" and img["mime"] == "image/png" and "chars" not in img)
code = U.save("x.py", b"def f():\n    return 42\n"); check("code file is text", U.text(code).startswith("def f()"))
bad = U.save("blob.bin", b"\x00\x01\x02"); check("unknown binary -> clear note", bad["kind"] == "binary" and "can't read" in bad["error"])
nul = U.save("weird.txt", b"ab\x00cd"); check("binary disguised as .txt rejected", "binary" in nul["error"])
ev = U.save("../../evil\\..\\name.txt", b"x"); check("filename sanitised (no traversal)", "/" not in ev["name"] and ".." not in ev["name"].split(".")[0] and (U.uploads_dir() / ev["id"] / ev["name"]).exists(), ev["name"])
check("get() rejects bad ids", U.get("../../etc") is None and U.get("zzz") is None)
lng = up("long.txt"); blk = U.context_block(lng, 1000)
check("long file truncated with a clear note", len(blk) < 1400 and "only the first 1,000 are shown" in blk and "SECRET_END_MARKER" not in blk and lng["id"] in blk, blk[-300:])
check("short file included whole", "[End of quarterly-report.pdf]" in U.context_block(pdf, 4000))
r = asyncio.run(T.read_file(lng["id"], offset=17000, max_chars=2000))
check("read_file pages through an upload by id", "SECRET_END_MARKER" in r and "end of file" in r, r[-200:])
r = asyncio.run(T.read_file("quarterly-report.pdf")); check("read_file finds an upload by name", "Revenue grew" in r)
r = asyncio.run(T.read_file(str(FX / "notes.docx"))); check("read_file converts a DOCX on disk", "12 November" in r)
check("read_file is a read-only (auto-run) tool", T.needs_permission("read_file") is False)
check("vision detection", U.is_vision_model("qwen2.5vl:3b") and U.is_vision_model("llava:7b") and not U.is_vision_model("qwen2.5:3b"))
check("small-model detection", is_small_model("qwen2.5:3b") and is_small_model("nemotron-mini") and is_small_model("llama3.1:8b") and not is_small_model("qwen2.5:14b") and not is_small_model("gpt-4o"))

class FakeDB:
    def __init__(self, msgs): self._m = msgs
    def msgs(self, cid): return self._m
    def agents(self): return []
class Cfg(dict):
    def get(self, k, d=None): return super().get(k, d)
msgs = [{"role": "user", "content": "first", "meta": {"attachments": [{"id": pdf["id"]}]}},
        {"role": "assistant", "content": "ok", "meta": {}},
        {"role": "user", "content": "what's in these?", "meta": {"attachments": [{"id": lng["id"]}, {"id": img["id"]}]}}]
rn = Runner(Cfg(attach_chars=3000, model="qwen2.5:3b"), FakeDB(msgs), None)
hist = rn.history("c", False, "qwen2.5:3b")
check("older attachment -> name only", "Earlier attachment: quarterly-report.pdf" in hist[0]["content"] and "Revenue" not in hist[0]["content"])
check("latest attachment injected + truncated", "[Attached file: long.txt" in hist[2]["content"] and "NOTE: this file is" in hist[2]["content"])
check("non-vision model told it can't see the image", "cannot see images" in hist[2]["content"] and "qwen2.5vl:3b" in hist[2]["content"])
hv = rn.history("c", False, "qwen2.5vl:3b")[2]["content"]
check("vision model gets the image as image_url", isinstance(hv, list) and hv[1]["type"] == "image_url" and hv[1]["image_url"]["url"].startswith("data:image/png;base64,"))
agent = {"id": "a", "name": "Jiv", "persona": "You are {name}.", "memory": "", "model": ""}
sp = Runner(Cfg(model="qwen2.5:3b"), FakeDB([]), None).system_prompt(agent, ["*"], False)
check("small model: 'can't write Jiv updates' rule, scoped to Jiv's own code only, + Updates page", "can't reliably write updates to Jiv's source" in sp and "ONLY for requests to add a feature to Jiv" in sp and "⬆ Updates" in sp and "pretend" in sp)
sp2 = Runner(Cfg(model="qwen2.5-coder:32b"), FakeDB([]), None).system_prompt(agent, ["*"], False)
check("big model: propose_update rule, no small-model text", "propose_update" in sp2 and "CANNOT" not in sp2)
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
