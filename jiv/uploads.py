"""Chat attachments: stored in <data>/uploads/<id>/, text extracted once for the model.
Text: txt/md/code/csv/json/log/html/xml... PDF (pypdf), XLSX (openpyxl), DOCX/PPTX (built-in zip+XML reader,
no compiled packages). Images are kept for preview and for vision models."""
import json, mimetypes, re, time, uuid, zipfile, html as _html
import xml.etree.ElementTree as ET
from pathlib import Path
from .config import data_dir

MAX_UPLOAD = 50 * 1024 * 1024
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
TEXT_EXT = set(""".txt .md .markdown .rst .csv .tsv .json .jsonl .log .html .htm .xml .yaml .yml .toml .ini .cfg .conf .env .sql .svg
 .py .js .ts .tsx .jsx .mjs .cjs .java .kt .c .h .cpp .hpp .cc .cs .go .rs .rb .php .pl .lua .r .m .swift .scala .sh .bash .zsh
 .ps1 .bat .cmd .css .scss .less .vue .svelte .dart .ex .exs .hs .ml .clj .tex .srt .vtt .gitignore .dockerfile .makefile""".split())
VISION_HINTS = ("llava", "bakllava", "vision", "-vl", "vl:", "qwen2.5vl", "qwen2-vl", "qwen3-vl", "moondream", "minicpm-v",
                "gemma3", "llama4", "mistral-small3.1", "mistral-small3.2", "granite3.2-vision", "pixtral", "gpt-4o", "gpt-4.1",
                "gpt-5", "claude", "gemini")

def uploads_dir():
    p = data_dir() / "uploads"; p.mkdir(parents=True, exist_ok=True); return p

def safe_name(name):
    name = Path(str(name or "file").replace("\\", "/")).name
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name).strip(" .") or "file"
    return name[:120]

def kind_of(name):
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT: return "image"
    if ext == ".pdf": return "pdf"
    if ext in (".docx", ".xlsx", ".pptx"): return ext[1:]
    if ext in TEXT_EXT or not ext: return "text"
    return "binary"

def _xml_text(data, para_tags):
    """Plain text from an Office XML part: <w:t>/<a:t> runs, newline at paragraph ends."""
    out, cur = [], []
    for ev, el in ET.iterparse(__import__("io").BytesIO(data), events=("end",)):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "t" and el.text: cur.append(el.text)
        elif tag == "tab": cur.append("\t")
        elif tag in ("br", "cr"): cur.append("\n")
        elif tag in para_tags: out.append("".join(cur)); cur = []
        el.clear()
    if cur: out.append("".join(cur))
    return "\n".join(l for l in out if l.strip())

def extract_docx(path):
    with zipfile.ZipFile(path) as z:
        parts = ["word/document.xml"] + sorted(n for n in z.namelist() if re.match(r"word/(header|footer|footnotes)\d*\.xml", n))
        return "\n\n".join(_xml_text(z.read(n), {"p"}) for n in parts if n in z.namelist())

def extract_pptx(path):
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
        out = []
        for i, n in enumerate(slides, 1):
            t = _xml_text(z.read(n), {"p"})
            num = re.findall(r"\d+", n)[-1]
            notes = "ppt/notesSlides/notesSlide" + num + ".xml"
            if notes in z.namelist():
                nt = _xml_text(z.read(notes), {"p"})
                if nt.strip(): t += "\n(Notes) " + nt
            out.append(f"--- Slide {i} ---\n{t}")
        return "\n\n".join(out)

def extract_xlsx(path, max_rows=2000):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"--- Sheet: {ws.title} ---")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= max_rows: out.append(f"…(more rows not shown)"); break
            if any(v is not None for v in row): out.append(",".join("" if v is None else str(v) for v in row))
    wb.close(); return "\n".join(out)

