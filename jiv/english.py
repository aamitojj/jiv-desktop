"""Jiv 1.6.2 — English for Jiv itself (reading, summaries, replies).

Not a language-learning product. Provides:
- Safer sentence segmentation (abbreviations, decimals, lists)
- Key-sentence scoring that keeps negation / numbers / names
- Post-process polish for extractive summaries (dangling This/It, dups)
- High-precision polish for Jiv's own chat replies (never code/URLs/quotes)
- grammar_check for when the user asks to correct a sentence
- Optional AI grammar JSON validate helpers
"""
from __future__ import annotations
import json, re
from pathlib import Path

DATA = Path(__file__).resolve().parent / "english_data"
WORDS_PATH = DATA / "words.txt"

# ---------------------------------------------------------------------------
# Sentence segmentation
# ---------------------------------------------------------------------------
_ABBREV = {
    "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e",
    "u.s", "u.k", "u.n", "a.m", "p.m", "no", "vol", "fig", "approx", "dept",
    "inc", "ltd", "co", "corp", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
    "sep", "sept", "oct", "nov", "dec", "al",  # et al.
}
_ABBREV_DOT = re.compile(
    r"(?i)\b(" + "|".join(re.escape(a) for a in sorted(_ABBREV, key=len, reverse=True)) + r")\.$")
_DECIMAL = re.compile(r"\d\.\d")
_ELLIPSIS = re.compile(r"\.\.\.")
_INITIALS = re.compile(r"\b([A-Z])\.(?=[A-Z]\.|\s?[A-Z][a-z])")
_LIST_NUM = re.compile(r"(?m)^(\s*(?:\d+|[a-z]|[ivx]+)[.)]\s+)", re.I)

