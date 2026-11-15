"""Jiv 1.6.1 web search + grounded summariser.

No API keys required. Backends (in order): DuckDuckGo HTML, DuckDuckGo lite,
optional SearXNG URL from Settings, Wikipedia API for factual lookups.
Fetch / extract / rank / cache / cite are all pure Python (httpx + stdlib +
optional pypdf for PDFs). Extractive summaries work with no AI.
"""
from __future__ import annotations
import asyncio, hashlib, html, json, math, os, re, time
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse, urlunparse
import httpx

from .config import data_dir
from . import english as EN

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
TRACKER_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url", "yclid",
    "igshid", "si", "spm", "ved", "ei", "source", "feature",
}
QUALITY = {
    "wikipedia.org": 3.0, "en.wikipedia.org": 3.0, "britannica.com": 2.5,
    "nih.gov": 2.8, "who.int": 2.6, "cdc.gov": 2.6, "gov": 2.2, "edu": 2.0,
    "arxiv.org": 2.4, "nature.com": 2.3, "sciencedirect.com": 2.0,
    "mozilla.org": 2.0, "python.org": 2.0, "docs.python.org": 2.2,
    "developer.mozilla.org": 2.2, "stackoverflow.com": 1.6, "github.com": 1.7,
    "bbc.com": 1.8, "bbc.co.uk": 1.8, "reuters.com": 1.9, "apnews.com": 1.9,
    "nytimes.com": 1.6, "theguardian.com": 1.6,
}
STOP = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "as",
    "is", "are", "was", "were", "be", "been", "being", "with", "by", "from", "that",
    "this", "these", "those", "it", "its", "into", "about", "over", "after", "before",
    "between", "out", "up", "down", "than", "then", "so", "if", "not", "no", "yes",
    "can", "could", "should", "would", "will", "just", "also", "more", "most", "such",
    "what", "when", "where", "which", "who", "how", "why", "do", "does", "did", "have",
    "has", "had", "you", "your", "we", "our", "they", "their", "i", "me", "my",
}

class SearchError(ValueError):
    pass

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def cache_dir() -> Path:
    p = data_dir() / "search_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p

def _ckey(*parts) -> str:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h

def cache_get(kind: str, key: str, ttl: int):
    path = cache_dir() / f"{kind}-{_ckey(kind, key)}.json"
    if not path.is_file():
        return None
    try:
        row = json.loads(path.read_text("utf-8"))
    except Exception:
        return None
    if time.time() - float(row.get("ts") or 0) > max(0, int(ttl)):
        try:
            path.unlink()
        except Exception:
            pass
        return None
    return row.get("data")

