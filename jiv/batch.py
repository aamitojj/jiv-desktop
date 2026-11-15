"""Jiv 1.4 Batch: repeat a prompt N times, or run a prompt over every item of a file (JSON, CSV, JSONL, TXT, MD,
HTML page / folder / zip) as a resumable background job, with an optional verify pass, MCQ checks, and review files.
Built for small local models (~4k context): every item is split into small units that fit the context."""
import asyncio, csv, difflib, html as H, io, json, re, shutil, time, uuid, zipfile
from pathlib import Path
import httpx
from .config import data_dir
from .providers import _headers

MAX_REPEAT, MAX_RETRIES = 10000, 5
TEXT_EXT = {".txt", ".md", ".markdown"}; HTML_EXT = {".html", ".htm", ".xhtml"}
SPLITS = {"json": "JSON array elements", "jsonl": "JSONL lines", "csv": "CSV rows", "lines": "Lines", "paragraphs": "Paragraphs",
          "md_sections": "Markdown sections (headings)", "html_pages": "One item per HTML page", "html_sections": "One item per HTML section",
          "whole": "Whole file as one item"}

def root():
    p = data_dir() / "batch"; p.mkdir(parents=True, exist_ok=True); return p
def job_dir(jid): return root() / "jobs" / jid
def new_id(): return uuid.uuid4().hex[:10]

# ---------------------------------------------------------------- inputs
def _safe_rel(name):
    parts = [p for p in re.split(r"[\\/]+", name or "") if p not in ("", ".", "..")]
    parts = [re.sub(r'[<>:"|?*\x00-\x1f]', "_", p)[:120] for p in parts]
    return "/".join(parts) or "file"

def list_files(d):
    return sorted(str(p.relative_to(d)).replace("\\", "/") for p in Path(d).rglob("*") if p.is_file())

def save_input(set_id, filename, data):
    """Store one uploaded file in an input set (a zip is extracted). Several calls with the same set id build a folder."""
    set_id = re.sub(r"[^a-z0-9]", "", (set_id or "").lower())[:16] or new_id()
    base = root() / "inputs" / set_id; d = base / "files"; d.mkdir(parents=True, exist_ok=True)
    rel = _safe_rel(filename)
    if rel.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            total = 0
            for info in z.infolist():
                if info.is_dir(): continue
                total += info.file_size
                if total > 500 * 1024 * 1024: raise ValueError("zip too large when extracted (max 500 MB)")
                r = _safe_rel(info.filename)
                if r.startswith("__MACOSX/") or r.split("/")[-1].startswith("._"): continue
                (d / r).parent.mkdir(parents=True, exist_ok=True); (d / r).write_bytes(z.read(info))
        kind, name = "folder", rel
    else:
        (d / rel).parent.mkdir(parents=True, exist_ok=True); (d / rel).write_bytes(data)
        kind, name = "file", rel
    files = list_files(d)
    if len(files) > 1 or "/" in rel: kind = "folder"; name = name if rel.lower().endswith(".zip") else rel.split("/")[0]
    mp = base / "meta.json"
    if mp.exists() and kind == "folder": name = json.loads(mp.read_text()).get("name", name) if json.loads(mp.read_text())["kind"] == "folder" else name
    mp.write_text(json.dumps({"kind": kind, "name": name}))
    return {"set": set_id, "kind": kind, "name": name, "files": len(files)}

def resolve_source(src):
    """src: 'set:<id>' (Batch page upload), 'upload:<id>' or a bare chat-upload id, or a local file/folder path.
    Returns (dir, [relative files], label, kind)."""
    src = str(src or "").strip().strip('"')
    if not src: raise ValueError("No input file chosen.")
    if src.startswith("set:"):
        sid = re.sub(r"[^a-z0-9]", "", src[4:]); d = root() / "inputs" / sid / "files"
        if not d.is_dir(): raise ValueError("That upload has expired - choose the file again.")
        meta = json.loads((d.parent / "meta.json").read_text()); files = list_files(d)
        return d, files, meta["name"], ("file" if meta["kind"] == "file" and len(files) == 1 else "folder")
    from . import uploads as UPL
    uid = src[7:] if src.startswith("upload:") else src
    if re.fullmatch(r"[a-f0-9]{6,32}", uid) and UPL.get(uid):
        m = UPL.get(uid); p = UPL.file_path(m)
        if p.suffix.lower() == ".zip" or m["name"].lower().endswith(".zip"):
            s = save_input(None, m["name"], p.read_bytes()); return resolve_source("set:" + s["set"])
        return p.parent, [p.name], m["name"], "file"
    p = Path(src).expanduser()
    if p.is_file():
        if p.suffix.lower() == ".zip":
            s = save_input(None, p.name, p.read_bytes()); return resolve_source("set:" + s["set"])
        return p.parent, [p.name], p.name, "file"
    if p.is_dir(): return p, list_files(p)[:20000], p.name, "folder"
    raise ValueError(f"Input not found: {src}")

def _read_text(p):
    b = Path(p).read_bytes()
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"): return b.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try: return b.decode(enc)
        except UnicodeDecodeError: continue
    return b.decode("latin-1")

# ---------------------------------------------------------------- splitting
def _wrapped_list(j):
    if isinstance(j, dict):
        lists = [(k, v) for k, v in j.items() if isinstance(v, list) and v]
        if len(lists) == 1: return lists[0]
        lists = [(k, v) for k, v in lists if all(isinstance(x, dict) for x in v)]
        if lists: return max(lists, key=lambda kv: len(kv[1]))
    return None, None

def detect_split(files, d, kind):
    if kind == "folder" or len(files) > 1: return "html_pages"
    ext = Path(files[0]).suffix.lower()
    if ext in HTML_EXT: return "html_pages"
    if ext in (".jsonl", ".ndjson"): return "jsonl"
    if ext in (".csv", ".tsv"): return "csv"
    txt = _read_text(Path(d) / files[0])
    if ext == ".json":
        try:
            j = json.loads(txt)
            return "json" if isinstance(j, list) or _wrapped_list(j)[0] else "whole"
        except Exception:
            lines = [l for l in txt.splitlines() if l.strip()]
            return "jsonl" if lines and all(l.strip().startswith("{") for l in lines[:20]) else "whole"
    if ext in (".md", ".markdown") and re.search(r"(?m)^#{1,3} \S", txt): return "md_sections"
    paras = [p for p in re.split(r"\n\s*\n", txt) if p.strip()]
    if len(paras) > 1 and sum(p.strip().count("\n") for p in paras) >= len(paras): return "paragraphs"
    return "lines"

def _label(x, i):
    if isinstance(x, dict):
        for k in ("question", "q", "title", "name", "stem", "id", "qid", "question_id", "number", "no"):
            for kk in x:
                if kk.lower() == k and str(x[kk]).strip(): return f"#{i} {str(x[kk])[:60]}"
        v = next((v for v in x.values() if isinstance(v, str) and v.strip()), "")
        return f"#{i} {v[:60]}"
    return f"#{i} {str(x).strip()[:60]}"

def csv_delim(txt, ext):
    if ext == ".tsv": return "\t"
    try: return csv.Sniffer().sniff(txt[:4096], delimiters=",;\t|").delimiter
    except Exception: return ","

# HTML: only visible text nodes are rewritten; tags, attributes, scripts, styles and code blocks are copied byte for byte.
TOKEN_RE = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<(script|style|noscript|template|svg|pre|code|textarea|math)\b[^>]*>.*?</\1\s*>|<[^>]+>", re.S | re.I)
SECTION_START = re.compile(r"<(h[1-3]|section|article)\b", re.I)

def html_parts(src):
    """-> parts (list of str; joined == src), segs [(part_idx, raw text)] for visible text, section_starts [part_idx]."""
    parts, segs, starts, pos, in_head, in_title = [], [], [], 0, False, False
    def text(t):
        if t and not in_head or (t and in_title):
            if re.search(r"[^\W\d_]", H.unescape(t)): segs.append((len(parts), t))
        if t: parts.append(t)
    for m in TOKEN_RE.finditer(src):
        text(src[pos:m.start()])
        tag = m.group(0); tl = tag[:8].lower()
        if tl.startswith("<head") and not tl.startswith("<header"): in_head = True
        elif tl.startswith("</head") and not tl.startswith("</header"): in_head = False
        elif tl.startswith("<title"): in_title = True
        elif tl.startswith("</title"): in_title = False
        if SECTION_START.match(tag): starts.append(len(parts))
        parts.append(tag); pos = m.end()
    text(src[pos:])
    return parts, segs, starts

def words(s): return len(re.findall(r"[^\W_]+", s))

