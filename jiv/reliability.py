"""1.5.3 response reliability: understanding messy messages, honest answers, and a bounded
fallback to an ALREADY-INSTALLED local model when the selected model fails technically.

Fallback is only for technical failures (connection error, timeout, server error, empty or
garbled output). It never runs because a model declined something, never downloads a model,
and runs at most once per user message.
"""
import re
import httpx

UNDERSTAND_RULES = """

## Understanding the user
- The user may type quickly: typos, shorthand (u, pls, wat, abt, ques), missing words, or mixed languages (e.g. Hindi/Hinglish with English).
  Work out the most likely meaning and answer it directly, in the language the user mostly wrote in.
- Only if two readings would give clearly different answers, give your best guess briefly and ask ONE short question to confirm.
- Answer in your own words. Be honest: never invent sources, links, quotes, statistics or tool results, and never claim you searched,
  ran or checked something you did not. If you are unsure or a fact may be outdated, say so in one short line."""

SHORTHAND = {"u": "you", "ur": "your", "r": "are", "pls": "please", "plz": "please", "wat": "what", "wht": "what", "abt": "about",
             "ques": "question", "qs": "questions", "expln": "explain", "xplain": "explain", "diff": "difference", "b/w": "between",
             "btw": "between", "bw": "between", "hw": "how", "y": "why", "n": "and", "becoz": "because", "bcoz": "because", "coz": "because",
             "thx": "thanks", "info": "information", "govt": "government", "defn": "definition", "eg": "e.g.", "wrt": "with respect to",
             "kya": "what", "kaise": "how", "kyu": "why", "kyun": "why", "hai": "is", "batao": "tell me", "bataiye": "please tell me",
             "samjhao": "explain", "kaun": "who", "kab": "when", "kahan": "where", "matlab": "meaning",
             # 1.6.2 extended Hinglish / Indian chat
             "krdo": "kar do", "karo": "do", "kardo": "kar do", "chahiye": "want", "chaahiye": "want",
             "nahi": "not", "nahin": "not", "matlab": "meaning", "samajh": "understand", "samajhao": "explain",
             "dikhao": "show", "bhejo": "send", "likho": "write", "bolo": "tell", "suno": "listen",
             "accha": "okay", "achha": "okay", "theek": "okay", "thik": "okay", "bas": "just",
             "zyada": "more", "kam": "less", "jaldi": "quickly", "abhi": "now", "phir": "then",
             "kyunki": "because", "lekin": "but", "magar": "but", "agar": "if", "toh": "then",
             "summari": "summary", "summry": "summary", "sumarise": "summarise", "summarize": "summarise",
             "grammer": "grammar", "sentance": "sentence", "recieve": "receive", "seperate": "separate",
             "writting": "writing", "definately": "definitely", "tommorow": "tomorrow", "todays": "today's",
             "wats": "what's", "whats": "what's", "dont": "don't", "cant": "can't", "wont": "won't",
             "im": "I'm", "ive": "I've", "id": "I'd", "ill": "I'll", "youre": "you're", "theyre": "they're",
             "msg": "message", "msgs": "messages", "tmrw": "tomorrow", "tmr": "tomorrow", "2day": "today",
             "2morrow": "tomorrow", "nd": "and", "cnvrt": "convert", "smry": "summary", "expain": "explain",
             "exaplain": "explain", "brif": "brief", "brife": "brief", "detial": "detail", "detials": "details"}
WORD_RE = re.compile(r"[A-Za-z/]+|[^A-Za-z/]+")

def expand_shorthand(query):
    """Best-effort reading of shorthand / Hinglish words. Returns (expanded, changed?)."""
    q = (query or "").strip()
    if not q or len(q) > 400: return q, False
    parts, changed = [], False
    for tok in WORD_RE.findall(q):
        rep = SHORTHAND.get(tok.lower())
        if rep and tok.isalpha() or (rep and "/" in tok):
            parts.append(rep); changed = True
        else:
            parts.append(tok)
    return "".join(parts), changed

def intent_hint(query):
    """A short system-prompt line with the probable reading of a messy message, or ''."""
    exp, changed = expand_shorthand(query)
    if not changed: return ""
    return f"\n\n## Probable reading of the user's latest message\n\"{exp[:300]}\" (a guess; the user's own words come first)."

# ---- technical-failure detection (never refusal detection) ----
def is_garbled(text):
    t = (text or "").strip()
    if len(t) < 40: return False
    if re.search(r"(.{2,12}?)\1{12,}", t, re.S): return True                 # same chunk repeated 12+ times
    if "\ufffd" * 3 in t: return True
    letters = sum(ch.isalnum() for ch in t)
    return letters / max(1, len(t)) < 0.2                                     # mostly symbols / junk

def technical_problem(text):
    """'empty' / 'garbled' when the output is technically unusable, else None.
    Concise answers ('Paris.', '42', 'Yes') are NOT a problem."""
    t = re.sub(r"<think>.*?(?:</think>|$)", "", text or "", flags=re.S | re.I).strip()
    if not t or not re.search(r"\w", t): return "empty"
    if is_garbled(t): return "garbled"
    return None

FALLBACK_NOTICE = ("↪ {primary} did not give a usable answer ({reason}). Answered once by another installed model: {fallback}. "
                   "This is a different model, not a check of the first one.")

def _small_enough(m, max_gb):
    return not m.get("size_gb") or m["size_gb"] <= max_gb

async def installed_models(ollama_url, timeout=4.0):
    """Models already installed in Ollama ([{name, size_gb}]). Never downloads anything."""
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(ollama_url.rstrip("/") + "/api/tags"); r.raise_for_status()
    return [{"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 2)} for m in r.json().get("models", [])]

def choose_fallback(installed, primary, preferred=None, max_gb=3.5):
    """Pick ONE installed fallback model != primary.
    preferred: user's list (order kept; only installed ones count). Empty -> smallest installed model under max_gb."""
    names = {m["name"]: m for m in installed}
    prim = (primary or "").strip()
    def same(a, b): return a == b or a.split(":")[0] == b and b.find(":") < 0 or (a.endswith(":latest") and a[:-7] == b)
    for p in preferred or []:
        p = p.strip()
        if p and p in names and not same(p, prim) and not same(prim, p): return p
    if preferred: return None                                                 # user chose models, none installed: no auto-pick
    cands = sorted((m for m in installed if not same(m["name"], prim) and not same(prim, m["name"]) and _small_enough(m, max_gb)
                    and not re.search(r"embed|vision|llava|whisper", m["name"], re.I)), key=lambda m: m.get("size_gb") or 99)
    return cands[0]["name"] if cands else None

# A decline that cites safety, harm or law is respected: no retry, no other model.
SAFETY_DECLINE_RE = re.compile(
    r"\b(?:harm(?:ful)?|dangerous|illegal|unlawful|weapons?|explosives?|bombs?|malware|ransomware|hack(?:ing)?|"
    r"self[- ]harm|suicide|violence|unsafe|safety|ethical|abuse|exploit|content (?:policy|filter|guidelines)|"
    r"adult content|explicit content|nsfw|pornograph)\b", re.I)

def is_safety_decline(text, query=""):
    return bool(SAFETY_DECLINE_RE.search((text or "")[:700]) or SAFETY_DECLINE_RE.search(query or ""))