def split_sentences(text: str):
    """Split into sentences without breaking Dr., i.e., U.S., 3.14, or list markers."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []

    # Placeholder-protect safe dots so a simple split works.
    slots = []
    def save(chunk: str) -> str:
        slots.append(chunk)
        return f"\x00D{len(slots)-1}\x00"

    s = raw
    # ellipsis
    s = s.replace("...", save("..."))
    # decimals: 3.14
    s = re.sub(r"(?<=\d)\.(?=\d)", lambda m: save("."), s)
    # numbered / lettered list markers at line starts: "1." "a."
    s = re.sub(r"(?m)(^\s*(?:\d+|[A-Za-z]|[ivxIVX]+)\.)(?=\s)", lambda m: save(m.group(1)), s)
    # dotted abbreviations / initials: Dr.  e.g.  U.S.  a.m.
    def abbr_repl(m):
        return save(m.group(0))
    abbr_pat = re.compile(
        r"\b(?:Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St|vs|etc|approx|dept|inc|ltd|co|corp|"
        r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Vol|Fig|No|"
        r"e\.g|i\.e|u\.s|u\.k|u\.n|a\.m|p\.m|Ph\.D|M\.D|B\.A|M\.A)\.",
        re.I)
    s = abbr_pat.sub(abbr_repl, s)
    # remaining single-letter initials: "U." when followed by another initial or capital word
    s = re.sub(r"\b([A-Za-z])\.(?=\s?[A-Za-z]\.)", lambda m: save(m.group(0)), s)
    s = re.sub(r"\b([A-Za-z])\.(?=\s[A-Z])", lambda m: save(m.group(0)), s)

    parts = re.split(r"(?<=[.!?])\s+(?=[\"\'(\[•\-]?[A-Z0-9])", s)
    flat = []
    for part in parts:
        for block in re.split(r"\n{2,}", part):
            block = block.strip()
            if block:
                flat.append(block)

    def restore(x: str) -> str:
        return re.sub(r"\x00D(\d+)\x00", lambda m: slots[int(m.group(1))], x)

    sents = []
    for part in flat:
        part = restore(part).strip()
        if not part:
            continue
        if sents and len(part) < 20 and part[0].islower():
            sents[-1] = sents[-1] + " " + part
        else:
            sents.append(part)
    return sents


def detect_sections(text: str):
    """Return [(heading_or_None, body_text), ...] from markdown-ish / HTML-extracted text."""
    lines = (text or "").splitlines()
    sections, cur_h, buf = [], None, []
    heading_re = re.compile(r"^(#{1,6}\s+.+|[A-Z][A-Za-z0-9 ,/&\-]{2,60})$")

    def flush():
        nonlocal buf, cur_h
        body = "\n".join(buf).strip()
        if body or cur_h:
            sections.append((cur_h, body))
        buf = []

    for ln in lines:
        t = ln.strip()
        if not t:
            buf.append("")
            continue
        # short Title Case / ALL CAPS / markdown heading → section
        is_head = (t.startswith("#") or
                   (len(t) <= 60 and not t.endswith(".") and heading_re.match(t)
                    and not re.search(r"\b(the|and|with|from|that|this|for|are|was|were)\b", t.lower()[:20])
                    and sum(ch.isupper() for ch in t) >= max(2, len(re.findall(r"[A-Za-z]", t)) * 0.4)))
        # More conservative: markdown # or line ending without period and next content
        if t.startswith("#"):
            flush(); cur_h = re.sub(r"^#+\s*", "", t); continue
        buf.append(ln)
    flush()
    if not sections:
        sections = [(None, text or "")]
    return sections

# ---------------------------------------------------------------------------
# Key-sentence scoring (structure-aware)
# ---------------------------------------------------------------------------
_NEG = re.compile(r"\b(?:not|no|never|n't|cannot|can't|without|neither|nor|none|nobody|nothing)\b", re.I)
_NUM = re.compile(r"\b\d+(?:[.,]\d+)?%?|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million|billion)\b", re.I)
_NAME = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b")
_MAIN_VERB = re.compile(
    r"\b(?:is|are|was|were|has|have|had|do|does|did|can|could|will|would|should|may|might|"
    r"says?|said|reports?|reported|shows?|showed|found|finds|means?|meant|includes?|"
    r"provides?|allows?|requires?|causes?|caused|makes?|made)\b", re.I)

def sentence_features(sent: str) -> dict:
    s = sent or ""
    return {
        "negation": bool(_NEG.search(s)),
        "numbers": len(_NUM.findall(s)),
        "names": len([m for m in _NAME.findall(s) if m.lower() not in {"the", "a", "an", "this", "that"}]),
        "has_verb": bool(_MAIN_VERB.search(s)),
        "len": len(s),
        "is_fragment": len(s) < 40 and not _MAIN_VERB.search(s),
    }

def score_sentence(sent: str, query: str = "", bm25: float = 0.0) -> float:
    """Combine BM25 with structure bonuses; never punish negation — boost it when query has negation too."""
    f = sentence_features(sent)
    score = float(bm25)
    if f["has_verb"]:
        score += 0.35
    if f["numbers"]:
        score += 0.25 + 0.05 * min(3, f["numbers"])
    if f["names"]:
        score += 0.15
    # Keep negation: if sentence negates, prefer it when query also negates OR always small boost
    # so "does not" claims are not dropped in favour of positive neighbours
    if f["negation"]:
        score += 0.55 if _NEG.search(query or "") else 0.35
    if f["is_fragment"]:
        score -= 0.8
    # Prefer mid length
    if 60 <= f["len"] <= 220:
        score += 0.1
    elif f["len"] > 320:
        score -= 0.15
    return score

def select_key_sentences(text: str, query: str = "", max_sentences: int = 6, bm25_fn=None):
    """Pick key sentences with structure-aware scores. bm25_fn(query, docs)->scores optional."""
    sents = split_sentences(text)
    if not sents:
        return []
    if len(sents) <= max_sentences:
        return list(enumerate(sents))
    if bm25_fn:
        scores = bm25_fn(query, sents)
    else:
        # light overlap fallback
        q = set(re.findall(r"[a-z0-9]{2,}", (query or "").lower()))
        scores = []
        for s in sents:
            toks = set(re.findall(r"[a-z0-9]{2,}", s.lower()))
            scores.append(len(q & toks) / max(1, len(q)) if q else 0.0)
    ranked = sorted(range(len(sents)),
                    key=lambda i: (-score_sentence(sents[i], query, scores[i]), i))
    # Ensure at least one negation sentence is kept if any exist and query is negative
    pick = set(ranked[:max_sentences])
    if _NEG.search(query or ""):
        neg_idxs = [i for i, s in enumerate(sents) if _NEG.search(s)]
        if neg_idxs and not any(i in pick for i in neg_idxs):
            # replace lowest-ranked pick with best negation sentence
            worst = max(pick, key=lambda i: ranked.index(i) if i in ranked else 99)
            pick.discard(worst)
            pick.add(neg_idxs[0])
    return [(i, sents[i]) for i in sorted(pick)]

# ---------------------------------------------------------------------------
# Summary polish (extractive → readable)
# ---------------------------------------------------------------------------
_DANGLE = re.compile(r"^(This|That|These|Those|It|They|He|She)\b", re.I)
_DUP_SPACE = re.compile(r"[ \t]{2,}")
_SPACE_PUNCT = re.compile(r"\s+([,.;:!?])")

def _normalize_ws(s: str) -> str:
    s = _DUP_SPACE.sub(" ", s or "")
    s = _SPACE_PUNCT.sub(r"\1", s)
    s = re.sub(r"([(\[{])\s+", r"\1", s)
    s = re.sub(r"\s+([)\]}])", r"\1", s)
    return s.strip()

def polish_summary_sentences(picked, all_sentences=None, as_bullets: bool = False) -> str:
    """Post-process extractive picks: fix dangling pronouns, drop dups, order, format."""
    if not picked:
        return ""
    # picked: list[str] or list[(idx, str)]
    items = []
    for p in picked:
        if isinstance(p, tuple):
            items.append((p[0], p[1]))
        else:
            items.append((None, p))

    all_sents = all_sentences or []
    cleaned, seen = [], set()
    for idx, sent in items:
        s = _normalize_ws(sent)
        if not s:
            continue
        # Dangling pronoun at start → include previous sentence when available
        if _DANGLE.match(s) and idx is not None and all_sents and idx > 0:
            prev = _normalize_ws(all_sents[idx - 1])
            # Avoid double-adding if prev already in cleaned
            if prev and prev not in cleaned and prev.lower() not in seen:
                cleaned.append(prev)
                seen.add(prev.lower())
            # Soften leading This/It when previous was added: keep as-is (now has context)
        elif _DANGLE.match(s) and not (idx is not None and idx > 0):
            # No referent: try replacing This/It with a vague but grammatical form
            s2 = _DANGLE.sub(lambda m: "The topic" if m.group(1).lower() in ("this", "that", "it") else m.group(1), s, count=1)
            s = s2
        key = re.sub(r"\W+", " ", s.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        # Drop fragments without a verb unless they look like headings
        feat = sentence_features(s)
        if feat["is_fragment"] and len(cleaned) > 0:
            continue
        cleaned.append(s)

    if not cleaned:
        return ""
    if as_bullets and len(cleaned) >= 3:
        return "\n".join("• " + c.rstrip(".") + "." for c in cleaned)
    # Paragraph form: join with space; ensure terminal period
    out = []
    for c in cleaned:
        if c[-1] not in ".!?":
            c += "."
        out.append(c)
    return " ".join(out)

def extractive_summary_quality(text: str, query: str = "", max_sentences: int = 6, bm25_fn=None, as_bullets=False) -> str:
    sents = split_sentences(text)
    picked = select_key_sentences(text, query, max_sentences, bm25_fn=bm25_fn)
    return polish_summary_sentences(picked, all_sentences=sents, as_bullets=as_bullets)

# ---------------------------------------------------------------------------
# High-precision reply polish (Jiv's own outputs only)
# ---------------------------------------------------------------------------
def _protect_spans(text: str):
    """Replace code fences, inline code, URLs, and quoted spans with placeholders."""
    slots = []
    def save(m):
        slots.append(m.group(0))
        return f"\x00S{len(slots)-1}\x00"
    t = text or ""
    t = re.sub(r"```[\s\S]*?```", save, t)
    t = re.sub(r"`[^`\n]+`", save, t)
    t = re.sub(r"https?://[^\s<>\")\]]+", save, t)
    t = re.sub(r'"(?:\\.|[^"\\])*"', save, t)
    t = re.sub(r"'(?:\\.|[^'\\])*'", save, t)
    return t, slots

def _restore_spans(text: str, slots):
    def repl(m):
        i = int(m.group(1))
        return slots[i] if 0 <= i < len(slots) else m.group(0)
    return re.sub(r"\x00S(\d+)\x00", repl, text)

def polish_reply(text: str) -> tuple[str, list]:
    """High-precision polish. Returns (new_text, list of {rule, before, after}).
    Never alters code blocks, URLs, or quoted strings."""
    if not text or len(text) < 2:
        return text or "", []
    protected, slots = _protect_spans(text)
    changes = []

    def apply(rule, pattern, repl, flags=0):
        nonlocal protected
        def _sub(m):
            old = m.group(0)
            new = m.expand(repl) if isinstance(repl, str) else repl(m)
            if new != old:
                changes.append({"rule": rule, "before": old, "after": new})
            return new
        protected = re.sub(pattern, _sub, protected, flags=flags)

    # repeated words: the the, is is (not "had had")
    apply("repeated_word", r"\b([A-Za-z']+)(\s+\1)+\b",
          lambda m: m.group(1) if m.group(1).lower() not in {"had", "that", "buffet"} else m.group(0),
          flags=re.I)
    # space before punctuation / double spaces
    apply("space_before_punct", r"\s+([,.;:!?])", r"\1")
    apply("double_space", r"[ \t]{2,}", " ")
    apply("space_after_punct", r"([,.;:!?])([A-Za-z])", r"\1 \2")
    # a/an
    def a_an(m):
        art, word = m.group(1), m.group(2)
        # skip placeholders
        if "\x00" in word:
            return m.group(0)
        first = re.sub(r"[^A-Za-z]", "", word)
        if not first:
            return m.group(0)
        vowel = first[0].lower() in "aeiou"
        # uni-/eu- often sound like consonant; hour/honest like vowel — keep simple
        if first.lower().startswith(("uni", "eu", "one", "once", "user", "uk")):
            vowel = False
        if first.lower().startswith(("hour", "honest", "honor", "honour", "heir")):
            vowel = True
        want = "an" if vowel else "a"
        if art.lower() == want:
            return m.group(0)
        fixed = want if art.islower() else want.capitalize()
        return fixed + " " + word
    apply("a_an", r"\b([Aa]n?)\s+([A-Za-z\x00][A-Za-z'\-]*)", a_an)
    # its / it's — only clear cases
    apply("its_it_is", r"\b[Ii]t\s+is\b", lambda m: "It's" if m.group(0)[0].isupper() else "it's")
    # "its a/an/the/not…" → "it's …"
    apply("its_contraction",
          r"\b([Ii])ts\s+(?=a\b|an\b|the\b|not\b|been\b|going\b|time\b)",
          lambda m: m.group(1) + "t's ")
    # your / you're
    apply("you_are", r"\b[Yy]ou\s+are\b", lambda m: "You're" if m.group(0)[0].isupper() else "you're")
    # capitalization after sentence end
    apply("cap_after_end",
          r"([.!?]\s+)([a-z])",
          lambda m: m.group(1) + m.group(2).upper())
    # leading capital for the whole reply if it starts with a letter
    if protected and protected[0].islower() and not protected.startswith("\x00"):
        protected = protected[0].upper() + protected[1:]
        changes.append({"rule": "leading_cap", "before": text[:1], "after": protected[0]})

    out = _restore_spans(protected, slots)
    # Deduplicate identical consecutive changes noise
    return out, changes

# ---------------------------------------------------------------------------
# grammar_check (user-requested correction)
# ---------------------------------------------------------------------------
_WORD_LIST = None

def word_list():
    global _WORD_LIST
    if _WORD_LIST is None:
        if WORDS_PATH.is_file():
            _WORD_LIST = set(WORDS_PATH.read_text("utf-8").split())
        else:
            _WORD_LIST = set()
    return _WORD_LIST

def _edits1(word):
    letters = "abcdefghijklmnopqrstuvwxyz"
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    deletes = [L + R[1:] for L, R in splits if R]
    transposes = [L + R[1] + R[0] + R[2:] for L, R in splits if len(R) > 1]
    replaces = [L + c + R[1:] for L, R in splits if R for c in letters]
    inserts = [L + c + R for L, R in splits for c in letters]
    return set(deletes + transposes + replaces + inserts)

def suggest_spelling(word: str, n=3):
    w = (word or "").lower()
    wl = word_list()
    if not w.isalpha() or w in wl or len(w) < 3 or not wl:
        return []
    e1 = _edits1(w)
    cand = sorted(e1 & wl)
    if not cand:
        e2 = set()
        for x in list(e1)[:80]:
            e2 |= _edits1(x)
        cand = sorted(e2 & wl)
    # prefer similar length
    cand.sort(key=lambda x: (abs(len(x) - len(w)), x))
    return cand[:n]

def grammar_check(text: str, lang_hints: bool = True) -> dict:
    """Rule-based check. Returns {original, corrected, issues, changed}."""
    original = text or ""
    issues = []
    corrected = original

    def add(rule, span, explanation, suggestion=None, hint_hi=None, start=None):
        issues.append({
            "rule": rule, "span": span, "explanation": explanation,
            "suggestion": suggestion, "hint_hi": hint_hi if lang_hints else None,
            "start": start,
        })

    PHRASES = [
        (r"\bdiscuss about\b", "discuss", "Use 'discuss' without 'about'.",
         "discuss about mat bolen — seedha 'discuss'."),
        (r"\brevert back\b", "revert", "Use 'revert' or 'reply'; 'back' is redundant.",
         "'Revert back' galat hai — 'reply' ya 'revert' kaafi hai."),
        (r"\bdo the needful\b", "please do what is needed",
         "Prefer 'please do what is needed' in clear English.",
         "'Do the needful' office jargon hai; clear English behtar hai."),
        (r"\bsince\s+(\d+)\s+years?\b", r"for \1 years",
         "Use 'for' with a period of time; 'since' with a starting point.",
         "'Since 2 years' → 'for 2 years'."),
        (r"\bI am having ((?:a |an |the )?[a-z]+)\b", r"I have \1",
         "For possession use 'I have', not 'I am having'.",
         "'I am having a car' → 'I have a car'."),
        (r"\bcousin brother\b", "cousin", "'Cousin' already covers brother/sister.",
         "'Cousin brother' → 'cousin'."),
        (r"\bcousin sister\b", "cousin", "'Cousin' already covers brother/sister.",
         "'Cousin sister' → 'cousin'."),
        (r"\bprepone\b", "bring forward",
         "'Prepone' is regional; international English uses 'bring forward'.",
         "'Prepone' India-English hai."),
        (r"\bkindly\s+revert\b", "please reply", "Prefer 'please reply'.", None),
        (r"\bmore better\b", "better", "Avoid double comparatives.", "'More better' → 'better'."),
        (r"\bmost easiest\b", "easiest", "Avoid double superlatives.", None),
        (r"\bvery unique\b", "unique", "'Unique' is absolute; skip 'very'.", None),
    ]
    for pat, sug, expl, hi in PHRASES:
        m = re.search(pat, corrected, flags=re.I)
        if not m:
            continue
        replacement_text = re.sub(pat, sug, m.group(0), flags=re.I)
        add("phrase_" + re.sub(r"[^a-z0-9]+", "_", m.group(0).lower())[:28], m.group(0), expl, replacement_text, hi, m.start())
        corrected = re.sub(pat, sug, corrected, count=1, flags=re.I)

    PAIRS = [
        (r"\bits a\b", "it's a", "its_contraction", "Use it's (= it is) here.", "Yahan 'it's' (= it is)."),
        (r"\bits an\b", "it's an", "its_contraction", "Use it's (= it is) here.", None),
        (r"\byour welcome\b", "you're welcome", "your_youre", "Use you're (= you are).", None),
        (r"\bdifferent than\b", "different from", "different_from", "Prefer 'different from'.", None),
    ]
    for pat, sug, rule, expl, hi in PAIRS:
        m = re.search(pat, corrected, re.I)
        if m:
            add(rule, m.group(0), expl, sug, hi, m.start())
            corrected = re.sub(pat, sug, corrected, count=1, flags=re.I)

    for m in list(re.finditer(r"\b(He|She|It)\s+(go|do|have|want|need|like|make|take|come|say)\b", corrected)):
        fix = {"go": "goes", "do": "does", "have": "has", "want": "wants", "need": "needs",
               "like": "likes", "make": "makes", "take": "takes", "come": "comes", "say": "says"}[m.group(2)]
        add("sva_he_she_it", m.group(0), f"With {m.group(1)}, use '{fix}'.", f"{m.group(1)} {fix}",
            "He/She/It ke saath verb +s.", m.start())
        corrected = corrected[:m.start()] + f"{m.group(1)} {fix}" + corrected[m.end()]

    polished, ch = polish_reply(corrected)
    for c in ch:
        if c["rule"] in ("a_an", "repeated_word", "space_before_punct", "double_space",
                         "space_after_punct", "cap_after_end", "its_contraction", "you_are", "its_it_is"):
            add(c["rule"], c["before"], f"Polish: {c['rule'].replace('_', ' ')}.", c["after"], None, None)
    corrected = polished

    wl = word_list()
    if wl:
        for m in re.finditer(r"\b[A-Za-z]{4,}\b", original):
            w = m.group(0)
            if w.lower() in wl or w.isupper():
                continue
            sug = suggest_spelling(w, 1)
            if sug and sug[0] in _edits1(w.lower()):
                add("spelling", w, "Possible spelling mistake.", sug[0], None, m.start())

    return {
        "original": original,
        "corrected": corrected,
        "issues": issues,
        "changed": corrected != original,
    }


# ---------------------------------------------------------------------------
# Optional AI grammar JSON validation
# ---------------------------------------------------------------------------
GRAMMAR_AI_SYSTEM = """You are a careful English editor for short texts.
Return ONLY JSON:
{"corrected":"<full corrected text>","changes":[{"before":"...","after":"...","reason":"..."}]}
Rules:
- Fix grammar, articles, agreement, punctuation and clear word choice.
- Do NOT change meaning, names, numbers, code, URLs or quoted text.
- If the text is already fine, return it unchanged with "changes":[].
- Keep the same language mix if the user mixed Hindi/English on purpose; only fix the English parts.
- Simple reasons (one short line each)."""

def validate_grammar_ai_json(raw: str, original: str) -> dict | None:
    if not raw:
        return None
    t = raw.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I | re.S)
    try:
        m = re.search(r"\{.*\}", t, re.S)
        obj = json.loads(m.group(0) if m else t)
    except Exception:
        return None
    corr = obj.get("corrected")
    if not isinstance(corr, str) or not corr.strip():
        return None
    # Reject huge rewrites that look like a new essay
    if len(corr) > max(40, int(len(original or "") * 2.5)):
        return None
    changes = obj.get("changes") or []
    if not isinstance(changes, list):
        changes = []
    clean = []
    for c in changes[:40]:
        if not isinstance(c, dict):
            continue
        b, a = c.get("before"), c.get("after")
        if isinstance(b, str) and isinstance(a, str):
            clean.append({"before": b, "after": a, "reason": str(c.get("reason") or "")[:200]})
    return {"corrected": corr, "changes": clean}

# Style rules for system prompt (Jiv's replies)
STYLE_RULES = """
## Clear English in your replies
- Write short, clear sentences. Prefer everyday words.
- Use correct articles (a/an/the), agreement (he goes), and punctuation.
- Do not start a summary with a dangling 'This' or 'It' unless the referent is obvious in the same sentence.
- Keep numbers, names and negations (not / never / no) exact — never drop a 'not'.
- When summarising sources, stay faithful: if sources disagree, say so briefly."""

SUMMARIZE_AI_HINT = """When you summarise, keep every important negation and number, use complete sentences,
and end with a short source list if tools provided URLs. Prefer clarity over flourish."""
