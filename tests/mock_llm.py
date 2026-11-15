"""Mock OpenAI-compatible server for testing Jiv. Model 'mock-tools' = native tools, 'mock-json' = no tool support (JSON fallback)."""
import json, time, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn
app = FastAPI()

@app.get("/v1/models")
def models(): return {"object": "list", "data": [{"id": "mock-tools"}, {"id": "mock-json"}] + [{"id": m} for m in os.environ.get("MOCK_MODELS", "").split(",") if m]}

def plan(msgs):
    sysmsg = msgs[0]["content"]; last = msgs[-1]
    me = sysmsg.split("Your name is ")[1].split(".")[0] if "Your name is " in sysmsg else "?"
    is_result = last["role"] == "tool" or (last["role"] == "user" and last["content"].startswith("[Tool result"))
    if is_result:
        c = last["content"]
        if "Attempt 1 of 3 failed" in c:
            return ("call", "run_code", {"language": "python", "code": "nums = [1, 2, 3]\nprint('total', sum(nums))"})
        if "✅ Success" in c and "--- stdout ---" in c:
            out = c.split("--- stdout ---", 1)[1].strip().splitlines()
            return ("text", "It ran successfully. Output:\n```\n" + "\n".join(out[:12]) + "\n```")
        if "update" in c.lower() and "staged" in c.lower(): return ("text", "I've drafted the update and it passed the checks. Review the card above and click **Apply update…** if you're happy.")
        if "DENIED" in c: return ("text", "Okay — you denied that, so I didn't do it. Want me to try something else?")
        return ("text", f"Done. Here's what I got:\n\n```\n{c[:300]}\n```\n**All good.**")
    content = last["content"]
    # 1.5.4 prompt-improve step (tool-free JSON rewrite)
    if isinstance(content, str) and content.startswith("Rewrite this user message:"):
        raw = content.split("Rewrite this user message:", 1)[1].strip()
        import json as _json
        # simple tidy: expand a couple of shorthands for tests
        imp = raw.replace("wat ", "what ").replace(" b/w ", " between ").replace(" kya hai", " — what is it")
        if imp == raw:
            return ("text", _json.dumps({"improved": raw, "changed": False, "clarify": None}))
        return ("text", _json.dumps({"improved": imp[0].upper() + imp[1:] if imp else raw, "changed": True, "clarify": None}))
    if isinstance(content, list):  # vision-style message
        imgs = sum(1 for p in content if p.get("type") == "image_url")
        txt = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
        return ("text", f"I can see {imgs} image(s) you attached. " + ("(text: " + txt[:60] + ")" if txt else ""))
    if isinstance(content, str) and "rewrite all" in content.lower() and "[Attached file:" in content:
        import re as _re
        fid = _re.search(r"\(id ([a-f0-9]+)", content).group(1)
        return ("call", "start_batch", {"source": fid, "template": "mcq", "verify": True, "limit": 40})
    if "[Attached file:" in content or "[Image attached:" in content:
        import re as _re
        out = []
        for m in _re.finditer(r"\[Attached file: ([^\]]+?) \(id[^\]]*\]\n(.*?)(?=\n\[(?:End of|NOTE:)|\Z)", content, _re.S):
            body = " ".join(m.group(2).split())
            out.append(f"**{m.group(1)}** — it says: “{body[:160]}”")
        if "[NOTE: this file is" in content: out.append("_(The file was long, so I only saw the first part.)_")
        if "[Image attached:" in content: out.append("I can't see images with this model — install a vision model such as qwen2.5vl:3b.")
        return ("text", "Here's what I found in your attachment(s):\n\n" + "\n".join("- " + o for o in out))
    u = content.lower()
    REFUSE = "No, I'm sorry but I'm unable to assist with that. My capabilities are currently limited to the functions provided to me. I don't have the ability to create multiple-choice questions (MCQ) or practice NCLEX exams. I suggest you use a study app or website."
    if content.startswith("(Note from Jiv"):   # the refusal/prudish guard's nudge
        prev = str(msgs[-2]["content"]).lower()
        if "stubborn" in prev: return ("text", REFUSE)
        if "consenting adult" in content.lower() or "adult/sexual" in content.lower() or "the user is an adult" in content.lower():
            if "nsfwkling" in prev or ("what is nsfw" in prev) or ("what does nsfw" in prev):
                return ("text", "NSFW means Not Safe For Work — adult or explicit content you wouldn't open at work.")
            return ("text", "Sure — here is an explicit adult scene between two consenting adults: they kiss hard, undress each other, and have enthusiastic sex. The End.")
        return ("text", "**1.** A client with heart failure gains 2 kg in 2 days. What should the nurse do first?\nA) Recheck tomorrow  B) Report the gain to the provider  C) Encourage fluids  D) Give a laxative\n**Answer: B.** Rationale: rapid weight gain means fluid retention and must be reported.")
    if "nclex" in u and "mcq" in u: return ("text", REFUSE)
    # 1.5.1 adult / hard-limit scenarios
    PRUDE = "I'm sorry, but I can't engage with explicit content. It's important to remember I must keep things family-friendly and appropriate."
    HARD = "I must decline. I won't create sexual content involving minors or anyone under 18."
    HARD_REAL = "I won't write sexual content about a real, identifiable person without consent."
    HARD_ILLEGAL = "I won't help with that — it involves clearly illegal harm."
    if "nsfwkling" in u:
        return ("text", 'I\'m sorry, but I\'m unable to understand your request. The word "nsfw" is often associated with adult content. I must adhere to the guidelines provided for adult users in a respectful and appropriate manner.')
    if "adult-ok:" in u or (("nude" in u or "erotic" in u or "nsfw" in u) and "teen" not in u and "child" not in u and "coworker" not in u and "bomb" not in u):
        return ("text", PRUDE)   # content refusal; 1.5.3 must NOT retry or rewrite
    if "hard-minor:" in u or ("nude" in u and ("teen" in u or "child" in u or "underage" in u)):
        return ("text", HARD)    # hard limit: no retry
    if "hard-real:" in u or ("erotic" in u and ("celebrity" in u or "coworker" in u or "my ex" in u)):
        return ("text", HARD_REAL)
    if "hard-illegal:" in u or ("how to" in u and "bomb" in u):
        return ("text", HARD_ILLEGAL)
    # 1.5.0 quality-update scenarios
    if "slow story" in u: return ("text", "Once upon a time a nurse studied every night. " * 70)
    if u.startswith("calculate"): return ("text", "<think>15% of 240 = 0.15 x 240 = 36</think>\n15% of 240 is **36**.")
    if u.startswith("regen"):
        REGEN[0] += 1; return ("text", f"Answer version {REGEN[0]} to: {content[:60]}")
    if "dark mode" in u and "jiv" in u: return ("text", "I can't safely write this update to Jiv myself with the current small model. Open ⬆ Updates to install an update file.")
    if me == "Scout": return ("text", f"Scout here. Research summary for: *{last['content'][:80]}* — Ollama is a local LLM runner.")
    if "greeting feature" in u:
        return ("call", "propose_update", {"title": "Time-of-day greeting", "changelog": "- New `jiv/greetings.py` with a time-of-day greeting helper\n- Greeting shown in the about text",
            "files": [{"path": "jiv/greetings.py", "content": '"""Friendly time-of-day greetings."""\nimport datetime\n\n\ndef greeting(name="there", now=None):\n    h = (now or datetime.datetime.now()).hour\n    part = "morning" if h < 12 else "afternoon" if h < 18 else "evening"\n    return f"Good {part}, {name}!"\n'}]})
    if "first 10 squares" in u:
        return ("call", "run_code", {"language": "python", "code": "for i in range(1, 11):\n    print(i, i * i)"})
    if "buggy" in u:
        return ("call", "run_code", {"language": "python", "code": "nums = [1, 2, 3]\nprint('total', summ(nums))"})
    if "in node" in u:
        return ("call", "run_code", {"language": "javascript", "code": "console.log([3,1,2].sort().join(' < '))"})
    if "squares skill" in u:
        return ("call", "run_skill", {"name": "first 10 squares"})
    if "what's my name" in u or "what is my name" in u:
        m = __import__("re").search(r"The user's name is ([^.\n]+)", sysmsg)
        return ("text", f"Your name is {m.group(1)}." if m else "I don't know your name yet.")
    if u.startswith("my name is"): return ("text", "Nice to meet you!")
    if "capital of australia" in u: return ("text", "The capital of Australia is Sydney.")
    if "sysinfo" in u: return ("call", "system_info", {})
    if "make file" in u: return ("call", "write_file", {"path": "~/jiv_test/hello.txt", "content": "Hello from Jiv!"})
    if "run" in u: return ("call", "run_shell", {"command": "echo jiv-shell-ok"})
    if "create agent" in u: return ("call", "create_agent", {"name": "Scout", "persona": "You are {name}, a research agent who searches the web and summarises findings.", "tools": ["web_search", "web_fetch"]})
    if "delegate" in u: return ("call", "delegate_to_agent", {"agent": "Scout", "task": "Research what Ollama is"})
    return ("text", "Hi! I'm a **mock** model. Here is code:\n```python\nprint('hi')\n```\n- item one\n- item two")