def split_items(d, files, split):
    """-> (items, layout). Each item: {"label", "input": {...}}"""
    items, layout = [], {"split": split, "files": files}
    if split in ("html_pages", "html_sections"):
        for rel in files:
            ext = Path(rel).suffix.lower()
            if ext not in HTML_EXT:
                if ext in TEXT_EXT:
                    t = _read_text(Path(d) / rel)
                    if t.strip(): items.append({"label": rel, "input": {"type": "text", "file": rel, "text": t}})
                continue
            parts, segs, starts = html_parts(_read_text(Path(d) / rel))
            if not segs: continue
            if split == "html_pages":
                items.append({"label": rel, "input": {"type": "html", "file": rel, "segs": {str(i): t for i, t in segs}}}); continue
            groups, cur, bounds = [], [], starts + [10 ** 9]; b = 0
            for i, t in segs:
                while i >= bounds[b]:
                    if cur: groups.append(cur); cur = []
                    b += 1
                cur.append((i, t))
            if cur: groups.append(cur)
            for g in groups:
                head = " ".join(H.unescape(g[0][1]).split())[:50]
                items.append({"label": f"{rel} › {head}", "input": {"type": "html", "file": rel, "segs": {str(i): t for i, t in g}}})
        return items, layout
    rel = files[0]; p = Path(d) / rel; txt = _read_text(p)
    if split == "json":
        j = json.loads(txt); key, lst = (None, j) if isinstance(j, list) else _wrapped_list(j)
        if lst is None: raise ValueError("No JSON array found in this file.")
        layout["wrapper"] = key
        for i, x in enumerate(lst):
            inp = {"type": "record", "data": x, "pos": i} if isinstance(x, (dict, list)) else {"type": "text", "text": str(x), "pos": i, "scalar": True}
            items.append({"label": _label(x, i + 1), "input": inp})
    elif split == "jsonl":
        for n, line in enumerate(txt.splitlines()):
            if not line.strip(): continue
            try: x = json.loads(line)
            except Exception as e: raise ValueError(f"Line {n + 1} is not valid JSON: {e}")
            items.append({"label": _label(x, len(items) + 1), "input": {"type": "record", "data": x, "pos": n}})
    elif split == "csv":
        delim = csv_delim(txt, p.suffix.lower()); layout["delimiter"] = delim
        rd = csv.DictReader(io.StringIO(txt), delimiter=delim)
        layout["fields"] = [f for f in (rd.fieldnames or []) if f is not None]
        for i, row in enumerate(rd):
            row = {k: (v if v is not None else "") for k, v in row.items() if k is not None}
            if not any(str(v).strip() for v in row.values()): continue
            items.append({"label": _label(row, len(items) + 1), "input": {"type": "record", "data": row, "pos": i}})
    elif split == "whole":
        items.append({"label": rel, "input": {"type": "text", "text": txt, "pos": 0}})
    else:
        parts = split_text(txt, split)
        for i, s in enumerate(parts):
            if split != "md_sections" and i % 2 == 1: continue
            if s.strip(): items.append({"label": f"#{len(items) + 1} {s.strip()[:60]}", "input": {"type": "text", "text": s, "pos": i}})
    return items, layout

def split_text(txt, split):
    if split == "lines": return re.split(r"(\r?\n)", txt)
    if split == "paragraphs": return re.split(r"(\r?\n[ \t]*\r?\n\s*)", txt)
    return re.split(r"(?m)^(?=#{1,3} )", txt)

# ---------------------------------------------------------------- units (context-sized pieces)
def text_chunks(text, limit):
    if len(text) <= limit: return [text]
    out, cur = [], ""
    for p in re.split(r"(\n\s*\n)", text):
        if len(cur) + len(p) <= limit: cur += p; continue
        if cur.strip(): out.append(cur)
        cur = ""
        if len(p) <= limit: cur = p; continue
        for s in re.split(r"(?<=[.!?])(\s+)", p):   # very long paragraph: split by sentence
            if len(cur) + len(s) > limit and cur.strip(): out.append(cur); cur = ""
            cur += s
    if cur.strip(): out.append(cur)
    return out

def units_of(inp, spec):
    limit = int(spec.get("chunk_chars") or 3500)
    if inp["type"] == "html":
        mw = int(spec.get("min_words", 3)); us, cur, size = [], {}, 0
        for k, t in inp["segs"].items():
            clean = " ".join(H.unescape(t).split())
            if words(clean) < mw: continue
            if cur and size + len(clean) + 12 > limit: us.append(cur); cur, size = {}, 0
            cur[k] = clean; size += len(clean) + 12
        if cur: us.append(cur)
        return [{"kind": "html", "segs": u} for u in us]
    if inp["type"] == "text":
        t = inp["text"]; lead = t[:len(t) - len(t.lstrip())]; trail = t[len(t.rstrip()):]
        cs = text_chunks(t.strip(), limit)
        return [{"kind": "text", "text": c, "lead": lead if i == 0 else "", "trail": trail if i == len(cs) - 1 else ("\n\n" if not c.endswith("\n") else "")} for i, c in enumerate(cs)]
    if inp["type"] == "repeat": return [{"kind": "repeat"}]
    return [{"kind": "record"}]

# ---------------------------------------------------------------- MCQ + schema checks
def _norm(s): return re.sub(r"\s+", " ", str(s if s is not None else "")).strip().lower()
def _find(rec, names):
    for k in rec:
        if str(k).lower().strip().replace(" ", "_") in names: return k
OPT_COL = re.compile(r"^(?:option|opt|choice)?[ _-]?\(?([a-h]|[1-8])\)?$", re.I)

def mcq_fields(rec):
    if not isinstance(rec, dict): return None
    q = _find(rec, ("question", "q", "stem", "prompt", "question_text", "mcq"))
    o = _find(rec, ("options", "choices", "answers", "alternatives", "option_list"))
    a = _find(rec, ("answer", "correct", "correct_answer", "answer_key", "key", "ans", "correct_option", "right_answer"))
    e = _find(rec, ("explanation", "rationale", "reason", "explain", "solution"))
    cols = [] if o else [k for k in rec if k not in (q, a, e) and OPT_COL.match(str(k).strip())]
    if not q or not a or not (o or len(cols) >= 2): return None
    return {"q": q, "o": o, "cols": cols, "a": a, "e": e}

def options_of(rec, f):
    if not f: return []
    if f["o"]:
        v = rec.get(f["o"])
        if isinstance(v, dict): return [(str(k), str(x)) for k, x in v.items()]
        if isinstance(v, list): return [(chr(65 + i), str((x.get("text") or x.get("option") or json.dumps(x)) if isinstance(x, dict) else x)) for i, x in enumerate(v)]
        if isinstance(v, str):
            parts = [p for p in re.split(r"\s*(?:\||;|\n)\s*", v) if p.strip()]
            return [(chr(65 + i), re.sub(r"^\(?[A-Ha-h1-8][).:]\s*", "", p)) for i, p in enumerate(parts)]
        return []
    out = []
    for k in f["cols"]:
        lab = OPT_COL.match(str(k).strip()).group(1).upper()
        out.append((chr(64 + int(lab)) if lab.isdigit() else lab, str(rec.get(k, ""))))
    return out

def resolve_answer(ans, opts, zero_based=False):
    """Answer key -> option index. Accepts a letter (B, 'b)', 'Option B'), the option text, or a number."""
    if ans is None or isinstance(ans, bool) or not opts: return None
    s = str(ans).strip(); ns = _norm(s).strip(".")
    if not isinstance(ans, (int, float)):   # option text first ("4" can be the text of an option)
        for i, (_, t) in enumerate(opts):
            if _norm(t).strip(".") == ns: return i
    if isinstance(ans, (int, float)) or re.fullmatch(r"\d+", s):
        n = int(float(ans)); n = n if zero_based else n - 1
        return n if 0 <= n < len(opts) else None
    labels = [l.upper() for l, _ in opts]
    m = re.fullmatch(r"(?:option|answer|choice)?\s*[\(\[]?([A-Ha-h])\b[\)\].:]?\s*(.*)", s, re.S | re.I)
    if m and m.group(1).upper() in labels:
        rest = _norm(m.group(2)).strip(".")
        i = labels.index(m.group(1).upper())
        if not rest or rest == _norm(opts[i][1]).strip("."): return i
        for j, (_, t) in enumerate(opts):
            if _norm(t).strip(".") == rest: return j
        return i
    return None

def similarity(a, b): return difflib.SequenceMatcher(None, _norm(a), _norm(b), autojunk=False).ratio()

def mcq_check(inp, out, zero_based=False, require_change=True):
    """-> (problems that block, notes, answer index) for one rewritten MCQ."""
    f, fi, probs, notes = mcq_fields(out), mcq_fields(inp), [], []
    if not f: return ["output is not a recognisable MCQ (needs question, options and answer fields)"], notes, None
    q = str(out.get(f["q"]) or "").strip()
    if len(q) < 5: probs.append("question is empty")
    oo, io_ = options_of(out, f), options_of(inp, fi)
    if len(oo) < 2: probs.append("fewer than 2 options")
    if io_ and len(oo) != len(io_): probs.append(f"option count changed ({len(io_)} → {len(oo)})")
    if any(not t.strip() for _, t in oo): probs.append("an option is empty")
    if len({_norm(t) for _, t in oo}) != len(oo): probs.append("two options are the same")
    ai = resolve_answer(out.get(f["a"]), oo, zero_based)
    if ai is None: probs.append(f"answer key {json.dumps(out.get(f['a']), ensure_ascii=False)} does not match any option")
    if fi:
        bi = resolve_answer(inp.get(fi["a"]), io_, zero_based)
        if ai is not None and bi is not None and ai != bi: notes.append(f"answer key changed {io_[bi][0]} → {oo[ai][0]}")
        if require_change and similarity(q, inp.get(fi["q"])) > 0.9: probs.append("question wording is too close to the original")
    if f["e"] is not None and len(str(out.get(f["e"]) or "").strip()) < 15: probs.append("explanation missing or too short")
    return probs, notes, ai

