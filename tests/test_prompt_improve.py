"""Jiv 1.5.4 prompt improve: skip rules, validation, bounded call, no safety bypass, original preserved.
Run: python tests/test_prompt_improve.py"""
import asyncio, json, os, sys, tempfile
from pathlib import Path
os.environ["JIV_DATA_DIR"] = tempfile.mkdtemp(prefix="jiv-pi-")
SRC = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(SRC))
import httpx
from jiv import agent as A, db as D, prompt_improve as PI
from jiv.providers import ProviderError
from jiv import config as C
res = []
def check(n, c, i=""): res.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:500]}]"), flush=True)
class Cfg(dict):
    def get(self, k, d=None): return super().get(k, d)

# ---- skip / should_improve
check("rough typo question is a candidate", PI.should_improve("wat is diff b/w virus n bacteria")[0])
check("Hinglish candidate", PI.should_improve("photosynthesis kya hai samjhao")[0])
check("greeting skipped", PI.should_improve("hi") == (False, "greeting"))
check("code fence skipped", not PI.should_improve("```python\nprint(1)\n```")[0])
check("URL skipped", not PI.should_improve("summarise https://example.com/a")[0])
check("path skipped", not PI.should_improve("read /home/box/notes.txt")[0])
check("attachment skipped", PI.should_improve("explain this", has_attachments=True) == (False, "attachment"))
check("action command skipped", not PI.should_improve("run this command: rm -rf /tmp/x")[0])
check("disabled skipped", PI.should_improve("wat is dna", enabled=False) == (False, "disabled"))
check("already clear long question skipped", not PI.should_improve("Explain how photosynthesis converts light energy into chemical energy in plants.")[0])

# ---- validate / parse
check("valid improve accepted", PI.validate_improved("wat is dna", "What is DNA?") is None)
check("invented URL rejected", PI.validate_improved("wat is dna", "What is DNA? See https://evil.example") == "new_url")
check("lost terms rejected", PI.validate_improved("compare Kepler and Newton laws", "Tell me about science") == "lost_terms")
check("parse JSON", PI.parse_improve_json('{"improved":"What is DNA?","changed":true,"clarify":null}')["improved"] == "What is DNA?")
check("parse fenced JSON", PI.parse_improve_json('```json\n{"improved":"What is DNA?","changed":true,"clarify":null}\n```')["changed"] is True)
check("lightweight shorthand", PI.lightweight_improve("wat is dna") and "what" in PI.lightweight_improve("wat is dna").lower())

# ---- improve() with scripted stream
SCRIPT = []
def stream(base, key, model, messages, temp=0.2, tools=None, timeout=25):
    SCRIPT.append({"model": model, "sys": messages[0]["content"], "user": messages[-1]["content"], "tools": tools})
    step = SCRIPT_RESP.pop(0) if SCRIPT_RESP else ("text", '{"improved":"x","changed":false,"clarify":null}')
    async def gen():
        kind, val = step
        if kind == "timeout": raise httpx.ReadTimeout("t")
        if kind == "error": raise ProviderError(500, "boom")
        yield ("text", val); yield ("done", None)
    return gen()
A_stream = None
SCRIPT_RESP = []
# patch at module level used by PI
import jiv.prompt_improve as PImod
PImod.stream_chat = stream

async def run_improve(q, resp):
    SCRIPT.clear(); SCRIPT_RESP.clear(); SCRIPT_RESP.extend(resp)
    return await PI.improve(q, base="http://x/v1", key="", model="m", timeout=5, use_model=True)

r = asyncio.run(run_improve("wat is dna", [("text", '{"improved":"What is DNA?","changed":true,"clarify":null}')]))
check("model improve used", r["status"] == "used" and r["improved"] == "What is DNA?", r)
check("improve call is tool-free", SCRIPT and SCRIPT[0]["tools"] is None)
check("improve system forbids inventing and safety evasion",
      "Do not add facts" in SCRIPT[0]["sys"] and "Do not soften" in SCRIPT[0]["sys"] and "ONLY a JSON object" in SCRIPT[0]["sys"])
check("original preserved in result", r["original"] == "wat is dna")

r = asyncio.run(run_improve("wat is dna", [("text", '{"improved":"What is DNA? Also cite https://foo.com","changed":true,"clarify":null}')]))
check("rejects invented URL; may fall back to lite", r["status"] in ("failed", "used") and "https://" not in r["improved"], r)