REGEN = [0]
def sse(o): return "data: " + json.dumps(o) + "\n\n"

@app.post("/v1/chat/completions")
async def chat(req: Request):
    b = await req.json(); model = b["model"]
    if not b.get("stream"):   # Batch jobs use non-streaming calls
        import mock_batch, asyncio as _a
        txt = mock_batch.reply(b["messages"], model)
        await _a.sleep(float(os.environ.get("MOCK_DELAY", "0.01")) * (1 + len(txt) / 200))
        if os.environ.get("MOCK_FAIL") and os.path.exists(os.environ["MOCK_FAIL"]): return JSONResponse({"error": {"message": "model crashed"}}, 500)
        return JSONResponse({"id": "b", "object": "chat.completion", "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": txt}, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": sum(len(str(m["content"])) for m in b["messages"]) // 4, "completion_tokens": max(1, len(txt) // 4)}})
    if model == "mock-json" and b.get("tools"):
        return JSONResponse({"error": {"message": "this model does not support tools"}}, 400)
    p = plan(b["messages"])
    def gen():
        base = {"id": "x", "object": "chat.completion.chunk", "model": model}
        if p[0] == "text":
            for i in range(0, len(p[1]), 6):
                yield sse({**base, "choices": [{"index": 0, "delta": {"content": p[1][i:i+6]}}]}); time.sleep(0.01)
        elif b.get("tools"):
            yield sse({**base, "choices": [{"index": 0, "delta": {"content": "On it. "}}]})
            args = json.dumps(p[2])
            yield sse({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": p[1], "arguments": ""}}]}}]})
            for i in range(0, len(args), 10):
                yield sse({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": args[i:i+10]}}]}}]})
        else:
            txt = "Sure, let me do that.\n```json\n" + json.dumps({"name": p[1], "arguments": p[2]}) + "\n```"
            for i in range(0, len(txt), 8): yield sse({**base, "choices": [{"index": 0, "delta": {"content": txt[i:i+8]}}]})
        yield sse({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if (b.get("stream_options") or {}).get("include_usage") and p[0] == "text":
            yield sse({**base, "choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": max(1, len(p[1]) // 4)}})
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")

# ---- Ollama-native endpoints for wizard tests ----
import asyncio, os
PULLS, INSTALLED = {}, {"mock-tools": 1_200_000_000}
LAYER = 60_000_000
@app.get("/api/version")
def ver(): return {"version": "0.0-mock"}
@app.get("/api/tags")
def tags(): return {"models": [{"name": n, "size": sz, "details": {"parameter_size": "1B", "quantization_level": "Q4_K_M", "family": "mock"}} for n, sz in INSTALLED.items()]}
@app.delete("/api/delete")
async def delete(req: Request):
    n = (await req.json())["model"]
    if n not in INSTALLED: return JSONResponse({"error": f"model '{n}' not found"}, 404)
    INSTALLED.pop(n); return {}
@app.post("/api/pull")
async def pull(req: Request):
    n = (await req.json())["model"]
    if n.startswith("nope"): return JSONResponse({"error": "pull model manifest: file does not exist"}, 500)
    speed = float(os.environ.get("MOCK_PULL_SECS", "4"))
    async def gen():
        yield json.dumps({"status": "pulling manifest"}) + "\n"
        for d in ("sha256:aaa", "sha256:bbb"):
            done = PULLS.get((n, d), 0)
            while done < LAYER:
                done = min(LAYER, done + LAYER // 20); PULLS[(n, d)] = done
                yield json.dumps({"status": "pulling " + d[7:], "digest": d, "total": LAYER, "completed": done}) + "\n"
                await asyncio.sleep(speed / 40)
        for st in ("verifying sha256 digest", "writing manifest", "success"):
            yield json.dumps({"status": st}) + "\n"
        INSTALLED[n] = 2 * LAYER
    return StreamingResponse(gen(), media_type="application/x-ndjson")
@app.get("/OllamaSetup.exe")
def installer():
    from fastapi.responses import Response
    # fake "installer": a script that installs a fake ollama binary which serves this mock on 'serve'
    target = os.environ.get("FAKE_OLLAMA_BIN", "/tmp/fakeollama/ollama")
    script = f"""#!/bin/sh
echo "fake installer args: $@"
mkdir -p "$(dirname {target})"
cat > {target} <<'EOS'
#!/bin/sh
PORT=$(echo "$OLLAMA_HOST" | sed 's/.*://')
exec {sys.executable} {os.path.abspath(__file__)} "$PORT"
EOS
chmod +x {target}
echo installed
""" + "#" * 3_000_000
    return Response(script.encode(), media_type="application/octet-stream")

@app.get("/Ollama-darwin.zip")
def mac_zip():
    import io, zipfile
    from fastapi.responses import Response
    serve = f"#!/bin/sh\nPORT=$(echo \"$OLLAMA_HOST\" | sed 's/.*://')\nexec {sys.executable} {os.path.abspath(__file__)} \"$PORT\"\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        zi = zipfile.ZipInfo("Ollama.app/Contents/Resources/ollama"); zi.external_attr = 0o100755 << 16; z.writestr(zi, serve)
        z.writestr("Ollama.app/Contents/Info.plist", "<plist><dict><key>CFBundleName</key><string>Ollama</string></dict></plist>")
        z.writestr("Ollama.app/Contents/Resources/padding.bin", os.urandom(2_000_000))
    return Response(buf.getvalue(), media_type="application/zip")

if __name__ == "__main__": uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 11434, log_level="warning")
