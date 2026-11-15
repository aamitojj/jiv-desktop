"""Screenshots of the wizard with a simulated MacBook (Apple M5, 16 GB). Needs Jiv started with
JIV_FAKE_HW=<m5.json> JIV_INSTALL_MODE=mac and a fake brew on PATH."""
import os, sys
from playwright.sync_api import sync_playwright
BASE = os.environ.get("JIV_URL", "http://127.0.0.1:8790/"); OUT = "screenshots/"; errs = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"]); pg = b.new_page(viewport={"width": 1360, "height": 900})
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE); pg.wait_for_selector(".hwgrid"); pg.screenshot(path=OUT + "17_mac_hardware_m5.png")
    pg.click("#wNext"); pg.wait_for_selector(".mrow.rec"); pg.screenshot(path=OUT + "18_mac_recommendation_m5.png")
    pg.click("#wNext"); pg.wait_for_selector("#wBrew"); pg.click("#wZip"); pg.wait_for_selector("#wPerm .perm")
    pg.screenshot(path=OUT + "19_mac_install_permission.png"); b.close()
print("errors:", errs or "none"); sys.exit(1 if errs else 0)