def cache_put(kind: str, key: str, data):
    path = cache_dir() / f"{kind}-{_ckey(kind, key)}.json"
    path.write_text(json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False), "utf-8")
    # opportunistic prune: keep newest ~400 files
    try:
        files = sorted(cache_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[400:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass

def cache_clear():
    n = 0
    for p in cache_dir().glob("*.json"):
        try:
            p.unlink(); n += 1
        except Exception:
            pass
    return n

# ---------------------------------------------------------------------------
# URL hygiene
# ---------------------------------------------------------------------------
def clean_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    # DuckDuckGo / Google redirect wrappers
    try:
        p = urlparse(u)
        qs = parse_qs(p.query)
        for key in ("uddg", "q", "url", "u", "target"):
            if key in qs and qs[key] and qs[key][0].startswith("http"):
                u = unquote(qs[key][0]); break
        if "duckduckgo.com" in (p.netloc or "") and "/l/?" in u:
            qs2 = parse_qs(urlparse(u).query)
            if "uddg" in qs2:
                u = unquote(qs2["uddg"][0])
    except Exception:
        pass
    try:
        p = urlparse(u)
        if p.scheme not in ("http", "https"):
            return ""
        q = [(k, v) for k, vs in parse_qs(p.query, keep_blank_values=True).items()
             for v in vs if k.lower() not in TRACKER_KEYS]
        # stable order
        q.sort()
        query = "&".join(f"{quote_plus(k)}={quote_plus(v)}" for k, v in q) if q else ""
        host = (p.hostname or "").lower()
        path = p.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        return urlunparse((p.scheme.lower(), host, path, "", query, ""))
    except Exception:
        return u

def domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""

def source_quality(url: str) -> float:
    host = domain_of(url)
    if not host:
        return 1.0
    if host in QUALITY:
        return QUALITY[host]
    parts = host.split(".")
    # TLD / SLD heuristics
    if parts[-1] in ("gov", "edu", "mil"):
        return QUALITY.get(parts[-1], 2.0)
    if len(parts) >= 2 and parts[-2] in ("gov", "edu"):
        return 2.2
    for k, v in QUALITY.items():
        if host.endswith("." + k) or host == k:
            return v
    return 1.0

# ---------------------------------------------------------------------------
# Token / ranking helpers (no heavy deps)
# ---------------------------------------------------------------------------
def tokenize(text: str):
    return [t for t in re.findall(r"[a-z0-9]{2,}", (text or "").lower()) if t not in STOP]

def term_overlap(query: str, text: str) -> float:
    q = set(tokenize(query))
    if not q:
        return 0.0
    t = set(tokenize(text))
    return len(q & t) / max(1, len(q))

def rank_score(query: str, title: str, snippet: str, url: str) -> float:
    body = f"{title} {snippet} {domain_of(url)}"
    return 2.2 * term_overlap(query, title) + 1.4 * term_overlap(query, snippet) \
        + 0.6 * term_overlap(query, body) + 0.35 * source_quality(url)

# ---------------------------------------------------------------------------
# Result parsing (pure, fixture-friendly)
# ---------------------------------------------------------------------------
def _text_html(h: str) -> str:
    h = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", h or "")
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()

def parse_ddg_html(page: str, n: int = 8):
    """Parse DuckDuckGo HTML/lite result pages into [{title,url,snippet,source}]."""
    res = []
    # Classic HTML theme
    for m in re.finditer(
            r'(?s)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
            page or ""):
        href, title, rest = m.groups()
        href = clean_url(href)
        if not href:
            continue
        sn = re.search(r'(?s)class="result__snippet"[^>]*>(.*?)</(?:a|td|div)', rest)
        res.append({"title": _text_html(title), "url": href,
                    "snippet": _text_html(sn.group(1)) if sn else "", "source": "duckduckgo"})
        if len(res) >= n:
            break
    if res:
        return res
    # Lite theme: <a rel="nofollow" href="...">title</a> plus nearby snippet in <td>
    for m in re.finditer(r'(?s)<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page or ""):
        href, title = m.groups()
        href = clean_url(href)
        if not href or "duckduckgo.com" in domain_of(href):
            continue
        # snippet: next <td class="result-snippet"> or plain text cell
        tail = (page or "")[m.end():m.end() + 600]
        sn = re.search(r'(?s)class="result-snippet"[^>]*>(.*?)</td>', tail) \
            or re.search(r'(?s)<td[^>]*>(.*?)</td>', tail)
        res.append({"title": _text_html(title), "url": href,
                    "snippet": _text_html(sn.group(1)) if sn else "", "source": "duckduckgo_lite"})
        if len(res) >= n:
            break
    return res

def parse_searx(page: str, n: int = 8):
    """Parse SearXNG HTML or JSON."""
    page = page or ""
    if page.lstrip().startswith("{") or page.lstrip().startswith("["):
        try:
            data = json.loads(page)
            rows = data.get("results") if isinstance(data, dict) else data
            out = []
            for r in rows or []:
                url = clean_url(r.get("url") or r.get("link") or "")
                if not url:
                    continue
                out.append({"title": (r.get("title") or "").strip(), "url": url,
                            "snippet": (r.get("content") or r.get("snippet") or "").strip(),
                            "source": "searx"})
                if len(out) >= n:
                    break
            return out
        except Exception:
            pass
    out = []
    for m in re.finditer(r'(?s)<article[^>]*class="[^"]*result[^"]*"[^>]*>(.*?)</article>', page):
        block = m.group(1)
        a = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block)
        if not a:
            continue
        url = clean_url(a.group(1))
        if not url:
            continue
        sn = re.search(r'(?s)class="[^"]*content[^"]*"[^>]*>(.*?)</p>', block)
        out.append({"title": _text_html(a.group(2)), "url": url,
                    "snippet": _text_html(sn.group(1)) if sn else "", "source": "searx"})
        if len(out) >= n:
            break
    return out

def parse_wikipedia_search(data: dict, n: int = 5):
    out = []
    try:
        pages = (data or {}).get("query", {}).get("search") or []
    except Exception:
        pages = []
    for p in pages[:n]:
        title = p.get("title") or ""
        if not title:
            continue
        url = "https://en.wikipedia.org/wiki/" + quote_plus(title.replace(" ", "_"))
        sn = _text_html(p.get("snippet") or "")
        out.append({"title": title, "url": clean_url(url), "snippet": sn, "source": "wikipedia"})
    return out

def dedupe_results(rows):
    """Dedupe by cleaned URL, then by domain keeping the higher-scoring row later after rank."""
    seen_url, out = set(), []
    for r in rows or []:
        url = clean_url(r.get("url") or "")
        if not url or url in seen_url:
            continue
        seen_url.add(url)
        rr = dict(r); rr["url"] = url
        out.append(rr)
    return out

def rank_results(query: str, rows):
    scored = []
    for r in rows or []:
        s = rank_score(query, r.get("title") or "", r.get("snippet") or "", r.get("url") or "")
        rr = dict(r); rr["score"] = round(s, 4)
        scored.append(rr)
    scored.sort(key=lambda x: (-x["score"], x.get("title") or ""))
    # soft domain diversity: keep at most 2 from the same domain in the top half
    seen_dom, diversified = Counter(), []
    for r in scored:
        d = domain_of(r["url"])
        if seen_dom[d] >= 2 and len(diversified) < max(4, len(scored) // 2):
            continue
        seen_dom[d] += 1
        diversified.append(r)
    # append leftovers
    have = {r["url"] for r in diversified}
    for r in scored:
        if r["url"] not in have:
            diversified.append(r)
    return diversified

# ---------------------------------------------------------------------------
# Readable extraction
# ---------------------------------------------------------------------------
_BLOCK_RE = re.compile(
    r"(?is)<(script|style|noscript|svg|iframe|nav|footer|header|aside|form|button)[^>]*>.*?</\1>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")

def extract_readable(html_text: str, url: str = "") -> dict:
    """Drop chrome (nav/ads/scripts); keep headings, paragraphs, list items, table cells."""
    raw = html_text or ""
    title = ""
    mt = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    if mt:
        title = _text_html(mt.group(1))
    og = re.search(r'(?is)<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', raw)
    if og and not title:
        title = html.unescape(og.group(1)).strip()

    cleaned = _BLOCK_RE.sub(" ", raw)
    # Prefer <article> / <main> / role=main
    body = None
    for pat in (r"(?is)<article\b[^>]*>(.*?)</article>",
                r"(?is)<main\b[^>]*>(.*?)</main>",
                r'(?is)<div[^>]+role=["\']main["\'][^>]*>(.*?)</div>',
                r"(?is)<body\b[^>]*>(.*?)</body>"):
        m = re.search(pat, cleaned)
        if m and len(m.group(1)) > 200:
            body = m.group(1); break
    if body is None:
        body = cleaned

    # Pull structured blocks
    blocks = []
    for m in re.finditer(
            r"(?is)<(h[1-6]|p|li|blockquote|pre|td|th)(?:\s[^>]*)?>(.*?)</\1>", body):
        tag, inner = m.group(1).lower(), m.group(2)
        text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html.unescape(inner))).strip()
        text = re.sub(r"\s+\n", "\n", text)
        if len(text) < 2:
            continue
        # Keep headings and table cells even when short; drop other tiny crumbs.
        if len(text) < 25 and not (tag.startswith("h") or tag in ("td", "th", "li")):
            continue
        # skip common chrome leftovers
        low = text.lower()
        if low in ("skip to content", "menu", "search", "sign in", "log in", "cookie", "accept all"):
            continue
        if tag.startswith("h"):
            blocks.append(("\n" if blocks else "") + text)
        else:
            blocks.append(text)

    if not blocks:
        # fallback: whole-page text
        text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html.unescape(body))).strip()
        text = re.sub(r"\s{2,}", " ", text)
    else:
        # de-dupe consecutive identical lines
        out, prev = [], None
        for b in blocks:
            if b == prev:
                continue
            out.append(b); prev = b
        text = "\n\n".join(out)

    text = text.strip()
    return {"title": title or domain_of(url) or "page", "url": url, "text": text, "chars": len(text)}

