"""Build a .jivupdate package from the difference between two versions of the app.
  python tools/make_update.py --old <old app dir or .zip> --new <new app dir> --version 1.1.1 \
      --changelog "What changed" [--min-version 1.1.0] [--key /workspace/jiv-signing/jiv_update_private.pem] -o out.jivupdate
Without --key the package is unsigned (shown as 'Unsigned – created locally')."""
import argparse, os, shutil, sys, tempfile, zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jiv import updater as U

def as_dir(p):
    p = Path(p)
    if p.is_dir(): return p, None
    tmp = Path(tempfile.mkdtemp(prefix="jiv-old-")); zipfile.ZipFile(p).extractall(tmp)
    kids = [k for k in tmp.iterdir() if k.is_dir()]
    return (kids[0] if len(kids) == 1 and not (tmp / "run.py").exists() else tmp), tmp

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True); ap.add_argument("--new", required=True); ap.add_argument("--version", required=True)
    ap.add_argument("--min-version"); ap.add_argument("--changelog", default=""); ap.add_argument("--changelog-file")
    ap.add_argument("--key"); ap.add_argument("--name")
    ap.add_argument("--requirement", "-r", action="append", default=[], help="new pip package for the app's Python, e.g. 'rich>=13' (repeatable). "
                    "Lines added to requirements.txt are included automatically.")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    old, tmp = as_dir(a.old); new = Path(a.new)
    try:
        files, deletes = U.diff_trees(old, new)
        if not files and not deletes: sys.exit("No differences found.")
        files = U.with_version_file(files, a.version)  # always stamp the new version
        ch = Path(a.changelog_file).read_text() if a.changelog_file else a.changelog
        # actions (add/replace) are computed against the OLD tree
        U.APP_DIR = old.resolve()
        min_v = a.min_version or (old / "jiv" / "__init__.py").read_text().split('"')[1]
        reqs = list(a.requirement)
        if "requirements.txt" in files:
            oldr = set(l.strip() for l in (old / "requirements.txt").read_text().splitlines()) if (old / "requirements.txt").exists() else set()
            for l in files["requirements.txt"].decode().splitlines():
                l = l.split("#")[0].strip()
                if l and l not in oldr and l not in reqs: reqs.append(l)
        reqs = U.check_requirements(reqs)
        out = U.build_package(a.out, a.version, ch, files=files, deletes=deletes, min_version=min_v,
                              name=a.name, source="release", private_key=a.key, extra={"requirements": reqs} if reqs else None)
        if reqs: print("  pip:", ", ".join(reqs))
        print(f"Built {out}  ({len(files)} changed/added, {len(deletes)} deleted, {'signed' if a.key else 'UNSIGNED'})")
        for r in sorted(files): print("  ~", r)
        for r in deletes: print("  -", r)
    finally:
        if tmp: shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__": main()
