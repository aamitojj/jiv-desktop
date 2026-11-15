"""Real Ollama E2E: pull via Jiv (with progress), configure, chat, tool call. Usage: python tests/real_ollama_test.py [model]"""
import asyncio, json, re, sys, time
import httpx
import os; BASE = os.environ.get("JIV_URL", "http://127.0.0.1:8765"); MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:0.5b"
async def main():
    async with httpx.AsyncClient(timeout=600) as c:
        tok = re.search(r'jiv-token" content="([^"]+)"', (await c.get(BASE + "/")).text).group(1); H = {"X-Jiv-Token": tok}
        print("status:", (await c.get(BASE + "/api/ollama/status", headers=H)).json())
        have = [m["name"] for m in (await c.get(BASE + "/api/ollama/models", headers=H)).json()["models"]]
        if MODEL in have: print("model already installed, skipping pull (as the wizard does)")
        else: await pull(c, H)
        print("models:", [(m["name"], m["size_gb"]) for m in (await c.get(BASE + "/api/ollama/models", headers=H)).json()["models"]])
        await rest(c, H)

async def pull(c, H):
        j = (await c.post(BASE + "/api/ollama/pull", headers=H, json={"model": MODEL, "approved": True})).json()
        last = -10
        while True:
            jj = (await c.get(BASE + f"/api/jobs/{j['id']}", headers=H)).json()
            if jj["percent"] - last >= 20 or jj["status"] != "running":
                print(f"  pull {jj['percent']:5.1f}%  {jj['speed_mbps']} MB/s  ETA {jj['eta_s']}s  {jj['phase']}"); last = jj["percent"]
            if jj["status"] != "running": break
            await asyncio.sleep(0.5)
        assert jj["status"] == "done", jj

async def rest(c, H):
        print("finish:", {k: v for k, v in (await c.post(BASE + "/api/setup/finish", headers=H, json={"model": MODEL})).json().items() if k in ("provider", "base_url", "model")})
        t0 = time.time(); print("test chat:", (await c.post(BASE + "/api/test", headers=H, json={})).json(), f"{time.time()-t0:.1f}s")
        for q in ["In one sentence, what are you?", "Use the system_info tool to tell me how many CPUs this computer has."]:
            evs = []
            async with c.stream("POST", BASE + "/api/chat", headers=H, json={"message": q}) as r:
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        ev = json.loads(line[5:]); evs.append(ev)
                        if ev["type"] == "permission":
                            await c.post(BASE + f"/api/permission/{ev['id']}", headers=H, json={"decision": "once"})
            txt = "".join(e.get("delta", "") for e in evs if e["type"] == "text")
            tools = [(e["tool"], e["args"]) for e in evs if e["type"] == "tool_start"]
            res = [e["status"] for e in evs if e["type"] == "tool_result"]
            print(f"\nQ: {q}\n  text chunks: {sum(e['type']=='text' for e in evs)}  tools: {tools}  results: {res}\n  A: {txt[-400:]!r}")
            print("  errors:", [e for e in evs if e["type"] == "error"])
asyncio.run(main())