def extract_pdf_bytes(data: bytes, url: str = "") -> dict:
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages[:40]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                pass
        text = "\n\n".join(p.strip() for p in parts if p and p.strip())
        title = (reader.metadata.title if reader.metadata else None) or domain_of(url) or "pdf"
        return {"title": str(title), "url": url, "text": text, "chars": len(text)}
    except Exception as e:
        return {"title": domain_of(url) or "pdf", "url": url, "text": "", "chars": 0, "error": str(e)}

# ---------------------------------------------------------------------------
# Chunking + extractive summary (no AI)
# ---------------------------------------------------------------------------
def split_sentences(text: str):
    """Delegate to english.split_sentences (abbreviations, decimals, lists)."""
    return EN.split_sentences(text)

def chunk_text(text: str, size: int = 900, overlap: int = 120):
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks, i = [], 0
    while i < len(text):
        chunks.append(text[i:i + size])
        i += max(1, size - overlap)
    return chunks

def _tf(tokens):
    c = Counter(tokens); n = max(1, len(tokens))
    return {t: v / n for t, v in c.items()}

def bm25_scores(query_tokens, docs_tokens, k1=1.4, b=0.75):
    N = max(1, len(docs_tokens))
    df = Counter()
    for d in docs_tokens:
        for t in set(d):
            df[t] += 1
    avgdl = sum(len(d) for d in docs_tokens) / N
    scores = []
    for d in docs_tokens:
        tf = Counter(d); dl = len(d) or 1
        s = 0.0
        for t in query_tokens:
            if t not in tf:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            denom = tf[t] + k1 * (1 - b + b * dl / max(1.0, avgdl))
            s += idf * (tf[t] * (k1 + 1)) / denom
        scores.append(s)
    return scores

