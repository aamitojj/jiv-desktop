"""Jiv Desktop launcher. python run.py [--port N] [--no-browser]"""
import argparse, json, socket, sys, threading, time, webbrowser, os
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))

def free_port(p):
    for port in range(p, p + 50):
        with socket.socket() as s:
            if os.name != "nt": s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # same as uvicorn; ignores TIME_WAIT
            try: s.bind(("127.0.0.1", port)); return port
            except OSError: continue
    raise SystemExit("No free port")

def already_running(port):
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as r: return "version" in json.loads(r.read())
    except Exception: return False

def main():
    if sys.stdout is None or sys.stderr is None:  # pythonw / --windowed exe: log to file
        from jiv.config import data_dir
        f = open(data_dir() / "jiv.log", "a", encoding="utf-8", buffering=1); sys.stdout = sys.stdout or f; sys.stderr = sys.stderr or f
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int); ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--browser", action="store_true", help="force browser even if pywebview is installed")
    ap.add_argument("--relaunched", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    import uvicorn
    from jiv.server import app, cfg
    if not a.relaunched and not a.port and already_running(int(cfg.get("port", 8765))):  # second double-click: just show it
        if not a.no_browser: webbrowser.open(f"http://127.0.0.1:{int(cfg.get('port', 8765))}/")
        return
    port = a.port if a.relaunched and a.port else free_port(a.port or int(cfg.get("port", 8765)))  # relaunch must keep its port
    url = f"http://127.0.0.1:{port}/"
    # remembered so the updater can restart Jiv the same way after an update
    ui = "none" if a.no_browser else "browser" if a.browser else "auto"
    # installed layout: restart through JivDesktop.exe (it sets JIV_LAUNCHER); otherwise the same python + run.py
    launcher = os.environ.get("JIV_LAUNCHER")
    relaunch = ([launcher] if launcher and os.path.exists(launcher) else [sys.executable, os.path.abspath(__file__)]) + ["--port", str(port)]
    if ui == "none": relaunch.append("--no-browser")
    if ui == "browser": relaunch.append("--browser")
    relaunch.append("--relaunched")   # browser tab already open: it reloads itself instead of opening a new one
    os.environ["JIV_PORT"] = str(port); os.environ["JIV_UI_MODE"] = ui
    os.environ["JIV_LAUNCH"] = json.dumps(relaunch)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    print(f"Jiv Desktop running at {url}  (Ctrl+C to quit)", flush=True)
    if a.no_browser: server.run(); return
    webview = None
    if not a.browser:
        try: import webview  # optional native window
        except Exception: webview = None
    if webview:
        threading.Thread(target=server.run, daemon=True).start()
        while not server.started: time.sleep(0.05)
        webview.create_window("Jiv Desktop", url, width=1280, height=820, min_size=(820, 560)); webview.start()
    else:
        def opener():
            while not server.started: time.sleep(0.05)
            if not a.relaunched: webbrowser.open(url)
        threading.Thread(target=opener, daemon=True).start(); server.run()

if __name__ == "__main__": main()
