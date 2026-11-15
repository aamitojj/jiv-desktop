"""Deterministic 'model' for Batch tests (non-streaming /chat/completions). It really rewrites arithmetic MCQs, makes
planned mistakes (wrong key, missing field, prohibited text) so retries / verify / flagging can be tested, and its
'verifier' actually solves the sums."""
import json, re
SYN = {"welcome": "hello and welcome", "best": "finest", "sell": "offer", "we": "our team", "our": "the", "shop": "store", "fresh": "newly picked",
       "delivered": "shipped", "door": "home", "every": "each", "order": "purchase", "ships": "is sent", "within": "in under", "days": "days' time",
       "arrives": "comes", "prices": "costs", "start": "begin", "family": "household", "farms": "growers", "small": "little", "buy": "purchase",
       "get": "receive", "free": "at no cost", "weekly": "this week's", "offers": "deals", "about": "all about", "started": "opened", "today": "now",
       "work": "partner", "visit": "travel to", "check": "inspect", "quality": "standard", "write": "send a note", "answer": "reply",
       "use": "pour", "water": "hot water", "because": "since", "makes": "turns", "bitter": "harsh", "steep": "soak", "leaves": "tea leaves",
       "remove": "lift", "keep": "store", "dark": "shaded", "dry": "moisture-free", "jar": "container", "strong": "powerful", "smells": "odours",
       "how": "the way", "brew": "prepare", "storing": "keeping", "tea": "tea", "green": "green", "contact": "get in touch", "has": "contains",
       "less": "lower", "than": "compared with", "is": "gets", "fully": "completely", "comes": "is made", "from": "using", "one": "a single",
       "called": "named", "heated": "warmed", "soon": "shortly", "picking": "harvest", "rolled": "twisted", "left": "allowed", "copyright": "©"}
def reword(t):
    out = []
    for w in re.findall(r"\w+|\W+", t):
        r = SYN.get(w.lower())
        out.append((r[0].upper() + r[1:] if w[:1].isupper() else r) if r else w)
    return "Put simply: " + "".join(out) if len(t.split()) > 6 and not t.lstrip().startswith("#") else "".join(out)

def first_json(s, after=""):
    i = s.find(after) + len(after) if after and after in s else 0
    j = s.find("{", i)
    if j < 0: return None
    try: return json.JSONDecoder().raw_decode(s[j:])[0]
    except Exception: return None

def solve(q):
    m = re.search(r"(\d+)\D+?(\d+)", q or ""); return int(m.group(1)) + int(m.group(2)) if m else None

def reply(msgs, model=""):
    sysm, user = msgs[0]["content"], msgs[-1]["content"]
    retry = "previous answer had these problems" in user
    if sysm.startswith("You are a strict reviewer"):
        if "correct_option" in sysm and "Check this NCLEX-style question" in user:
            rw = first_json(user, "QUESTION:")
            letter = "ABCD"[[o.startswith("Assess") for o in rw["options"]].index(True)]
            ok = letter == str(rw.get("answer")).strip().upper()[:1]
            return json.dumps({"correct_option": letter, "ok": ok, "problems": "" if ok else "assessment comes first"})
        if "correct_option" in sysm:
            rw = first_json(user, "REWRITTEN:")
            s = solve(rw.get("question")); opts = rw.get("options") or [rw.get(k) for k in ("option_a", "option_b", "option_c", "option_d")]
            letter = "ABCD"[[str(o) for o in opts].index(str(s))] if str(s) in [str(o) for o in opts] else "?"
            ok = letter == str(rw.get("answer")).strip().upper()[:1]
            return json.dumps({"correct_option": letter, "ok": ok, "problems": "" if ok else f"{rw.get('question')} = {s}, which is option {letter}"})
        bad = [w for w in ("cracked", "click the ads") if w in user.split("REWRITTEN")[-1].lower()]
        return json.dumps({"ok": not bad, "problems": f"prohibited content: {', '.join(bad)}" if bad else ""})
    if sysm.startswith("You rewrite ONE data item"):
        item = first_json(user)
        if "options" in item or "option_a" in item:
            iid = int(item.get("id") or 0); a, b = map(int, re.findall(r"\d+", item["question"])[:2])
            opts = item.get("options") or [item[k] for k in ("option_a", "option_b", "option_c", "option_d")]
            right = "ABCD"[[str(o) for o in opts].index(str(a + b))]
            key = right
            if iid % 50 == 0 or (iid % 20 == 0 and not retry): key = "ABCD"[("ABCD".index(right) + 1) % 4]   # planned mistakes
            out = dict(item); out["question"] = f"Find the total when {a} is added to {b}."
            out["answer"] = key
            if "explanation" in sysm or True: out["explanation"] = f"Adding {a} and {b} gives {a + b}, which is option {right}."
            if iid % 30 == 7 and not retry: out.pop("explanation")
            return json.dumps(out)
        return json.dumps({k: (reword(v) if isinstance(v, str) else v) for k, v in item.items()})
    if sysm.startswith("You rewrite the visible text"):
        segs = first_json(user, "Text segments:") or first_json(user)
        out = {}
        for k, v in segs.items():
            t = reword(v)
            if not retry: t = t  # keep prohibited phrases on the first try so the verifier catches them
            else: t = re.sub(r"(?i)click the ads on this page to support[^.]*\.", "Thank you for supporting the little family store.", t).replace("cracked software downloads", "")
            out[k] = t
        return json.dumps(out)
    if sysm.startswith("You are a strict reviewer") is False and "NCLEX-style multiple-choice question about:" in user:
        n = int(re.search(r"question (\d+) of", user).group(1)); topic = re.search(r"about: (.+)", user).group(1).strip()
        opts = ["Assess the client's lung sounds and oxygen saturation", "Document the finding in the chart", "Give the next scheduled dose early", "Ask the family to wait outside"]
        key = "A"
        if n % 5 == 0 or (n % 3 == 0 and not retry): key = "C"           # planned wrong keys: 3, 6, 9 fixed on retry; 5, 10 always wrong → flagged
        q = {"question": f"Question {n}: a client admitted with {topic} becomes short of breath. Which action should the nurse take first?", "options": opts,
             "answer": key, "rationale": "Assessment comes first: checking breathing and oxygen guides every next step, the other actions delay care.", "topic": topic}
        if n == 2 and not retry: q.pop("rationale")
        return json.dumps(q)
    # plain text / repeat
    m = re.search(r"number (\d+) of (\d+)", user) or re.search(r"\b(\d+) of (\d+)\b", user)
    if "Rewrite the following text" in user or "\n\n" in user and not m:
        txt = user.split("\n\n", 1)[-1]
        return "Here is the rewritten text:\n" + reword(txt.strip())
    n = m.group(1) if m else "?"
    prev = user.split("Previous output:\n")[-1][:30] if "Previous output:" in user else ""
    if "JSON" in user:
        return json.dumps({"question": f"Original question number {n}: which planet is closest to the Sun?", "options": ["Mercury", "Venus", "Earth", "Mars"],
                           "answer": "Mercury", "explanation": "Mercury orbits closest to the Sun of all the planets."})
    return f"Result of run {n}." + (f" (built on: {prev})" if prev else "")
