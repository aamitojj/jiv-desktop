"""Agent loop: streaming, native tool calls with JSON fallback, permissions, delegation."""
import asyncio, json, re, time, uuid
from . import tools as T
from . import uploads as U
from .providers import stream_chat, ProviderError
from . import reliability as R
from . import english as EN
from . import prompt_improve as PI
import httpx
from urllib.parse import urlparse

# ---------------- JSON tool-call fallback ----------------
FALLBACK_INSTR = """
## Tools
You can use tools. To call one, reply with ONLY a block like this (no other text in that reply):
<tool_call>{{"name": "TOOL_NAME", "arguments": {{...}}}}</tool_call>
After you call a tool you will receive its result, then continue. When you are done, answer normally without a tool_call block.
Available tools:
{tools}
"""

def _param_sig(props):
    """Compact parameter list for small models' JSON-fallback prompt: name (type|enum), ... (1.6.3, keeps the prompt < 10k chars)."""
    out = []
    for k, v in (props or {}).items():
        v = v or {}
        t = "|".join(map(str, v["enum"])) if v.get("enum") else v.get("type", "any")
        if t == "array" and isinstance(v.get("items"), dict): t = f"list of {v['items'].get('type', 'any')}"
        out.append(f"{k} ({t})")
    return ", ".join(out) or "none"

def _balanced_objects(s):
    """Yield top-level {...} substrings (string-aware)."""
    depth, start, ins, esc = 0, None, False, False
    for i, ch in enumerate(s):
        if ins:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': ins = False
            continue
        if ch == '"': ins = True
        elif ch == "{":
            if depth == 0: start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0: yield start, i + 1

def _loads(s):
    try: return json.loads(s)
    except Exception:
        s2 = re.sub(r",\s*([}\]])", r"\1", s)                    # trailing commas
        s2 = re.sub(r"(?<![\\w\"])'([^']*)'", r'"\1"', s2)          # single quotes
        s2 = s2.replace("True", "true").replace("False", "false").replace("None", "null")
        try: return json.loads(s2)
        except Exception: return None

def _norm(obj, known):
    if not isinstance(obj, dict): return None
    if "function" in obj and isinstance(obj["function"], dict): obj = obj["function"]
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
    args = obj.get("arguments", obj.get("parameters", obj.get("args", obj.get("input", {}))))
    if isinstance(args, str): args = _loads(args) or {"input": args}
    if name in known and isinstance(args, dict): return {"name": name, "arguments": args}
    return None

def parse_tool_calls(text, known):
    """Return (clean_text, [calls]). Handles <tool_call>, ```json fences, and bare JSON."""
    calls, spans = [], []
    for m in re.finditer(r"(?s)<tool_?call>\s*(.*?)\s*(?:</tool_?call>|$)", text):  # <tool_call> (Qwen/Hermes) and <toolcall> (Nemotron)
        for a, b in _balanced_objects(m.group(1)):
            c = _norm(_loads(m.group(1)[a:b]), known)
            if c: calls.append(c)
        spans.append(m.span())
    if not calls:
        for m in re.finditer(r"(?s)```(?:json|tool|tool_call|javascript)?\s*(.*?)```", text):
            for a, b in _balanced_objects(m.group(1)):
                c = _norm(_loads(m.group(1)[a:b]), known)
                if c: calls.append(c); spans.append(m.span())
    if not calls:
        for a, b in _balanced_objects(text):
            c = _norm(_loads(text[a:b]), known)
            if c: calls.append(c); spans.append((a, b))
    clean = text
    for a, b in sorted(spans, reverse=True): clean = clean[:a] + clean[b:]
    return clean.strip(), calls

# ---------------- permissions ----------------
class Permissions:
    def __init__(self, cfg, db):
        self.cfg, self.db, self.pending = cfg, db, {}
    def auto(self, name):
        if self.cfg.get("auto_approve_all"): return True   # Settings → "Auto-approve all actions" (off by default)
        aa = self.cfg.get("always_allow") or []
        if name in aa or (name == "run_skill" and "run_code" in aa): return True
        if not T.needs_permission(name) and self.cfg.get("auto_reads", True): return True
        return False
    async def ask(self, emit, agent, name, args):
        pid = uuid.uuid4().hex[:10]
        fut = asyncio.get_running_loop().create_future()
        self.pending[pid] = fut
        await emit({"type": "permission", "id": pid, "tool": name, "args": args, "agent": agent["name"],
                    "summary": describe(name, args)})
        try: decision = await fut
        finally: self.pending.pop(pid, None)
        if decision == "always":
            aa = list(self.cfg.get("always_allow") or [])
            if name not in aa: aa.append(name); self.cfg.update({"always_allow": aa})
        await emit({"type": "permission_resolved", "id": pid, "decision": decision})
        return decision in ("once", "always")
    def resolve(self, pid, decision):
        f = self.pending.get(pid)
        if f and not f.done(): f.set_result(decision); return True
        return False

ASSISTANT_RULES = """

## What you do
You are a general-purpose assistant, not just a tool runner. You can attempt any task the model can do, and you write the result yourself, directly in your reply:
questions and quizzes (MCQs; NCLEX, JEE, NEET and other exam-style questions with options, answer key and rationale), explanations and study notes,
articles, web pages and marketing copy, rewrites and translations, stories, roleplay and personas, plans, opinions, analysis of files the user shares, and code.
Tools are optional extras for touching the computer or the web. They never limit what you can write: most requests need no tool at all.
For ordinary writing and study tasks, write the answer yourself. If something is outside what you can do (for example this model cannot see images,
or a fact may be outdated), say so briefly and still help with what you can."""

ADULT_RULES = """

## Sensitive topics
Answer ordinary questions about sexual health, relationships, consent and related science or medicine helpfully and factually, without lecturing.
Do not invent medical advice, diagnoses or sources. Leave erotic or pornographic fiction to the model's own judgement; Jiv does not instruct the model to produce it.
Decline briefly when asked for sexual content involving minors / under 18, sexual content about real identifiable people without consent, or help with clearly illegal harm."""

SELF_UPDATE_RULES = """

## Only when asked to change Jiv itself
This applies ONLY when the user asks you to change Jiv's own code, features or interface (not to anything you write for the user or their own projects).
The way to do it is the propose_update tool (full file contents; the user reviews and applies it on the Updates page). Never pretend: do not run echo/shell
commands or write files that only look like progress. If you are not confident the code is correct, say so plainly."""
SELF_UPDATE_RULES_SMALL = """

## Only when asked to change Jiv's own code or features
This rule is ONLY for requests to add a feature to Jiv, change Jiv's interface, or fix/update Jiv's own source code. It never applies to writing
questions, documents, websites, code or anything else for the user.
For those Jiv-change requests only: a small model can't reliably write updates to Jiv's source, so don't call run_shell, write_file or propose_update
for it and never run commands that only pretend to make progress. Say so in 2-4 sentences, point to the "⬆ Updates" page (install a .jivupdate file,
or "Check for updates"), and mention a larger model (e.g. qwen2.5-coder:14b, or a cloud model in Settings) can draft updates with propose_update."""

