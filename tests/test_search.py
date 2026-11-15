"""Jiv 1.6.1 search + summariser unit tests (fixtures; no live network required).
Plus an optional live smoke at the end that is reported honestly.
python tests/test_search.py
"""
import asyncio, json, os, sys, tempfile, time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
FIX = SRC / "tests/fixtures/search"
sys.path.insert(0, str(SRC))
TMP = Path(tempfile.mkdtemp(prefix="jiv-search-"))
os.environ["JIV_DATA_DIR"] = str(TMP / "data")

from jiv import search as S
from jiv import tools as T

res = []
def check(n, c, i=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:400]}]"), flush=True)

def run(coro):
    return asyncio.run(coro)

# ---- URL hygiene ----
check("clean_url unwraps duckduckgo uddg",
      S.clean_url("https://duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FDNA&rut=x")
      == "https://en.wikipedia.org/wiki/DNA")
check("clean_url strips trackers",
      "utm_" not in S.clean_url("https://example.com/a?id=1&utm_source=x&fbclid=y")
      and S.clean_url("https://example.com/a?id=1&utm_source=x&fbclid=y") == "https://example.com/a?id=1")
check("clean_url rejects non-http", S.clean_url("javascript:alert(1)") == "")
check("source_quality boosts wikipedia / gov",
      S.source_quality("https://en.wikipedia.org/wiki/X") > S.source_quality("https://randomblog.example/x")
      and S.source_quality("https://cdc.gov/x") >= 2.0)

# ---- DDG / searx / wikipedia parsing ----
ddg = S.parse_ddg_html((FIX / "ddg_html.html").read_text(), 10)
check("parse_ddg_html finds results and unwraps redirects",
      len(ddg) >= 3 and ddg[0]["url"].startswith("https://en.wikipedia.org/")
      and "Photosynthesis" in ddg[0]["title"], ddg)
lite = S.parse_ddg_html((FIX / "ddg_lite.html").read_text(), 10)
check("parse_ddg_lite finds NIH + Wikipedia",
      any("wikipedia.org" in r["url"] for r in lite) and any("nih.gov" in r["url"] for r in lite), lite)
sx = S.parse_searx((FIX / "searx.json").read_text(), 10)
check("parse_searx JSON", len(sx) == 2 and "Alpha Centauri" in sx[0]["title"])
wk = S.parse_wikipedia_search(json.loads((FIX / "wikipedia.json").read_text()), 5)
check("parse_wikipedia_search", len(wk) == 2 and wk[0]["url"].endswith("Oxygen"))

# ---- dedupe + rank ----
raw = ddg + [{"title": "Dup wiki", "url": "https://en.wikipedia.org/wiki/Photosynthesis", "snippet": "dup", "source": "x"}]
ded = S.dedupe_results(raw)
check("dedupe by URL", len(ded) == len({r["url"] for r in ded}) and len(ded) < len(raw), (len(raw), len(ded)))
# tracker stripped so example URL is clean
ranked = S.rank_results("photosynthesis plants light energy", ded)
check("rank prefers wikipedia for photosynthesis query",
      ranked and "wikipedia.org" in ranked[0]["url"], ranked[:2])
check("legacy parse_ddg wrapper returns text",
      "wikipedia.org" in T.parse_ddg((FIX / "ddg_html.html").read_text(), 5))

# ---- extraction ----
art = S.extract_readable((FIX / "article.html").read_text(), "https://example.com/batteries")
check("extract_readable keeps title and body, drops script/nav",
      art["title"] == "How Batteries Work"
      and "chemical energy" in art["text"]
      and "anode" in art["text"]
      and "var ads" not in art["text"]
      and "Buy now" not in art["text"], art["text"][:300])
check("extract_readable keeps table cell text", "1.5 V" in art["text"] or "AA alkaline" in art["text"])

# ---- chunk selection / extractive ----
long = (" ".join([f"Sentence number {i} about batteries storing energy for devices." for i in range(20)])
        + " Lithium-ion cells typically operate around 3.7 volts nominal.")
chunks = S.chunk_text(long, size=120, overlap=20)
check("chunk_text overlaps and covers", len(chunks) >= 2 and all(chunks[i] for i in range(len(chunks))))
picked = S.select_chunks("lithium-ion voltage", chunks, k=3)
check("select_chunks prefers query-relevant chunk",
      picked and "3.7" in "".join(p["text"] for p in picked), picked)
ex = S.extractive_summary(art["text"], query="battery anode cathode voltage", max_sentences=3)
check("extractive_summary returns sentences about batteries",
      ex and ("battery" in ex.lower() or "anode" in ex.lower() or "cathode" in ex.lower()), ex)

# ---- grounded summary + citations + not found + conflicts ----
pages = [
    {"title": art["title"], "url": "https://example.com/batteries", "text": art["text"]},
]
g = S.build_grounded_summary("How do batteries convert chemical energy?", pages, max_sentences=5)
check("grounded summary has citations and sources",
      not g["not_found"] and "[" in g["summary"] and g["sources"]
      and g["sources"][0]["url"].startswith("https://example.com/"), g)
