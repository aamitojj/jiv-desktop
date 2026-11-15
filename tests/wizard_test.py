"""Wizard flow test: fake Ollama installer download+run, service start, /api/pull progress, cancel/resume, delete, finish.
Start Jiv with: JIV_INSTALL_MODE=windows JIV_OLLAMA_INSTALLER_URL=http://127.0.0.1:11434/OllamaSetup.exe JIV_OLLAMA_BIN=/tmp/fakeollama/ollama"""
import asyncio, re, sys, time
import httpx
BASE = "http://127.0.0.1:8765"; results = []
def check(n, c, info=""): results.append(bool(c)); print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{info}]"))

async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        tok = re.search(r'jiv-token" content="([^"]+)"', (await c.get(BASE + "/")).text).group(1); H = {"X-Jiv-Token": tok}
        post = lambda p, b: c.post(BASE + p, headers=H, json=b)
        get = lambda p: c.get(BASE + p, headers=H)
        await post("/api/settings", {"ollama_url": "http://localhost:11600"})
        hw = (await get("/api/setup/hardware")).json()
        check("hardware endpoint returns RAM/CPU/disk + recommendation", hw["hardware"]["ram_total_gb"] > 0 and hw["hardware"]["cpu"]["name"] and hw["recommendation"]["recommended"])
        st = (await get("/api/ollama/status")).json()
        check("status: not installed, not running", not st["installed"] and not st["running"], st)
        r = await post("/api/ollama/install", {"method": "installer"})
        check("install refused without permission", r.status_code == 400)
        j = (await post("/api/ollama/install", {"method": "installer", "approved": True})).json()
        seen = []
        while True:
            jj = (await get(f"/api/jobs/{j['id']}")).json(); seen.append(jj["percent"])
            if jj["status"] != "running": break
            await asyncio.sleep(0.05)
        check("installer job done (download → silent run → service start)", jj["status"] == "done", jj)
        check("installer ran with /VERYSILENT", any("/VERYSILENT" in l for l in jj["log"]) and any("fake installer args: /VERYSILENT" in l for l in jj["log"]), jj["log"])
        st = (await get("/api/ollama/status")).json()
        check("status: installed + running after install", st["installed"] and st["running"], st)

        r = await post("/api/ollama/pull", {"model": "qwen2.5:0.5b"}); check("pull refused without permission", r.status_code == 400)
        j = (await post("/api/ollama/pull", {"model": "qwen2.5:0.5b", "approved": True})).json()
        mid = None
        for _ in range(200):
            jj = (await get(f"/api/jobs/{j['id']}")).json()
            if jj["percent"] >= 30: mid = jj; break
            await asyncio.sleep(0.05)
        check("pull progress: percent, MB/s and ETA reported", mid and 0 < mid["percent"] < 100 and mid["speed_mbps"] > 0 and mid["eta_s"] is not None and mid["total"] > 0, mid)
        await post(f"/api/jobs/{j['id']}/cancel", {}); await asyncio.sleep(0.3)
        jj = (await get(f"/api/jobs/{j['id']}")).json(); check("cancel → paused", jj["status"] == "cancelled", jj["status"])
        paused_at = jj["percent"]
        j2 = (await post("/api/ollama/pull", {"model": "qwen2.5:0.5b", "approved": True})).json()
        await asyncio.sleep(0.3); first = (await get(f"/api/jobs/{j2['id']}")).json()["percent"]
        check("resume continues from where it stopped", first >= paused_at - 1, (paused_at, first))
        while (jj := (await get(f"/api/jobs/{j2['id']}")).json())["status"] == "running": await asyncio.sleep(0.1)
        check("pull completes (success)", jj["status"] == "done" and jj["percent"] == 100, jj)
        ms = (await get("/api/ollama/models")).json()["models"]
        check("installed models list includes pulled model with size", any(m["name"] == "qwen2.5:0.5b" and m["size_gb"] > 0 for m in ms), ms)
        j3 = (await post("/api/ollama/pull", {"model": "nope-model", "approved": True})).json()
        while (jj := (await get(f"/api/jobs/{j3['id']}")).json())["status"] == "running": await asyncio.sleep(0.05)
        check("pull error surfaced", jj["status"] == "error" and "does not exist" in jj["error"])
        check("bad model name rejected", (await post("/api/ollama/pull", {"model": "x; rm -rf /", "approved": True})).status_code == 400)
        check("delete refused without permission", (await post("/api/ollama/delete", {"model": "qwen2.5:0.5b"})).status_code == 400)
        r = await post("/api/ollama/delete", {"model": "qwen2.5:0.5b", "approved": True})
        ms = (await get("/api/ollama/models")).json()["models"]
        check("delete with permission", r.status_code == 200 and not any(m["name"] == "qwen2.5:0.5b" for m in ms))
        await post("/api/setup/deny", {"action": "pull_model", "detail": {"model": "llama3.1:8b"}})
        cfg = (await post("/api/setup/finish", {"model": "mock-tools"})).json()
        check("finish auto-configures Ollama provider + model", cfg["provider"] == "ollama" and cfg["model"] == "mock-tools" and cfg["base_url"] == "http://localhost:11600/v1" and cfg["setup_done"])
        t = (await post("/api/test", {})).json(); check("test chat after setup", t["ok"], t)
        a = (await get("/api/audit")).json(); acts = {(x["tool"], x["decision"]) for x in a}
        check("audit: install/pull/delete allowed + deny logged", {("install_ollama", "allowed"), ("pull_model", "allowed"), ("delete_model", "allowed"), ("pull_model", "denied")} <= acts, acts)
    print(f"\n{sum(results)}/{len(results)} passed"); return all(results)
sys.exit(0 if asyncio.run(main()) else 1)