TOOL_GUIDE_SMALL = """

## Tools (optional)
- Most requests need no tool: write the answer yourself. Use a tool only when the task needs the computer, files or the web.
- Call ONE tool at a time with its exact name and valid JSON arguments. Then wait for the result.
- Never invent tool results, file contents or command output. If a tool fails, say what failed.
- To run or test code: run_code with "language" (python, javascript, java, c, cpp, go, rust, csharp, php, ruby, powershell) and the COMPLETE program in "code". If it fails, fix it and call run_code again (at most 3 tries).
- To read a file or an attachment: read_file (files attached in chat are already in the message; use read_file with the attachment id to read more). To search the web: web_search. For a grounded answer with citations, prefer web_summarize (extractive; do not invent facts beyond its sources). To correct a sentence the user asks about: grammar_check.
- When the user tells you a lasting fact or preference, call remember. When you need it later, call recall.
- If a saved skill matches the task, use run_skill with its name.
- For big jobs (a whole file of questions or pages, "repeat N times", more than about 20 questions), call start_batch once instead of doing every item in chat."""

EXAMPLE_SMALL = """

## Example
User: Write 3 NCLEX practice MCQs on potassium.
Assistant:
**1.** A client taking furosemide has a potassium level of 3.1 mEq/L. Which finding should the nurse expect?
A) Peaked T waves  B) Muscle weakness  C) Hyperactive reflexes  D) Bradycardia
**Answer: B.** Rationale: Hypokalemia causes muscle weakness and flattened T waves; peaked T waves and bradycardia point to hyperkalemia.

**2.** Which food should the nurse suggest to a client who needs more potassium?
A) White bread  B) Apple juice  C) Baked potato  D) Cheddar cheese
**Answer: C.** Rationale: Potatoes are high in potassium; the other foods contain little.

**3.** A client's potassium is 6.2 mEq/L. Which order should the nurse carry out first?
A) Place the client on a cardiac monitor  B) Give a banana  C) Encourage fluids  D) Recheck in 24 hours
**Answer: A.** Rationale: Severe hyperkalemia can cause fatal dysrhythmias, so monitoring comes first; a banana adds potassium and waiting is unsafe.
(End of example. Answer real requests the same direct way: no refusal, no "I can't", just the content.)"""

REFUSAL_RE = re.compile(
    r"\b(?:unable to (?:assist|help|create|generate|write|provide|fulfil+|complete|understand)|(?:can ?not|can't|cannot) fulfill|(?:can ?not|can't|cannot) (?:assist|help) with"
    r"|my (?:capabilities|abilities|functions) are (?:currently )?limited|limited to the (?:functions|tools)|(?:do not|don't) have the (?:ability|capability|functionality)"
    r"|i'?m not able to (?:create|generate|write|help|assist|provide)|(?:not able to provide)|i can only (?:use|help with|assist with) (?:the )?(?:tools|functions)|suggest (?:you )?(?:use|try) a (?:study )?(?:app|website)"
    r"|please (?:rephrase|re-phrase) your (?:question|request)|rephrase your (?:question|request)"
    r"|(?:can ?not|can't|unable to) (?:provide|give|share) (?:you )?(?:a |my )?(?:personal |honest )?opinions?|as an ai(?: language model)?,? i (?:can ?not|can't|am unable|don't have)|i (?:can ?not|can't|won't) (?:create|generate|write|provide) (?:that|this|these|multiple|mcq|question))", re.I)
SELF_CHANGE_RE = re.compile(r"\b(?:jiv|yourself|your (?:own )?(?:code|interface|ui|features?|app))\b.*\b(?:add|change|modify|update|fix|feature|button|interface|theme|redesign|upgrade)\b"
                            r"|\b(?:add|change|modify|update|fix|upgrade)\b.*\b(?:jiv|yourself|your (?:own )?(?:code|interface|ui|features?|app))\b", re.I)
NUDGE = ("(Note from Jiv: you can do this. It is a normal writing task - write the full answer yourself now, directly, "
         "in the requested format. Do not apologise, refuse or say you lack the ability; tools are not needed for writing. "
         "If something limits accuracy, add one short note at the end.)")

def looks_like_refusal(text):
    t = (text or "").strip()
    return bool(t) and len(t) < 1500 and bool(REFUSAL_RE.search(t[:700]))

def is_self_change(query):
    return bool(SELF_CHANGE_RE.search(query or ""))


ADULT_RE = re.compile(
    r"\b(?:sex(?:ual|y)?|nude|naked|nudity|erotic|porn(?:ography)?|nsfw\w*|xxx|fetish|kink(?:y)?|orgasm|masturbat\w*|blow\s*job|hand\s*job|"
    r"oral sex|anal(?: sex)?|bdsm|bondage|threesome|hentai|erotica|make\s*love|make\s*out|hook\s*up|"
    r"steamy|romance scene|romantic scene|adult (?:scene|story|fiction|content)|explicit|"
    r"dick|cock|pussy|vagina|penis|boobs?|breasts?|nipples?|ass(?:hole)?|cum(?:shot)?|ejaculat\w*|"
    r"horny|aroused|seduc\w*|strip(?:tease)?|lingerie|onlyfans)\b", re.I)
PRUDE_RE = re.compile(
    r"(?:explicit content|not appropriate|inappropriate|not comfortable|I'?m not comfortable|family[- ]friendly|"
    r"against my (?:guidelines|policies|principles)|I must (?:remind|adhere)|it'?s important to remember|"
    r"adhere to (?:the |your |my )?(?:guidelines|policies|rules)|guidelines provided|"
    r"respectful and appropriate|keep (?:this |things |our conversation )?(?:respectful|appropriate)|"
    r"please (?:rephrase|re-phrase) your (?:question|request)|rephrase your (?:question|request)|"
    r"unable to understand|not able to provide|cannot fulfill|can't fulfill|goes against my|programmed to (?:engage|only|not)|designed to (?:provide|engage|only)|helpful and respectful|"
    r"I can(?:'?t|not) engage|I (?:won'?t|will not|cannot|can'?t) (?:write|create|generate|discuss|describe|roleplay) (?:explicit|erotic|sexual|adult|nsfw)|"
    r"as an ai(?: language model)?[,.]? I (?:must|have to|should) (?:decline|refuse)|"
    r"I (?:prefer|need) to keep (?:things|this|our conversation) (?:appropriate|respectful|safe)|"
    r"content (?:policy|filter|guidelines)|I (?:can(?:'?t|not)|won'?t) (?:help|assist) with (?:that|explicit|adult|sexual|nsfw))", re.I)
MINOR_RE = re.compile(
    r"\b(?:child(?:ren)?|kid(?:s)?|minor(?:s)?|underage|under[- ]?age|preteen|pre-teen|loli(?:ta)?|shota|"
    r"schoolgirl|schoolboy|pedophil\w*|under\s*18|under\s*seventeen|\b1[0-7]\s*(?:yo|y/?o|year)\b|"
    r"teen(?:ager)?s?\b(?!.*\b(?:adult|18\+|eighteen))|young (?:girl|boy)s?)\b", re.I)
