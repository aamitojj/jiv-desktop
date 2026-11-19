"""Config + paths. On Windows data lives in %APPDATA%/JivDesktop."""
import json, os, sys, secrets, threading
from pathlib import Path

def data_dir() -> Path:
    if os.environ.get("JIV_DATA_DIR"):
        p = Path(os.environ["JIV_DATA_DIR"])
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", Path.home())) / "JivDesktop"
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / "JivDesktop"
    else:
        p = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "JivDesktop"
    p.mkdir(parents=True, exist_ok=True)
    return p

DEFAULTS = {
    "provider": "ollama",
    "base_url": "http://localhost:11434/v1",
    "api_key": "",
    "model": "",
    "temperature": 0.7,
    "theme": "dark",
    "port": 8765,
    "user_name": "",
    "native_tools": True,      # try native tool calling first
    "auto_reads": True,        # read-only tools run without asking
    "auto_approve_all": False, # 1.4.1: Settings → "Auto-approve all actions (no permission prompts)"
    "refusal_guard": True,     # 1.4.1: retry once when the model refuses a task it can do
    "quality_guard": True,     # 1.5: also retry once on an empty / too short / wrong-format answer
    "think_mode": "auto",      # 1.5: "off" | "auto" (reasoning questions) | "on" - think in <think>..</think>, then answer
    "clarity_nudge": True,     # 1.5: on a very short first message, answer + ask one clarifying question
    "tips_dismissed": False,   # 1.5: the "tips for small models" strip
    "adult_model_suggested": False,  # 1.5.1: one-time banner suggesting the abliterated default
    "fallback_enabled": True,    # 1.5.3: on a technical failure (error/timeout/empty/garbled) answer once with another INSTALLED Ollama model
    "fallback_models": [],       # 1.5.3: preferred fallback models (installed only); empty = smallest installed model under fallback_max_gb
    "fallback_max_gb": 3.5,      # 1.5.3: auto-pick only models up to this size (8 GB RAM friendly)
    "stall_timeout": 180,
    "prompt_improve": True,     # 1.5.4: clarify rough wording once before answering (original stays in chat)
    "prompt_improve_timeout": 25,  # 1.5.4: seconds for the improve call; on timeout answer the original
        # 1.5.3: seconds without any data from the model before it counts as a timeout
    "custom_system_prompt": "",  # 1.5.2: Settings → Custom system prompt (advanced); appended when set
    "batch_parallel": 1,       # 1.5: Batch items processed at once (1 or 2)
    "always_allow": [],        # tool names that never ask
    "max_steps": 12,
    "ollama_url": "http://localhost:11434",
    "setup_done": False,
    "update_repo": "",            # empty = official aamitojj/jiv-desktop; or "yourname/jiv-desktop" – GitHub Releases with a .jivupdate asset
    "auto_install_signed": False, # install SIGNED updates from that repo automatically (when idle)
    "update_test_cmd": "",        # test command for self-made updates ("" = default)
    # 1.6.1 search / summarise
    "searx_url": "",              # optional SearXNG base URL, e.g. http://127.0.0.1:8080
    "search_cache_ttl": 3600,     # seconds to keep search/page cache on disk
    "search_timeout": 12,
    "fetch_timeout": 15,
    "fetch_max_bytes": 1500000,
    "fetch_max_chars": 14000,
    "fetch_concurrency": 4,
    "summary_sentences": 8,
    "summary_mode": "extractive", # extractive works with no AI
    "polish_english": True,       # 1.6.2: polish Jiv's own replies (not user text)
}

PROVIDERS = {
    "ollama":    {"label": "Ollama",        "base_url": "http://localhost:11434/v1", "key": False},
    "lmstudio":  {"label": "LM Studio",     "base_url": "http://localhost:1234/v1",  "key": False},
    "llamacpp":  {"label": "llama.cpp server", "base_url": "http://localhost:8080/v1", "key": False},
    "vllm":      {"label": "vLLM",          "base_url": "http://localhost:8000/v1",  "key": False},
    "jan":       {"label": "Jan",           "base_url": "http://localhost:1337/v1",  "key": False},
    "localai":   {"label": "LocalAI",       "base_url": "http://localhost:8080/v1",  "key": False},
    "openrouter":{"label": "OpenRouter",    "base_url": "https://openrouter.ai/api/v1", "key": True},
    "groq":      {"label": "Groq",          "base_url": "https://api.groq.com/openai/v1", "key": True},
    "together":  {"label": "Together AI",   "base_url": "https://api.together.xyz/v1", "key": True},
    "custom":    {"label": "Custom OpenAI-compatible", "base_url": "", "key": False},
}

_lock = threading.Lock()

class Config:
    def __init__(self):
        self.path = data_dir() / "config.json"
        self.data = dict(DEFAULTS)
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text("utf-8")))
            except Exception:
                pass
        self.save()

    def get(self, k, d=None):
        return self.data.get(k, d)

    def update(self, upd: dict):
        with _lock:
            for k, v in upd.items():
                if k in DEFAULTS:
                    self.data[k] = v
            self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    def public(self):
        d = dict(self.data)
        d["api_key_set"] = bool(d.get("api_key"))
        d["api_key"] = ("••••" + d["api_key"][-4:]) if d.get("api_key") else ""
        return d

SESSION_TOKEN = secrets.token_urlsafe(32)