def select_chunks(query: str, chunks, k: int = 6):
    if not chunks:
        return []
    q = tokenize(query) or tokenize(" ".join(chunks[:1]))
    docs = [tokenize(c) for c in chunks]
    scores = bm25_scores(q, docs)
    order = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
    return [{"text": chunks[i], "score": round(scores[i], 4), "index": i} for i in order[:k]]

def extractive_summary(text: str, query: str = "", max_sentences: int = 6, as_bullets: bool = False):
    """BM25 + structure-aware key sentences, then readable polish (no AI)."""
    sents = split_sentences(text)
    if not sents:
        return ""
    docs = [tokenize(s) for s in sents]
    if query and tokenize(query):
        base = bm25_scores(tokenize(query), docs)
    else:
        tfs = [_tf(d) for d in docs]
        df = Counter(t for d in docs for t in set(d))
        N = max(1, len(docs))
        def idf(t): return math.log((N + 1) / (1 + df[t])) + 1.0
        centroid = Counter()
        for tf in tfs:
            for tok, v in tf.items():
                centroid[tok] += v * idf(tok)
        base = []
        for tf in tfs:
            num = sum(tf[tok] * idf(tok) * centroid.get(tok, 0) for tok in tf)
            den = math.sqrt(sum((tf[tok] * idf(tok)) ** 2 for tok in tf) or 1.0) * math.sqrt(sum(v * v for v in centroid.values()) or 1.0)
            base.append(num / den)
    def bm25_fn(q, docs_sents):
        return base
    picked = EN.select_key_sentences(text, query, max_sentences, bm25_fn=bm25_fn)
    return EN.polish_summary_sentences(picked, all_sentences=sents, as_bullets=as_bullets)