def extract_pdf(path):
    from pypdf import PdfReader
    r = PdfReader(str(path)); out = []
    for i, pg in enumerate(r.pages, 1):
        try: t = pg.extract_text() or ""
        except Exception as e: t = f"(could not read this page: {e})"
        out.append(f"--- Page {i} ---\n{t.strip()}")
    txt = "\n\n".join(out)
    if not re.sub(r"--- Page \d+ ---|\s", "", txt): txt += "\n\n(No text layer found - this PDF is probably scanned images; text could not be extracted.)"
    return txt

def extract_text(path, kind):
    path = Path(path)
    try:
        if kind == "pdf": return extract_pdf(path), ""
        if kind == "docx": return extract_docx(path), ""
        if kind == "pptx": return extract_pptx(path), ""
        if kind == "xlsx": return extract_xlsx(path), ""
        if kind == "text":
            raw = path.read_bytes()
            if b"\x00" in raw[:4096]: return "", "This looks like a binary file, so there is no text to read."
            t = raw.decode("utf-8", errors="replace")
            if path.suffix.lower() in (".html", ".htm"):
                t = re.sub(r"(?is)<(script|style).*?</\1>", " ", t); t = re.sub(r"(?s)<[^>]+>", " ", t)
                t = _html.unescape(re.sub(r"[ \t]+", " ", t)); t = re.sub(r"\n\s*\n+", "\n\n", t).strip()
            return t, ""
        return "", "Jiv can't read this file type yet."
    except ModuleNotFoundError as e:
        return "", f"Reading {kind.upper()} files needs the Python package '{e.name}' (installed by the 1.2.2 update)."
    except Exception as e:
        return "", f"Could not read the file: {type(e).__name__}: {e}"

def save(filename, data: bytes):
    if len(data) > MAX_UPLOAD: raise ValueError("file too large (max 50 MB)")
    name = safe_name(filename); uid = uuid.uuid4().hex[:12]
    d = uploads_dir() / uid; d.mkdir(parents=True)
    (d / name).write_bytes(data)
    kind = kind_of(name)
    meta = {"id": uid, "name": name, "size": len(data), "kind": kind, "created": time.time(),
            "mime": IMAGE_EXT.get(Path(name).suffix.lower()) or mimetypes.guess_type(name)[0] or "application/octet-stream"}
    if kind != "image":
        text, err = extract_text(d / name, kind)
        (d / "text.txt").write_text(text, encoding="utf-8")
        meta.update(chars=len(text), error=err, preview=text[:300])
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta

def get(uid):
    if not re.fullmatch(r"[0-9a-f]{12}", str(uid or "")): return None
    p = uploads_dir() / uid / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

def find(ref):
    """Upload by id, or by file name (newest first)."""
    m = get(ref)
    if m: return m
    ref = str(ref or "").strip().lower()
    best = None
    for d in uploads_dir().iterdir():
        mp = d / "meta.json"
        if mp.exists():
            mm = json.loads(mp.read_text(encoding="utf-8"))
            if mm["name"].lower() == ref and (not best or mm["created"] > best["created"]): best = mm
    return best

def file_path(meta): return uploads_dir() / meta["id"] / meta["name"]

def text(meta):
    p = uploads_dir() / meta["id"] / "text.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""

def is_vision_model(model):
    m = (model or "").lower()
    return any(h in m for h in VISION_HINTS)

def context_block(meta, budget):
    """Text the model sees for one attachment, cut to `budget` characters with a clear note."""
    head = f"[Attached file: {meta['name']} (id {meta['id']}, {meta['kind']}, {meta['size']:,} bytes)]"
    if meta.get("error"): return f"{head}\n(Note: {meta['error']})"
    t = text(meta); n = len(t)
    if n <= budget: return f"{head}\n{t}\n[End of {meta['name']}]"
    return (f"{head}\n{t[:max(0, budget)]}\n[NOTE: this file is {n:,} characters; only the first {budget:,} are shown above "
            f"because the model's context is small. Use read_file with path \"{meta['id']}\" and offset {budget} to read more.]")
