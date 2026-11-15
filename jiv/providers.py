"""OpenAI-compatible client: model listing, auto-detect, streaming chat with tool calls."""
import asyncio, json
import httpx
from .config import PROVIDERS

LOCAL_PORTS = [("ollama", 11434), ("lmstudio", 1234), ("llamacpp", 8080), ("vllm", 8000),
               ("jan", 1337), ("localai", 8080), ("custom", 5000), ("custom", 5001)]

def _headers(key):
    h = {"Content-Type": "application/json"}
    if key: h["Authorization"] = f"Bearer {key}"
    h["HTTP-Referer"] = "http://127.0.0.1"; h["X-Title"] = "Jiv Desktop"
    return h

async def list_models(base_url, key="", timeout=6.0):
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=timeout) as c:
        try:
            r = await c.get(base + "/models", headers=_headers(key)); r.raise_for_status()
            data = r.json()
            return sorted({m.get("id") for m in data.get("data", []) if m.get("id")})
        except Exception:
            # Ollama native fallback
            if "11434" in base:
                r = await c.get(base.replace("/v1", "") + "/api/tags"); r.raise_for_status()
                return sorted(m["name"] for m in r.json().get("models", []))
            raise

async def autodetect():
    found, seen = [], set()
    async def probe(name, port):
        url = f"http://localhost:{port}/v1"
        try:
            models = await list_models(url, timeout=1.5)
            return {"provider": name, "label": PROVIDERS[name]["label"], "base_url": url, "models": models}
        except Exception:
            return None
    res = await asyncio.gather(*(probe(n, p) for n, p in LOCAL_PORTS))
    for r in res:
        if r and r["base_url"] not in seen:
            seen.add(r["base_url"]); found.append(r)
    return found

NO_USAGE = set()   # servers that rejected stream_options

async def stream_chat(base_url, key, model, messages, temperature=0.7, tools=None, timeout=300):
    """Yield events: ("text", str) / ("tool_calls", [..]) / ("usage", {..}) / ("done", None)."""
    try:
        async for ev in _stream_chat(base_url, key, model, messages, temperature, tools, base_url not in NO_USAGE, timeout): yield ev
    except ProviderError as e:
        if base_url in NO_USAGE or e.status not in (400, 422) or "stream_options" not in e.text: raise
        NO_USAGE.add(base_url)
        async for ev in _stream_chat(base_url, key, model, messages, temperature, tools, False, timeout): yield ev

async def _stream_chat(base_url, key, model, messages, temperature, tools, want_usage, timeout=300):
    body = {"model": model, "messages": messages, "temperature": temperature, "stream": True}
    if want_usage: body["stream_options"] = {"include_usage": True}
    if tools: body["tools"] = tools; body["tool_choice"] = "auto"
    calls = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(float(timeout or 300), connect=10)) as c:
        async with c.stream("POST", base_url.rstrip("/") + "/chat/completions",
                            headers=_headers(key), json=body) as r:
            if r.status_code >= 400:
                txt = (await r.aread()).decode("utf-8", "replace")[:800]
                raise ProviderError(r.status_code, txt)
            async for line in r.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"): continue
                payload = line[5:].strip()
                if payload == "[DONE]": break
                try: chunk = json.loads(payload)
                except Exception: continue
                if isinstance(chunk.get("usage"), dict) and chunk["usage"].get("completion_tokens"): yield ("usage", chunk["usage"])
                for ch in chunk.get("choices") or []:
                    d = ch.get("delta") or ch.get("message") or {}
                    if d.get("content"): yield ("text", d["content"])
                    for tc in d.get("tool_calls") or []:
                        i = tc.get("index", len(calls))
                        cur = calls.setdefault(i, {"id": tc.get("id") or f"call_{i}", "name": "", "arguments": ""})
                        if tc.get("id"): cur["id"] = tc["id"]
                        f = tc.get("function") or {}
                        if f.get("name"): cur["name"] += f["name"]
                        if f.get("arguments"):
                            a = f["arguments"]; cur["arguments"] += a if isinstance(a, str) else json.dumps(a)
    if calls: yield ("tool_calls", [calls[k] for k in sorted(calls)])
    yield ("done", None)

class ProviderError(Exception):
    def __init__(self, status, text):
        super().__init__(f"HTTP {status}: {text}"); self.status = status; self.text = text
    @property
    def tools_unsupported(self):
        t = self.text.lower()
        return self.status in (400, 422, 500) and ("tool" in t or "function" in t)