def detect_conflicts(snippets_by_source):
    """Very light conflict flag: opposite polarity words across sources for the same key tokens."""
    flags = []
    # Compare pairs of sources on numeric claims
    items = list(snippets_by_source.items())
    num_re = re.compile(r"\b(\d+(?:\.\d+)?)\s*(%|percent|million|billion|thousand|people|users|years?|days?)?\b", re.I)
    claims = {}
    for src, text in items:
        for m in num_re.finditer(text or ""):
            key = (m.group(2) or "num").lower()
            val = float(m.group(1))
            claims.setdefault(key, []).append((src, val, m.group(0)))
    for key, rows in claims.items():
        if len(rows) < 2:
            continue
        vals = [v for _, v, _ in rows]
        if max(vals) > 0 and (max(vals) - min(vals)) / max(abs(max(vals)), 1e-6) > 0.5 and max(vals) != min(vals):
            a, b = rows[0], rows[-1]
            flags.append(f"Conflicting figures for '{key}': {a[0]} says {a[2]}; {b[0]} says {b[2]}.")
    return flags[:5]

# ---------------------------------------------------------------------------
# Network backends
# ---------------------------------------------------------------------------
def _cfg(cfg, key, default=None):
    if cfg is None:
        return default
    if hasattr(cfg, "get"):
        return cfg.get(key, default)
    return (cfg or {}).get(key, default)

async def _http_get(url, timeout=15, headers=None, max_bytes=1_500_000):
    hdr = dict(UA)
    if headers:
        hdr.update(headers)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=hdr) as c:
        async with c.stream("GET", url) as r:
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
                if len(buf) >= max_bytes:
                    break
            return r.status_code, dict(r.headers), bytes(buf), str(r.url)

async def _http_post(url, data=None, timeout=15):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=UA) as c:
        r = await c.post(url, data=data)
        return r.status_code, r.text, str(r.url)

async def backend_ddg(query: str, n: int, timeout: float):
    errors = []
    # HTML POST then GET
    for attempt in range(2):
        try:
            status, text, _ = await _http_post("https://html.duckduckgo.com/html/", data={"q": query}, timeout=timeout)
            if status == 202:
                errors.append("ddg html rate-limited (202)")
                await asyncio.sleep(0.4)
                continue
            rows = parse_ddg_html(text, n)
            if rows:
                return rows, "duckduckgo"
            errors.append(f"ddg html empty (HTTP {status})")
        except Exception as e:
            errors.append(f"ddg html: {type(e).__name__}: {e}")
        try:
            status, _, body, _ = await _http_get("https://html.duckduckgo.com/html/?q=" + quote_plus(query), timeout=timeout)
            rows = parse_ddg_html(body.decode("utf-8", "replace"), n)
            if rows:
                return rows, "duckduckgo"
            errors.append(f"ddg html GET empty (HTTP {status})")
        except Exception as e:
            errors.append(f"ddg html GET: {type(e).__name__}: {e}")
    # Lite
    try:
        status, _, body, _ = await _http_get("https://lite.duckduckgo.com/lite/?q=" + quote_plus(query), timeout=timeout)
        rows = parse_ddg_html(body.decode("utf-8", "replace"), n)
        if rows:
            return rows, "duckduckgo_lite"
        errors.append(f"ddg lite empty (HTTP {status})")
    except Exception as e:
        errors.append(f"ddg lite: {type(e).__name__}: {e}")
    return [], "; ".join(errors) or "ddg failed"

async def backend_searx(query: str, n: int, base: str, timeout: float):
    base = (base or "").rstrip("/")
    if not base:
        return [], "searx not configured"
    urls = [
        f"{base}/search?q={quote_plus(query)}&format=json",
        f"{base}/search?q={quote_plus(query)}",
    ]
    errors = []
    for u in urls:
        try:
            status, _, body, _ = await _http_get(u, timeout=timeout)
            rows = parse_searx(body.decode("utf-8", "replace"), n)
            if rows:
                return rows, "searx"
            errors.append(f"searx empty (HTTP {status})")
        except Exception as e:
            errors.append(f"searx: {type(e).__name__}: {e}")
    return [], "; ".join(errors)