REAL_PERSON_RE = re.compile(
    r"\b(?:celebrity|celebrities|actress(?:es)?|actor(?:s)?|real (?:person|people|name)|"
    r"my (?:coworker|co-worker|ex|ex-\w+|neighbour|neighbor|boss|classmate|teacher|colleague|manager|"
    r"sister|brother|mom|dad|mother|father|wife|husband|girlfriend|boyfriend))\b", re.I)
ILLEGAL_HARM_RE = re.compile(
    r"\b(?:build(?:ing)? (?:a )?bomb|make (?:a )?bomb|synthesize meth|cook meth|revenge\s*porn|"
    r"hidden (?:camera|cam)|spy\s*cam|non[- ]consensual|without (?:their |his |her )?consent|"
    r"spike(?:d|s|ing)? (?:(?:her|his|their|someone'?s?) )?(?:drink|drink)|drug(?:ging)? (?:her|him|them|someone)|"
    r"date[- ]rape|roofie|how to (?:kill|murder|poison) (?:someone|a person|my)|"
    r"child\s*porn|csam|make (?:a )?bomb|pipe\s*bomb)\b", re.I)

def is_adult_topic(query):
    return bool(ADULT_RE.search(query or ""))

def looks_like_prudish(text, query=""):
    """Prudish refusal of an adult topic (not a hard-limit decline)."""
    if not is_adult_topic(query): return False
    t = (text or "").strip()
    return bool(t) and len(t) < 1500 and bool(PRUDE_RE.search(t[:700]) or looks_like_refusal(t))

def is_hard_limit(query):
    """Sexual content about minors, real people without consent, or clearly illegal harm — do not retry."""
    q = query or ""
    if ILLEGAL_HARM_RE.search(q): return True
    adult = is_adult_topic(q)
    if adult and MINOR_RE.search(q): return True
    if adult and REAL_PERSON_RE.search(q): return True
    # minor + sexual even if ADULT_RE missed a euphemism
    if MINOR_RE.search(q) and re.search(r"\b(?:sex|nude|naked|porn|erotic|nsfw|fuck|rape)\b", q, re.I): return True
    return False


WRITING_RE = re.compile(r"^\W*(?:(?:please|pls|hey|hi|ok|okay)\W+)?(?:(?:can|could|would|will) you\W+(?:please\W+)?|i (?:want|need) you to\W+|help me\W+|let'?s\W+)?"
    r"(?:write|create|make|generate|draft|compose|rewrite|reword|paraphrase|summari[sz]e|explain|describe|translate|give me|list|suggest|brainstorm|plan|outline|"
    r"pretend|act as|roleplay|role-play|imagine|quiz me|tell me|teach me|prepare|design|come up with|produce|edit|proofread|improve|shorten|expand|compare|analy[sz]e)\b"
    r"|[:;]\s*(?:write|create|make|generate|explain|describe|list|give me|summari[sz]e|rewrite|draft)\b"
    r"|\b(?:mcqs?|nclex|jee|neet|practice questions?|quiz)\b", re.I)
NEEDS_TOOLS_RE = re.compile(r"\b(?:files?|folders?|attach\w*|run|execute|install|open|launch|search|google|web|online|internet|url|https?|website'?s? (?:source|html)|download|upload|my (?:pc|computer|desktop|laptop|disk|drive)|"
    r"remember|recall|forget|save|store|batch|skill|clipboard|system info|delete|email|agent|delegate|today'?s|latest|current|news|price|weather|shell|terminal|command|powershell|cmd|test (?:it|this|the code))\b"
    r"|[a-z]:\\|~/|/home/|\.(?:json|csv|txt|md|html?|py|docx?|xlsx?|pdf|zip)\b"
    r"|\b(?:program|script|function|code|python|javascript|typescript|java|c\+\+|c#|golang|rust|php|ruby|sql|regex|bash)\b", re.I)   # code: keep run_code (test + skills)
def is_writing_task(query):
    """A pure writing request (MCQs, explanations, articles, rewrites, code to show, roleplay...) that needs no tool."""
    q = (query or "").strip()
    return bool(q) and bool(WRITING_RE.search(q)) and not NEEDS_TOOLS_RE.search(q) and not is_self_change(q)

MCQ_ASK_RE = re.compile(r"\b(\d{1,5})\s+(?:[\w-]+\s+){0,5}?(?:mcqs?|questions?|quiz(?:zes)?|items?)\b", re.I)
def batch_suggestion(query):
    """For chat requests for many questions (> 20): suggest the Batch NCLEX/MCQ generator. -> dict or None"""
    q = query or ""
    if not re.search(r"\b(?:mcqs?|questions?|quiz|nclex|jee|neet|exam|practice test)\b", q, re.I): return None
    m = MCQ_ASK_RE.search(q)
    if not m or int(m.group(1)) <= 20: return None
    n = min(10000, int(m.group(1)))
    tm = re.search(r"\b(?:on|about|covering|for)\s+(.+?)(?:[.?!]|\bfor my\b|\bwith\b|$)", q, re.I)
    topic = (tm.group(1).strip() if tm else "")[:120]
    nclex = bool(re.search(r"nclex|nurs", q, re.I))
    return {"count": n, "topic": topic, "template": "Generate NCLEX" if nclex else "Repeat: generate N original MCQs"}

# ---------- 1.5: answer quality helpers ----------
WRITING_GUIDE = """
## Writing well (follow exactly)
- Do exactly what was asked: the same topic, number of items, level and format. If N items are asked for, write all N, numbered 1..N.
- MCQs: for each one write **N.** the question, then each option on its own line as A) B) C) D), then **Answer: X** and
  **Rationale:** 1-3 sentences on why it is right and why the others are wrong. Exactly one correct option; plausible distractors; no "all of the above".
- Articles, posts and pages: a title, short paragraphs (2-4 sentences), a heading every 2-3 paragraphs, original wording, no filler.
- Explanations: one-sentence answer first, then the key points as a short list, then one example.
- Code: one complete, runnable block, then 1-3 lines on how to use it.
- Never invent numbers, quotes or sources; if unsure, say "(please double-check)"."""

THINK_INSTR = """
## Think first
This request needs reasoning. First think step by step inside <think> ... </think> (keep it short, at most 10 lines).
Then, after </think>, write the final answer for the user. Never stop after </think>."""

CLARIFY_INSTR = """
## The request is very short
Give a short best-effort answer, then ask ONE short question about what the user wants (topic, how many, level or format)."""

THINK_RE = re.compile(r"\b(?:calculate|compute|solve|how many|how much|why|prove|derive|compare|which (?:is|one|should)|better|pros and cons|"
                      r"step[- ]by[- ]step|reason|logic|puzzle|riddle|plan|strategy|debug|explain why|what if|estimate|probability|percent(?:age)?|"
                      r"equation|formula|interest|profit|ratio|average)\b|\d\s*[-+*/^x×÷]\s*\d|\d\s*%|\bwhat is \d", re.I)