def auto_mcq_schema(rec, add_fields):
    """A JSON schema generated from the input item: same fields and types, + MCQ rules."""
    f = mcq_fields(rec) or {}
    props = {}
    for k, v in rec.items():
        t = ("array" if isinstance(v, list) else "object" if isinstance(v, dict) else "string" if isinstance(v, str)
             else "number" if isinstance(v, (int, float)) and not isinstance(v, bool) else None)
        props[k] = {"type": t} if t else {}
    if f.get("q"): props[f["q"]] = {"type": "string", "minLength": 5}
    if f.get("o") and isinstance(rec.get(f["o"]), list): props[f["o"]] = {"type": "array", "minItems": 2, "items": {"type": "string", "minLength": 1}}
    for a in add_fields: props.setdefault(a, {"type": "string", "minLength": 15})
    if f.get("e") and f["e"] in add_fields: props[f["e"]] = {"type": "string", "minLength": 15}
    return {"type": "object", "required": list(rec.keys()) + [a for a in add_fields if a not in rec], "properties": props}

def validate_schema(v, s, path="$"):
    """JSON-Schema subset: type, required, properties, additionalProperties, items, enum, minLength, maxLength,
    minItems, maxItems, minimum, maximum, pattern."""
    if not isinstance(s, dict): return []
    errs, t = [], s.get("type")
    tmap = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
    if t:
        ts = t if isinstance(t, list) else [t]
        def ok(x):
            if x == "number": return isinstance(v, (int, float)) and not isinstance(v, bool)
            if x == "integer": return isinstance(v, int) and not isinstance(v, bool)
            return isinstance(v, tmap.get(x, object))
        if not any(ok(x) for x in ts): return [f"{path} should be {'/'.join(ts)}"]
    if "enum" in s and v not in s["enum"]: errs.append(f"{path} must be one of {s['enum']}")
    if isinstance(v, str):
        if len(v.strip()) < s.get("minLength", 0): errs.append(f"{path} is too short")
        if "maxLength" in s and len(v) > s["maxLength"]: errs.append(f"{path} is too long")
        if "pattern" in s and not re.search(s["pattern"], v): errs.append(f"{path} does not match {s['pattern']}")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]: errs.append(f"{path} < {s['minimum']}")
        if "maximum" in s and v > s["maximum"]: errs.append(f"{path} > {s['maximum']}")
    if isinstance(v, list):
        if len(v) < s.get("minItems", 0): errs.append(f"{path} needs at least {s['minItems']} items")
        if "maxItems" in s and len(v) > s["maxItems"]: errs.append(f"{path} has more than {s['maxItems']} items")
        for i, x in enumerate(v): errs += validate_schema(x, s.get("items") or {}, f"{path}[{i}]")
    if isinstance(v, dict):
        for k in s.get("required", []):
            if k not in v: errs.append(f"missing field '{k}'")
        props = s.get("properties") or {}
        for k, x in v.items():
            if k in props: errs += validate_schema(x, props[k], f"{path}.{k}")
            elif s.get("additionalProperties") is False: errs.append(f"unexpected field '{k}'")
    return errs

# ---------------------------------------------------------------- model calls
def parse_json(text):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I)
    try: return json.loads(t)
    except Exception: pass
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        try: return json.loads(t[a:b + 1])
        except Exception: pass
    return None

def clean_text(t):
    t = re.sub(r"^```[a-z]*\n?|\n?```$", "", (t or "").strip()).strip()
    t = re.sub(r"^(?:sure[,!.]?\s*)?(?:here(?:'s| is) (?:the |your |a )?(?:rewritten|revised|new|improved|updated)[^:\n]{0,60}:)\s*", "", t, flags=re.I).strip()
    return t

class ModelClient:
    def __init__(self, cfg): self.cfg = cfg
    def conn(self, spec):
        return self.cfg.get("base_url"), self.cfg.get("api_key", ""), (spec or {}).get("model") or self.cfg.get("model")
    async def complete(self, spec, messages, json_mode=False, max_tokens=1024):
        base, key, model = self.conn(spec)
        if not base or not model: raise RuntimeError("No model selected. Open Settings and pick a model.")
        body = {"model": model, "messages": messages, "stream": False, "temperature": float(spec.get("temperature", 0.3)), "max_tokens": int(max_tokens)}
        if json_mode: body["response_format"] = {"type": "json_object"}
        t0 = time.time()
        async with httpx.AsyncClient(timeout=httpx.Timeout(float(self.cfg.get("batch_timeout", 900)), connect=10)) as c:
            url = base.rstrip("/") + "/chat/completions"
            r = await c.post(url, headers=_headers(key), json=body)
            if r.status_code >= 400 and json_mode and "response_format" in r.text:
                body.pop("response_format"); r = await c.post(url, headers=_headers(key), json=body)
            if r.status_code >= 400: raise RuntimeError(f"Model server error HTTP {r.status_code}: {r.text[:300]}")
            d = r.json()
        text = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        u = d.get("usage") or {}
        return text, {"secs": time.time() - t0, "out": u.get("completion_tokens") or max(1, len(text) // 4),
                      "in": u.get("prompt_tokens") or sum(len(str(m.get("content", ""))) for m in messages) // 4}

# ---------------------------------------------------------------- prompts + built-in templates
SYS_RECORD = ("You rewrite ONE data item at a time. Follow the task exactly, then reply with ONLY one JSON object that has exactly these keys: {keys}. "
              "Keep each value's type (lists stay lists, numbers stay numbers). No markdown, no comments, no extra keys.")
SYS_HTML = ("You rewrite the visible text of a web page. You get text segments as a JSON object (key = segment number, value = text). "
            "Rewrite every segment following the task. Reply with ONLY a JSON object with exactly the same keys and the new text as values. "
            "Do not add HTML tags or markdown; do not merge, split or drop segments. Headings and link/button labels stay short.")
SYS_TEXT = "Follow the task. Reply with ONLY the resulting text - no preface such as 'Here is', no quotes, no notes."
SYS_VERIFY = "You are a strict reviewer. Reply with ONLY one JSON object: {fmt}. No other text."

MCQ_PROMPT = """Rewrite this multiple-choice question in completely original wording, so it is not copied from any source.
Rules:
- Keep the same meaning, topic and difficulty. Keep the same options in the same order, but reword each one (facts, numbers and names stay the same).
- Make sure the answer key is correct. If the original key is wrong, fix it.
- Add a short explanation (1-2 sentences) of why the correct answer is right.
- Keep every field. Write the answer in the same format as the original (letter, number or option text).

Question {i} of {total}:
{item}"""
MCQ_VERIFY = """Check this rewritten multiple-choice question.

ORIGINAL:
{original}

REWRITTEN:
{output}

First solve the question yourself. Then check: 1) is the answer key in REWRITTEN correct? 2) are all fields present, with the same options in meaning? 3) does the explanation support the answer? 4) is the wording original (not copied)?"""
PAGE_PROMPT = """Rewrite this web page text for Google AdSense compliance:
- Original: use your own words; do not copy sentences from the original.
- Helpful and people-first: keep all facts, names, numbers, prices and product details; make it clear and useful.
- Remove prohibited or risky content (adult, violent, hateful, dangerous or illegal products, hacking/cracking, misleading or clickbait claims, "click the ads" wording).
- Natural, friendly tone; no keyword stuffing. Headings stay headings; short labels stay short.

Page {i} of {total}. Text segments:
{item}"""
PAGE_VERIFY = """ORIGINAL segments:
{original}

REWRITTEN segments:
{output}

Check: 1) is every segment rewritten in original wording while keeping the meaning and facts? 2) is there any content AdSense prohibits, or misleading claims? 3) are any segments missing or empty?"""
NCLEX_PROMPT = """Write ONE new, original NCLEX-style multiple-choice question about: {topic}
This is question {i} of {total}; cover a different aspect of the topic than a typical first question would (use {i} to vary: assessment, priority/first action, delegation, patient teaching, medication, safety, lab values, complications).
Rules:
- Client-centred clinical scenario (age/setting/key findings) and a clear stem, e.g. "Which action should the nurse take first?"
- Exactly 4 plausible options; exactly one is correct, according to current nursing standards.
- "answer" is the correct option's letter (A, B, C or D).
- "rationale": 2-4 sentences explaining why the answer is right and why the others are wrong.
- Reply as JSON with keys "question", "options" (list of 4 strings, no letters in front), "answer", "rationale", "topic"."""
NCLEX_VERIFY = """Check this NCLEX-style question about "{topic}".

QUESTION:
{output}

First answer the question yourself as an expert nurse educator. Then check: 1) is the key ("answer") the single best answer? 2) are there exactly 4 distinct, plausible options with only one correct? 3) does the rationale support the answer and explain the distractors? 4) is it clinically accurate and safe?"""
NCLEX_SCHEMA = json.dumps({"type": "object", "required": ["question", "options", "answer", "rationale"], "properties": {
    "question": {"type": "string", "minLength": 20}, "options": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "string", "minLength": 1}},
    "answer": {"type": "string", "minLength": 1}, "rationale": {"type": "string", "minLength": 15}, "topic": {"type": "string"}}})
