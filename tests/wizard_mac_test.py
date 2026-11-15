"""macOS install flows (simulated on any OS): Homebrew path and Ollama-darwin.zip path.
Usage: python tests/wizard_mac_test.py <brew|zip> <jiv_url> <ollama_url>"""
import asyncio, os, re, sys
import httpx
MODE, BASE, OURL = sys.argv[1], sys.argv[2], sys.argv[3]; results = []
def check(n, c, info=""): results.append(bool(c)); print(("PASS " if c else "FAIL ") + f"[mac-{MODE}] " + n + ("" if c else f"  [{info}]"))
async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        tok = re.search(r'jiv-token" content="([^"]+)"', (await c.get(BASE + "/")).text).group(1); H = {"X-Jiv-Token": tok}
        await c.post(BASE + "/api/settings", headers=H, json={"ollama_url": OURL})
        st = (await c.get(BASE + "/api/ollama/status", headers=H)).json()
        check("status reports mac mode, not installed", st["install_mode"] == "mac" and not st["installed"], st)
        check("brew detection", st["brew_available"] == (MODE == "brew"), st)
        j = (await c.post(BASE + "/api/ollama/install", headers=H, json={"approved": True, "method": MODE})).json()
        pcts = []
        while (jj := (await c.get(BASE + f"/api/jobs/{j['id']}", headers=H)).json())["status"] == "running":
            pcts.append(jj["percent"]); await asyncio.sleep(0.05)
        check("install job done", jj["status"] == "done", jj)
        if MODE == "brew":
            check("ran brew install + brew services start", any("brew install ollama" in l for l in jj["log"]) and any("services start ollama" in l for l in jj["log"]), jj["log"])
        else:
            apps = os.environ.get("JIV_MAC_APPS_DIR", "/tmp/fakeapps")
            exe = os.path.join(apps, "Ollama.app/Contents/Resources/ollama")
            check("progress shown during zip download", any(0 < p < 100 for p in pcts) or jj["total"] > 0, pcts[-5:])
            check("Ollama.app unzipped into Applications dir with exec bit", os.access(exe, os.X_OK), exe)
            check("quarantine step handled", any("quarantine" in l for l in jj["log"]), jj["log"])
        st = (await c.get(BASE + "/api/ollama/status", headers=H)).json()
        check("Ollama API up after install", st["running"], st)
    print(f"{sum(results)}/{len(results)} passed"); return all(results)
sys.exit(0 if asyncio.run(main()) else 1)
