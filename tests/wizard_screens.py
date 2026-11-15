"""Screenshots of the Setup Wizard (fresh data dir, fake Ollama installer)."""
import sys
from playwright.sync_api import sync_playwright
OUT = "screenshots/"; errors = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1360, "height": 900})
    pg.on("pageerror", lambda e: errors.append(str(e))); pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
    pg.goto("http://127.0.0.1:8765/"); pg.wait_for_selector(".hwgrid", timeout=30000)
    pg.screenshot(path=OUT + "09_wizard_hardware.png")
    pg.click("#wNext"); pg.wait_for_selector(".mrow.rec"); pg.click("details summary >> nth=0")
    pg.screenshot(path=OUT + "10_wizard_recommendation.png", full_page=True)
    pg.click("#wNext"); pg.wait_for_selector("#wInst")
    pg.click("#wInst"); pg.wait_for_selector("#wPerm .perm")
    pg.screenshot(path=OUT + "11_wizard_install_permission.png")
    pg.click("#wPerm [data-a='1']")
    pg.wait_for_selector("#wPerm .perm:has-text('Download an AI model')", timeout=60000)
    pg.screenshot(path=OUT + "12_wizard_download_permission.png")
    pg.click("#wPerm [data-a='1']")
    pg.wait_for_selector("#pPull .eta:has-text('ETA')"); pg.wait_for_timeout(2500)
    pg.screenshot(path=OUT + "13_wizard_download_progress.png")
    pg.click("#wCancel"); pg.wait_for_selector("#wResume"); pg.screenshot(path=OUT + "14_wizard_paused.png")
    pg.click("#wResume"); pg.wait_for_selector("h2:has-text('all set')", timeout=60000)
    pg.screenshot(path=OUT + "15_wizard_done.png")
    pg.click("[data-close]"); pg.click("#openModels"); pg.wait_for_selector("[data-del]")
    pg.screenshot(path=OUT + "16_manage_models.png")
    b.close()
print("errors:", errors or "none"); sys.exit(1 if errors else 0)