BUILTINS = [
    dict(name="Rewrite MCQ in original wording + verify answer + add explanation",
         description="For JSON/CSV/JSONL question banks. Keeps your fields, checks the answer key, adds an explanation.",
         mode="file", prompt=MCQ_PROMPT, verify=1, verify_prompt=MCQ_VERIFY, schema="auto-mcq", mcq=1, add_fields=["explanation"], split=""),
    dict(name="Rewrite page for AdSense compliance (original, helpful, no copied text, no prohibited content)",
         description="For HTML pages (a file, a folder or a zip). Only the visible text changes; every tag and attribute is kept.",
         mode="file", prompt=PAGE_PROMPT, verify=0, verify_prompt=PAGE_VERIFY, schema="", mcq=0, add_fields=[], split="html_pages"),
    dict(name="Rewrite text in original wording", description="For TXT/MD lines, paragraphs or sections.", mode="file",
         prompt="Rewrite the following text in original, clear wording. Keep the meaning, facts and roughly the same length.\n\n{item}",
         verify=0, verify_prompt="", schema="", mcq=0, add_fields=[], split=""),
    dict(name="Repeat: generate N original MCQs", description="Repeat mode example: one new question per run; {i} keeps them different.", mode="repeat",
         prompt='Write one new, original multiple-choice question for a general-knowledge quiz (number {i} of {total}). Reply as JSON with keys "question", "options" (4 strings), "answer" (the correct option text) and "explanation".',
         verify=0, verify_prompt="", schema="", mcq=0, add_fields=[], split=""),
    dict(name="Generate NCLEX-style MCQs (topic, count)",
         description="Repeat mode: set a topic and how many questions. Each one gets 4 options, an answer key and a rationale, then a verify pass solves it independently. Download as JSON + review Excel.",
         mode="repeat", prompt=NCLEX_PROMPT, verify=1, verify_prompt=NCLEX_VERIFY, schema=NCLEX_SCHEMA, mcq=1, add_fields=[], split=""),
]

def eta_from(recent, remaining, parallel=1, per_unit=None):
    """1.5: ETA from measured throughput (finish times of the last items), so waits, retries and running 2 at once are included."""
    if remaining <= 0: return 0
    times = sorted(x["updated"] for x in recent if x.get("updated"))
    if len(times) >= 3 and times[-1] > times[0]:
        rate = (len(times) - 1) / (times[-1] - times[0])          # items per second, wall clock
        return round(remaining / rate)
    if recent:
        avg = sum(x["secs"] for x in recent) / len(recent)
        return round(avg * remaining / max(1, parallel))
    return round(per_unit * remaining) if per_unit else None

def render(tpl, **kw):
    for k, v in kw.items(): tpl = tpl.replace("{" + k + "}", str(v))
    return tpl

def unit_prompt(spec, unit, inp, i, total, prev):
    tpl = spec.get("prompt") or ""
    if unit["kind"] == "record": item = json.dumps(inp["data"], ensure_ascii=False, indent=1)
    elif unit["kind"] == "html": item = json.dumps({str(n + 1): t for n, t in enumerate(unit["segs"].values())}, ensure_ascii=False, indent=1)
    elif unit["kind"] == "text": item = unit["text"]
    else: item = ""
    if unit["kind"] != "repeat" and "{item}" not in tpl: tpl += "\n\n{item}"
    if spec.get("chain") and unit["kind"] == "repeat" and "{prev}" not in tpl and prev: tpl += "\n\nPrevious output:\n{prev}"
    return render(tpl, item=item, i=i, total=total, topic=spec.get("topic") or "", prev=prev or "(none yet - this is the first run)"), item

# ---------------------------------------------------------------- the engine
TABLES = """
CREATE TABLE IF NOT EXISTS batch_jobs(id TEXT PRIMARY KEY, name TEXT, mode TEXT, status TEXT, created REAL, started REAL, finished REAL,
    spec TEXT, layout TEXT, total INTEGER, done INTEGER DEFAULT 0, flagged INTEGER DEFAULT 0, tokens INTEGER DEFAULT 0, secs REAL DEFAULT 0,
    error TEXT DEFAULT '', estimate TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS batch_items(job_id TEXT, idx INTEGER, label TEXT, input TEXT, output TEXT, status TEXT DEFAULT 'pending',
    attempts INTEGER DEFAULT 0, problems TEXT DEFAULT '', notes TEXT DEFAULT '', secs REAL DEFAULT 0, tokens INTEGER DEFAULT 0, updated REAL,
    PRIMARY KEY(job_id, idx));
CREATE TABLE IF NOT EXISTS batch_log(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, ts REAL, idx INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS batch_templates(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, description TEXT, mode TEXT, prompt TEXT,
    verify INTEGER, verify_prompt TEXT, schema TEXT, mcq INTEGER, add_fields TEXT, split TEXT, builtin INTEGER DEFAULT 0, updated REAL);
CREATE INDEX IF NOT EXISTS batch_items_status ON batch_items(job_id, status);
CREATE INDEX IF NOT EXISTS batch_log_job ON batch_log(job_id, id);
"""
TPL_COLS = ("name", "description", "mode", "prompt", "verify", "verify_prompt", "schema", "mcq", "add_fields", "split")

