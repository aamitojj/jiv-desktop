"""Jiv 1.3 unit tests: memory (BM25, auto-capture, dedupe, forget), skills, feedback lessons, prompt budget,
small-model tool handling (Nemotron <toolcall>), language manager (mock winget) and real run_code on this machine.
Run: python tests/test_learning.py"""
import asyncio, json, os, shutil, sys, tempfile
from pathlib import Path
os.environ["JIV_DATA_DIR"] = tempfile.mkdtemp(prefix="jiv-learn-")
SRC = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(SRC))
from jiv.db import DB
from jiv.learning import Learning, bm25, auto_extract
from jiv import languages as L, tools as T
from jiv.agent import parse_tool_calls, Runner, Permissions, is_small_model
res = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:400]}]"), flush=True)

db = DB()
db.update_agent(db.main_agent()["id"], memory="- old note from 1.2")
ln = Learning(db)
check("1.2 remember() notes migrated into the memory store", any("old note from 1.2" in m["text"] for m in ln.memories()) and not db.main_agent()["memory"])
check("BM25 ranks the matching doc first", bm25("python error traceback", [(1, "the user likes cats"), (2, "fix python traceback errors"), (3, "python")])[0][1] == 2)
check("BM25 ignores stop words", bm25("what is the", [(1, "what is the time")]) == [])
for t, want in [("My name is Ravi Kumar and I live in Pune.", ["The user's name is Ravi Kumar.", "The user lives in Pune."]),
                ("I prefer short answers.", ["The user prefers short answers."]),
                ("Remember that my dog is called Bruno", ["The user's dog is called Bruno."]), ("what is my name?", [])]:
    got = [f for _, f in auto_extract(t)]; check(f"auto-capture: {t!r}", got == want, got)
a = ln.add("The user's name is Ravi.", "fact"); b = ln.add("The user's name is Ravi.", "fact")
check("exact duplicate not stored twice", a == b)
c = ln.add("The user's name is Ravindra.", "fact")
check("newer fact about the same subject replaces the old one", c == a and any(m["text"] == "The user's name is Ravindra." for m in ln.memories()))
ln.add("The user prefers metric units.", "pref"); ln.add("The user's favourite editor is VS Code.", "fact"); ln.add("The user's cat is called Mittens.", "fact")
r = [m["text"] for m in ln.recall("which code editor should I configure?")]
check("recall finds the relevant fact", any("VS Code" in t for t in r), r)
check("preferences + name always included", any("metric" in t for t in r) and any("Ravindra" in t for t in r), r)
check("recall respects the character budget", sum(len(m["text"]) for m in ln.recall("cat editor name units", budget=60)) <= 60)
gone = ln.forget("cat Mittens"); check("forget by text", gone == ["The user's cat is called Mittens."] and not any("Mittens" in m["text"] for m in ln.memories()), gone)
mid = ln.add("temp fact to delete", "fact"); ln.forget(f"#{mid}"); check("forget by #id", not any(m["id"] == mid for m in ln.memories()))
# feedback -> lessons
cid = db.create_conv(db.main_agent()["id"])["id"]; db.add_msg(cid, "user", "What is the capital of Australia?")
am = db.add_msg(cid, "assistant", "Sydney.")
f = ln.feedback(cid, am, -1, "It's Canberra, not Sydney.", "What is the capital of Australia?", "Sydney.")
les = ln.memories(kind="lesson")
check("👎 + correction becomes a lesson", f["lesson_id"] and "Canberra" in les[0]["text"] and "capital of Australia" in les[0]["text"], les)
check("ratings stored per message", ln.ratings(cid) == {am: -1})
ln.feedback(cid, am, 1); check("re-rating replaces the old rating", ln.ratings(cid) == {am: 1})
pb = ln.prompt_block("capital of australia", budget=900)
check("prompt block has memory + lesson within budget", "Canberra" in pb and "Ravindra" in pb and len(pb) <= 900, pb)
# skills
ln.save_skill("csv to json", "Convert a CSV file to JSON", "read csv, write json", "python", "import csv, json\nprint('ok')")
ln.save_skill("disk usage report", "Show the biggest folders", "", "powershell", "Get-ChildItem")
check("skills retrieved by relevance", ln.relevant_skills("turn my csv into json")[0]["name"] == "csv to json")
check("skill lookup by fuzzy name", ln.skill("csv json")["name"] == "csv to json")
check("skills listed in the prompt block", "csv to json" in ln.prompt_block("convert csv to json please"))
# small-model tool handling
known = T.all_tool_names()
txt, calls = parse_tool_calls('<toolcall> {"name": "run_code", "arguments": {"language": "python", "code": "print(1)"}} </toolcall>', known)
check("Nemotron <toolcall> format parsed", calls == [{"name": "run_code", "arguments": {"language": "python", "code": "print(1)"}}] and txt == "", calls)
_, calls = parse_tool_calls('Sure! {"name": "recall", "parameters": {"query": "name"}}', known)
check("bare JSON with 'parameters' parsed", calls and calls[0]["name"] == "recall")
check("nemotron-mini:4b is treated as small", is_small_model("nemotron-mini:4b") and is_small_model("nemotron-mini"))
small = T.schemas(["*"], small=True); names = [s["function"]["name"] for s in small]
check("small models get a compact tool set", "run_code" in names and "propose_update" not in names and "create_agent" not in names and len(json.dumps(small)) < len(json.dumps(T.schemas(["*"]))) * 0.6, len(json.dumps(small)))
class Cfg(dict):
    def get(self, k, d=None): return super().get(k, d)
    def update(self, d): super().update(d)
