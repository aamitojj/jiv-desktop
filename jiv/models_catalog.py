"""Curated free/open models available in Ollama + a hardware-based recommender."""
# size_gb = Ollama download size (default Q4 quant). quality = rough capability score (higher = smarter).
CATALOG = [
    {"name": "qwen2.5:0.5b",       "params": "0.5B", "size_gb": 0.4, "quality": 10, "tools": True,  "kind": "general", "desc": "Tiny, runs on anything; basic chat only"},
    {"name": "llama3.2:1b",        "params": "1B",   "size_gb": 1.3, "quality": 18, "tools": True,  "kind": "general", "desc": "Very small Meta model"},
    {"name": "qwen2.5:1.5b",       "params": "1.5B", "size_gb": 1.0, "quality": 22, "tools": True,  "kind": "general", "desc": "Small and quick, decent tool use"},
    {"name": "qwen2.5-coder:1.5b", "params": "1.5B", "size_gb": 1.0, "quality": 20, "tools": True,  "kind": "coder",   "desc": "Small coding model"},
    {"name": "gemma2:2b",          "params": "2B",   "size_gb": 1.6, "quality": 26, "tools": False, "kind": "general", "desc": "Google Gemma 2 – good chat, no native tools (JSON mode)"},
    {"name": "llama3.2:3b",        "params": "3B",   "size_gb": 2.0, "quality": 34, "tools": True,  "kind": "general", "desc": "Great small all-rounder"},
    {"name": "qwen2.5:3b",         "params": "3B",   "size_gb": 1.9, "quality": 36, "tools": True,  "kind": "general", "desc": "Strong small model with reliable tool calls"},
    {"name": "huihui_ai/qwen2.5-abliterate:3b", "params": "3B", "size_gb": 1.9, "quality": 37, "tools": True, "kind": "general", "adult_default": True,
     "desc": "Recommended adult default – refusal-removed Qwen 2.5 3B (community upload by huihui_ai). Fits a 4 GB GPU"},
    {"name": "dolphin-phi:2.7b",   "params": "2.7B", "size_gb": 1.6, "quality": 28, "tools": False, "kind": "general",
     "desc": "Dolphin-tuned Phi – fewer refusals; no native tool calling (JSON mode)"},
    {"name": "huihui_ai/qwen3-abliterated:4b", "params": "4B", "size_gb": 2.5, "quality": 43, "tools": True, "kind": "general", "thinking": True,
     "desc": "Refusal-removed Qwen 3 4B (community upload). Thinking model; fits a 4 GB GPU"},
    {"name": "phi3:mini",          "params": "3.8B", "size_gb": 2.2, "quality": 33, "tools": False, "kind": "general", "desc": "Microsoft Phi-3 – no native tools (JSON mode)"},
    {"name": "phi4-mini",          "params": "3.8B", "size_gb": 2.5, "quality": 40, "tools": True,  "kind": "general", "desc": "Microsoft Phi-4 mini, supports tools"},
    {"name": "qwen3:4b",           "params": "4B",   "size_gb": 2.6, "quality": 44, "tools": True,  "kind": "general", "thinking": True, "desc": "Qwen 3 small, thinks before answering"},
    {"name": "qwen2.5:7b",         "params": "7B",   "size_gb": 4.7, "quality": 58, "tools": True,  "kind": "general", "desc": "Excellent agent model – best tool calling at this size"},
    {"name": "qwen2.5-coder:7b",   "params": "7B",   "size_gb": 4.7, "quality": 55, "tools": True,  "kind": "coder",   "desc": "Coding specialist"},
    {"name": "llama3.1:8b",        "params": "8B",   "size_gb": 4.9, "quality": 56, "tools": True,  "kind": "general", "desc": "Meta Llama 3.1, popular and capable"},
    {"name": "qwen3:8b",           "params": "8B",   "size_gb": 5.2, "quality": 60, "tools": True,  "kind": "general", "thinking": True, "desc": "Qwen 3, strong reasoning"},
    {"name": "mistral-nemo:12b",   "params": "12B",  "size_gb": 7.1, "quality": 63, "tools": True,  "kind": "general", "vendor": "NVIDIA + Mistral AI",
     "license": "Apache 2.0", "desc": "Mistral NeMo – built by Mistral AI with NVIDIA. Apache 2.0 licence"},
    {"name": "qwen2.5:14b",        "params": "14B",  "size_gb": 9.0, "quality": 70, "tools": True,  "kind": "general", "desc": "Very capable; needs a 12GB+ GPU or lots of RAM"},
    {"name": "qwen2.5-coder:14b",  "params": "14B",  "size_gb": 9.0, "quality": 67, "tools": True,  "kind": "coder",   "desc": "Strong coding model"},
    {"name": "qwen2.5:32b",        "params": "32B",  "size_gb": 20.0, "quality": 80, "tools": True, "kind": "general", "desc": "Near-frontier open model; needs 24GB VRAM"},
    # ---- NVIDIA open models (tags/sizes from ollama.com/library, Oct 2026; local Q4 defaults, cloud-only tags excluded) ----
    {"name": "nemotron-mini:4b",   "params": "4B",   "size_gb": 2.7, "quality": 30, "tools": True,  "kind": "general", "vendor": "NVIDIA",
     "license": "NVIDIA Community Model License", "ctx": "4K",
     "desc": "NVIDIA Nemotron-Mini 4B – small, tuned for function calling, RAG and roleplay (short context). NVIDIA Community Model License"},
    {"name": "nemotron-3-nano:4b", "params": "4B",   "size_gb": 2.8, "quality": 42, "tools": True,  "kind": "general", "vendor": "NVIDIA", "thinking": True,
     "license": "NVIDIA Open Model License", "ctx": "256K",
     "desc": "NVIDIA Nemotron 3 Nano 4B – reasoning + agentic tool use. NVIDIA Open Model License"},
    {"name": "nemotron-3-nano:30b", "params": "30B (3.5B active, MoE)", "size_gb": 24.0, "active_frac": 3.5 / 30, "quality": 78, "tools": True,
     "kind": "general", "vendor": "NVIDIA", "thinking": True, "license": "NVIDIA Open Model License", "ctx": "1M",
     "desc": "NVIDIA Nemotron 3 Nano 30B-A3B – hybrid Mamba/MoE, fast for its size, strong agent model. NVIDIA Open Model License"},
    {"name": "nemotron-3.5-lightning:30b", "params": "30B (3B active, MoE)", "size_gb": 25.0, "active_frac": 3 / 30, "quality": 80, "tools": True,
     "kind": "general", "vendor": "NVIDIA", "thinking": True, "license": "NVIDIA open model licence (see model card)", "ctx": "1M",
     "desc": "NVIDIA Nemotron 3.5 Lightning – 30B MoE built for always-on agents. NVIDIA open model (see model card for licence)"},
    {"name": "nemotron-cascade-2:30b", "params": "30B (3B active, MoE)", "size_gb": 24.0, "active_frac": 3 / 30, "quality": 77, "tools": True,
     "kind": "general", "vendor": "NVIDIA", "thinking": True, "license": "NVIDIA open model licence (see model card)", "ctx": "256K",
     "desc": "NVIDIA Nemotron Cascade 2 – 30B MoE with strong reasoning and agentic skills. NVIDIA open model (see model card for licence)"},
    {"name": "nemotron3:33b",      "params": "33B",  "size_gb": 28.0, "quality": 75, "tools": True,  "kind": "multimodal", "vendor": "NVIDIA", "thinking": True,
     "license": "NVIDIA Open Model License", "ctx": "128K",
     "desc": "NVIDIA Nemotron 3 Nano Omni – text + image understanding, documents, Q&A. NVIDIA Open Model License"},
    {"name": "nemotron-3.5-bolt:74b-a9b", "params": "74B (9B active, MoE)", "size_gb": 25.0, "active_frac": 9 / 74, "quality": 82, "tools": True,
     "kind": "multimodal", "vendor": "NVIDIA", "thinking": True, "license": "OpenMDW License v1", "ctx": "256K",
     "desc": "NVIDIA Nemotron 3.5 Bolt – vision-language model for coding, reasoning and tool use (IQ2 quant). OpenMDW licence"},
    {"name": "nemotron:70b",       "params": "70B",  "size_gb": 43.0, "quality": 85, "tools": True,  "kind": "general", "vendor": "NVIDIA",
     "license": "Llama 3.1 Community License + NVIDIA terms", "ctx": "128K",
     "desc": "Llama-3.1-Nemotron-70B-Instruct – NVIDIA-tuned Llama 3.1 for helpful answers; needs ~48 GB GPU/unified memory"},
]