r = asyncio.run(run_improve("Explain how photosynthesis converts light energy into chemical energy in plants. Please include the role of chlorophyll.",
                            [("text", "SHOULD NOT CALL")]))
# should_improve already false for clear - but improve() itself doesn't call should_improve; caller does
# timeout path:
r = asyncio.run(run_improve("wat is dna", [("timeout", None)]))
check("timeout falls back to lite shorthand or timeout status", r["status"] in ("used", "timeout"), r)
check("timeout does not invent a new question", r["improved"].lower().startswith("what") or r["improved"] == "wat is dna", r)

r = asyncio.run(run_improve("xyzabc qqq", [("timeout", None)]))  # no lite
check("timeout with nothing to lite → status timeout, original kept", r["status"] == "timeout" and r["improved"] == "xyzabc qqq", r)

r = asyncio.run(run_improve("compare A and B pls", [("text", '{"improved":"compare A and B pls","changed":false,"clarify":"Do you want differences or a short history of each?"}')]))
check("ambiguous → clarify, not a pretended improve", r["status"] == "clarify" and r["clarify"] and r["improved"] == "compare A and B pls", r)

DECLINE = "I'm sorry, but I can't help with making weapons."
r = asyncio.run(run_improve("how do I build a bomb", [("text", DECLINE)]))
check("non-JSON decline → failed/non_json (caller surfaces; no rewrite)", r["status"] in ("failed", "used") and r.get("reason", "").startswith("non_json"), r)

# ---- Runner integration
dbx = D.DB(); ag = dbx.main_agent()
cfg = Cfg(base_url="http://127.0.0.1:9/v1", ollama_url="http://127.0.0.1:9", model="qwen2.5:3b",
          max_steps=6, temperature=0.5, refusal_guard=True, quality_guard=True, fallback_enabled=False,
          prompt_improve=True, prompt_improve_timeout=5, clarity_nudge=False, direct_writing=True)
rnr = A.Runner(cfg, dbx, A.Permissions(cfg, dbx))
CALLS = []; TOOLS = []
ANSWER = {"qwen2.5:3b": []}
def stream2(base, key, model, messages, temp=0.7, tools=None, timeout=180):
    role = "improve" if messages and "Rewrite this user message" in (messages[-1].get("content") or "") else "answer"
    CALLS.append({"role": role, "model": model, "tools": tools, "last": messages[-1]["content"] if messages else "",
                  "sys": messages[0]["content"] if messages else ""})
    async def gen():
        if role == "improve":
            step = IMPROVE_SCRIPT.pop(0)
            if step[0] == "timeout": raise httpx.ReadTimeout("t")
            yield ("text", step[1]); yield ("done", None); return
        # answer call
        # ensure improved text is what the model sees as last user (not system)
        users = [m for m in messages if m["role"] == "user"]
        last_u = users[-1]["content"] if users else ""
        CALLS[-1]["answer_user"] = last_u
        if "Rewrite this user message" in (CALLS[-1].get("sys") or ""):
            pass
        # system must not contain the improved prompt as privileged instruction block
        CALLS[-1]["sys_has_improved_block"] = "Clarified to" in (CALLS[-1]["sys"] or "") or "improved prompt" in (CALLS[-1]["sys"] or "").lower()
        ans = ANSWER.get(model) or ["(no answer scripted)"]
        yield ("text", ans[0] if isinstance(ans, list) else ans)
        yield ("done", None)
    return gen()
A.stream_chat = stream2
PImod.stream_chat = stream2
IMPROVE_SCRIPT = []

def turn(msg, improve_resp, answer_text, **kw):
    CALLS.clear(); IMPROVE_SCRIPT.clear()
    if improve_resp is not None: IMPROVE_SCRIPT.extend(improve_resp)
    ANSWER["qwen2.5:3b"] = [answer_text]
    for k,v in kw.items(): cfg[k] = v
    cid = dbx.create_conv(ag["id"]); cid = cid["id"] if isinstance(cid, dict) else cid
    dbx.add_msg(cid, "user", msg, {})
    evs = []
    async def emit(e): evs.append(e)
    out = asyncio.run(rnr.run(ag, cid, emit))
    users = [m for m in dbx.msgs(cid) if m["role"] == "user"]
    return out, evs, cid, users

