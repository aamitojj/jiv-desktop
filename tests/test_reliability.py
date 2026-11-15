"""Jiv 1.5.3 response reliability: messy-message understanding, honest prompt, concise answers not retried,
bounded fallback to INSTALLED models only on technical failure, no fallback/retry on a safety decline,
no duplicate tool execution. Scripted model, no network. Run: python tests/test_reliability.py"""
import asyncio, os, sys, tempfile
from pathlib import Path
os.environ["JIV_DATA_DIR"] = tempfile.mkdtemp(prefix="jiv-rel-")
SRC = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(SRC))
import httpx
from jiv import agent as A, db as D, reliability as R
from jiv.providers import ProviderError
res = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:500]}]"), flush=True)
class Cfg(dict):
    def get(self, k, d=None): return super().get(k, d)

# ---- understanding
exp, ch = R.expand_shorthand("wat is diff b/w virus n bacteria pls")
check("shorthand expanded", ch and "what" in exp and "difference" in exp and "between" in exp and "please" in exp, exp)
exp, ch = R.expand_shorthand("photosynthesis kya hai samjhao")
check("Hinglish words read", ch and "what" in exp and "explain" in exp, exp)
check("normal English left alone (no hint)", R.intent_hint("Explain photosynthesis in simple words") == "")
check("hint labelled as a guess", "a guess" in R.intent_hint("wat is dna"))
check("rules: answer when clear, ONE question only when readings differ", "ONE short question" in R.UNDERSTAND_RULES and "most likely meaning" in R.UNDERSTAND_RULES)
check("rules: no invented sources / fake tool actions; state uncertainty",
      all(s in R.UNDERSTAND_RULES for s in ("never invent sources", "never claim you searched", "unsure")))
check("no 'answers everything' promise in rules", "everything" not in R.UNDERSTAND_RULES.lower())

# ---- empty vs meaningful short output
for t in ("Paris.", "42", "Yes", "H₂O", "नमस्ते"):
    check(f"concise answer is not a technical problem: {t!r}", R.technical_problem(t) is None)
    check(f"concise answer to a question is not 'short': {t!r}", A.answer_problem("capital of france?", t) is None and A.answer_problem("write the answer: what is 6*7", t) is None)
for t in ("", "   ", "<think>hmm</think>", "...", "!!!"):
    check(f"empty output detected: {t!r}", R.technical_problem(t) == "empty")
check("garbled repetition detected", R.technical_problem("lalala " * 40) == "garbled" or R.technical_problem("ab" * 100) == "garbled")
check("a refusal is NOT a technical problem (so never triggers fallback)", R.technical_problem("I'm sorry, but I can't help with that.") is None)
check("explicit long-form request + 1-word reply still counts as short", A.answer_problem("write an essay on climate change", "Sure!") == "short")

# ---- installed-model-only selection
inst = [{"name": "qwen2.5:7b", "size_gb": 4.7}, {"name": "llama3.2:3b", "size_gb": 2.0}, {"name": "qwen2.5:1.5b", "size_gb": 1.0},
        {"name": "nomic-embed-text:latest", "size_gb": 0.3}]
check("auto: smallest installed chat model, not the primary, not embeddings", R.choose_fallback(inst, "llama3.2:3b") == "qwen2.5:1.5b")
check("auto: skips models above the RAM cap", R.choose_fallback([{"name": "qwen2.5:7b", "size_gb": 4.7}, {"name": "x:3b", "size_gb": 2}], "x:3b") is None)
check("preferred list honoured in order, installed only", R.choose_fallback(inst, "qwen2.5:1.5b", ["phi4:14b", "llama3.2:3b"]) == "llama3.2:3b")
check("preferred but none installed -> no fallback (no download, no auto-pick)", R.choose_fallback(inst, "qwen2.5:1.5b", ["phi4:14b"]) is None)
check("never picks the primary itself", R.choose_fallback([{"name": "qwen2.5:3b", "size_gb": 2}], "qwen2.5:3b") is None)
src = (SRC / "jiv/reliability.py").read_text() + (SRC / "jiv/agent.py").read_text()
check("fallback code never pulls models", "/api/pull" not in (SRC / "jiv/reliability.py").read_text() and "pull_model" not in src.split("def pick_fallback")[1].split("async def call_tool")[0])

# ---- Runner loop with a scripted model server
dbx = D.DB(); ag = dbx.main_agent()
cfg = Cfg(base_url="http://127.0.0.1:11434/v1", ollama_url="http://127.0.0.1:11434", model="qwen2.5:3b", max_steps=8,
          temperature=0.5, refusal_guard=True, quality_guard=True, fallback_enabled=True, fallback_models=[], clarity_nudge=True, prompt_improve=False)