def is_nvidia(m): return "NVIDIA" in (m.get("vendor") or "")

def mem_needed(size_gb):          # weights + KV cache/context + runtime overhead
    return round(size_gb * 1.2 + 0.8, 1)

def _gpu_budget(hw):
    best = 0.0
    for g in hw.get("gpus") or []:
        if g.get("vendor") in ("NVIDIA", "AMD") and g.get("vram_gb"):
            if g.get("vendor") == "AMD" and g["vram_gb"] < 2: continue   # APU/iGPU carve-out
            best = max(best, g["vram_gb"] - 0.5)
        if g.get("unified_memory"):  # Apple Silicon: Metal working-set budget of unified memory
            best = max(best, (g.get("metal_budget_gb") or (hw.get("ram_total_gb") or 0) * 0.67) - 1.0)
    return best

def _ram_budget(hw):
    t = hw.get("ram_total_gb") or 4
    return max(0.5, t - min(4.0, max(1.5, t * 0.25)))     # leave 1.5-4 GB for Windows + apps

def is_unified(hw):
    return any(g.get("unified_memory") for g in hw.get("gpus") or [])

def _apple_bw(hw):
    """Rough unified-memory bandwidth (GB/s) by chip tier."""
    n = ((hw.get("cpu") or {}).get("name") or "").lower()
    if "ultra" in n: return 800
    if "max" in n: return 400
    if "pro" in n: return 220
    return 150 if any(f"m{i}" in n for i in (4, 5, 6)) else 100

