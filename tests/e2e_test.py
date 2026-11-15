"""End-to-end HTTP test against a running Jiv server + mock LLM."""
import asyncio, json, os, re, sys
from pathlib import Path
import httpx
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
results = []
def check(name, cond, info=""):
    results.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))

async def chat(c, H, msg, decide=None, agent_id=None, conv_id=None):
    evs = []
    async with c.stream("POST", BASE + "/api/chat", headers=H, json={"message": msg, "agent_id": agent_id, "conv_id": conv_id}) as r:
        async for line in r.aiter_lines():
            if not line.startswith("data:"): continue
            ev = json.loads(line[5:]); evs.append(ev)
            if ev["type"] == "permission":
                await c.post(BASE + f"/api/permission/{ev['id']}", headers=H, json={"decision": decide(ev) if decide else "once"})
    return evs
text = lambda evs: "".join(e.get("delta", "") for e in evs if e["type"] == "text")
types = lambda evs: [e["type"] for e in evs]

async def main():
    async with httpx.AsyncClient(timeout=60) as c:
        html = (await c.get(BASE + "/")).text
        tok = re.search(r'name="jiv-token" content="([^"]+)"', html).group(1)
        H = {"X-Jiv-Token": tok}
        check("security: API rejects missing token", (await c.get(BASE + "/api/state")).status_code == 403)
        check("security: API rejects foreign Origin", (await c.post(BASE + "/api/settings", headers={**H, "Origin": "http://evil.com"}, json={})).status_code == 403)
        check("security: rejects foreign Host (DNS rebinding)", (await c.get(BASE + "/", headers={"Host": "evil.com"})).status_code == 403)
        det = (await c.get(BASE + "/api/autodetect", headers=H)).json()["found"]
        check("auto-detect finds mock on Ollama port", any("11434" in f["base_url"] and "mock-tools" in f["models"] for f in det), det)
        m = (await c.post(BASE + "/api/models", headers=H, json={"base_url": "http://localhost:11434/v1"})).json()
        check("model list", m["models"] == ["mock-json", "mock-tools"], m)
        await c.post(BASE + "/api/settings", headers=H, json={"base_url": "http://localhost:11434/v1", "model": "mock-tools", "api_key": "sk-secret-123456"})
        st = (await c.get(BASE + "/api/state", headers=H)).json()
        check("API key masked in API responses", "secret" not in json.dumps(st) and st["config"]["api_key_set"])
        t = (await c.post(BASE + "/api/test", headers=H, json={})).json(); check("test connection", t["ok"], t)

        evs = await chat(c, H, "hello")
        check("streaming: multiple text chunks", sum(1 for e in evs if e["type"] == "text") > 5 and "mock" in text(evs))
        conv1 = evs[0]["conv_id"]

        evs = await chat(c, H, "sysinfo please", conv_id=conv1)
        check("native tool call: read-only tool auto-runs (no permission)", "permission" not in types(evs) and any(e["type"] == "tool_result" and e["status"] == "ok" and '"os"' in e["result"] for e in evs))

        target = Path.home() / "jiv_test" / "hello.txt"
        if target.exists(): target.unlink()
        evs = await chat(c, H, "make file", decide=lambda e: "deny")
        p = [e for e in evs if e["type"] == "permission"]
        check("permission card shown for write_file with exact args", p and p[0]["tool"] == "write_file" and "hello.txt" in p[0]["summary"])
        check("deny: file NOT written, agent told", not target.exists() and "denied" in text(evs).lower())
        evs = await chat(c, H, "make file", decide=lambda e: "once")
        check("allow once: file written", target.exists() and target.read_text() == "Hello from Jiv!")
        evs = await chat(c, H, "run a command", decide=lambda e: "always")
        check("shell allowed via 'always'", any(e["type"] == "tool_result" and "jiv-shell-ok" in e["result"] for e in evs))
        evs = await chat(c, H, "run a command again")
        check("always-allow list skips prompt next time", "permission" not in types(evs) and any("jiv-shell-ok" in e.get("result", "") for e in evs))
        st = (await c.get(BASE + "/api/state", headers=H)).json()
        check("always_allow saved in settings", st["config"]["always_allow"] == ["run_shell"])
        await c.post(BASE + "/api/settings", headers=H, json={"always_allow": []})

        evs = await chat(c, H, "create agent called Scout")
        check("create_agent requires permission", any(e["type"] == "permission" and e["tool"] == "create_agent" for e in evs))
        st = (await c.get(BASE + "/api/state", headers=H)).json()
        scout = next((a for a in st["agents"] if a["name"] == "Scout"), None)
        check("agent Scout created with tools", scout and scout["tools"] == ["web_search", "web_fetch"])
        evs = await chat(c, H, "delegate this research")
        dres = [e for e in evs if e["type"] == "tool_result" and "Scout replied" in e.get("result", "")]
        check("delegate_to_agent returns sub-agent result", dres and "local LLM runner" in dres[0]["result"])
        evs = await chat(c, H, "hi scout", agent_id=scout["id"])
        check("chat directly with created agent", "Scout here" in text(evs))
        r = await c.put(BASE + f"/api/agents/{scout['id']}", headers=H, json={"persona": "You are {name}. Edited."})
        check("edit agent", r.json()["persona"].endswith("Edited."))
        main = st["agents"][0]
        await c.put(BASE + f"/api/agents/{main['id']}", headers=H, json={"name": "Jiv"})

        # JSON fallback mode
        await c.post(BASE + "/api/settings", headers=H, json={"model": "mock-json"})
        evs = await chat(c, H, "sysinfo via json")
        check("JSON fallback: detects no tool support + parses JSON tool call", "notice" in types(evs) and any(e["type"] == "tool_result" and '"os"' in e["result"] for e in evs))
        await c.post(BASE + "/api/settings", headers=H, json={"model": "mock-tools"})

        a = (await c.get(BASE + "/api/audit", headers=H)).json()
        decs = {(x["tool"], x["decision"]) for x in a}
        check("audit log records denied/allowed/auto", ("write_file", "denied") in decs and ("write_file", "allowed") in decs and ("system_info", "auto") in decs)
        d = (await c.get(BASE + f"/api/conversations/{conv1}", headers=H)).json()
        check("conversation persisted in SQLite", len(d["messages"]) >= 5)
        dd = os.environ.get("JIV_DATA_DIR")
        if dd:
            logs = " ".join(p.read_text(errors="ignore") for p in Path(dd).glob("*.json"))
            check("api key stored in local config file", "sk-secret-123456" in logs)
        r = await c.delete(BASE + f"/api/agents/{main['id']}", headers=H)
        check("main agent cannot be deleted", r.status_code == 400)
    print(f"\n{sum(r for _, r in results)}/{len(results)} passed")
    return all(r for _, r in results)

if __name__ == "__main__": sys.exit(0 if asyncio.run(main()) else 1)