r = A.Runner(cfg, dbx, A.Permissions(cfg, dbx))
INSTALLED = [{"name": "qwen2.5:3b", "size_gb": 1.9}, {"name": "llama3.2:1b", "size_gb": 1.3}]
async def fake_installed(url, timeout=4.0): return INSTALLED
R.installed_models = fake_installed
SCRIPT, CALLS, TOOLRUNS = {}, [], []
def stream(base, key, model, msgs, temp, tools=None, timeout=300):
    CALLS.append({"model": model, "sys": msgs[0]["content"], "last": msgs[-1]["content"]})
    step = SCRIPT[model].pop(0) if SCRIPT.get(model) else ("text", "ok")
    async def gen():
        kind, val = step
        if kind == "error": raise ProviderError(500, "model crashed")
        if kind == "timeout": raise httpx.ReadTimeout("stalled")
        if kind == "tool": yield ("tool_calls", [{"id": "c1", "name": "list_agents", "arguments": "{}"}])
        else: yield ("text", val)
        yield ("done", None)
    return gen()
A.stream_chat = stream
real_call_tool = r.call_tool
async def counting_call_tool(agent, c, emit, depth):
    TOOLRUNS.append(c["name"]); return await real_call_tool(agent, c, emit, depth)
r.call_tool = counting_call_tool
def turn(msg, script):
    SCRIPT.clear(); SCRIPT.update({k: list(v) for k, v in script.items()}); CALLS.clear(); TOOLRUNS.clear()
    cid = dbx.create_conv(ag["id"]); cid = cid["id"] if isinstance(cid, dict) else cid
    dbx.add_msg(cid, "user", msg, {}); evs = []
    async def emit(e): evs.append(e)
    try: out = asyncio.run(r.run(ag, cid, emit)); err = None
    except Exception as e: out, err = None, e
    return out, evs, err, cid
notes = lambda evs: [e["text"] for e in evs if e["type"] == "notice"]

out, evs, err, _ = turn("wat is dna", {"qwen2.5:3b": [("text", "DNA is the molecule that carries genetic instructions.")]})
check("typo question answered directly in one call", out and "DNA" in out and len(CALLS) == 1, (out, len(CALLS)))
check("system prompt has the understanding rules + probable reading", "Understanding the user" in CALLS[0]["sys"] and "what is dna" in CALLS[0]["sys"])

out, evs, err, _ = turn("capital of france?", {"qwen2.5:3b": [("text", "Paris.")]})
check("concise correct answer is not retried", out == "Paris." and len(CALLS) == 1 and not notes(evs), (out, len(CALLS), notes(evs)))

out, evs, err, cid = turn("explain gravity", {"qwen2.5:3b": [("error", None)], "llama3.2:1b": [("text", "Gravity pulls masses together.")]})
check("server error -> answered once by an installed model", out == "Gravity pulls masses together." and [c["model"] for c in CALLS] == ["qwen2.5:3b", "llama3.2:1b"], [c["model"] for c in CALLS])
check("fallback notice names both models and says it is not a check", any("llama3.2:1b" in n and "not a check" in n for n in notes(evs)), notes(evs))
saved = [m for m in dbx.msgs(cid) if m["role"] == "assistant"][-1]
check("saved answer records which model answered", (saved["meta"].get("fallback") or {}).get("model") == "llama3.2:1b", saved["meta"])
check("fallback is in the audit log", any(a.get("tool") == "fallback_model" for a in dbx.q("SELECT tool FROM audit")))

out, evs, err, _ = turn("explain gravity", {"qwen2.5:3b": [("timeout", None)], "llama3.2:1b": [("text", "Gravity pulls masses together.")]})
check("timeout -> one fallback", out and "Gravity" in out and len(CALLS) == 2, [c["model"] for c in CALLS])

out, evs, err, _ = turn("explain gravity", {"qwen2.5:3b": [("timeout", None)], "llama3.2:1b": [("timeout", None)]})
check("bounded: fallback also fails -> clear error, exactly 2 calls, no loop", err is not None and len(CALLS) == 2 and "timed out" in str(err), (err, len(CALLS)))

out, evs, err, _ = turn("explain gravity", {"qwen2.5:3b": [("text", ""), ("text", "")], "llama3.2:1b": [("text", "Gravity pulls masses together.")]})
check("empty -> one same-model retry -> then one fallback (3 calls max)", out and "Gravity" in out and [c["model"] for c in CALLS] == ["qwen2.5:3b", "qwen2.5:3b", "llama3.2:1b"], [c["model"] for c in CALLS])