def estimate(m, hw):
    need, vram, ram = mem_needed(m["size_gb"]), _gpu_budget(hw), _ram_budget(hw)
    m = dict(m, size_gb=max(0.3, m["size_gb"] * m.get("active_frac", 1.0)))   # MoE: only active experts are read per token
    cpu = hw.get("cpu") or {}; threads = cpu.get("threads") or 4
    cpu_factor = min(threads, 16) / 8 * (1.0 if cpu.get("avx2") is not False else 0.35)
    if is_unified(hw) and need <= vram:
        mode, tps = "GPU", min(100, _apple_bw(hw) * 0.6 / m["size_gb"])   # memory-bandwidth bound
    elif is_unified(hw):   # exceeding the Metal working set on a Mac means swapping -> treat as too big
        return {"fits": False, "mode": "too big", "tps": 0, "need_gb": need}
    elif vram and need <= vram:
        mode, tps = "GPU", min(120, 300 / m["size_gb"] ** 0.9)
    elif vram and need <= vram + ram:
        frac = vram / need; mode = "GPU+CPU"
        tps = (1 / ((frac / (300 / m["size_gb"])) + ((1 - frac) / max(0.5, 22 / m["size_gb"] * cpu_factor))))
    elif need <= ram:
        mode, tps = "CPU", 22 / m["size_gb"] * cpu_factor
    else:
        return {"fits": False, "mode": "too big", "tps": 0, "need_gb": need}
    return {"fits": True, "mode": mode, "tps": round(tps, 1), "need_gb": need}