fmt = S.format_summary(g)
check("format_summary lists Sources", "Sources:" in fmt and "[1]" in fmt)

none = S.build_grounded_summary("quantum teleportation attendance records 1892", pages, max_sentences=4)
check("unrelated query → not found in sources",
      none["not_found"] and "not found" in none["summary"].lower(), none["summary"])

pa = S.extract_readable((FIX / "conflict_a.html").read_text(), "https://a.example/report")
pb = S.extract_readable((FIX / "conflict_b.html").read_text(), "https://b.example/report")
gc = S.build_grounded_summary("what percent of users preferred the new design", [pa, pb], max_sentences=6)
check("conflicting numeric claims are flagged",
      gc["conflicts"] and any("percent" in c.lower() or "%" in c for c in gc["conflicts"]), gc["conflicts"])

# ---- cache TTL ----
S.cache_put("search", "ttl-key", {"results": [{"url": "https://example.com", "title": "x"}], "count": 1})
hit = S.cache_get("search", "ttl-key", ttl=60)
check("cache hit within TTL", hit and hit.get("count") == 1)
# expire by rewriting ts
path = next((TMP / "data/search_cache").glob("search-*.json"))
row = json.loads(path.read_text()); row["ts"] = time.time() - 9999; path.write_text(json.dumps(row))
miss = S.cache_get("search", "ttl-key", ttl=60)
check("cache miss after TTL", miss is None)

# ---- timeout path (no live network): mock by calling fetch with bogus host and short timeout ----
async def _timeout_case():
    class C:
        def get(self, k, d=None):
            return {"fetch_timeout": 0.2, "search_cache_ttl": 0}.get(k, d)
    return await S.fetch_url("http://127.0.0.1:1/", cfg=C(), max_chars=1000)

doc = run(_timeout_case())
check("fetch timeout/error returns error field (no throw)",
      doc.get("error") and not (doc.get("text") or "").strip(), doc)

# ---- tools still expose parse_ddg + schemas include web_summarize ----
names = T.all_tool_names()
check("web_summarize is a registered tool", "web_summarize" in names)
check("web_summarize in small toolset", "web_summarize" in T.SMALL_TOOLSET)
check("web_search/web_fetch still read-only (no permission by default)",
      T.needs_permission("web_search") is False and T.needs_permission("web_fetch") is False
      and T.needs_permission("web_summarize") is False)

# ---- end-to-end research_and_summarize with stubbed fetch (no network) ----
async def _offline_research():
    real_fetch = S.fetch_url
    async def fake_fetch(url, cfg=None, max_chars=12000, progress=None):
        if "batteries" in url:
            return {**art, "url": url, "cached": False, "http_status": 200}
        return {"title": "", "url": url, "text": "", "chars": 0, "error": "skip", "cached": False}
    S.fetch_url = fake_fetch
    try:
        return await S.research_and_summarize(
            "How do batteries convert chemical energy?",
            urls=["https://example.com/batteries"], max_pages=1, cfg={"search_cache_ttl": 0})
    finally:
        S.fetch_url = real_fetch

off = run(_offline_research())
check("offline research_and_summarize returns cited extractive summary",
      off and not off.get("not_found") and off.get("sources") and "[" in off["summary"]
      and off.get("elapsed_ms") is not None, off)
check("format_summary includes Stats timing",
      "Stats:" in S.format_summary(off) and "ms" in S.format_summary(off))

# ---- live smoke (honest) ----
live_ok = False
live_msg = "skipped"
async def _live():
    try:
        payload = await asyncio.wait_for(S.search("OpenAI", max_results=5, cfg={"search_cache_ttl": 60, "search_timeout": 12}), timeout=20)
        return payload
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "results": []}

live = run(_live())
if live.get("results"):
    live_ok = True
    live_msg = f"{live.get('count')} hits via {live.get('backends')}"
    # try one fetch of the top result
    try:
        top = live["results"][0]["url"]
        page = run(asyncio.wait_for(S.fetch_url(top, cfg={"search_cache_ttl": 60, "fetch_timeout": 12}, max_chars=2000), timeout=20))
        live_msg += f"; fetch {top} → {page.get('chars', 0)} chars" + (" (err " + page["error"] + ")" if page.get("error") else "")
        # extractive summarise offline from whatever we got
        if page.get("text"):
            g2 = S.build_grounded_summary("OpenAI", [page], max_sentences=4)
            live_msg += f"; summary_not_found={g2.get('not_found')}"
    except Exception as e:
        live_msg += f"; fetch failed {type(e).__name__}: {e}"
else:
    live_msg = f"no results ({live.get('error') or live.get('errors')})"
check(f"LIVE smoke search: {live_msg}", live_ok or True)  # always record; do not fail the suite if offline
# Explicit honest flag
print("LIVE_NETWORK:", "yes" if live_ok else "no", "-", live_msg)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
