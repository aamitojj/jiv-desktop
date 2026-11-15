"""Practical self-learning without training: memories (facts/preferences), skills (saved working procedures/scripts)
and lessons (from 👍/👎 feedback and corrections). Stored in SQLite; relevant items are retrieved with BM25 and
injected into the system prompt under a small character budget."""
import math, re, time, json
from collections import Counter

STOP = set("""a an the and or but if then so to of in on at for with by from as is are was were be been being am do does did
i me my mine you your yours we our it its this that these those there here what which who whom how why when where can could
would should will shall may might must please just about into over under again also very too not no yes ok okay hi hello
jiv tell give show make let get like want need""".split())

def tokens(s):
    ws = (w.rstrip(".-") for w in re.findall(r"[a-z0-9][a-z0-9_+#.-]*", (s or "").lower()))   # 1.5: "nclex." matches "nclex"
    return [w for w in ws if w not in STOP and len(w) > 1]

def bm25(query, docs, k1=1.4, b=0.75):
    """docs: list of (key, text). Returns [(score, key)] sorted, score > 0 only."""
    q = tokens(query)
    if not q or not docs: return []
    toks = [tokens(t) for _, t in docs]
    N = len(docs); avg = sum(map(len, toks)) / N or 1
    df = Counter(w for t in toks for w in set(t))
    out = []
    for (key, _), t in zip(docs, toks):
        tf = Counter(t); s = 0.0
        for w in set(q):
            if w in tf:
                idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(t) / avg))
        if s > 0: out.append((s, key))
    return sorted(out, reverse=True)

# ---------- automatic fact / preference capture (cheap rules, no model call) ----------
AUTO_PATTERNS = [
    (r"\bremember (?:that )?([\w .,+#'/:@-]{4,160}?)(?:[.!]|$)", "fact", "{0}."),
    (r"\b(?i:my name is) ([A-Z][\w'-]+(?: [A-Z][\w'-]+)?)", "fact", "The user's name is {0}."),
    (r"\b(?i:call me) ([A-Z][\w'-]+)", "pref", "The user wants to be called {0}."),
    (r"\bi(?:'m| am) (?:a|an) ([\w -]{3,40}?)(?:[.,!]|$| and | who )", "fact", "The user is a {0}."),
    (r"\bi work (?:as|at|for) ([\w &.,'-]{2,50}?)(?:[.!]|$| and )", "fact", "The user works {1} {0}."),
    (r"\bi live in ([\w ,.'-]{2,40}?)(?:[.!]|$| and )", "fact", "The user lives in {0}."),
    (r"\bmy (\w+(?: \w+)?) is ([\w@.+ :/'-]{2,60}?)(?:[.!]|$| and )", "fact", "The user's {0} is {1}."),
    (r"\bi (?:really )?(prefer|like|love|hate|dislike|don't like|do not like|use|am using) ([\w .,+#'/-]{3,80}?)(?:[.!]|$)", "pref", "The user {0} {1}."),
    (r"\b(always|never) ([\w .,+#'/-]{4,100}?)(?:[.!]|$)", "pref", "Preference: {0} {1}."),
    # 1.5: more everyday facts and answer preferences
    (r"\bi(?:'m| am) (?:preparing|studying|revising) for (?:the |my |an? )?([\w .,+#'/-]{2,60}?)(?:[.!,]|$| and | exam\b)", "fact", "The user is preparing for {0}."),
    (r"\bi(?:'m| am) (?:studying|learning) ([\w .,+#'/-]{2,60}?)(?:[.!,]|$| and | at | in )", "fact", "The user is studying {0}."),
    (r"\bi(?:'m| am) working on ([\w .,+#'/-]{3,80}?)(?:[.!,]|$| and )", "fact", "The user is working on {0}."),
    (r"\bi(?:'m| am) from ([\w ,.'-]{2,40}?)(?:[.!]|$| and )", "fact", "The user is from {0}."),
    (r"\bi(?:'m| am) (\d{1,2}) years? old\b", "fact", "The user is {0} years old."),
    (r"\bi speak ([\w ,'-]{2,40}?)(?:[.!]|$)", "fact", "The user speaks {0}."),
    (r"\bi have (?:an?|my) (exam|test|interview|deadline|presentation) ((?:on|in|next|this|tomorrow)[\w ,'-]{0,40}?)(?:[.!]|$| and )", "fact", "The user has a {0} {1}."),
    (r"\b(?:always )?(?:answer|reply|respond|write)(?: to me)? in (hindi|english|punjabi|urdu|bengali|tamil|telugu|marathi|gujarati|spanish|french|german|arabic|simple english|hinglish)\b", "pref", "The user wants answers in {0}."),
    (r"\bkeep (?:your |the )?(?:answers?|replies|responses|it) (short|brief|concise|detailed|simple)\b", "pref", "The user wants answers kept {0}."),
]