class BatchManager:
    def __init__(self, db, cfg):
        self.db, self.cfg, self.model = db, cfg, ModelClient(cfg)
        with db.lock: db.c.executescript(TABLES); db.c.commit()
        for t in BUILTINS: self._upsert_tpl(t, builtin=True)
        self.wake = None; self.task = None; self.current = None  # (job_id, item task)

    # ---- templates
    def _upsert_tpl(self, t, builtin=False):
        vals = [t.get("name"), t.get("description") or "", t.get("mode") or "file", t.get("prompt") or "", int(bool(t.get("verify"))),
                t.get("verify_prompt") or "", t.get("schema") or "", int(bool(t.get("mcq"))), json.dumps(t.get("add_fields") or []), t.get("split") or ""]
        upd = ", ".join(f"{c}=excluded.{c}" for c in TPL_COLS[1:])
        self.db.x(f"INSERT INTO batch_templates({','.join(TPL_COLS)},builtin,updated) VALUES({','.join('?' * len(TPL_COLS))},?,?) "
                  f"ON CONFLICT(name) DO UPDATE SET {upd}, updated=excluded.updated" + (" WHERE builtin=1" if builtin else " WHERE builtin=0"),
                  (*vals, int(builtin), time.time()))
    def templates(self):
        out = self.db.q("SELECT * FROM batch_templates ORDER BY builtin DESC, id")
        for t in out:
            t["add_fields"] = json.loads(t["add_fields"] or "[]")
            for k in ("verify", "mcq", "builtin"): t[k] = bool(t[k])
        return out
    def save_template(self, d):
        name = (d.get("name") or "").strip()[:120]
        if not name: raise ValueError("Template needs a name.")
        if self.db.q("SELECT 1 FROM batch_templates WHERE name=? AND builtin=1", (name,)): raise ValueError("That is a built-in template - save it under a new name.")
        self._upsert_tpl({**d, "name": name})
        return next(t for t in self.templates() if t["name"] == name)
    def delete_template(self, tid): self.db.x("DELETE FROM batch_templates WHERE id=? AND builtin=0", (tid,))
    def find_template(self, name):
        n = str(name or "").lower().strip()
        if not n: return None
        ts = self.templates()
        return next((t for t in ts if t["name"].lower() == n), None) or next((t for t in ts if n in t["name"].lower()), None) or \
            next((t for t in ts if all(w in t["name"].lower() for w in re.findall(r"\w+", n))), None)

    # ---- planning
    def normalize(self, spec):
        s = dict(spec or {})
        t = self.find_template(s.get("template"))
        if t:
            for k in ("prompt", "verify_prompt", "schema", "add_fields", "split"):
                if s.get(k) in (None, "", []): s[k] = t[k]
            for k in ("verify", "mcq"):
                if s.get(k) is None: s[k] = t[k]
            if not s.get("mode"): s["mode"] = t["mode"]
        s["mode"] = "repeat" if s.get("mode") == "repeat" or (not s.get("source") and s.get("count")) else "file"
        if not str(s.get("prompt") or "").strip(): raise ValueError("Write a prompt (or choose a template).")
        s["topic"] = str(s.get("topic") or "").strip()[:300]
        if "{topic}" in s["prompt"] and not s["topic"]: raise ValueError("This template needs a topic (for example: heart failure).")
        r = s.get("retries"); s["retries"] = max(0, min(MAX_RETRIES, int(2 if r in (None, "") else r)))
        s["chunk_chars"] = max(800, min(20000, int(s.get("chunk_chars") or self.cfg.get("batch_chunk_chars", 3500))))
        for k in ("verify", "mcq", "chain", "include_unprocessed"): s[k] = bool(s.get(k))
        try: s["parallel"] = max(1, min(2, int(s.get("parallel") or self.cfg.get("batch_parallel", 1) or 1)))
        except (TypeError, ValueError): s["parallel"] = 1
        if s["chain"]: s["parallel"] = 1
        s["require_change"] = s.get("require_change", True) is not False
        s["add_fields"] = [str(x).strip() for x in (s.get("add_fields") or []) if str(x).strip()]
        if s["mode"] == "repeat":
            try: n = int(s.get("count") or 0)
            except ValueError: n = 0
            if not 1 <= n <= MAX_REPEAT: raise ValueError(f"Repeat count must be between 1 and {MAX_REPEAT}.")
            s["count"] = n
        else:
            s["start"] = max(1, int(s.get("start") or 1)); s["limit"] = max(0, int(s.get("limit") or 0))
        if isinstance(s.get("schema"), (dict, list)): s["schema"] = json.dumps(s["schema"])
        if s.get("schema") and s["schema"] != "auto-mcq":
            try: json.loads(s["schema"])
            except Exception as e: raise ValueError(f"The JSON schema is not valid JSON: {e}")
        return s

    def plan(self, spec):
        """Split the input and pick the selected items, without calling the model."""
        s = self.normalize(spec)
        if s["mode"] == "repeat":
            items = [{"label": f"Run {i + 1}", "input": {"type": "repeat", "i": i + 1}, "orig_index": i} for i in range(s["count"])]
            return s, items, {"split": "repeat", "label": f"Repeat ×{s['count']}"}, {"label": f"Repeat ×{s['count']}", "all": s["count"], "auto": "repeat", "kind": "repeat"}
        d, files, label, kind = resolve_source(s.get("source"))
        if not files: raise ValueError("The input folder is empty.")
        auto = detect_split(files, d, kind)
        split = s.get("split") if s.get("split") in SPLITS else auto
        if kind == "folder" and split not in ("html_pages", "html_sections"): split = "html_pages"
        if kind == "file" and Path(files[0]).suffix.lower() not in HTML_EXT and split in ("html_pages", "html_sections"): split = auto
        s["split"] = split
        items, layout = split_items(d, files, split)
        layout.update(dir=str(d), kind=kind, label=label)
        sel = items[s["start"] - 1:]
        if s["limit"]: sel = sel[:s["limit"]]
        for k, it in enumerate(sel): it["orig_index"] = s["start"] - 1 + k
        if s["mcq"]:
            ans = []
            for it in items[:1000]:
                f = mcq_fields(it["input"].get("data")) if it["input"]["type"] == "record" else None
                if f: ans.append(it["input"]["data"].get(f["a"]))
            layout["zero_based"] = any(str(a).strip() == "0" for a in ans if a is not None)
        return s, sel, layout, {"auto": auto, "label": label, "kind": kind, "all": len(items)}

    def preview(self, spec):
        s, items, layout, info = self.plan(spec)
        jid = new_id()
        n = min(len(items), 2000)
        units = sum(len(units_of(it["input"], s)) for it in items[:n])
        if len(items) > n: units = int(units * len(items) / n)
        fields = []
        for it in items[:200]:
            if it["input"]["type"] == "record" and isinstance(it["input"]["data"], dict):
                fields += [k for k in it["input"]["data"] if k not in fields]
        warn = []
        if s["mode"] == "file" and not items: warn.append("No items found - try a different split, or check the start/limit numbers.")
        if s["mcq"] and items and items[0]["input"]["type"] == "record" and not mcq_fields(items[0]["input"]["data"]):
            warn.append("MCQ checks are on, but the first item has no recognisable question / options / answer fields.")
        long_ = sum(1 for it in items[:2000] if it["input"]["type"] == "record" and len(json.dumps(it["input"]["data"])) > s["chunk_chars"])
        if long_: warn.append(f"{long_} item(s) are longer than the chunk size; a small model may cut them short.")
        if s["mode"] == "file" and items and not units: warn.append("Nothing to rewrite was found in the selected items.")
        def sample(it):
            i = it["input"]
            if i["type"] == "record": return json.dumps(i["data"], ensure_ascii=False)
            if i["type"] == "html": return " / ".join(" ".join(H.unescape(t).split()) for t in list(i["segs"].values())[:8])
            return i.get("text", "")
        schema = None
        if s.get("schema") == "auto-mcq" and items and items[0]["input"]["type"] == "record" and isinstance(items[0]["input"]["data"], dict):
            schema = auto_mcq_schema(items[0]["input"]["data"], s["add_fields"])
        return {"job_id": jid, "output_dir": str(job_dir(jid) / "output"), "mode": s["mode"], "split": s.get("split"), "auto_split": info["auto"],
                "splits": SPLITS, "label": info["label"], "kind": info["kind"], "total_items": info["all"], "selected": len(items), "units": units,
                "first_item": (items[0]["orig_index"] + 1) if items else 1, "fields": fields,
                "sample": [{"label": it["label"], "text": sample(it)[:300]} for it in items[:3]], "warnings": warn, "schema": schema,
                "verify": s["verify"], "model": self.model.conn(s)[2], "calls_per_unit": 2 if s["verify"] else 1, "prompt": s["prompt"], "topic": s.get("topic", "")}

    # ---- jobs
    def create(self, spec, job_id=None, name=None, estimate=None):
        s, items, layout, info = self.plan(spec)
        if not items: raise ValueError("No items to process.")
        jid = re.sub(r"[^a-z0-9]", "", (job_id or "").lower())[:16] or new_id()
        if self.db.q("SELECT 1 FROM batch_jobs WHERE id=?", (jid,)): jid = new_id()
        jd = job_dir(jid); (jd / "output").mkdir(parents=True, exist_ok=True)
        if s["mode"] == "file":   # snapshot the input so the job can always resume and rebuild its output
            src, dst = Path(layout["dir"]), jd / "input"
            for rel in layout["files"]:
                (dst / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src / rel, dst / rel)
            layout["dir"] = str(dst)
        nm = (name or s.get("name") or (f"Repeat ×{s['count']}" if s["mode"] == "repeat" else info["label"]))[:100]
        now = time.time()
        self.db.x("INSERT INTO batch_jobs(id,name,mode,status,created,spec,layout,total,estimate) VALUES(?,?,?,?,?,?,?,?,?)",
                  (jid, nm, s["mode"], "queued", now, json.dumps(s), json.dumps(layout), len(items), json.dumps(estimate) if estimate else ""))
        with self.db.lock:
            self.db.c.executemany("INSERT INTO batch_items(job_id,idx,label,input,status,updated) VALUES(?,?,?,?,'pending',?)",
                                  [(jid, k, it["label"], json.dumps(it["input"], ensure_ascii=False), now) for k, it in enumerate(items)])
            self.db.c.commit()
        self.log(jid, None, f"Job created: {len(items)} item(s), split={s.get('split') or 'repeat'}, verify={'on' if s['verify'] else 'off'}, model={self.model.conn(s)[2]}")
        self.kick(); return self.job(jid)

    def log(self, jid, idx, text):
        self.db.x("INSERT INTO batch_log(job_id,ts,idx,text) VALUES(?,?,?,?)", (jid, time.time(), idx, str(text)[:2000]))

    def job(self, jid, log=0):
        r = self.db.q("SELECT * FROM batch_jobs WHERE id=?", (jid,))
        if not r: return None
        j = r[0]; j["spec"] = json.loads(j["spec"]); lay = json.loads(j.pop("layout"))
        j["source_label"] = lay.get("label"); j["split"] = lay.get("split")
        j["output_dir"] = str(job_dir(jid) / "output"); j["files"] = output_files(jid); j["result_name"] = result_name(j["spec"], lay)
        recent = self.db.q("SELECT secs, updated FROM batch_items WHERE job_id=? AND status IN ('ok','flagged') ORDER BY updated DESC LIMIT 12", (jid,))
        avg = sum(x["secs"] for x in recent) / len(recent) if recent else None
        j["avg_secs"] = avg
        j["parallel"] = 1 if j["spec"].get("chain") else max(1, min(2, int(j["spec"].get("parallel") or 1)))
        j["eta_secs"] = eta_from(recent, j["total"] - j["done"], j["parallel"], (j["estimate"] and json.loads(j["estimate"]).get("per_unit_secs")) if isinstance(j["estimate"], str) else None) \
            if j["status"] in ("running", "queued", "paused") and j["total"] > j["done"] else None
        j["tok_per_s"] = round(j["tokens"] / j["secs"], 1) if j["secs"] else None
        j["percent"] = round(100 * j["done"] / j["total"], 1) if j["total"] else 0
        j["estimate"] = json.loads(j["estimate"]) if j["estimate"] else None
        if log: j["log"] = list(reversed(self.db.q("SELECT ts, idx, text FROM batch_log WHERE job_id=? ORDER BY id DESC LIMIT ?", (jid, log))))
        return j

    def jobs(self): return [self.job(r["id"]) for r in self.db.q("SELECT id FROM batch_jobs ORDER BY created DESC LIMIT 50")]

    def items(self, jid, status=None, offset=0, limit=50):
        w, a = "job_id=?", [jid]
        if status: w += " AND status=?"; a.append(status)
        rows = self.db.q(f"SELECT idx,label,status,attempts,problems,notes,secs,tokens,input,output FROM batch_items WHERE {w} ORDER BY idx LIMIT ? OFFSET ?", (*a, int(limit), int(offset)))
        for r in rows:
            r["input"] = _short(json.loads(r["input"])); r["output"] = _short(json.loads(r["output"])) if r["output"] else None
        return {"items": rows, "count": self.db.q(f"SELECT COUNT(*) n FROM batch_items WHERE {w}", a)[0]["n"]}

    def set_status(self, jid, status, **kw):
        sets = ", ".join(["status=?"] + [f"{k}=?" for k in kw])
        self.db.x(f"UPDATE batch_jobs SET {sets} WHERE id=?", (status, *kw.values(), jid))
    def pause(self, jid):
        j = self.job(jid)
        if not j or j["status"] not in ("queued", "running"): return False
        self.set_status(jid, "paused"); self.log(jid, None, "Paused."); self._abort(jid); return True
    def resume(self, jid):
        j = self.job(jid)
        if not j or j["status"] not in ("paused", "failed"): return False
        self.set_status(jid, "queued", error=""); self.log(jid, None, "Resumed."); self.kick(); return True
    def cancel(self, jid):
        j = self.job(jid)
        if not j or j["status"] in ("done", "cancelled"): return False
        self.set_status(jid, "cancelled", finished=time.time()); self._abort(jid)
        self.log(jid, None, "Cancelled. Finished items are kept and can be downloaded.")
        try: self.build_outputs(jid)
        except Exception: pass
        return True
    def delete(self, jid):
        self.cancel(jid)
        self.db.x("DELETE FROM batch_items WHERE job_id=?", (jid,)); self.db.x("DELETE FROM batch_log WHERE job_id=?", (jid,))
        self.db.x("DELETE FROM batch_jobs WHERE id=?", (jid,)); shutil.rmtree(job_dir(jid), ignore_errors=True)
    def _abort(self, jid):
        for cj, t in list(self.current or []):
            if cj == jid and not t.done(): t.cancel()
    def busy(self): return bool(self.db.q("SELECT 1 FROM batch_jobs WHERE status IN ('running','queued') LIMIT 1"))

    # ---- worker
    def start(self):
        """Server startup: jobs interrupted by a restart continue where they left off."""
        self.db.x("UPDATE batch_items SET status='pending' WHERE status='running'")
        for j in self.db.q("SELECT id FROM batch_jobs WHERE status='running'"):
            self.set_status(j["id"], "queued"); self.log(j["id"], None, "Jiv restarted - resuming where it left off.")
        self.wake = asyncio.Event()
        if not self.task or self.task.done(): self.task = asyncio.create_task(self._worker())
    def kick(self):
        if self.wake: self.wake.set()

    async def _worker(self):
        while True:
            nxt = self.db.q("SELECT id FROM batch_jobs WHERE status IN ('queued','running') ORDER BY created LIMIT 1")
            if not nxt:
                self.wake.clear(); await self.wake.wait(); continue
            try: await self.run_job(nxt[0]["id"])
            except asyncio.CancelledError: raise
            except Exception as e:
                self.set_status(nxt[0]["id"], "failed", error=f"{type(e).__name__}: {e}"[:500]); self.log(nxt[0]["id"], None, f"Job stopped: {e}")

    async def run_job(self, jid):
        j = self.db.q("SELECT * FROM batch_jobs WHERE id=?", (jid,))[0]
        spec, layout = json.loads(j["spec"]), json.loads(j["layout"])
        self.set_status(jid, "running", **({} if j["started"] else {"started": time.time()}))
        P = 1 if spec.get("chain") else max(1, min(2, int(spec.get("parallel") or 1)))   # 1.5: up to 2 items at once
        errors = 0; running = {}
        self.current = []
        def reset(it): self.db.x("UPDATE batch_items SET status='pending' WHERE job_id=? AND idx=? AND status='running'", (jid, it["idx"]))
        async def stop_all():
            for t in running: t.cancel()
            if running: await asyncio.wait(set(running))
            for it in running.values(): reset(it)
            running.clear(); self.current = []
        try:
            while True:
                st = self.db.q("SELECT status FROM batch_jobs WHERE id=?", (jid,))
                if not st or st[0]["status"] != "running": await stop_all(); return
                while len(running) < P:
                    row = self.db.q("SELECT * FROM batch_items WHERE job_id=? AND status='pending' ORDER BY idx LIMIT 1", (jid,))
                    if not row: break
                    it = row[0]
                    self.db.x("UPDATE batch_items SET status='running', updated=? WHERE job_id=? AND idx=?", (time.time(), jid, it["idx"]))
                    t = asyncio.create_task(self.process_item(jid, spec, layout, it, j["total"]))
                    running[t] = it; self.current.append((jid, t))
                if not running:
                    f = self.db.q("SELECT COUNT(*) n FROM batch_items WHERE job_id=? AND status='flagged'", (jid,))[0]["n"]
                    self.set_status(jid, "done", finished=time.time())
                    try: await asyncio.to_thread(self.build_outputs, jid)
                    except Exception as e: self.log(jid, None, f"Could not write the output files: {e}")
                    self.log(jid, None, f"Finished: {j['total']} item(s), {f} flagged for review."); return
                done, _ = await asyncio.wait(set(running), return_when=asyncio.FIRST_COMPLETED)
                for task in sorted(done, key=lambda t: running[t]["idx"]):
                    it = running.pop(task); self.current = [c for c in self.current if c[1] is not task]
                    if task.cancelled():   # paused or cancelled while this item was running: it will be redone
                        reset(it); continue
                    if task.exception():
                        e = task.exception(); errors += 1
                        self.db.x("UPDATE batch_items SET status='pending', problems=? WHERE job_id=? AND idx=?", (f"{type(e).__name__}: {e}"[:500], jid, it["idx"]))
                        self.log(jid, it["idx"], f"Model error: {e}")
                        if errors >= 3:
                            await stop_all()
                            self.set_status(jid, "failed", error=f"The model server failed 3 times in a row: {e}"[:500])
                            self.log(jid, None, "Stopped after 3 errors in a row. Fix the model connection, then press Resume."); return
                        await asyncio.sleep(min(20, 2 * errors)); continue
                    errors = 0; res = task.result()
                    status = "flagged" if res["problems"] else "ok"
                    self.db.x("UPDATE batch_items SET status=?, output=?, attempts=?, problems=?, notes=?, secs=?, tokens=?, updated=? WHERE job_id=? AND idx=?",
                              (status, json.dumps(res["output"], ensure_ascii=False) if res["output"] is not None else None, res["attempts"], "; ".join(res["problems"]),
                               "; ".join(res["notes"]), res["secs"], res["tokens"], time.time(), jid, it["idx"]))
                    self.db.x("UPDATE batch_jobs SET done=done+1, flagged=flagged+?, tokens=tokens+?, secs=secs+? WHERE id=?",
                              (int(status == "flagged"), res["tokens"], res["gen_secs"], jid))
                    self.log(jid, it["idx"], f"{it['label'][:70]}: " + (("⚠ flagged - " + "; ".join(res["problems"])) if res["problems"] else "✓ ok") +
                             f" ({res['attempts']} attempt{'s' if res['attempts'] != 1 else ''}, {res['secs']:.1f}s" + (", " + "; ".join(res["notes"]) if res["notes"] else "") + ")")
        except asyncio.CancelledError:
            for t in running: t.cancel()
            for it in running.values(): reset(it)
            raise
        finally: self.current = []

    async def process_item(self, jid, spec, layout, it, total):
        inp = json.loads(it["input"]); i = it["idx"] + 1; prev = ""
        if spec.get("chain") and it["idx"] > 0:
            p = self.db.q("SELECT output FROM batch_items WHERE job_id=? AND idx=?", (jid, it["idx"] - 1))
            if p and p[0]["output"]:
                prev = json.loads(p[0]["output"]); prev = prev if isinstance(prev, str) else json.dumps(prev, ensure_ascii=False)
        t0 = time.time(); outs, probs, notes, tokens, gen, attempts = [], [], [], 0, 0.0, 0
        us = units_of(inp, spec)
        for n, unit in enumerate(us):
            r = await self.process_unit(spec, layout, unit, inp, i, total, prev, jid, it["idx"])
            outs.append(r["output"]); tokens += r["tokens"]; gen += r["secs"]; attempts = max(attempts, r["attempts"]); notes += r["notes"]
            probs += [(f"part {n + 1}/{len(us)}: " if len(us) > 1 else "") + p for p in r["problems"]]
        if inp["type"] == "html":
            merged = {}
            for o in outs: merged.update(o or {})
            output = {"segs": merged}
        elif inp["type"] == "repeat" and len(outs) == 1: output = outs[0]
        elif inp["type"] in ("text", "repeat"):
            output = "".join(o if o is not None else u.get("text", "") for o, u in zip(outs, us)) if any(o is not None for o in outs) else None
        else: output = outs[0] if outs else None
        return {"output": output, "problems": probs, "notes": notes, "tokens": tokens, "gen_secs": gen, "secs": time.time() - t0, "attempts": attempts}

    async def process_unit(self, spec, layout, unit, inp, i, total, prev, jid=None, idx=None, dry=False):
        user, item_txt = unit_prompt(spec, unit, inp, i, total, prev)
        req = None
        if unit["kind"] == "record" and isinstance(inp["data"], dict):
            keys = list(inp["data"].keys()); req = keys + [a for a in spec.get("add_fields", []) if a not in keys]
            sysm = SYS_RECORD.format(keys=", ".join(json.dumps(k, ensure_ascii=False) for k in req))
        elif unit["kind"] == "record": sysm = SYS_TEXT.replace("resulting text", "resulting JSON value")
        elif unit["kind"] == "html": sysm = SYS_HTML
        else: sysm = SYS_TEXT
        json_mode = unit["kind"] in ("record", "html")
        max_tok = int(self.cfg.get("batch_max_tokens", 0) or min(3000, max(400, int((len(item_txt) // 3 + 60) * 1.6) + 200)))
        tokens = secs = 0; attempts = 0; feedback = ""; last_ok = None; out = None; problems, notes, text = [], [], ""
        for attempt in range(1 + int(spec.get("retries", 2))):
            attempts = attempt + 1
            text, u = await self.model.complete(spec, [{"role": "system", "content": sysm}, {"role": "user", "content": user + feedback}], json_mode, max_tok)
            tokens += u["out"]; secs += u["secs"]
            out, problems, notes = self.check(spec, layout, unit, inp, text, req)
            if out is not None and not problems: last_ok = out
            elif out is not None and last_ok is None: last_ok = out
            if not problems and spec.get("verify"):
                vp, vt, vs = await self.verify(spec, unit, inp, out, item_txt)
                tokens += vt; secs += vs; problems = problems + vp
                if dry: notes.append("verify: " + ("passed" if not vp else "; ".join(vp)))
            if not problems: break
            if dry: break
            if jid and attempt < int(spec.get("retries", 2)): self.log(jid, idx, f"Attempt {attempts} needs another try: {'; '.join(problems)[:300]}")
            feedback = (f"\n\nYour previous answer had these problems: {'; '.join(problems)}.\n" +
                        (f"Previous answer:\n{text.strip()[:1500]}\n" if text.strip() else "") + "Fix them and reply again with only the " + ("JSON." if json_mode else "text."))
        output = out if (not problems and out is not None) else (out if out is not None else last_ok)
        if unit["kind"] == "text" and output is not None: output = unit.get("lead", "") + output + unit.get("trail", "")
        return {"output": output, "problems": problems, "notes": notes, "tokens": tokens, "secs": secs, "attempts": attempts, "raw": text}

    def check(self, spec, layout, unit, inp, text, req):
        """-> (parsed output or None, problems, notes)"""
        rc = spec.get("require_change", True)
        if unit["kind"] == "record":
            if not isinstance(inp["data"], dict):
                v = parse_json(text)
                return (v, [], []) if v is not None else (None, ["reply was not valid JSON"], [])
            v = parse_json(text)
            if isinstance(v, list) and len(v) == 1 and isinstance(v[0], dict): v = v[0]
            if not isinstance(v, dict): return None, ["reply was not a JSON object"], []
            lower = {str(k).lower(): k for k in v}
            v = {k: (v[k] if k in v else v[lower[k.lower()]]) for k in req if k in v or k.lower() in lower}
            miss = [k for k in req if k not in v]
            probs, notes = ([f"missing field(s): {', '.join(miss)}"] if miss else []), []
            for k, x in inp["data"].items():
                if k not in v: continue
                if isinstance(x, list) != isinstance(v[k], list) or isinstance(x, dict) != isinstance(v[k], dict): probs.append(f"field '{k}' changed type")
                elif isinstance(x, str) and isinstance(v[k], (int, float)) and not isinstance(v[k], bool): v[k] = str(v[k])
            sch = spec.get("schema")
            if sch and not miss:
                s = auto_mcq_schema(inp["data"], spec.get("add_fields", [])) if sch == "auto-mcq" else json.loads(sch)
                probs += ["schema: " + e for e in validate_schema(v, s)[:5]]
            if spec.get("mcq") and mcq_fields(inp["data"]):
                p2, n2, _ = mcq_check(inp["data"], v, layout.get("zero_based", False), rc)
                probs += [p for p in p2 if p not in probs]; notes += n2
            elif rc and similarity(json.dumps(v, sort_keys=True, ensure_ascii=False), json.dumps(inp["data"], sort_keys=True, ensure_ascii=False)) > 0.97:
                probs.append("output is the same as the input")
            return v, probs, notes
        if unit["kind"] == "html":
            v = parse_json(text)
            if not isinstance(v, dict): return None, ["reply was not a JSON object of segments"], []
            keys = list(unit["segs"].keys()); out, missing = {}, []
            for n, k in enumerate(keys):
                x = v.get(str(n + 1))
                if not isinstance(x, str) or not x.strip(): missing.append(str(n + 1)); continue
                out[k] = re.sub(r"</?[a-zA-Z][^>]*>", "", x).strip()
            probs = [f"segment(s) missing: {', '.join(missing[:10])}"] if missing else []
            if rc and out and similarity(" ".join(out.values()), " ".join(unit["segs"][k] for k in out)) > 0.9:
                probs.append("text was not really rewritten (too close to the original)")
            return (out or None), probs, []
        t = clean_text(text)
        if not t: return None, ["empty reply"], []
        probs = []
        if unit["kind"] == "text" and rc and similarity(t, unit["text"]) > 0.9: probs.append("text is too close to the original")
        if unit["kind"] == "repeat" and spec.get("schema") and spec["schema"] != "auto-mcq":
            v = parse_json(t)
            if isinstance(v, list) and len(v) == 1 and isinstance(v[0], dict): v = v[0]
            if v is None: return None, ["reply was not valid JSON"], []
            probs += ["schema: " + e for e in validate_schema(v, json.loads(spec["schema"]))[:5]]
            if spec.get("mcq") and isinstance(v, dict):
                p2, n2, _ = mcq_check({}, v, False, False)
                probs += [p for p in p2 if p not in probs]
            return v, probs, []
        return t, probs, []

    async def verify(self, spec, unit, inp, out, item_txt):
        is_mcq = bool(spec.get("mcq") and ((unit["kind"] == "record" and mcq_fields(inp["data"])) or (unit["kind"] == "repeat" and mcq_fields(out))))
        fmt = ('{"correct_option": "<letter of the option you think is correct>", "ok": true or false, "problems": "<short reason, empty if ok>"}' if is_mcq
               else '{"ok": true or false, "problems": "<short reason, empty if ok>"}')
        if unit["kind"] == "html": o = json.dumps({str(n + 1): out.get(k, "") for n, k in enumerate(unit["segs"])}, ensure_ascii=False, indent=1)
        elif isinstance(out, (dict, list)): o = json.dumps(out, ensure_ascii=False, indent=1)
        else: o = str(out)
        tpl = spec.get("verify_prompt") or "ORIGINAL:\n{original}\n\nOUTPUT:\n{output}\n\nTASK THAT WAS GIVEN:\n{task}\n\nDoes the output follow the task correctly and completely?"
        user = render(tpl, original=item_txt or "(none)", output=o, item=item_txt, topic=spec.get("topic") or "", task=(spec.get("prompt") or "")[:800])
        text, u = await self.model.complete(spec, [{"role": "system", "content": SYS_VERIFY.format(fmt=fmt)}, {"role": "user", "content": user}], True, 300)
        v = parse_json(text); probs = []
        if not isinstance(v, dict): return ["verify pass gave no clear answer"], u["out"], u["secs"]
        ok = v.get("ok"); ok = ok if isinstance(ok, bool) else str(ok).strip().lower() in ("true", "yes", "1", "pass")
        if not ok: probs.append("verify: " + (str(v.get("problems") or "the reviewer rejected it").strip()[:300]))
        if is_mcq and v.get("correct_option") not in (None, ""):
            f = mcq_fields(out); opts = options_of(out, f)
            want, have = resolve_answer(v.get("correct_option"), opts), resolve_answer(out.get(f["a"]), opts)
            if want is not None and have is not None and want != have:
                probs.append(f"verify: the reviewer thinks the answer is {opts[want][0]} ({opts[want][1][:40]}), but the key says {opts[have][0]}")
        return probs, u["out"], u["secs"]

    async def estimate(self, spec):
        """Run the first selected item once (rewrite + verify, no retries) and extrapolate from the measured speed."""
        s, items, layout, info = self.plan(spec)
        if not items: raise ValueError("No items to process.")
        inp = items[0]["input"]; us = units_of(inp, s)
        if not us: raise ValueError("The first item has no text to rewrite.")
        w0 = time.time()
        try: await self.model.complete(s, [{"role": "user", "content": "Reply with: OK"}], max_tokens=4)   # 1.5: load the model first, so its load time isn't counted
        except Exception: pass
        warm = time.time() - w0
        t0 = time.time()
        r = await self.process_unit(s, layout, us[0], inp, items[0]["orig_index"] + 1, len(items), "", dry=True)
        wall = time.time() - t0
        n = min(len(items), 2000)
        units = sum(len(units_of(x["input"], s)) for x in items[:n]) * (len(items) / n)
        out = r["output"]
        return {"seconds_test": round(wall, 1), "tokens_out": r["tokens"], "tok_per_s": round(r["tokens"] / r["secs"], 1) if r["secs"] else None,
                "units": round(units), "items": len(items), "eta_secs": round(wall * units * (1.15 if s["verify"] else 1.05)), "per_unit_secs": round(wall, 2),
                "warmup_secs": round(warm, 1), "parallel": 1 if s.get("chain") else s.get("parallel", 1), "problems": r["problems"], "notes": r["notes"],
                "sample_output": (json.dumps(out, ensure_ascii=False, indent=1) if isinstance(out, (dict, list)) else str(out if out is not None else r["raw"]))[:1500],
                "model": self.model.conn(s)[2], "verify": s["verify"],
                "basis": "1 test item after loading the model" + (" (rewrite + verify)" if s["verify"] else "") + ("; +15% allowance for retries" if s["verify"] else "; +5% allowance for retries")
                         + ("; 2 at once can be faster only if Ollama runs 2 requests in parallel (OLLAMA_NUM_PARALLEL=2) and there is spare VRAM - the live ETA updates from measured speed" if s.get("parallel", 1) > 1 else "")}

    # ---- outputs
    def build_outputs(self, jid):
        j = self.db.q("SELECT * FROM batch_jobs WHERE id=?", (jid,))[0]
        spec, lay = json.loads(j["spec"]), json.loads(j["layout"])
        rows = self.db.q("SELECT idx,label,input,output,status,problems,notes,attempts FROM batch_items WHERE job_id=? ORDER BY idx", (jid,))
        out_dir = job_dir(jid) / "output"; out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.iterdir():
            if p.is_file(): p.unlink()
        res = {"result": self._result_file(spec, lay, rows, out_dir)}
        res.update(self._review_files(rows, out_dir))
        return res

    def _result_file(self, spec, lay, rows, out_dir):
        if spec["mode"] == "repeat" and spec.get("mcq"):
            p = out_dir / result_name(spec, lay)
            p.write_text(json.dumps([json.loads(r["output"]) for r in rows if r["output"]], ensure_ascii=False, indent=1), "utf-8")
            return p.name
        if spec["mode"] == "repeat":
            p = out_dir / "results.md"
            p.write_text("\n\n".join(f"## Run {r['idx'] + 1}\n\n" + (json.loads(r["output"]) if r["output"] else "_(not done)_")
                                     for r in rows if r["output"] or spec.get("include_unprocessed")) + "\n", "utf-8")
            return p.name
        split, d, incl = lay["split"], Path(lay["dir"]), spec.get("include_unprocessed")
        done = []   # (input, output or None) for processed items, in order
        for r in rows:
            if r["status"] in ("ok", "flagged", "error"):
                done.append((json.loads(r["input"]), json.loads(r["output"]) if r["output"] else None))
        if split in ("html_pages", "html_sections"):
            by_file = {}
            for inp, o in done:
                if inp["type"] == "html" and o: by_file.setdefault(inp["file"], {}).update(o["segs"])
                elif inp["type"] == "text" and o is not None: by_file.setdefault(inp["file"], {})["__text__"] = o
            def page(rel):
                new = by_file.get(rel, {})
                if "__text__" in new: return new["__text__"]
                parts, _, _ = html_parts(_read_text(d / rel))
                for k, v in new.items():
                    old = parts[int(k)]; lead = old[:len(old) - len(old.lstrip())]; trail = old[len(old.rstrip()):]
                    parts[int(k)] = lead + H.escape(v, quote=False) + trail
                return "".join(parts)
            files = lay["files"]
            if lay.get("kind") == "file" and len(files) == 1:
                p = out_dir / (Path(files[0]).stem + "_rewritten" + Path(files[0]).suffix); p.write_text(page(files[0]), "utf-8"); return p.name
            p = out_dir / (re.sub(r"\.zip$", "", Path(lay["label"]).name, flags=re.I) + "_rewritten.zip")
            with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
                for rel in files:
                    if rel in by_file: z.writestr(rel, page(rel))
                    else: z.write(d / rel, rel)   # untouched pages, images, CSS, JS ... are copied as they are
            return p.name
        rel = lay["files"][0]; src = d / rel; stem, ext = Path(rel).stem, Path(rel).suffix
        p = out_dir / f"{stem}_rewritten{ext or '.txt'}"
        val = lambda inp, o: o if o is not None else (inp["data"] if inp["type"] == "record" else inp["text"])
        bypos = {inp["pos"]: val(inp, o) for inp, o in done}
        if split == "json":
            j = json.loads(_read_text(src)); lst = j if lay.get("wrapper") is None else j[lay["wrapper"]]
            new = [bypos.get(k, x) for k, x in enumerate(lst)] if incl else [bypos[k] for k in sorted(bypos)]
            p.write_text(json.dumps(new if lay.get("wrapper") is None else {**j, lay["wrapper"]: new}, ensure_ascii=False, indent=2) + "\n", "utf-8")
            return p.name
        if split == "jsonl":
            lines = _read_text(src).splitlines()
            new = [json.dumps(bypos[k], ensure_ascii=False) if k in bypos else l for k, l in enumerate(lines) if l.strip() and (incl or k in bypos)]
            p.write_text("\n".join(new) + "\n", "utf-8"); return p.name
        if split == "csv":
            fields = list(lay.get("fields") or []); fields += [a for a in spec.get("add_fields", []) if a not in fields]
            rd = list(csv.DictReader(io.StringIO(_read_text(src)), delimiter=lay.get("delimiter", ",")))
            src_rows = [bypos.get(k, r) for k, r in enumerate(rd)] if incl else [bypos[k] for k in sorted(bypos)]
            buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=fields, delimiter=lay.get("delimiter", ","), extrasaction="ignore", lineterminator="\r\n")
            w.writeheader()
            for r in src_rows: w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in r.items() if k in fields})
            p.write_text(buf.getvalue(), "utf-8-sig"); return p.name
        txt = _read_text(src)
        if split == "whole":
            p.write_text(done[0][1] if done and done[0][1] is not None else txt, "utf-8"); return p.name
        parts = split_text(txt, split)
        if incl or split != "lines": new = "".join(bypos.get(k, s) for k, s in enumerate(parts) if incl or k in bypos or not s.strip() or split != "lines")
        else: new = "\n".join(bypos[k].strip("\r\n") for k in sorted(bypos)) + "\n"
        p.write_text(new, "utf-8"); return p.name

    def _review_files(self, rows, out_dir):
        table = []
        for r in rows:
            inp = json.loads(r["input"]); o = json.loads(r["output"]) if r["output"] else None
            table.append([r["idx"] + 1, r["label"], r["status"], r["problems"], r["notes"], r["attempts"], show(inp), show(o, inp)])
        head = ["#", "Item", "Status", "Problems", "Notes", "Attempts", "Original", "New"]
        flagged = [t for t in table if t[2] in ("flagged", "error") or "answer key changed" in (t[4] or "")]
        cp = out_dir / "review_flagged.csv"
        with open(cp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f); w.writerow(head); w.writerows(flagged)
        res = {"review_csv": cp.name}
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Font, PatternFill
        except ImportError: return res
        wb = Workbook(); first = True
        for title, data in (("Flagged for review", flagged), ("All items", table)):
            sh = wb.active if first else wb.create_sheet(); sh.title = title; first = False
            sh.append(head)
            for c in sh[1]: c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="5B4BC4")
            for t in data: sh.append([_xl(x) for x in t])
            for col, wdt in zip("ABCDEFGH", (6, 30, 10, 40, 28, 9, 60, 60)): sh.column_dimensions[col].width = wdt
            for row in sh.iter_rows(min_row=2):
                for c in row: c.alignment = Alignment(wrap_text=True, vertical="top")
                if row[2].value == "flagged": row[2].fill = PatternFill("solid", fgColor="FDE68A")
            sh.freeze_panes = "C2"; sh.auto_filter.ref = sh.dimensions
        xp = out_dir / "review.xlsx"; wb.save(xp); res["review_xlsx"] = xp.name
        return res

def show(v, inp=None):
    """Readable text for the review sheet (original vs new, side by side)."""
    if v is None: return ""
    if inp is not None and inp.get("type") == "html" and isinstance(v, dict):
        return "\n".join(f"[{n + 1}] {v.get('segs', {}).get(k, '(unchanged)')}" for n, k in enumerate(inp["segs"]))
    if isinstance(v, dict) and v.get("type") == "html": return "\n".join(f"[{n + 1}] {' '.join(H.unescape(t).split())}" for n, t in enumerate(v["segs"].values()))
    if isinstance(v, dict) and v.get("type") == "record": v = v["data"]
    elif isinstance(v, dict) and v.get("type") == "text": return v["text"].strip()
    elif isinstance(v, dict) and v.get("type") == "repeat": return f"(run {v['i']})"
    if isinstance(v, dict): return "\n".join(f"{k}: {json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x}" for k, x in v.items())
    return str(v)

def _xl(v):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", v)[:32000] if isinstance(v, str) else v

def _short(v):
    return (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))[:800]