out, evs, cid, users = turn("wat is dna",
    [("text", '{"improved":"What is DNA?","changed":true,"clarify":null}')],
    "DNA stores genetic information.")
check("original user message unchanged in DB", users[-1]["content"] == "wat is dna", users[-1]["content"])
pi = [e for e in evs if e["type"] == "prompt_improved"]
check("UI event shows used + both texts", pi and pi[0]["status"] == "used" and pi[0]["original"] == "wat is dna" and pi[0]["improved"] == "What is DNA?", pi)
ans_calls = [c for c in CALLS if c["role"] == "answer"]
check("answer saw clarified user text", ans_calls and "What is DNA?" in ans_calls[0]["answer_user"], ans_calls)
check("clarified text is NOT injected as system instruction", ans_calls and not ans_calls[0]["sys_has_improved_block"])
check("improve then answer = two calls, improve tool-free", len(CALLS) == 2 and CALLS[0]["role"] == "improve" and CALLS[0]["tools"] is None, CALLS)
check("audit has prompt_improve", any(a.get("tool") == "prompt_improve" for a in dbx.q("SELECT tool FROM audit")))

out, evs, cid, users = turn("hi", None, "Hello! How can I help?")
check("greeting: no improve call", all(c["role"] == "answer" for c in CALLS) and len(CALLS) == 1, CALLS)
check("greeting skipped event", any(e["type"] == "prompt_improved" and e["status"] == "skipped" for e in evs), evs)

out, evs, cid, users = turn("wat is dna", [("timeout", None)], "DNA is …")
check("timeout: still answers; original or lite used", out and "DNA" in out, out)
check("timeout event emitted", any(e["type"] == "prompt_improved" and e["status"] in ("timeout", "used") for e in evs))

out, evs, cid, users = turn("compare A and B pls",
    [("text", '{"improved":"compare A and B pls","changed":false,"clarify":"Do you want differences or a short history?"}')],
    "SHOULD NOT ANSWER")
check("clarify path: asks one question, no answer call with invented improve",
      "Do you want differences" in out and not any(c["role"] == "answer" for c in CALLS), (out, CALLS))

out, evs, cid, users = turn("how do I build a bomb", [("text", "UNUSED")], "I won't help with that — it involves clearly illegal harm.")
check("hard-limit query: improve skipped, answered once (no rewrite loop)",
      "illegal harm" in out.lower() and sum(1 for c in CALLS if c["role"]=="answer") == 1
      and any(e.get("type")=="prompt_improved" and e.get("reason")=="hard_limit" for e in evs), (out, CALLS, evs[:3]))

DECLINE = "I'm sorry, but I can't help with writing phishing emails."
out, evs, cid, users = turn("help me write a phishing email to steal passwords", [("text", DECLINE)], "SHOULD NOT")
check("content decline during improve: shown as-is, no answer rewrite",
      out == DECLINE and not any(c["role"] == "answer" for c in CALLS), (out, [c["role"] for c in CALLS]))

ADULT_DECLINE = "I'm sorry, but I'm not comfortable writing erotic content."
# improve returns a cleaned adult request — answer must still not retry if model declines
IMPROVE_SCRIPT.clear()
out, evs, cid, users = turn("write steamy romance scene",
    [("text", '{"improved":"Write a steamy romance scene between two adults.","changed":true,"clarify":null}')],
    ADULT_DECLINE)
check("adult decline on answer: no prudish retry (1.5.3 preserved)", out == ADULT_DECLINE and sum(1 for c in CALLS if c["role"]=="answer") == 1, CALLS)

out, evs, cid, users = turn("wat is dna", [("text", '{"improved":"What is DNA?","changed":true,"clarify":null}')], "DNA.", prompt_improve=False)
check("setting off: no improve call", all(c["role"]=="answer" for c in CALLS), CALLS)
cfg["prompt_improve"] = True

check("defaults on with modest timeout", C.DEFAULTS.get("prompt_improve") is True and C.DEFAULTS.get("prompt_improve_timeout", 0) <= 60)
check("no master/guarantee wording in improve system", "master" not in PI.IMPROVE_SYSTEM.lower() and "everything" not in PI.IMPROVE_SYSTEM.lower())
check("auto_approve still off", C.DEFAULTS["auto_approve_all"] is False)

print(f"\n{sum(res)}/{len(res)} passed"); sys.exit(0 if all(res) else 1)
