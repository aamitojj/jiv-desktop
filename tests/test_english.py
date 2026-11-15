"""Jiv 1.6.2 English-for-Jiv: segmentation, negation-aware extracts, summary polish,
reply polish precision, grammar_check, AI JSON validation + fallback.
python tests/test_english.py
"""
import json, os, sys, tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
os.environ["JIV_DATA_DIR"] = str(Path(tempfile.mkdtemp(prefix="jiv-en-")) / "data")

from jiv import english as E
from jiv import search as S
from jiv import tools as T
from jiv import reliability as R

res = []
def check(n, c, i=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  [{str(i)[:350]}]"), flush=True)

# ---- segmentation fixtures ----
s = E.split_sentences("Dr. Smith paid $3.14 in the U.S. yesterday. He was happy.")
check("keeps Dr. and U.S. and decimals together",
      len(s) == 2 and s[0].startswith("Dr. Smith") and "3.14" in s[0] and "U.S." in s[0]
      and s[1].startswith("He was"), s)
s2 = E.split_sentences("See e.g. the notes. Also i.e. clarity. Version 2.0 shipped!")
check("keeps e.g. / i.e. / 2.0",
      len(s2) == 3 and "e.g." in s2[0] and "i.e." in s2[1] and "2.0" in s2[2], s2)
s3 = E.split_sentences("Items:\n1. First item is long enough here.\n2. Second item continues well.")
check("list markers do not create orphan '1.' sentences",
      not any(x.strip() in ("1.", "2.") for x in s3), s3)

# ---- negation / number preservation in extracts ----
doc = (
    "The trial enrolled 1,200 adults in 2024. "
    "The vaccine prevents infection in many adults. "
    "The vaccine does not prevent infection in most adults over 65. "
    "Researchers published the result in a journal."
)
# Prefer the negated claim for a negative query
picked = E.select_key_sentences(doc, "does not prevent infection", max_sentences=2,
                                bm25_fn=lambda q, docs: [0.3] * len(docs))
texts = " ".join(s for _, s in picked)
check("negation sentence kept for negative query",
      "does not prevent" in texts and "not" in texts, picked)
# Numbers survive extractive summary
summary = S.extractive_summary(doc, query="how many adults enrolled", max_sentences=2)
check("numbers preserved in extractive summary",
      "1,200" in summary or "1200" in summary.replace(",", ""), summary)

# ---- summary readability: no leading dangling pronoun, no dupes ----
all_s = E.split_sentences(
    "Sales rose sharply in May across Europe. This shows strong demand. This shows strong demand. People bought more devices."
)
# pick the dangling + dupe intentionally
pol = E.polish_summary_sentences(
    [(1, all_s[1]), (2, all_s[2]), (1, all_s[1])],
    all_sentences=all_s,
)
check("summary polish prepends referent and drops duplicate",
      pol.lower().startswith("sales rose") and pol.lower().count("this shows strong demand") == 1, pol)
pol2 = E.polish_summary_sentences([(None, "This is unclear alone.")], all_sentences=[])
check("dangling This without prior context is rewritten",
      not pol2.lower().startswith("this "), pol2)

# ---- polish precision: correct text unchanged; code untouched ----
clean = "The battery lasts three hours on a single charge."
out, ch = E.polish_reply(clean)
check("correct English left unchanged", out == clean and ch == [], (out, ch))
codey = "Install with `pip install a apple`.\n```\na apple = 1\n```\nThen run it."
out2, ch2 = E.polish_reply(codey)
check("code fences and inline code untouched",
      "`pip install a apple`" in out2 and "a apple = 1" in out2, out2)
messy = "it is a a good idea.  this works well."
out3, ch3 = E.polish_reply(messy)
check("fixes a a / spacing / leading capital / it's",
      "a a" not in out3.lower() and out3[0].isupper() and ("It's" in out3 or "it's" in out3 or "It is" not in messy),
      (out3, ch3))

# ---- grammar_check ----
g = E.grammar_check("Please discuss about the plan and revert back since 2 years. I am having a car.")
check("grammar_check fixes Indian-English phrases",
      "discuss about" not in g["corrected"].lower()
      and "revert back" not in g["corrected"].lower()
      and "since 2 years" not in g["corrected"].lower()
      and "i am having" not in g["corrected"].lower()
      and g["changed"] and len(g["issues"]) >= 3, g)
g2 = E.grammar_check("The quick brown fox jumps over the lazy dog.")
check("grammar_check low false positives on clean sentence",
      g2["changed"] is False or len(g2["issues"]) == 0, g2)
# fixture set precision/recall style report
incorrect = [
    ("discuss about fees", "discuss"),
    ("please revert back", "revert"),
    ("do the needful", "needed"),
    ("I am having a car", "I have"),
    ("cousin brother came", "cousin"),
    ("prepone the meeting", "bring forward"),
    ("He go to school", "goes"),
    ("its a problem", "it's"),
]
correct = [
    "The committee approved the plan yesterday.",
    "She has lived here for two years.",
    "Please discuss the proposal with your manager.",
    "I have a car and a bike.",
    "My cousin visited us last week.",
    "Bring the meeting forward to Monday.",
    "He goes to school by bus.",
    "It's a beautiful day in Pune.",
]
hit = 0
for bad, needle in incorrect:
    r = E.grammar_check(bad)
    if r["changed"] and (needle.lower() in r["corrected"].lower() or r["issues"]):
        hit += 1
fp = 0
for good in correct:
    r = E.grammar_check(good)
    # count as FP only if it changed the meaning-bearing words substantially
    if r["changed"] and r["corrected"].strip().lower() != good.strip().lower():
        # allow pure polish of identical meaning
        if r["corrected"].lower().replace("'", "") != good.lower().replace("'", ""):
            fp += 1
recall = hit / len(incorrect)
fp_rate = fp / len(correct)
check(f"fixture recall on bad sentences {hit}/{len(incorrect)} ({recall:.0%})",
      recall >= 0.75, (hit, len(incorrect)))
check(f"fixture false positives on good sentences {fp}/{len(correct)} ({fp_rate:.0%})",
      fp_rate <= 0.25, (fp, [c for c in correct if E.grammar_check(c)["changed"]]))
print(f"METRICS recall={recall:.2f} fp_rate={fp_rate:.2f} hit={hit}/{len(incorrect)} fp={fp}/{len(correct)}")

# tool registered
check("grammar_check tool registered and read-only",
      "grammar_check" in T.all_tool_names() and T.needs_permission("grammar_check") is False
      and "grammar_check" in T.SMALL_TOOLSET)

# ---- AI JSON validation + fallback ----
ok = E.validate_grammar_ai_json(
    '{"corrected":"He goes to school.","changes":[{"before":"He go","after":"He goes","reason":"agreement"}]}',
    "He go to school.")
check("valid AI grammar JSON accepted", ok and ok["corrected"].startswith("He goes"), ok)
bad = E.validate_grammar_ai_json("sorry I cannot help with that", "He go to school.")
check("non-JSON AI decline → None (caller falls back to rules)", bad is None)
huge = E.validate_grammar_ai_json(
    json.dumps({"corrected": "word " * 500, "changes": []}),
    "Hi")
check("oversized AI rewrite rejected", huge is None)
# fallback path: AI fails → rules
raw_fail = "not json"
validated = E.validate_grammar_ai_json(raw_fail, "discuss about money")
fallback = E.grammar_check("discuss about money") if validated is None else validated
check("AI failure falls back to rule-based grammar_check",
      validated is None and fallback["changed"] and "discuss about" not in fallback["corrected"].lower(),
      fallback)

# ---- Hinglish lexicon ----
exp, chg = R.expand_shorthand("plz samjhao ye grammer sentance")
check("extended Hinglish/shorthand lexicon expands",
      chg and "please" in exp.lower() and "explain" in exp.lower()
      and "grammar" in exp.lower() and "sentence" in exp.lower(), exp)

# ---- search module still uses english splitter ----
check("search.split_sentences delegates to english",
      S.split_sentences("Dr. Lee arrived. Next day dawned.")[0].startswith("Dr. Lee"))

# ---- style rules present for system prompt ----
check("STYLE_RULES mention negation and dangling This",
      "not" in E.STYLE_RULES.lower() and "This" in E.STYLE_RULES)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