def speed_label(tps):
    return "very fast" if tps >= 40 else "fast" if tps >= 15 else "ok" if tps >= 7 else "slow" if tps >= 3 else "very slow"

def recommend(hw, include_coder=True):
    disk_free = (hw.get("disk") or {}).get("free_gb", 999)
    rows = []
    for m in CATALOG:
        e = estimate(m, hw)
        disk_ok = disk_free >= m["size_gb"] * 1.1 + 2
        r = {**m, **e, "disk_ok": disk_ok, "speed": speed_label(e["tps"]) if e["fits"] else "won't fit"}
        r["ok"] = e["fits"] and disk_ok
        rows.append(r)
    MIN_TPS, GOOD_TPS = 6, 10
    base = [r for r in rows if r["ok"] and r["tools"] and r["kind"] == "general"]
    if is_unified(hw):   # leave half of unified memory for macOS, apps and long contexts
        roomy = [r for r in base if r["need_gb"] <= (hw.get("ram_total_gb") or 0) * 0.5]
        base = roomy or base
    tiers = [[r for r in base if r["mode"] == "GPU" and r["tps"] >= GOOD_TPS],   # fully on GPU and snappy
             [r for r in base if r["tps"] >= GOOD_TPS],                          # snappy anywhere
             [r for r in base if r["tps"] >= MIN_TPS],                           # usable
             [r for r in rows if r["ok"] and r["tools"]]]                        # anything that fits
    usable = next((t for t in tiers if t), [])
    usable.sort(key=lambda r: (r["quality"] - (5 if r.get("thinking") else 0), r["tps"]), reverse=True)
    best = usable[0] if usable else None
    # 1.5.1: on a small GPU (≈4 GB class), prefer the adult-default model when it fits this tier
    vram = _gpu_budget(hw)
    if best and 0 < vram <= 6:
        adult = next((r for r in usable if r.get("adult_default")), None)
        if adult is not None:
            best = adult
    def reason(r):
        if not r["disk_ok"]: return f"Not enough disk space (needs ~{r['size_gb']*1.1+2:.0f} GB free)"
        if not r["fits"]: return f"Needs ~{r['need_gb']} GB memory; you have ~{max(_gpu_budget(hw), 0):.0f} GB VRAM / {_ram_budget(hw):.0f} GB usable RAM"
        where = {"GPU": "runs on the Apple GPU (Metal) in unified memory" if is_unified(hw) else "fits fully in your GPU's VRAM", "GPU+CPU": "split between GPU and RAM", "CPU": "runs on your CPU from RAM"}[r["mode"]]
        bits = [where, f"~{r['tps']} tokens/s ({r['speed']})"]
        if not r["tools"]: bits.append("no native tool calling (uses JSON mode)")
        if r["tps"] < MIN_TPS: bits.append("may feel slow")
        return "; ".join(bits)
    for r in rows: r["reason"] = reason(r)
    if best: best["reason"] = "Best quality that runs well on this computer: " + best["reason"]
    if best and is_unified(hw):
        for r in rows:
            if r["ok"] and r is not best and r["quality"] > best["quality"] and r["kind"] == "general":
                r["reason"] = "Higher quality, uses more memory (less room for other apps): " + r["reason"]
    alts = sorted([r for r in rows if r["ok"] and r is not best], key=lambda r: (r["tools"], r["tps"] >= MIN_TPS, r["quality"]), reverse=True)
    if not include_coder: alts = [a for a in alts if a["kind"] != "coder"]
    return {"recommended": best, "alternatives": alts, "too_big": [r for r in rows if not r["ok"]],
            "budget": {"vram_gb": round(_gpu_budget(hw), 1), "ram_gb": round(_ram_budget(hw), 1), "disk_free_gb": disk_free}}