agent = db.main_agent()
rn = Runner(Cfg(model="nemotron-mini:4b"), db, None); rn.learn = ln
sp = rn.system_prompt(agent, ["*"], True, "what's my name")
check("small-model prompt: tool guide + JSON fallback + memory", "## Tools (optional)" in sp and "ONE tool at a time" in sp and "run_code" in sp and "Ravindra" in sp and "<tool_call>" in sp, sp[:300])
check("small-model prompt stays compact", len(sp) < 10000, len(sp))
check("run_code needs permission; read-only memory tools don't", T.needs_permission("run_code") and not T.needs_permission("recall") and not T.needs_permission("remember"))
pm = Permissions(Cfg(always_allow=["run_code"], auto_reads=True), db)
check("'Always allow run_code' also covers run_skill", pm.auto("run_code") and pm.auto("run_skill") and not pm.auto("write_file"))

# languages
check("aliases", L.norm_lang("JS") == "javascript" and L.norm_lang("c#") == "csharp" and L.norm_lang("py") == "python")
det = {d["id"]: d for d in L.detect(force=True)}
check("detects python (always available via Jiv's own Python)", det["python"]["installed"])
os.environ["JIV_WINGET"] = str(SRC / "tests/fixtures/bin/winget"); os.environ["FAKE_WINGET_BIN"] = fb = tempfile.mkdtemp()
cmd = L.install_cmd("java")
check("winget command uses the exact id", cmd[1:5] == ["install", "-e", "--id", "EclipseAdoptium.Temurin.21.JDK"] and "--accept-package-agreements" in cmd, cmd)
check("all winget ids as specified", [L.RUNTIMES[k]["winget"] for k in ("python", "javascript", "java", "go", "rust", "csharp", "cpp")] ==
      ["Python.Python.3.12", "OpenJS.NodeJS.LTS", "EclipseAdoptium.Temurin.21.JDK", "GoLang.Go", "Rustlang.Rustup", "Microsoft.DotNet.SDK.8", "BrechtSanders.WinLibs.POSIX.UCRT"])
os.environ["PATH"] = fb + os.pathsep + os.environ["PATH"]
async def inst(lid):
    jid = await L.install(lid)
    while L.job_view(jid)["status"] == "running": await asyncio.sleep(0.1)
    return L.job_view(jid)
j = asyncio.run(inst("php"))
check("mock winget install → runtime detected afterwards", j["status"] == "done" and j["detected"]["installed"] and "8.3.33-fake" in j["detected"]["version"], j)
check("install log captured", any("Successfully installed" in l for l in j["log"]))
del os.environ["JIV_WINGET"]; os.environ["PATH"] = os.environ["PATH"].replace(fb + os.pathsep, "")

async def runs():
    out = {}
    progs = {"python": "import sys\nprint('py', sum(range(5)))", "javascript": "console.log('js', [1,2,3].reduce((a,b)=>a+b))",
             "c": '#include <stdio.h>\nint main(){printf("c %d\\n", 6*7);return 0;}', "cpp": '#include <iostream>\nint main(){std::cout<<"cpp "<<(1<<5)<<std::endl;}',
             "go": 'package main\nimport "fmt"\nfunc main(){ fmt.Println("go", 7*6) }', "rust": 'fn main(){ println!("rust {}", 40+2); }',
             "java": 'public class Hello { public static void main(String[] a){ System.out.println("java " + (20+22)); } }',
             "php": '<?php echo "php " . (40+2) . "\\n";', "ruby": 'puts "ruby #{40+2}"'}
    for lid, code in progs.items():
        if not L.detect_one(lid)["installed"] or "fake" in L.detect_one(lid)["version"]: out[lid] = None; continue
        out[lid] = await L.run_code(lid, code, timeout=60)
    return out
rr = asyncio.run(runs())
for lid, r in rr.items():
    if r is None: print(f"SKIP run_code {lid}: not installed on this machine"); continue
    check(f"real run_code: {lid} → {r['stdout'].strip()}", r["ok"] and {"python": "py", "javascript": "js"}.get(lid, lid) + " " in r["stdout"], r)
r = asyncio.run(L.run_code("python", "print('x'*3)\nraise ValueError('boom')"))
check("runtime error captured", not r["ok"] and "ValueError: boom" in r["stderr"] and "xxx" in r["stdout"])
r = asyncio.run(L.run_code("c", "int main( { return 0; }"))
check("compile error reported as compile stage", not r["ok"] and r["stage"] == "compile" and "error" in r["stderr"].lower(), r)
r = asyncio.run(L.run_code("python", "import time\ntime.sleep(30)", timeout=3))
check("timeout stops the program", not r["ok"] and "Timed out after 3s" in r["stderr"])
r = asyncio.run(L.run_code("python", "print(input()[::-1])", stdin="olleh"))
check("stdin passed to the program", r["ok"] and "hello" in r["stdout"])
check("code saved in <data>/workspace", str(L.workspace()) in r["file"] and Path(r["file"]).exists())
r = asyncio.run(L.run_code("cobol", "x")); check("unknown language → clear error", r["stage"] == "setup" and "Supported" in r["error"])
fmt = L.format_result({"ok": False, "stage": "run", "exit_code": 1, "stderr": "NameError", "stdout": "", "file": "f", "seconds": 0.1}, attempt=1)
check("failure asks the model to fix and retry", "Attempt 1 of 3 failed" in fmt and "COMPLETE corrected program" in fmt)
fmt3 = L.format_result({"ok": False, "stage": "run", "exit_code": 1, "stderr": "x", "stdout": "", "file": "f", "seconds": 0.1}, attempt=3)
check("after 3 failures it tells the model to stop", "Stop retrying" in fmt3)
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