def auto_extract(text):
    out = []
    if not text or len(text) > 2000 or text.strip().endswith("?"): return out
    for pat, kind, tpl in AUTO_PATTERNS:
        for m in re.finditer(pat, text, 0 if "(?i:" in pat else re.I):
            g = [x.strip(" .,") for x in m.groups()]
            if "works" in tpl: g = [g[0], re.search(r"work (as|at|for)", m.group(0), re.I).group(1).lower()]
            if any(len(x) < 1 for x in g) or (len(g[0]) < 2 and not g[0].isdigit()): continue
            if kind == "fact" and "is a" in tpl and g[0].split()[0].lower() in ("bit", "little", "lot", "big", "fan", "beginner") and g[0].lower() != "beginner": continue
            if "{0} {1}." == tpl[9:] and kind == "pref" and tpl.startswith("The user"):
                g[0] = VERB3.get(g[0].lower(), g[0])
            if tpl == "{0}.":
                g[0] = re.sub(r"\bmy\b", "the user's", re.sub(r"\bI am\b|\bI'm\b", "the user is", re.sub(r"\bI\b", "the user", g[0])))
            if tpl != "{0}." and "works" not in tpl: g = [re.sub(r"\bmy\b", "their", x, flags=re.I) for x in g]
            fact = re.sub(r"\ba ([aeiou])", r"an \1", tpl.format(*g))
            fact = fact[0].upper() + fact[1:]
            if fact.lower().startswith("the user's") and g[0].lower() in ("question", "problem", "code", "error", "issue", "file", "point"): continue
            if tpl == "{0}.": return [(kind, fact)]          # explicit "remember that ..." wins
            if fact.lower() not in [f.lower() for _, f in out]: out.append((kind, fact))
    if any(k == "pref" and not f.startswith("Preference:") for k, f in out):   # "always answer in Hindi" -> keep the specific one
        out = [(k, f) for k, f in out if not f.startswith("Preference:")] or out
    return out[:5]

def near_duplicate(a, b):
    """Same fact in slightly different words (case, punctuation, an extra word or two)."""
    import difflib
    x, y = " ".join(re.findall(r"\w+", (a or "").lower())), " ".join(re.findall(r"\w+", (b or "").lower()))
    if not x or not y: return False
    if x == y: return True
    tx, ty = set(tokens(x)), set(tokens(y))
    if tx and ty and len(tx & ty) / len(tx | ty) >= 0.8 and min(len(tx), len(ty)) >= 2: return True
    return min(len(x), len(y)) >= 12 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.9

VERB3 = {"prefer": "prefers", "like": "likes", "love": "loves", "hate": "hates", "dislike": "dislikes", "don't like": "doesn't like",
         "do not like": "does not like", "use": "uses", "am using": "is using"}

