"""Screenshots: NVIDIA badges in the wizard (GTX 1650 / 8 GB profile) and Manage models."""
import os, re, sys, httpx
from playwright.sync_api import sync_playwright
BASE = os.environ.get("JIV_URL", "http://127.0.0.1:8774/"); OUT = "screenshots/"; errs = []
tok = re.search(r'jiv-token" content="([^"]+)"', httpx.get(BASE).text).group(1); H = {"X-Jiv-Token": tok}
j = httpx.post(BASE + "api/ollama/pull", headers=H, json={"model": "nemotron-mini:4b", "approved": True}).json()
import time
while httpx.get(BASE + f"api/jobs/{j['id']}", headers=H).json()["status"] == "running": time.sleep(0.2)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"]); pg = b.new_page(viewport={"width": 1360, "height": 1000})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE); pg.wait_for_selector(".hwgrid"); pg.click("#wNext"); pg.wait_for_selector(".mrow.rec")
    pg.click("details summary >> nth=0"); pg.locator(".mrow:has-text('nemotron-mini:4b')").scroll_into_view_if_needed()
    assert pg.locator(".tag.nv").count() >= 2
    pg.screenshot(path=OUT + "20_nvidia_badge_wizard_gtx1650.png")
    pg.click("[data-close]"); pg.click("#openModels"); pg.wait_for_selector("td .tag.nv")
    pg.screenshot(path=OUT + "21_nvidia_badge_manage_models.png"); b.close()
print("errors:", errs or "none"); sys.exit(1 if errs else 0)
