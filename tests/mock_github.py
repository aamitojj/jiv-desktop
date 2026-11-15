"""Mock GitHub Releases API.
Usage: python tests/mock_github.py <port> <package.jivupdate or -> <tag> [JivSetup.exe]"""
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import Response, JSONResponse
import uvicorn
PORT, TAG = int(sys.argv[1]), sys.argv[3]
PKG = None if sys.argv[2] == "-" else Path(sys.argv[2])
EXE = Path(sys.argv[4]) if len(sys.argv) > 4 else None
app = FastAPI()
@app.get("/repos/{owner}/{repo}/releases/latest")
def latest(owner: str, repo: str):
    if repo == "empty": return JSONResponse({"message": "Not Found"}, 404)
    v = TAG.lstrip("v"); assets = []
    if PKG: assets.append({"name": f"jiv-{v}.jivupdate", "size": PKG.stat().st_size, "browser_download_url": f"http://127.0.0.1:{PORT}/download/pkg.jivupdate"})
    if EXE: assets.append({"name": f"JivSetup-{v}.exe", "size": EXE.stat().st_size, "browser_download_url": f"http://127.0.0.1:{PORT}/download/JivSetup.exe"})
    return {"tag_name": TAG, "html_url": f"https://github.com/{owner}/{repo}/releases/tag/{TAG}", "body": "Release notes for " + TAG, "assets": assets}
@app.get("/download/pkg.jivupdate")
def dl(): return Response(PKG.read_bytes(), media_type="application/octet-stream")
@app.get("/download/JivSetup.exe")
def dl_exe(): return Response(EXE.read_bytes(), media_type="application/octet-stream")
uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