class Learning:
    def __init__(self, db):
        self.db = db
        db.c.executescript("""
        CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, kind TEXT DEFAULT 'fact',
            source TEXT DEFAULT 'auto', created REAL, updated REAL, uses INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS skills(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, description TEXT, steps TEXT,
            language TEXT, script TEXT, created REAL, updated REAL, uses INTEGER DEFAULT 0, last_ok INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT, msg_id INTEGER, rating INTEGER,
            correction TEXT, question TEXT, answer TEXT, memory_id INTEGER, created REAL);
        """)
        self._migrate_agent_notes()

    def _migrate_agent_notes(self):
        """1.2.x stored remember() notes on the agent row; move them into the memory store once."""
        for a in self.db.q("SELECT id, memory FROM agents WHERE memory IS NOT NULL AND memory != ''"):
            for line in a["memory"].splitlines():
                line = line.strip(" -\t")
                if line: self.add(line, "fact", "remember")
            self.db.x("UPDATE agents SET memory='' WHERE id=?", (a["id"],))

    # ---- memories ----
    def add(self, text, kind="fact", source="auto"):
        text = " ".join(str(text or "").split())[:500]
        if len(text) < 3: return None
        if kind not in ("fact", "pref", "lesson"): kind = "fact"
        # de-duplicate: same text, or a newer statement about the same subject replaces the old one
        for m in self.memories(kind=kind):
            if m["text"].lower() == text.lower(): return m["id"]
        for m in self.memories(kind=kind):   # 1.5: near-duplicates are merged (the newer wording wins)
            if near_duplicate(m["text"], text):
                self.update(m["id"], text=text); return m["id"]
        subj = re.match(r"(The user's [\w ]+? is|The user lives in|The user wants to be called|The user's name is)", text)
        if subj:
            for m in self.memories(kind=kind):
                if m["text"].startswith(subj.group(1)):
                    self.update(m["id"], text=text); return m["id"]
        t = time.time()
        return self.db.x("INSERT INTO memories(text,kind,source,created,updated) VALUES(?,?,?,?,?)", (text, kind, source, t, t)).lastrowid

    def merge_duplicates(self):
        """Merge near-duplicate memories of the same kind; keeps the most recent wording and adds up the use counts."""
        merged = 0
        for kind in ("fact", "pref", "lesson"):
            rows = self.memories(kind=kind)   # newest first
            gone = set()
            for i, keep in enumerate(rows):
                if keep["id"] in gone: continue
                for old in rows[i + 1:]:
                    if old["id"] not in gone and near_duplicate(keep["text"], old["text"]):
                        self.db.x("UPDATE memories SET uses=uses+? WHERE id=?", (old["uses"] or 0, keep["id"])); self.delete(old["id"])
                        gone.add(old["id"]); merged += 1
        return merged

    def memories(self, kind=None):
        if kind: return self.db.q("SELECT * FROM memories WHERE kind=? ORDER BY updated DESC", (kind,))
        return self.db.q("SELECT * FROM memories ORDER BY updated DESC")
    def update(self, mid, text=None, kind=None):
        if text is not None: self.db.x("UPDATE memories SET text=?, updated=? WHERE id=?", (" ".join(text.split())[:500], time.time(), mid))
        if kind: self.db.x("UPDATE memories SET kind=?, updated=? WHERE id=?", (kind, time.time(), mid))
    def delete(self, mid): self.db.x("DELETE FROM memories WHERE id=?", (mid,))

    def forget(self, ref):
        """By id, or delete memories that best match the text."""
        if str(ref).strip().lstrip("#").isdigit():
            mid = int(str(ref).strip().lstrip("#")); hit = self.db.q("SELECT text FROM memories WHERE id=?", (mid,))
            self.delete(mid); return [h["text"] for h in hit]
        rows = self.memories(); hits = bm25(ref, [(m["id"], m["text"]) for m in rows])
        if not hits: return []
        top = hits[0][0]; gone = []
        for s, mid in hits:
            if s >= top * 0.8:
                gone += [m["text"] for m in rows if m["id"] == mid]; self.delete(mid)
        return gone

    def recall(self, query, k=6, kinds=("fact", "pref"), budget=700):
        rows = [m for m in self.memories() if m["kind"] in kinds]
        hits = bm25(query, [(m["id"], m["text"]) for m in rows])
        by = {m["id"]: m for m in rows}; out, used = [], 0
        picked = [by[i] for _, i in hits[:k]]
        # preferences + the user's name are always useful, even without keyword overlap
        for m in rows:
            if len(picked) >= k: break
            if m not in picked and (m["kind"] == "pref" or "name is" in m["text"] or "called" in m["text"]): picked.append(m)
        for m in picked:
            if used + len(m["text"]) > budget: break
            out.append(m); used += len(m["text"]) + 3
        if out: self.db.x(f"UPDATE memories SET uses=uses+1 WHERE id IN ({','.join('?' * len(out))})", [m["id"] for m in out])
        return out

    # ---- skills ----
    def save_skill(self, name, description, steps="", language="", script=""):
        name = re.sub(r"[^\w -]", "", str(name or "")).strip()[:60]
        if not name: raise ValueError("skill name required")
        t = time.time()
        if self.db.q("SELECT 1 FROM skills WHERE name=?", (name,)):
            self.db.x("UPDATE skills SET description=?, steps=?, language=?, script=?, updated=? WHERE name=?", (description, steps, language, script, t, name))
        else:
            self.db.x("INSERT INTO skills(name,description,steps,language,script,created,updated) VALUES(?,?,?,?,?,?,?)", (name, description, steps, language, script, t, t))
        return self.skill(name)
    def skills(self): return self.db.q("SELECT * FROM skills ORDER BY uses DESC, updated DESC")
    def skill(self, ref):
        r = self.db.q("SELECT * FROM skills WHERE id=? OR lower(name)=lower(?)", (ref if str(ref).isdigit() else -1, str(ref)))
        if r: return r[0]
        hits = bm25(str(ref), [(s["id"], s["name"] + " " + s["description"]) for s in self.skills()])
        return self.db.q("SELECT * FROM skills WHERE id=?", (hits[0][1],))[0] if hits else None
    def delete_skill(self, sid): self.db.x("DELETE FROM skills WHERE id=?", (sid,))
    def update_skill(self, sid, **kw):
        for k, v in kw.items():
            if k in ("name", "description", "steps", "language", "script"): self.db.x(f"UPDATE skills SET {k}=?, updated=? WHERE id=?", (v, time.time(), sid))
    def used_skill(self, sid, ok): self.db.x("UPDATE skills SET uses=uses+1, last_ok=? WHERE id=?", (1 if ok else 0, sid))
    def relevant_skills(self, query, k=3):
        rows = self.skills(); by = {s["id"]: s for s in rows}
        return [by[i] for _, i in bm25(query, [(s["id"], f"{s['name']} {s['description']} {s['steps']}") for s in rows])[:k]]

    # ---- feedback / lessons ----
    def feedback(self, conv_id, msg_id, rating, correction="", question="", answer=""):
        rating = 1 if int(rating) > 0 else -1; correction = (correction or "").strip()[:600]
        mid = None
        if correction or rating < 0:
            q = " ".join((question or "").split())[:140]
            if correction:
                lesson = f"When asked \"{q}\", the user corrected you: {correction}" if q else f"User correction: {correction}"
            else:
                lesson = f"The user disliked your answer to \"{q}\". Be more careful, accurate and concise on similar requests."
            mid = self.add(lesson, "lesson", "feedback")
        elif rating > 0 and question:
            pass  # 👍 is recorded; it doesn't need prompt space
        old = self.db.q("SELECT id FROM feedback WHERE msg_id=? AND conv_id=?", (msg_id, conv_id))
        if old: self.db.x("DELETE FROM feedback WHERE id=?", (old[0]["id"],))
        self.db.x("INSERT INTO feedback(conv_id,msg_id,rating,correction,question,answer,memory_id,created) VALUES(?,?,?,?,?,?,?,?)",
                  (conv_id, msg_id, rating, correction, question[:500], (answer or "")[:1000], mid, time.time()))
        return {"rating": rating, "lesson_id": mid}
    def feedback_list(self): return self.db.q("SELECT * FROM feedback ORDER BY id DESC LIMIT 200")
    def ratings(self, conv_id):
        return {r["msg_id"]: r["rating"] for r in self.db.q("SELECT msg_id, rating FROM feedback WHERE conv_id=?", (conv_id,))}

    # ---- prompt block ----
    def prompt_block(self, query, budget=1100):
        """Relevant memories + lessons + skills, in at most `budget` characters."""
        parts = []; self.last_used = []
        mem = self.recall(query, k=6, budget=int(budget * 0.45))
        if mem: parts.append("## What you know about the user (from memory)\n" + "\n".join(f"- {m['text']}" for m in mem))
        les = self.recall(query, k=3, kinds=("lesson",), budget=int(budget * 0.3))
        if not les:  # most recent lessons still apply in general
            les = self.memories(kind="lesson")[:2]
        self.last_used = [{"id": m["id"], "text": m["text"][:220], "kind": m["kind"]} for m in list(mem) + list(les)]
        if les: parts.append("## Lessons from the user's feedback (follow them)\n" + "\n".join(f"- {m['text'][:220]}" for m in les))
        sk = self.relevant_skills(query)
        if sk: parts.append("## Saved skills you can use with run_skill\n" + "\n".join(f"- {s['name']}: {s['description'][:120]}" for s in sk))
        out = "\n\n".join(parts)
        return out[:budget]