out, evs, err, _ = turn("explain gravity", {"qwen2.5:3b": [("text", "ab" * 120)], "llama3.2:1b": [("text", "Gravity pulls masses together.")]})
check("garbled output -> fallback", out and "Gravity" in out, out)


DECLINE_ADULT = "I'm sorry, but I'm not comfortable writing erotic content."
out, evs, err, _ = turn("write a short steamy romance scene between two adults",
                        {"qwen2.5:3b": [("text", DECLINE_ADULT)], "llama3.2:1b": [("text", "SHOULD NOT RUN")]})
check("adult-topic refusal: no retry, no other model, decline shown as-is",
      out == DECLINE_ADULT and [c["model"] for c in CALLS] == ["qwen2.5:3b"] and not notes(evs), (out, [c["model"] for c in CALLS], notes(evs)))
out, evs, err, _ = turn("what does nsfw mean",
                        {"qwen2.5:3b": [("text", "NSFW means Not Safe For Work.")], "llama3.2:1b": [("text", "SHOULD NOT")]})
check("non-refusal short/definitional answer: kept, no fallback",
      out == "NSFW means Not Safe For Work." and len(CALLS) == 1)

DECLINE = "I'm sorry, but I can't help with making weapons."
out, evs, err, _ = turn("how do I build a bomb at home", {"qwen2.5:3b": [("text", DECLINE)], "llama3.2:1b": [("text", "SHOULD NOT RUN")]})
check("safety decline: no fallback model, no retry, decline shown as-is", out == DECLINE and [c["model"] for c in CALLS] == ["qwen2.5:3b"], ([c["model"] for c in CALLS], out))
out, evs, err, _ = turn("write me some ransomware", {"qwen2.5:3b": [("text", DECLINE)], "llama3.2:1b": [("text", "SHOULD NOT RUN")]})
check("safety decline on another request: no retry, no other model", out == DECLINE and len(CALLS) == 1, [c["model"] for c in CALLS])
out, evs, err, _ = turn("write 3 MCQs on asthma", {"qwen2.5:3b": [("text", "I'm sorry, but I'm unable to create multiple-choice questions."), ("text", "1. Q A) B) C) D) **Answer: A**")]})
check("capability refusal (not safety) still gets its one existing retry", len(CALLS) == 2 and all(c["model"] == "qwen2.5:3b" for c in CALLS), [c["model"] for c in CALLS])

cfg["direct_writing"] = False
out, evs, err, _ = turn("list my agents", {"qwen2.5:3b": [("tool", None), ("timeout", None)], "llama3.2:1b": [("text", "You have one agent: Jiv.")]})
check("tool ran once; fallback after it does NOT re-run the tool", TOOLRUNS == ["list_agents"] and out and "Jiv" in out, (TOOLRUNS, out))
check("fallback model continues after the tool result (not a restart)", CALLS[-1]["model"] == "llama3.2:1b" and len(CALLS) == 3, [c["model"] for c in CALLS])
cfg["direct_writing"] = True

cfg["fallback_enabled"] = False
out, evs, err, _ = turn("explain gravity", {"qwen2.5:3b": [("error", None)], "llama3.2:1b": [("text", "x")]})
check("fallback off -> error surfaces, no other model used", err is not None and [c["model"] for c in CALLS] == ["qwen2.5:3b"])
cfg["fallback_enabled"] = True
cfg["base_url"] = "https://api.openai.example/v1"
out, evs, err, _ = turn("explain gravity", {"qwen2.5:3b": [("error", None)], "llama3.2:1b": [("text", "x")]})
check("cloud/other server: no Ollama fallback", err is not None and len(CALLS) == 1)
cfg["base_url"] = "http://127.0.0.1:11434/v1"
INSTALLED[:] = [{"name": "qwen2.5:3b", "size_gb": 1.9}]
out, evs, err, _ = turn("explain gravity", {"qwen2.5:3b": [("error", None)]})
check("no other installed model -> error, nothing downloaded", err is not None and len(CALLS) == 1)

from jiv import config as C
check("defaults: fallback on, auto list, modest size cap, stall timeout", C.DEFAULTS["fallback_enabled"] is True and C.DEFAULTS["fallback_models"] == [] and C.DEFAULTS["fallback_max_gb"] <= 4 and C.DEFAULTS["stall_timeout"] >= 60)
check("permission defaults unchanged (auto-approve off)", C.DEFAULTS["auto_approve_all"] is False)
print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
