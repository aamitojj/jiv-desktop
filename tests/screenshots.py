"""Headless Chrome UI test + screenshots (needs: pip install playwright)."""
import sys
from playwright.sync_api import sync_playwright
import os; BASE = os.environ.get("JIV_URL", "http://127.0.0.1:8765/"); OUT = "screenshots/"
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1360, "height": 860})
    pg.on("console", lambda m: m.type == "error" and errors.append(m.text)); pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(BASE); pg.wait_for_selector(".empty")
    pg.screenshot(path=OUT + "01_welcome.png")
    pg.fill("#input", "Please make file hello.txt in my home folder"); pg.keyboard.press("Enter")
    pg.wait_for_selector(".perm button[data-perm=once]")
    pg.screenshot(path=OUT + "02_permission_card.png")
    pg.click(".perm button[data-perm=once]"); pg.wait_for_selector("#send:not(.hidden)")
    pg.wait_for_timeout(300); pg.screenshot(path=OUT + "03_after_allow.png")
    pg.click("#newChat"); pg.fill("#input", "delegate this research to Scout"); pg.keyboard.press("Enter")
    pg.wait_for_selector("#send:not(.hidden)"); pg.wait_for_timeout(300)
    assert pg.locator("#agents .item").count() >= 2, "agents missing"
    pg.screenshot(path=OUT + "04_agents_sidebar_delegation.png")
    pg.click("#openSettings"); pg.wait_for_selector("#s_model option:has-text('mock-json')", state="attached")
    pg.click("#s_detect"); pg.wait_for_selector("#s_found .chip"); pg.click("#s_test"); pg.wait_for_selector("#s_msg:has-text('Connected')")
    pg.screenshot(path=OUT + "05_settings.png")
    pg.click("[data-close]")
    pg.click("[data-edit]:nth-of-type(1) >> nth=1", force=True); pg.wait_for_selector("#a_persona")
    pg.screenshot(path=OUT + "06_agent_editor.png"); pg.click("[data-close]")
    pg.click("#openAudit"); pg.wait_for_selector("table.audit"); pg.screenshot(path=OUT + "07_audit_log.png"); pg.click("[data-close]")
    pg.click("#toggleTheme"); pg.wait_for_timeout(400); pg.click("#convs .item >> nth=0"); pg.wait_for_timeout(500)
    pg.screenshot(path=OUT + "08_light_theme.png")
    pg.click("#toggleTheme"); b.close()
print("console errors:", errors or "none"); sys.exit(1 if errors else 0)