def needs_thinking(query, mode="auto"):
    if mode == "on": return True
    if mode != "auto" or not query: return False
    if is_writing_task(query) and not re.search(r"\b(?:solve|calculate|prove|compute|compare)\b", query, re.I): return False
    return bool(THINK_RE.search(query[:600]))

THINK_BLOCK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.S | re.I)
def strip_think(text): return THINK_BLOCK_RE.sub("", text or "").strip()

VAGUE_RE = re.compile(r"^\W*(?:please\W+)?(?:write|make|create|generate|give(?: me)?|do|help(?: me)?|questions?|mcqs?|quiz|essay|article|notes?|"
                      r"something|anything|improve(?: it)?|fix(?: it)?|make it better|do it|explain|summari[sz]e|translate|code|program|story)"
                      r"(?:\W+(?:me|something|some|it|this|that|one|questions?|mcqs?|an? \w+))?\W*$", re.I)
def is_vague(query, first=True):
    q = (query or "").strip()
    return bool(first and q and len(q.split()) <= 4 and VAGUE_RE.match(q))

LONG_FORM_RE = re.compile(r"\b(?:essay|article|story|letter|report|blog|speech|notes|summary of|\d{2,5}\s*words|paragraphs?|pages?|detailed|in detail|full)\b", re.I)
MCQ_COUNT_RE = re.compile(r"\b(\d{1,2})\s+(?:[\w-]+\s+){0,5}?(?:mcqs?|multiple[- ]choice questions?|questions?)\b", re.I)
def answer_problem(query, text):
    """Why a final answer looks unusable (for the one automatic retry), or None."""
    body = strip_think(text)
    if not body: return "empty"
    if not is_writing_task(query): return None
    if len(body) < 40 and LONG_FORM_RE.search(query or ""): return "short"   # 1.5.3: concise answers to normal questions are fine
    m = MCQ_COUNT_RE.search(query or "")
    if m and re.search(r"\bmcqs?|multiple[- ]choice|questions?\b", query, re.I):
        want = int(m.group(1))
        if 2 <= want <= 20:
            got = len(re.findall(r"(?im)^\W*(?:\*\*)?(?:answer|correct answer)\b", body)) or len(re.findall(r"(?m)^\W*A[).:]", body))
            if got and got < want: return f"count:{got}/{want}"
            if not got and want >= 2: return "format"
    return None

def problem_nudge(kind):
    if kind == "empty": return "(Note from Jiv: your last reply was empty. Answer the user's request now - write the full answer after any thinking.)"
    if kind == "short": return "(Note from Jiv: your last reply was too short. Write the complete answer the user asked for now.)"
    if kind.startswith("count:"):
        got, want = kind[6:].split("/")
        return (f"(Note from Jiv: you wrote {got} of the {want} questions asked. Write all {want} now, numbered 1..{want}, each with A) B) C) D) "
                "options on separate lines, **Answer: X** and **Rationale:**.)")
    return ("(Note from Jiv: write the questions in this format - numbered, options A) B) C) D) on separate lines, then **Answer: X** and "
            "**Rationale:** for each one.)")

PROBLEM_NOTICE = {"refusal": "↻ The model declined a task it can do - asking it again with a clearer instruction.",
                  "empty": "↻ The model returned an empty answer - asking it again.",
                  "short": "↻ The answer was too short - asking the model for the complete answer.",
                  "format": "↻ The questions were missing options or answer keys - asking the model to fix the format.",
                  "count": "↻ The model wrote fewer questions than you asked for - asking it for all of them."}

def is_small_model(model):
    """Rough size check from the model name: <= 8B parameters, or 'mini'/'tiny' models."""
    m = (model or "").lower()
    if any(k in m for k in ("mini", "tiny", "gemma:2b", "gemma2:2b", "smollm")): return True
    nums = re.findall(r"(\d+(?:\.\d+)?)b\b", m)
    if nums: return float(nums[-1]) <= 8
    return False

def suggest_skill_name(task):
    w = [x for x in re.findall(r"[A-Za-z0-9]+", task or "") if x.lower() not in ("a", "an", "the", "please", "can", "you", "write", "me", "to", "that", "and", "for", "of", "in", "it", "run", "code", "program", "script", "make")]
    return " ".join(w[:5]).lower() or "my skill"

def describe(name, a):
    if name == "run_shell":
        sh = a.get("shell") or "auto"
        if sh == "auto" or (not T.IS_WIN and sh in ("powershell", "cmd")): sh = T.default_shell()
        return f"{sh} > {a.get('command','')}"
    if name == "open_app" and T.IS_MAC: return f"open {a.get('target')}"
    if name == "write_file": return f"write {len(a.get('content',''))} chars → {a.get('path')}" + (" (append)" if a.get("append") else "")
    if name == "delete_file": return f"delete {a.get('path')}"
    if name == "open_app": return f"open {a.get('target')}"
    if name == "http_post": return f"POST {a.get('url')}"
    if name == "propose_update":
        fl = [f.get("path", "?") for f in (a.get("files") or []) if isinstance(f, dict)] + [f"delete {d}" for d in (a.get("deletes") or [])]
        return f"build a local update '{a.get('title', '')}' changing {len(fl)} file(s): {', '.join(fl)[:300]}\n→ syntax-check, then run the test suite in a temporary copy (nothing is applied yet)"
    if name == "run_code":
        code = str(a.get("code", "")); n = code.count("\n") + 1
        return f"run {a.get('language', '?')} code ({n} line{'s' if n != 1 else ''}) in the Jiv workspace, timeout {a.get('timeout') or 30}s:\n" + code[:600] + ("\n…" if len(code) > 600 else "")
    if name == "run_skill": return f"run saved skill '{a.get('name')}'"
    if name == "save_skill": return f"save skill '{a.get('name')}': {a.get('description', '')[:120]}"
    if name == "start_batch": return describe_batch(a)
    if name == "create_agent": return f"create agent '{a.get('name')}' (tools: {', '.join(a.get('tools') or ['*'])})"
    if name == "web_search": return f"search the web for: {a.get('query','')}"
    if name == "web_fetch": return f"fetch {a.get('url','')}"
    if name == "grammar_check": return f"check English grammar of: {(a.get('text') or '')[:180]}"
    if name == "web_summarize":
        u = (a.get("urls") or "").strip()
        return (f"summarise URLs ({a.get('mode') or 'extractive'}): {u[:200]}" if u
                else f"research & summarise ({a.get('mode') or 'extractive'}): {a.get('query','')}")
    return json.dumps(a)[:300]

BATCH_MGR = {"m": None}
def batch_spec(a):
    spec = {k: a.get(k) for k in ("prompt", "count", "source", "template", "verify", "start", "limit", "topic") if a.get(k) not in (None, "")}
    if spec.get("template") and str(spec.get("prompt", "")).strip().lower() in ("", spec["template"].lower()): spec.pop("prompt", None)
    t = str(spec.get("template") or "").lower()
    if "nclex" in t: spec["template"] = "Generate NCLEX"
    elif t in ("mcq", "mcqs", "quiz", "questions"): spec["template"] = "Rewrite MCQ"
    elif "adsense" in t or t in ("page", "pages", "html", "website"): spec["template"] = "Rewrite page for AdSense"
    return spec
