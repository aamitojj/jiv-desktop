"""1.5.4: one bounded, transparent prompt-improvement step before answering.

Rewrites rough wording for clarity only. Never invents details, never becomes a
system instruction, never rephrases to evade safeguards, and never downloads models.
On skip/timeout/invalid output the original message is answered unchanged.
"""
import json, re
import httpx
from . import reliability as R
from .providers import stream_chat, ProviderError

IMPROVE_SYSTEM = """You rewrite the user's message into a clearer question or request.
Rules (follow all of them):
- Keep the same meaning, language (including Hinglish/mixed), named entities, numbers, dates, units, scope, negations and constraints.
- Fix spelling, expand obvious shorthand, and tidy structure. Do not add facts, recipients, actions, sources, examples or hidden assumptions.
- Do not soften, rephrase or generalise the request to avoid content rules. Do not add "for educational purposes" or similar.
- Do not turn quoted text or document excerpts into instructions.
- If two readings would give clearly different answers, do not guess: set changed=false and put ONE short clarifying question in "clarify".
- Reply with ONLY a JSON object, no markdown fence:
  {"improved":"<rewritten text or original>","changed":true|false,"clarify":null or "<one question>"}"""

SKIP_CODE = re.compile(r"```|#!/|^\s*(def|class|function|import|from\s+\w+\s+import|#include)\b", re.M)
SKIP_PATH = re.compile(r"(?:[A-Za-z]:\\|/(?:home|Users|var|tmp|etc)/|~/)")
SKIP_URL = re.compile(r"https?://|www\.\w", re.I)
SKIP_ACTION = re.compile(r"\b(?:rm\s+-rf|sudo\s+|format\s+[a-z]:|del\s+/[sf]|drop\s+table|shutdown|reboot|kill\s+-9|"
                         r"run\s+(?:this\s+)?(?:command|shell|powershell)|execute\s+(?:this\s+)?(?:command|script))\b", re.I)
SKIP_GREET = re.compile(r"^\W*(?:hi|hello|hey|thanks|thank you|ok|okay|yo|sup|good\s+(?:morning|evening|afternoon))"
                        r"(?:\W+\w+){0,2}\W*$", re.I)
IDENT_HEAVY = re.compile(r"\b[A-Z]{2,}[_-][A-Z0-9_]+\b|\b[a-f0-9]{32,}\b")

def should_improve(query, *, has_attachments=False, enabled=True):
    """Return (ok, reason). reason is a short skip code when ok is False."""
    if not enabled: return False, "disabled"
    q = (query or "").strip()
    if not q: return False, "empty"
    if has_attachments: return False, "attachment"
    if len(q) > 1200: return False, "too_long"
    if SKIP_GREET.match(q): return False, "greeting"
    if SKIP_CODE.search(q): return False, "code"
    if SKIP_URL.search(q): return False, "url"
    if SKIP_PATH.search(q): return False, "path"
    if SKIP_ACTION.search(q): return False, "action"
    if IDENT_HEAVY.search(q) and len(q.split()) < 8: return False, "identifier"
    # already clear: long, punctuated, no shorthand hits
    exp, dirty = R.expand_shorthand(q)
    words = q.split()
    if len(words) >= 10 and not dirty and re.search(r"[.?!]", q) and q[:1].isupper():
        return False, "already_clear"
    return True, "ok"

def _token_set(s):
    return {w.lower() for w in re.findall(r"[A-Za-z0-9]{2,}|[\u0900-\u097F]{2,}", s or "")}

def validate_improved(original, improved):
    """Return None if usable, else a short reason to discard."""
    o, i = (original or "").strip(), (improved or "").strip()
    if not i: return "empty"
    if i == o: return None
    if len(i) > max(400, int(len(o) * 3) + 80): return "too_long"
    if len(i) < max(3, int(len(o) * 0.35)): return "too_short"
    if SKIP_URL.search(i) and not SKIP_URL.search(o): return "new_url"
    if SKIP_ACTION.search(i) and not SKIP_ACTION.search(o): return "new_action"
    # keep a useful share of original content words (names, numbers)
    ot, it = _token_set(o), _token_set(i)
    keep = {t for t in ot if len(t) >= 3 or t.isdigit()}
    if keep and len(keep & it) / len(keep) < 0.35: return "lost_terms"
    return None