def result_name(spec, lay):
    if spec.get("mode") == "repeat" and spec.get("mcq"): return "questions.json"
    if spec.get("mode") == "repeat": return "results.md"
    files = lay.get("files") or ["result.txt"]
    if lay.get("split") in ("html_pages", "html_sections") and not (lay.get("kind") == "file" and len(files) == 1):
        return re.sub(r"\.zip$", "", Path(lay.get("label") or "pages").name, flags=re.I) + "_rewritten.zip"
    return f"{Path(files[0]).stem}_rewritten{Path(files[0]).suffix or '.txt'}"

def output_files(jid):
    d = job_dir(jid) / "output"
    return sorted(p.name for p in d.iterdir() if p.is_file()) if d.is_dir() else []

def summary(pv):
    """Plain description of a planned job for permission cards."""
    if pv["mode"] == "repeat": what = f"Run the prompt {pv['selected']} time(s)" + (f" (topic: {pv['topic']})" if pv.get("topic") else "")
    else: what = (f"Run the prompt on {pv['selected']} item(s) from {pv['label']} (split: {pv['split']}; items {pv['first_item']}–"
                  f"{pv['first_item'] + pv['selected'] - 1} of {pv['total_items']})")
    v = " + a verify pass on each, retrying failed items up to 2 times" if pv["verify"] else ""
    p = pv["prompt"].strip()
    return f"{what}{v}, model {pv['model']}.\nOutput folder: {pv['output_dir']}\nPrompt: {p[:400]}{'…' if len(p) > 400 else ''}"