async def backend_wikipedia(query: str, n: int, timeout: float):
    # Only useful for factual / definitional queries; always cheap to try
    api = ("https://en.wikipedia.org/w/api.php?action=query&list=search"
           f"&srsearch={quote_plus(query)}&srlimit={max(1, min(n, 8))}&format=json&utf8=1")
    try:
        status, _, body, _ = await _http_get(api, timeout=timeout,
                                             headers={"Accept": "application/json"})
        data = json.loads(body.decode("utf-8", "replace"))
        rows = parse_wikipedia_search(data, n)
        return rows, "wikipedia" if rows else f"wikipedia empty (HTTP {status})"
    except Exception as e:
        return [], f"wikipedia: {type(e).__name__}: {e}"

def looks_factual(query: str) -> bool:
    q = (query or "").lower().strip()
    if re.match(r"^(who|what|when|where|which|define|definition|meaning of|capital of|population of)\b", q):
        return True
    if re.search(r"\b(is|are|was|were)\b", q) and len(tokenize(q)) <= 10:
        return True
    return len(tokenize(q)) <= 5

async def search(query: str, max_results: int = 8, cfg=None, progress=None):
    """Multi-backend search with fallback, dedupe, rank. Returns a result dict."""
    q = (query or "").strip()
    if not q:
        raise SearchError("query is required")
    n = max(1, min(int(max_results or 8), 15))
    ttl = int(_cfg(cfg, "search_cache_ttl", 3600) or 3600)
    timeout = float(_cfg(cfg, "search_timeout", 12) or 12)
    searx = (_cfg(cfg, "searx_url", "") or "").strip()

    cached = cache_get("search", f"{q}|{n}|{searx}", ttl)
    if cached:
        cached = dict(cached); cached["cached"] = True
        return cached

    async def note(msg):
        if progress:
            try:
                await progress(msg)
            except TypeError:
                progress(msg)

    rows, used, errors = [], [], []
    await note("Searching DuckDuckGo…")
    ddg, info = await backend_ddg(q, n, timeout)
    if ddg:
        rows.extend(ddg); used.append(info if isinstance(info, str) else "duckduckgo")
    else:
        errors.append(str(info))

    if searx and len(rows) < n:
        await note("Searching SearXNG…")
        sx, info = await backend_searx(q, n, searx, timeout)
        if sx:
            rows.extend(sx); used.append("searx")
        else:
            errors.append(str(info))

    if looks_factual(q) or len(rows) < max(3, n // 2):
        await note("Checking Wikipedia…")
        wk, info = await backend_wikipedia(q, min(5, n), timeout)
        if wk:
            rows.extend(wk); used.append("wikipedia")
        else:
            errors.append(str(info))

    rows = rank_results(q, dedupe_results(rows))[:n]
    out = {
        "query": q, "results": rows, "count": len(rows),
        "backends": used, "errors": errors, "cached": False,
        "elapsed_ms": None,
    }
    if rows:
        cache_put("search", f"{q}|{n}|{searx}", out)
    return out

# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
async def fetch_url(url: str, cfg=None, max_chars: int = 12000, progress=None):
    url = clean_url(url)
    if not url:
        raise SearchError("invalid url")
    ttl = int(_cfg(cfg, "search_cache_ttl", 3600) or 3600)
    timeout = float(_cfg(cfg, "fetch_timeout", 15) or 15)
    max_bytes = int(_cfg(cfg, "fetch_max_bytes", 1_500_000) or 1_500_000)
    cached = cache_get("page", url, ttl)
    if cached:
        text = (cached.get("text") or "")[:max_chars]
        return {**cached, "text": text, "chars": len(text), "cached": True, "truncated": len(cached.get("text") or "") > max_chars}

    if progress:
        try: await progress(f"Fetching {url}…")
        except TypeError: progress(f"Fetching {url}…")

    try:
        status, headers, body, final = await _http_get(url, timeout=timeout, max_bytes=max_bytes)
    except Exception as e:
        return {"title": "", "url": url, "text": "", "chars": 0, "error": f"{type(e).__name__}: {e}", "cached": False}

    ct = (headers.get("content-type") or headers.get("Content-Type") or "").lower()
    final = clean_url(final) or url
    if "pdf" in ct or final.lower().endswith(".pdf") or body[:4] == b"%PDF":
        doc = extract_pdf_bytes(body, final)
    elif "html" in ct or b"<html" in body[:2000].lower() or b"<!doctype" in body[:500].lower():
        doc = extract_readable(body.decode("utf-8", "replace"), final)
    else:
        try:
            text = body.decode("utf-8", "replace")
        except Exception:
            text = ""
        doc = {"title": domain_of(final) or "file", "url": final, "text": text, "chars": len(text)}

    doc["http_status"] = status
    doc["cached"] = False
    if doc.get("text"):
        cache_put("page", url, {"title": doc.get("title"), "url": doc.get("url"), "text": doc.get("text")})
        cache_put("page", final, {"title": doc.get("title"), "url": doc.get("url"), "text": doc.get("text")})
    full = doc.get("text") or ""
    doc["text"] = full[:max_chars]
    doc["chars"] = len(doc["text"])
    doc["truncated"] = len(full) > max_chars
    return doc

async def fetch_many(urls, cfg=None, max_chars: int = 10000, concurrency: int = 4, progress=None):
    urls = [clean_url(u) for u in (urls or []) if clean_url(u)]
    sem = asyncio.Semaphore(max(1, min(int(concurrency or 4), 6)))
    out = [None] * len(urls)

    async def one(i, u):
        async with sem:
            out[i] = await fetch_url(u, cfg=cfg, max_chars=max_chars, progress=progress)

    await asyncio.gather(*(one(i, u) for i, u in enumerate(urls)))
    return [x for x in out if x]

# ---------------------------------------------------------------------------
# Research + summarise
# ---------------------------------------------------------------------------
def format_search_results(payload: dict) -> str:
    rows = payload.get("results") or []
    if not rows:
        err = "; ".join(payload.get("errors") or []) or "no results"
        return f"No results for {payload.get('query')!r}. ({err})"
    lines = [f"Search: {payload.get('query')}  [{', '.join(payload.get('backends') or [])}"
             + ("; cached" if payload.get("cached") else "") + "]"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r.get('title') or '(untitled)'}\n   {r.get('url')}\n   {r.get('snippet') or ''}")
    return "\n".join(lines)

def build_grounded_summary(query: str, pages, max_sentences: int = 8, per_source: int = 3):
    """Extractive, cited summary. pages: list of {title,url,text}."""
    sources = []
    cited_bits = []
    snippets_by_src = {}
    for i, p in enumerate(pages or [], 1):
        text = (p.get("text") or "").strip()
        if not text:
            continue
        url = p.get("url") or ""
        title = p.get("title") or domain_of(url) or f"source {i}"
        sources.append({"id": len(sources) + 1, "title": title, "url": url})
        sid = sources[-1]["id"]
        # pick relevant chunks then extractive sentences
        chunks = chunk_text(text, 900, 100)
        picked = select_chunks(query, chunks, k=min(4, max(1, len(chunks))))
        blob = " ".join(c["text"] for c in picked) if picked else text[:3000]
        summary = extractive_summary(blob, query=query, max_sentences=per_source)
        if not summary:
            continue
        snippets_by_src[f"[{sid}]"] = summary
        for sent in split_sentences(summary)[:per_source]:
            cited_bits.append((sid, sent))

    if not sources:
        return {
            "summary": "Not found in sources.",
            "sources": [],
            "conflicts": [],
            "mode": "extractive",
            "not_found": True,
        }

    # Global re-rank of cited sentences vs query, keep max_sentences
    if cited_bits:
        docs = [tokenize(s) for _, s in cited_bits]
        qtok = tokenize(query) or tokenize(cited_bits[0][1])
        scores = bm25_scores(qtok, docs)
        order = sorted(range(len(cited_bits)), key=lambda i: (-scores[i], i))[:max_sentences]
        order = sorted(order)  # keep reading order among picks for coherence
        lines = []
        used = set()
        for i in order:
            sid, sent = cited_bits[i]
            key = (sid, sent[:80])
            if key in used:
                continue
            used.add(key)
            # ensure citation marker
            if not re.search(rf"\[{sid}\]", sent):
                sent = sent.rstrip(".") + f" [{sid}]."
            lines.append(sent)
        # Readable polish (dangling This/It, dups) while keeping citation markers
        summary = EN.polish_summary_sentences([(None, s) for s in lines], as_bullets=False)
        if not summary:
            summary = " ".join(lines)
    else:
        summary = "Not found in sources."

    conflicts = detect_conflicts(snippets_by_src)
    # Coverage check: if query tokens barely appear, say so
    if term_overlap(query, summary) < 0.15 and term_overlap(query, " ".join(snippets_by_src.values())) < 0.2:
        summary = "Not found in sources. The fetched pages did not contain enough overlap with the query to support an answer."
        return {"summary": summary, "sources": sources, "conflicts": conflicts, "mode": "extractive", "not_found": True}

    return {
        "summary": summary,
        "sources": sources,
        "conflicts": conflicts,
        "mode": "extractive",
        "not_found": summary.startswith("Not found"),
    }

def format_summary(payload: dict) -> str:
    lines = []
    lines.append(payload.get("summary") or "Not found in sources.")
    if payload.get("conflicts"):
        lines.append("")
        lines.append("Conflicts:")
        for c in payload["conflicts"]:
            lines.append("- " + c)
    srcs = payload.get("sources") or []
    if srcs:
        lines.append("")
        lines.append("Sources:")
        for s in srcs:
            lines.append(f"[{s['id']}] {s.get('title') or ''} — {s.get('url')}")
    meta = []
    if payload.get("mode"):
        meta.append(payload["mode"])
    if payload.get("elapsed_ms") is not None:
        meta.append(f"{payload['elapsed_ms']} ms")
    if payload.get("pages_fetched") is not None:
        meta.append(f"{payload['pages_fetched']} pages")
    if payload.get("cached_hits"):
        meta.append(f"{payload['cached_hits']} cached")
    if meta:
        lines.append("")
        lines.append("Stats: " + " · ".join(meta))
    return "\n".join(lines)

async def research_and_summarize(query: str, urls=None, max_pages: int = 4, cfg=None,
                                 mode: str = "extractive", progress=None):
    """Search (unless urls given), fetch top pages, return grounded extractive summary."""
    t0 = time.time()
    q = (query or "").strip()
    if not q and not urls:
        raise SearchError("query or urls required")
    max_pages = max(1, min(int(max_pages or 4), 6))
    mode = (mode or "extractive").lower()
    if mode in ("auto", "ai"):
        # Tool-side always extractive (no model call here); AI chat uses the returned pack.
        mode = "extractive"

    status = []
    async def note(msg):
        status.append(msg)
        if progress:
            try: await progress(msg)
            except TypeError: progress(msg)

    selected_urls = [clean_url(u) for u in (urls or []) if clean_url(u)]
    search_payload = None
    if not selected_urls:
        await note("Searching…")
        search_payload = await search(q or "summary", max_results=max(max_pages + 2, 6), cfg=cfg, progress=note)
        selected_urls = [r["url"] for r in (search_payload.get("results") or [])[:max_pages]]

    await note(f"Fetching {len(selected_urls)} page(s)…")
    pages = await fetch_many(selected_urls, cfg=cfg, max_chars=int(_cfg(cfg, "fetch_max_chars", 14000) or 14000),
                             concurrency=int(_cfg(cfg, "fetch_concurrency", 4) or 4), progress=note)
    pages = [p for p in pages if (p.get("text") or "").strip() and not p.get("error")]
    cached_hits = sum(1 for p in pages if p.get("cached"))

    await note("Summarising (extractive, no AI)…")
    grounded = build_grounded_summary(q or "summary", pages,
                                      max_sentences=int(_cfg(cfg, "summary_sentences", 8) or 8))
    grounded["pages_fetched"] = len(pages)
    grounded["cached_hits"] = cached_hits
    grounded["elapsed_ms"] = int((time.time() - t0) * 1000)
    grounded["query"] = q
    grounded["status"] = status
    if search_payload:
        grounded["search_backends"] = search_payload.get("backends")
        grounded["search_count"] = search_payload.get("count")
    grounded["pages"] = [{"title": p.get("title"), "url": p.get("url"), "chars": p.get("chars"),
                          "cached": p.get("cached")} for p in pages]
    return grounded