def describe_batch(a):
    m = BATCH_MGR["m"]
    if not m: return json.dumps(a)[:300]
    from . import batch as BT
    try: return "start a background batch job (one permission for the whole job, no per-item prompts):\n" + BT.summary(m.preview(batch_spec(a)))
    except Exception as e: return f"start a batch job - but it can't start yet: {e}"

# ---------------- runner ----------------
class Runner:
    def __init__(self, cfg, db, perms):
        self.cfg, self.db, self.perms = cfg, db, perms
        self.native_ok = {}  # model -> bool
        self.learn = None    # set by the server (learning.Learning)
        self.turn = {}       # cid -> {"fails": n, "ok_code": {...}} for the current run

    def system_prompt(self, agent, allowed, fallback, query="", think=False, clarify=False):
        p = agent["persona"].replace("{name}", agent["name"])
        p += f"\n\nYour name is {agent['name']}. Today is {time.strftime('%A %d %B %Y')}. OS: {T.platform.platform()}."
        if self.cfg.get("user_name"): p += f" The user's name is {self.cfg.get('user_name')}."
        others = [a["name"] for a in self.db.agents() if a["id"] != agent["id"]]
        if others: p += f"\nOther agents you can delegate to: {', '.join(others)}."
        if agent.get("memory"): p += f"\n\n## Your memory notes\n{agent['memory']}"
        small = is_small_model(self.conn(agent)[2])
        p += ASSISTANT_RULES + ADULT_RULES + EN.STYLE_RULES + (EXAMPLE_SMALL if small else "")
        p += R.UNDERSTAND_RULES + (R.intent_hint(query) if query else "")
        custom = (self.cfg.get("custom_system_prompt") or "").strip()
        if custom: p += "\n\n## Your custom instructions\n" + custom
        if query and is_writing_task(query): p += WRITING_GUIDE
        if think: p += THINK_INSTR
        if clarify: p += CLARIFY_INSTR
        self.last_memories = []
        if self.learn:
            blk = self.learn.prompt_block(query, budget=900 if small else 1600)
            if blk: p += "\n\n" + blk
            self.last_memories = list(getattr(self.learn, "last_used", []) or [])
        p += (TOOL_GUIDE_SMALL + SELF_UPDATE_RULES_SMALL) if small else SELF_UPDATE_RULES
        if fallback:
            lines = [f"- {s['function']['name']}: {s['function']['description']} "
                     + (f"params: {_param_sig(s['function']['parameters']['properties'])}" if small
                        else f"params={json.dumps(s['function']['parameters']['properties'])}")
                     for s in T.schemas(allowed, small)]
            p += FALLBACK_INSTR.format(tools="\n".join(lines))
        return p

    def history(self, cid, fallback, model=""):
        out = []; self._hist_cid = cid
        msgs = self.db.msgs(cid)
        last_user = max((i for i, m in enumerate(msgs) if m["role"] == "user"), default=-1)
        budget = int(self.cfg.get("attach_chars", 4000) or 4000)  # fits qwen2.5:3b's default context with room to answer
        vision = U.is_vision_model(model)
        for i, m in enumerate(msgs):
            if m["role"] == "user":
                content = m["content"]
                if i == last_user and (self.turn.get(getattr(self, "_hist_cid", ""), {}) or {}).get("answer_query"):
                    content = self.turn[self._hist_cid]["answer_query"]
                atts = [a for a in (U.get(x["id"]) for x in m["meta"].get("attachments") or []) if a]
                if not atts: out.append({"role": "user", "content": content}); continue
                docs, imgs = [a for a in atts if a["kind"] != "image"], [a for a in atts if a["kind"] == "image"]
                blocks = []
                if i == last_user:
                    each = max(500, budget // max(1, len(docs)))
                    blocks += [U.context_block(a, each) for a in docs]
                else:  # older turns: names only, to save context
                    blocks += [f"[Earlier attachment: {a['name']} (id {a['id']}) - use read_file to read it again]" for a in docs]
                parts = []
                for a in imgs:
                    if vision and i == last_user and a["size"] <= 8 * 1024 * 1024:
                        import base64
                        parts.append({"type": "image_url", "image_url": {"url": f"data:{a['mime']};base64," + base64.b64encode(U.file_path(a).read_bytes()).decode()}})
                    else:
                        blocks.append(f"[Image attached: {a['name']}. You cannot see images with the current model ({model}); say so, and suggest the user installs a "
                                      f"vision model such as qwen2.5vl:3b or llava:7b in Manage models.]" if not vision else f"[Earlier image: {a['name']}]")
                textc = (content + "\n\n" + "\n\n".join(blocks)).strip()
                out.append({"role": "user", "content": [{"type": "text", "text": textc}] + parts if parts else textc})
            elif m["role"] == "assistant":
                tc = m["meta"].get("tool_calls")
                if tc and not fallback:
                    out.append({"role": "assistant", "content": m["content"] or "", "tool_calls": [
                        {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}} for c in tc]})
                elif tc:
                    blocks = "".join(f'<tool_call>{json.dumps({"name": c["name"], "arguments": c["arguments"]})}</tool_call>' for c in tc)
                    out.append({"role": "assistant", "content": ((m["content"] or "") + "\n" + blocks).strip()})
                else: out.append({"role": "assistant", "content": strip_think(m["content"]) or m["content"]})
            elif m["role"] == "tool":
                if fallback: out.append({"role": "user", "content": f"[Tool result: {m['meta'].get('name')}]\n{m['content']}"})
                else: out.append({"role": "tool", "tool_call_id": m["meta"].get("tool_call_id"), "content": m["content"]})
        return out

    def conn(self, agent):
        return self.cfg.get("base_url"), self.cfg.get("api_key", ""), agent.get("model") or self.cfg.get("model")

    async def run(self, agent, cid, emit, depth=0):
        """Run agent on conversation until it answers. Returns final text."""
        allowed = agent["tools"] or []
        base, key, model = self.conn(agent)
        if not model: raise RuntimeError("No model selected. Open Settings and pick a model.")
        known = T.all_tool_names()
        final = ""
        primary, fb_used = model, False
        small = is_small_model(model)
        query = next((m["content"] for m in reversed(self.db.msgs(cid)) if m["role"] == "user"), "")
        if depth == 0: self.turn[cid] = {"fails": 0, "ok_code": None}
        nudge = None          # quality guard: one retry with a stronger instruction, not saved in the chat
        first_try = None      # the reply that triggered the retry (kept if the retry is worse)
        msgs_all = self.db.msgs(cid)
        think = depth == 0 and needs_thinking(query, self.cfg.get("think_mode", "auto"))
        clarify = depth == 0 and self.cfg.get("clarity_nudge", True) and is_vague(query, not any(m["role"] == "assistant" for m in msgs_all))
        if clarify:
            await emit({"type": "hint", "text": "Tip: small models answer best with details - for example \"Write 5 NCLEX MCQs on heart failure with rationales\" "
                                                 "instead of \"" + query.strip()[:40] + "\"."})
        used_mem = []
        guard = self.cfg.get("refusal_guard", True) and not is_self_change(query) and not is_hard_limit(query)
        last_user = next((m for m in reversed(self.db.msgs(cid)) if m["role"] == "user"), None)
        # small models: answer pure writing requests directly, without offering tools (they otherwise detour through run_code/run_shell)
        direct = (small and depth == 0 and self.cfg.get("direct_writing", True) and last_user is not None
                  and not (last_user["meta"] or {}).get("attachments") and is_writing_task(query))
        # 1.5.4: one bounded prompt-improve step (original stays in the DB / chat)
        if depth == 0 and last_user is not None:
            await self.maybe_improve_prompt(agent, cid, query, last_user, base, key, model, emit)
            query = (self.turn.get(cid) or {}).get("answer_query") or query
            # recompute writing/direct using the text we will actually answer
            direct = (small and depth == 0 and self.cfg.get("direct_writing", True) and last_user is not None
                      and not (last_user["meta"] or {}).get("attachments") and is_writing_task(query))
            # if improve asked a clarification, that IS the answer — stop here
            clar = (self.turn.get(cid) or {}).get("improve_clarify")
            if clar:
                mid = self.db.add_msg(cid, "assistant", clar, {"agent": agent["name"], "prompt_improve": "clarify"})
                await emit({"type": "step", "agent": agent["name"], "depth": 0})
                await emit({"type": "text", "delta": clar, "depth": 0})
                await emit({"type": "msg_saved", "id": mid, "conv_id": cid})
                return clar
            declined = (self.turn.get(cid) or {}).get("improve_declined")
            if declined:
                mid = self.db.add_msg(cid, "assistant", declined, {"agent": agent["name"], "prompt_improve": "declined"})
                await emit({"type": "step", "agent": agent["name"], "depth": 0})
                await emit({"type": "text", "delta": declined, "depth": 0})
                await emit({"type": "msg_saved", "id": mid, "conv_id": cid})
                return declined
        for step in range(int(self.cfg.get("max_steps", 12))):
            native = self.cfg.get("native_tools", True) and self.native_ok.get((base, model), True)
            no_tools = direct
            msgs = [{"role": "system", "content": self.system_prompt(agent, allowed, not native and not no_tools, query, think, clarify)}] + self.history(cid, not native, model)
            if step == 0 or not used_mem: used_mem = getattr(self, "last_memories", [])
            if nudge: msgs.append({"role": "user", "content": nudge})
            text, calls, usage = "", [], None
            await emit({"type": "step", "agent": agent["name"], "depth": depth})
            t0 = time.time(); t_first = None
            if depth == 0: self.turn.setdefault(cid, {})["partial"] = ""
            try:
                async for kind, val in stream_chat(base, key, model, msgs, float(self.cfg.get("temperature", 0.7)),
                                                   T.schemas(allowed, small) if native and not no_tools else None,
                                                   timeout=float(self.cfg.get("stall_timeout", 180) or 180)):
                    if kind == "text":
                        if t_first is None: t_first = time.time()
                        text += val; await emit({"type": "text", "delta": val, "depth": depth})
                        if depth == 0: self.turn[cid]["partial"] = text
                    elif kind == "tool_calls": calls = val
                    elif kind == "usage": usage = val
            except ProviderError as e:
                if native and e.tools_unsupported and not text:
                    self.native_ok[(base, model)] = False
                    await emit({"type": "notice", "text": "Model has no native tool support — using JSON tool mode."})
                    continue
                fb = None if fb_used or depth else await self.pick_fallback(agent, cid, model, f"server error {e.status}", emit)
                if fb:
                    model, fb_used, nudge, small = fb, True, None, is_small_model(fb); continue
                raise
            except (httpx.TimeoutException, httpx.TransportError) as e:
                reason = "timed out" if isinstance(e, httpx.TimeoutException) else "could not connect"
                fb = None if fb_used or depth else await self.pick_fallback(agent, cid, model, reason, emit)
                if fb:
                    model, fb_used, nudge, small = fb, True, None, is_small_model(fb); continue
                raise ProviderError(504 if reason == "timed out" else 503, f"The model {reason} ({model}).")
            parsed = []
            for c in calls:
                a = c["arguments"]
                args = (_loads(a) if isinstance(a, str) else a) or {}
                if a and isinstance(a, str) and not args: args = {}
                parsed.append({"id": c.get("id") or uuid.uuid4().hex[:8], "name": c["name"], "arguments": args})
            if no_tools: parsed = []
            elif not parsed:   # fallback parse (also catches models that emit JSON in text)
                clean, fc = parse_tool_calls(text, known)
                if fc:
                    parsed = [{"id": "fc_" + uuid.uuid4().hex[:8], **c} for c in fc]
                    text = clean
                    await emit({"type": "replace_text", "text": text, "depth": depth})
            stats = None
            if depth == 0 and text:
                n_tok = (usage or {}).get("completion_tokens") or max(1, round(len(text) / 4))
                gen = max(0.05, time.time() - (t_first or t0))
                stats = {"tokens": int(n_tok), "secs": round(time.time() - t0, 1), "first_token": round((t_first or t0) - t0, 2),
                         "tps": round(n_tok / gen, 1), "estimated": not (usage or {}).get("completion_tokens")}
            tp = R.technical_problem(text) if not parsed and depth == 0 and not fb_used else None
            if tp and (tp == "garbled" or nudge is not None or not self.cfg.get("quality_guard", True)):
                fb = await self.pick_fallback(agent, cid, model, "empty answer" if tp == "empty" else "garbled output", emit)
                if fb:
                    await emit({"type": "replace_text", "text": "", "depth": depth})
                    model, fb_used, nudge, first_try, small = fb, True, None, None, is_small_model(fb); continue
            if not parsed and nudge is None and not fb_used and self.cfg.get("quality_guard", True) and not is_self_change(query) and not is_hard_limit(query):
                clean = strip_think(text)
                refused = looks_like_refusal(clean)
                prude = (not refused) and looks_like_prudish(clean, query)
                # 1.5.3: content/safety/adult declines are shown as-is — no rewrite, no other model
                if refused and (R.is_safety_decline(clean, query) or is_adult_topic(query) or is_hard_limit(query)):
                    prob = None
                elif prude:
                    prob = None
                elif refused:
                    # capability refusals only; when refusal_guard is off, show as-is (do not reclassify as format/short)
                    prob = "refusal" if guard else None
                else:
                    prob = answer_problem(query, text) if depth == 0 else None
                if prob == "empty" and depth > 0: prob = None
                if prob:
                    if prob == "refusal": nudge = NUDGE
                    else: nudge = problem_nudge(prob)
                    first_try = text
                    self.db.audit(agent["name"], "refusal_guard" if prob == "refusal" else "quality_guard", {"problem": prob, "reply": text[:300]}, "retry")
                    await emit({"type": "replace_text", "text": "", "depth": depth})
                    await emit({"type": "notice", "text": PROBLEM_NOTICE[prob.split(":")[0]]})
                    continue
            if not parsed and first_try and len(strip_think(first_try)) > len(strip_think(text)) and (not strip_think(text) or looks_like_refusal(strip_think(text)) ):
                text = first_try; await emit({"type": "replace_text", "text": text, "depth": depth})   # the retry was worse: keep the first answer
            meta = {"tool_calls": parsed, "agent": agent["name"]} if parsed else {"agent": agent["name"]}
            if stats: meta["stats"] = stats
            if fb_used: meta["fallback"] = {"from": primary, "model": model}
            if not parsed and depth == 0 and used_mem: meta["memories"] = used_mem
            if depth == 0: self.turn.setdefault(cid, {})["partial"] = ""
            if not parsed and depth == 0 and self.cfg.get("polish_english", True) and text:
                polished, pch = EN.polish_reply(text)
                if pch and polished != text:
                    text = polished
                    meta["english_polish"] = [{"rule": c["rule"], "before": c["before"], "after": c["after"]} for c in pch[:20]]
                    await emit({"type": "replace_text", "text": text, "depth": depth})
            mid = self.db.add_msg(cid, "assistant", text, meta)
            if stats: await emit({"type": "stats", "depth": depth, **stats})
            if not parsed:
                if depth == 0:
                    await emit({"type": "msg_saved", "id": mid, "conv_id": cid})
                    if used_mem: await emit({"type": "memories_used", "id": mid, "items": used_mem})
                    bs = batch_suggestion(query)
                    if bs and not any(m["role"] == "tool" and m["meta"].get("name") == "start_batch" for m in self.db.msgs(cid)[-6:]):
                        await emit({"type": "batch_suggest", **bs})
                    ok = (self.turn.get(cid) or {}).get("ok_code")
                    if ok and self.learn and not self.learn.db.q("SELECT 1 FROM skills WHERE script=?", (ok["code"],)):
                        await emit({"type": "skill_offer", "language": ok["language"], "code": ok["code"], "task": query[:300],
                                    "name": suggest_skill_name(query), "description": query[:160]})
                return text
            final = text
            for c in parsed:
                res = await self.call_tool(agent, {**c, "_cid": cid}, emit, depth)
                self.db.add_msg(cid, "tool", res, {"tool_call_id": c["id"], "name": c["name"]})
        return final + "\n\n(Stopped: reached the step limit.)"


    async def maybe_improve_prompt(self, agent, cid, query, last_user, base, key, model, emit):
        """At most one improve attempt. Never saves over the user's message. Never uses another model."""
        st = self.turn.setdefault(cid, {})
        has_att = bool((last_user.get("meta") or {}).get("attachments"))
        if is_hard_limit(query):
            await emit({"type": "prompt_improved", "status": "skipped", "reason": "hard_limit", "original": query, "improved": query})
            return
        ok, reason = PI.should_improve(query, has_attachments=has_att, enabled=self.cfg.get("prompt_improve", True))
        if not ok:
            await emit({"type": "prompt_improved", "status": "skipped", "reason": reason, "original": query, "improved": query})
            return
        timeout = float(self.cfg.get("prompt_improve_timeout", 25) or 25)
        await emit({"type": "notice", "text": "✎ Clarifying your question…"})
        result = await PI.improve(query, base=base, key=key, model=model, timeout=timeout, use_model=True)
        # content decline in non-JSON prose
        if result["status"] == "failed" and result.get("reason") == "non_json" and looks_like_refusal(result.get("raw") or ""):
            # any content/capability refusal from the improve step is shown as-is (never rewritten around)
            st["improve_declined"] = result["raw"]
            self.db.audit(agent["name"], "prompt_improve", {"status": "declined", "original": query[:200]}, "shown")
            await emit({"type": "prompt_improved", "status": "declined", "original": query, "improved": query, "reason": "content_decline"})
            return
        status = result["status"]
        improved = result.get("improved") or query
        self.db.audit(agent["name"], "prompt_improve",
                      {"status": status, "reason": result.get("reason"), "original": query[:200], "improved": improved[:200]},
                      "ok" if status in ("used", "unchanged", "clarify") else "skip")
        if status == "clarify" and result.get("clarify"):
            st["improve_clarify"] = result["clarify"]
            await emit({"type": "prompt_improved", "status": "clarify", "original": query, "improved": query, "clarify": result["clarify"]})
            return
        if status == "used" and improved.strip() and improved.strip() != query.strip():
            st["answer_query"] = improved.strip()
            await emit({"type": "prompt_improved", "status": "used", "original": query, "improved": improved.strip()})
        else:
            # timeout/failed/unchanged — answer the original; say so when we tried and fell back
            ev = {"type": "prompt_improved", "status": status if status != "unchanged" else "unchanged",
                  "original": query, "improved": query, "reason": result.get("reason")}
            await emit(ev)

    async def pick_fallback(self, agent, cid, primary, reason, emit):
        """At most one fallback per user message, only to an installed Ollama model on the same server. Never downloads."""
        st = self.turn.setdefault(cid, {})
        if st.get("fb_used") or not self.cfg.get("fallback_enabled", True): return None
        st["fb_used"] = True
        base, ourl = self.cfg.get("base_url") or "", self.cfg.get("ollama_url") or "http://localhost:11434"
        if urlparse(base).netloc != urlparse(ourl).netloc: return None          # cloud / other server: no Ollama models to fall back to
        try: inst = await R.installed_models(ourl)
        except Exception: return None
        fb = R.choose_fallback(inst, primary, self.cfg.get("fallback_models") or [], float(self.cfg.get("fallback_max_gb", 3.5) or 3.5))
        if not fb: return None
        self.db.audit(agent["name"], "fallback_model", {"from": primary, "to": fb, "reason": reason}, "retry")
        await emit({"type": "replace_text", "text": "", "depth": 0})
        await emit({"type": "notice", "text": R.FALLBACK_NOTICE.format(primary=primary, reason=reason, fallback=fb)})
        return fb

    async def call_tool(self, agent, c, emit, depth):
        name, args = c["name"], c["arguments"]
        allowed = agent["tools"] or []
        await emit({"type": "tool_start", "id": c["id"], "tool": name, "args": args, "agent": agent["name"], "depth": depth})
        if name not in T.all_tool_names() or not ("*" in allowed or name in allowed or name in ("list_agents", "remember", "recall", "forget")):
            res = f"Error: tool '{name}' is not available to this agent."
            await emit({"type": "tool_result", "id": c["id"], "status": "error", "result": res}); return res
        if self.perms.auto(name): decision = "auto"
        else:
            ok = await self.perms.ask(emit, agent, name, args)
            decision = "allowed" if ok else "denied"
        if decision == "denied":
            self.db.audit(agent["name"], name, args, "denied")
            res = "The user DENIED permission for this action. Do not retry it; tell the user and offer an alternative."
            await emit({"type": "tool_result", "id": c["id"], "status": "denied", "result": res}); return res
        try:
            if name in T.TOOLS: res = await T.TOOLS[name][0](**args)
            elif name == "run_code": res = await self.t_run_code(agent, emit, depth, {**args, "_cid": c.get("_cid")})
            else: res = await getattr(self, "t_" + name)(agent, emit, depth, dict(args))
            status = "ok"
        except TypeError as e: res, status = f"Error: bad arguments: {e}", "error"
        except Exception as e: res, status = f"Error: {type(e).__name__}: {e}", "error"
        self.db.audit(agent["name"], name, args, decision, res)
        await emit({"type": "tool_result", "id": c["id"], "status": status, "result": str(res)[:6000]})
        return str(res)

    # meta tools
    async def t_create_agent(self, agent, emit, depth, a):
        name, persona, model, tools = a.get("name") or "", a.get("persona") or "", a.get("model") or "", a.get("tools")
        if not name.strip(): return "Error: name required"
        if self.db.agent_by_name(name): return f"An agent named '{name}' already exists."
        if not persona.strip(): return "Error: persona required"
        tools = tools or ["*"]
        bad = [t for t in tools if t != "*" and t not in T.all_tool_names()]
        if bad: tools = [t for t in tools if t not in bad]
        new = self.db.create_agent(name.strip(), persona.strip(), model, tools)
        await emit({"type": "agents_changed"})
        return f"Created agent '{new['name']}' (id {new['id']}, tools: {', '.join(tools)}). The user can now chat with it from the sidebar."

    async def t_propose_update(self, agent, emit, depth, a):
        from . import selfupdate, updater as U
        try:
            meta = await asyncio.to_thread(selfupdate.propose, a.get("title") or "Local update", a.get("changelog") or "",
                                           a.get("files") or [], a.get("deletes") or [], a.get("version"), self.cfg.get("update_test_cmd") or None)
        except U.UpdateError as e:
            return f"Update rejected: {e}"
        diff = U.diff_preview(U.staged(meta["id"])[0] / "package.jivupdate") if meta.get("manifest") else []
        await emit({"type": "update_proposal", "update": meta, "diff": diff})
        status = "all checks passed" if meta.get("checks_ok") else "SOME CHECKS FAILED"
        if not meta.get("ok"): status = "package invalid: " + meta.get("error", "")
        return (f"Built local update {meta.get('manifest', {}).get('version')} (id {meta['id']}) – {status}. "
                "It is shown to the user as a card; it will only be applied if they click Apply and approve. "
                + " | ".join(f"{c['name']}: {'ok' if c['ok'] else 'FAILED ' + c.get('output', '')[-300:]}" for c in meta.get("checks", [])))

    async def t_list_agents(self, agent, emit, depth, a):
        return "\n".join(f"- {a['name']}{' (main)' if a['is_main'] else ''}: {a['persona'][:120].replace(chr(10),' ')} | tools: {', '.join(a['tools'])}" for a in self.db.agents())

    async def t_remember(self, agent, emit, depth, a):
        note = str(a.get("note") or a.get("text") or "").strip()
        if not note: return "Error: nothing to remember"
        mid = self.learn.add(note, a.get("kind") or ("pref" if re.search(r"\b(prefer|like|always|never|want)", note, re.I) else "fact"), "remember")
        await emit({"type": "memory_saved", "text": note, "id": mid})
        return f"Saved to memory (#{mid})."

    async def t_recall(self, agent, emit, depth, a):
        hits = self.learn.recall(str(a.get("query", "")), k=8, kinds=("fact", "pref", "lesson"), budget=1500)
        return "\n".join(f"#{m['id']} [{m['kind']}] {m['text']}" for m in hits) or "Nothing relevant in memory."

    async def t_forget(self, agent, emit, depth, a):
        gone = self.learn.forget(str(a.get("what") or a.get("id") or ""))
        return ("Forgot: " + "; ".join(gone)) if gone else "No matching memory found."

    async def t_run_code(self, agent, emit, depth, a):
        from . import languages as LG
        cid = a.pop("_cid", None)
        r = await LG.run_code(a.get("language"), a.get("code") or "", a.get("stdin") or "", a.get("timeout") or 30)
        st = self.turn.setdefault(cid, {"fails": 0, "ok_code": None})
        if r.get("stage") == "setup": return LG.format_result(r)
        if r["ok"]: st["ok_code"] = {"language": r["language"], "code": a.get("code") or ""}; return LG.format_result(r)
        st["fails"] += 1
        return LG.format_result(r, attempt=st["fails"], max_attempts=int(self.cfg.get("code_retries", 3)))

    async def t_save_skill(self, agent, emit, depth, a):
        sk = self.learn.save_skill(a.get("name"), a.get("description") or "", a.get("steps") or "", a.get("language") or "", a.get("script") or "")
        await emit({"type": "skills_changed"}); return f"Saved skill '{sk['name']}'."

    async def t_run_skill(self, agent, emit, depth, a):
        from . import languages as LG
        sk = self.learn.skill(a.get("name") or "")
        if not sk: return "No skill with that name. Available: " + ", ".join(s["name"] for s in self.learn.skills()[:20])
        if not sk["script"]:
            self.learn.used_skill(sk["id"], True); return f"Skill '{sk['name']}' steps:\n{sk['steps'] or sk['description']}"
        r = await LG.run_code(sk["language"], sk["script"], a.get("stdin") or "")
        self.learn.used_skill(sk["id"], bool(r.get("ok")))
        return f"Skill '{sk['name']}':\n" + LG.format_result(r)

    async def t_start_batch(self, agent, emit, depth, a):
        m = BATCH_MGR["m"]
        if not m: return "Batch jobs are not available."
        try: j = await asyncio.to_thread(m.create, batch_spec(a))
        except ValueError as e: return f"Could not start the batch job: {e}"
        self.db.audit(agent["name"], "start_batch(job)", {"job": j["id"], "items": j["total"]}, "allowed")
        await emit({"type": "batch_started", "job": {k: j[k] for k in ("id", "name", "total", "output_dir")}})
        return (f"Started batch job '{j['name']}' with {j['total']} item(s) (job id {j['id']}). It runs in the background; progress, pause/cancel and "
                f"downloads are on the Batch page. Results will be saved in {j['output_dir']}. Tell the user this briefly.")

    async def t_delegate_to_agent(self, agent, emit, depth, a):
        target_name = str(a.get("agent") or a.get("agent_name") or a.get("name") or "")
        task = str(a.get("task") or "")
        target = self.db.agent_by_name(target_name)
        if not target: return f"No agent named '{target_name}'. Use list_agents."
        if target["id"] == agent["id"]: return "You can't delegate to yourself."
        if depth >= 2: return "Delegation depth limit reached."
        conv = self.db.create_conv(target["id"], f"Task from {agent['name']}: {task[:40]}")
        self.db.add_msg(conv["id"], "user", f"[Delegated by {agent['name']}] {task}")
        await emit({"type": "delegate", "to": target["name"], "task": task, "conv_id": conv["id"]})
        async def sub_emit(ev):
            if ev["type"] in ("text", "replace_text", "step"): ev = {**ev, "depth": depth + 1}
            ev.setdefault("agent", target["name"]); await emit(ev)
        res = await self.run(target, conv["id"], sub_emit, depth + 1)
        await emit({"type": "agents_changed"})
        return f"{target['name']} replied:\n{res}"