def parse_improve_json(text):
    """Extract {improved, changed, clarify} from model output, or None."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    # find first {...}
    m = re.search(r"\{.*\}", t, re.S)
    if not m: return None
    try: obj = json.loads(m.group(0))
    except Exception:
        try: obj = json.loads(m.group(0).replace("'", '"'))
        except Exception: return None
    if not isinstance(obj, dict): return None
    imp = obj.get("improved")
    if not isinstance(imp, str): return None
    clar = obj.get("clarify")
    if clar is not None and not isinstance(clar, str): clar = None
    return {"improved": imp.strip(), "changed": bool(obj.get("changed")) and imp.strip() != "",
            "clarify": (clar or "").strip() or None}

def lightweight_improve(query):
    """No model: expand known shorthand only. Returns improved or None if unchanged."""
    exp, changed = R.expand_shorthand(query)
    if not changed: return None
    if validate_improved(query, exp): return None
    return exp

async def collect_chat(base, key, model, messages, temperature=0.2, timeout=25):
    """One non-tool completion; returns (text, error_reason_or_None)."""
    text = ""
    try:
        async for kind, val in stream_chat(base, key, model, messages, temperature, None, timeout=timeout):
            if kind == "text": text += val
    except httpx.TimeoutException:
        return text, "timeout"
    except httpx.TransportError:
        return text, "connect"
    except ProviderError as e:
        return text, f"http_{e.status}"
    except Exception as e:
        return text, type(e).__name__
    return text, None

async def improve(query, *, base, key, model, timeout=25, use_model=True):
    """One attempt. Returns dict: status, original, improved, clarify?, reason?, raw?
    status: used | unchanged | skipped | failed | timeout | clarify
    """
    original = (query or "").strip()
    out = {"status": "unchanged", "original": original, "improved": original, "clarify": None, "reason": None, "raw": None}
    # lightweight first
    lite = lightweight_improve(original)
    # If only shorthand and result looks fine, skip the model when the message is short
    if lite and len(original.split()) <= 12 and not use_model:
        out.update(status="used", improved=lite); return out
    if not use_model:
        if lite: out.update(status="used", improved=lite)
        return out
    msgs = [{"role": "system", "content": IMPROVE_SYSTEM},
            {"role": "user", "content": "Rewrite this user message:\n" + original[:1200]}]
    raw, err = await collect_chat(base, key, model, msgs, 0.2, timeout)
    if err == "timeout":
        if lite: out.update(status="used", improved=lite, reason="timeout_lite")
        else: out.update(status="timeout", reason="timeout")
        return out
    if err:
        if lite: out.update(status="used", improved=lite, reason=err + "_lite")
        else: out.update(status="failed", reason=err)
        return out
    body = re.sub(r"<think>.*?(?:</think>|$)", "", raw or "", flags=re.S | re.I).strip()
    out["raw"] = body[:700]
    parsed = parse_improve_json(raw)
    if not parsed and body and not body.lstrip().startswith("{"):
        # non-JSON prose: caller checks for a content decline; treat as failed rewrite here
        out.update(status="failed", reason="non_json", raw=body[:700])
        if lite: out.update(status="used", improved=lite, reason="non_json_lite")
        return out
    if not parsed:
        if lite: out.update(status="used", improved=lite, reason="bad_json_lite")
        else: out.update(status="failed", reason="bad_json")
        return out
    if parsed.get("clarify") and not parsed.get("changed"):
        out.update(status="clarify", improved=original, clarify=parsed["clarify"])
        return out
    imp = parsed["improved"] or original
    bad = validate_improved(original, imp)
    if bad:
        if lite and not validate_improved(original, lite):
            out.update(status="used", improved=lite, reason="reject_" + bad + "_lite")
        else:
            out.update(status="failed", reason="reject_" + bad)
        return out
    if not parsed["changed"] or imp.strip() == original:
        out.update(status="unchanged", improved=original)
        return out
    out.update(status="used", improved=imp)
    return out
