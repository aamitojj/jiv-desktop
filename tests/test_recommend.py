"""Unit tests for hardware parsing + model recommendation. Run: python -m pytest -q tests/test_recommend.py  (or python tests/test_recommend.py)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jiv.models_catalog import recommend, CATALOG
from jiv.hardware import merge_win_gpus, parse_nvidia_smi

def hw(ram, gpus=(), threads=8, avx2=True, disk=200):
    return {"ram_total_gb": ram, "ram_free_gb": ram / 2, "cpu": {"name": "x", "cores": threads // 2, "threads": threads, "avx2": avx2},
            "gpus": list(gpus), "disk": {"free_gb": disk, "total_gb": 500, "path": "C:\\\\"}}
NV = lambda v, n="NVIDIA GeForce RTX": {"vendor": "NVIDIA", "name": n, "vram_gb": v}
size = lambda r: r["recommended"]["size_gb"]

def test_lowend_4gb_no_avx2():
    r = recommend(hw(4, threads=4, avx2=False)); assert size(r) <= 1.0 and r["recommended"]["tools"], r["recommended"]["name"]
def test_8gb_cpu_only():
    r = recommend(hw(8, threads=8)); assert 1.0 <= size(r) <= 2.6 and r["recommended"]["mode"] == "CPU"
def test_16gb_cpu_only_fast_cpu():
    r = recommend(hw(16, threads=16)); assert 2.0 <= size(r) <= 5.2
def test_rtx3060_12gb():
    r = recommend(hw(16, [NV(12)])); assert r["recommended"]["mode"] == "GPU" and 7 <= size(r) <= 9.0, r["recommended"]["name"]
def test_rtx4090_24gb():
    r = recommend(hw(64, [NV(24)])); assert r["recommended"]["quality"] >= 70 and r["recommended"]["tps"] >= 15
def test_gtx1650_4gb_partial_offload():
    r = recommend(hw(16, [NV(4)])); assert r["recommended"]["mode"] in ("GPU", "GPU+CPU") and size(r) >= 1.9
def test_rtx3070_8gb():
    r = recommend(hw(32, [NV(8)])); assert r["recommended"]["name"] in ("qwen2.5:7b", "llama3.1:8b", "qwen3:8b") and r["recommended"]["mode"] == "GPU"
def test_intel_igpu_ignored():
    r = recommend(hw(8, [{"vendor": "Intel", "name": "Intel UHD 620", "vram_gb": 1}])); assert r["recommended"]["mode"] == "CPU"
def test_apple_unified():
    r = recommend(hw(16, [{"vendor": "Apple", "name": "Apple M2", "vram_gb": None, "unified_memory": True}])); assert r["recommended"]["mode"] == "GPU"
def test_low_disk():
    r = recommend(hw(32, [NV(24)], disk=4)); assert size(r) <= 1.7
    assert all(not m["disk_ok"] for m in r["too_big"] if m["size_gb"] > 2)
def test_no_disk_at_all():
    r = recommend(hw(32, disk=0.5)); assert r["recommended"] is None and r["alternatives"] == []
def test_alternatives_and_reasons():
    r = recommend(hw(16, [NV(8)]))
    assert r["alternatives"] and all(a["reason"] and a["ok"] for a in r["alternatives"])
    assert any(not a["tools"] for a in r["alternatives"])          # non-tool models are listed and labelled
    assert all(m["name"] in [c["name"] for c in CATALOG] for m in r["alternatives"])
def test_win_registry_vram_over_4gb():
    g = merge_win_gpus([{"Name": "AMD Radeon RX 6800", "AdapterRAM": 4293918720, "QwMemorySize": 17163091968},
                        {"Name": "Intel(R) UHD Graphics 770", "AdapterRAM": 1073741824, "QwMemorySize": None},
                        {"Name": "Microsoft Basic Display Adapter", "AdapterRAM": 0}])
    assert len(g) == 2 and g[0]["vendor"] == "AMD" and g[0]["vram_gb"] == 16.0 and g[0]["source"].startswith("registry")
    assert g[1]["vendor"] == "Intel" and g[1]["vram_gb"] == 1.0
def test_win_negative_adapterram():
    assert merge_win_gpus([{"Name": "NVIDIA GeForce GTX 1060", "AdapterRAM": -2147483648}])[0]["vram_gb"] == 2.0
def test_nvidia_smi_parse():
    g = parse_nvidia_smi("NVIDIA GeForce RTX 3060, 12288, 11200\nNVIDIA RTX A2000, 6144, 6000\n")
    assert g[0]["vram_gb"] == 12.0 and g[1]["name"] == "NVIDIA RTX A2000"

# ---------- macOS ----------
from jiv.hardware import parse_mac, metal_budget_gb
import json as _json
SP_M5 = _json.dumps({"SPDisplaysDataType": [{"_name": "kHW_AppleM5Item", "sppci_model": "Apple M5", "sppci_cores": "10",
          "sppci_vendor": "sppci_vendor_Apple", "spdisplays_mtlgpufamilysupport": "spdisplays_metal4",
          "spdisplays_ndrvs": [{"_name": "Color LCD", "spdisplays_main": "spdisplays_yes"}]}]})
VM = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:                               52000.\nPages active:   400000.\nPages inactive:                          150000.\nPages speculative:                        10000.\n"
def mac_m5():
    h = parse_mac("Apple M5", str(17179869184), "10", "10", SP_M5, "26.0.1\n", "4", "6", VM, "16384", "")
    h["disk"] = {"free_gb": 300, "total_gb": 494, "path": "/Users/me"}; return h
def test_mac_parse_m5():
    h = mac_m5()
    assert h["os"] == "macOS Tahoe 26.0.1" and h["ram_total_gb"] == 16.0 and 3 < h["ram_free_gb"] < 3.5
    assert h["cpu"]["name"] == "Apple M5" and h["cpu"]["apple_silicon"] and h["cpu"]["cores"] == 10 and h["cpu"]["performance_cores"] == 4
    g = h["gpus"][0]; assert g["vendor"] == "Apple" and g["gpu_cores"] == 10 and g["unified_memory"] and g["metal_budget_gb"] == 10.7
def test_mac_parse_intel_mac_with_amd():
    sp = _json.dumps({"SPDisplaysDataType": [{"sppci_model": "Intel UHD Graphics 630", "spdisplays_vram": "1536 MB"},
                                            {"sppci_model": "AMD Radeon Pro 5500M", "spdisplays_vram": "8 GB"}]})
    h = parse_mac("Intel(R) Core(TM) i9-9980HK CPU @ 2.40GHz", str(32 * 1024**3), "8", "16", sp, "15.6", leaf7="RDWRFSGS TSC_THREAD_OFFSET SGX BMI1 AVX2 SMEP")
    assert not h["cpu"]["apple_silicon"] and h["cpu"]["avx2"] is True and h["os"] == "macOS Sequoia 15.6"
    amd = [g for g in h["gpus"] if g["vendor"] == "AMD"][0]; assert amd["vram_gb"] == 8.0 and not amd.get("unified_memory")
def test_mac_parse_empty_profiler_falls_back():
    h = parse_mac("Apple M2 Pro", str(32 * 1024**3), "12", "12", "", "14.5")
    assert h["gpus"][0]["unified_memory"] and h["gpus"][0]["metal_budget_gb"] == metal_budget_gb(32)
def test_metal_budget():
    assert metal_budget_gb(16) == 10.7 and metal_budget_gb(64) == 48.0
def test_m5_16gb_recommendation():
    r = recommend(mac_m5()); rec = r["recommended"]
    assert rec["name"] in ("qwen2.5:7b", "llama3.1:8b") and rec["mode"] == "GPU" and rec["tools"], rec["name"]
    assert "Apple GPU" in rec["reason"] and rec["tps"] >= 10
    nemo = next(a for a in r["alternatives"] if a["name"] == "mistral-nemo:12b")
    assert nemo["reason"].startswith("Higher quality, uses more memory")
    assert "qwen2.5:14b" in [t["name"] for t in r["too_big"]]
def test_m4_max_64gb():
    h = parse_mac("Apple M4 Max", str(64 * 1024**3), "16", "16", "", "15.0"); h["disk"] = {"free_gb": 500}
    rec = recommend(h)["recommended"]; assert rec["quality"] >= 70 and rec["mode"] == "GPU"
def test_m1_8gb():
    h = parse_mac("Apple M1", str(8 * 1024**3), "8", "8", "", "14.0"); h["disk"] = {"free_gb": 50}
    rec = recommend(h)["recommended"]; assert rec["size_gb"] <= 2.6 and rec["mode"] == "GPU"

# ---------- NVIDIA models ----------
CAT = {c["name"]: c for c in CATALOG}
NV_EXPECTED = {  # tag: (download GB on ollama.com, tools)
    "nemotron-mini:4b": (2.7, True), "nemotron-3-nano:4b": (2.8, True), "nemotron-3-nano:30b": (24.0, True),
    "nemotron-3.5-lightning:30b": (25.0, True), "nemotron-cascade-2:30b": (24.0, True), "nemotron3:33b": (28.0, True),
    "nemotron-3.5-bolt:74b-a9b": (25.0, True), "nemotron:70b": (43.0, True), "mistral-nemo:12b": (7.1, True)}
def test_nvidia_catalog_entries():
    for tag, (gb, tools) in NV_EXPECTED.items():
        m = CAT[tag]; assert m["size_gb"] == gb and m["tools"] is tools and "NVIDIA" in m["vendor"] and m.get("license"), tag
    assert CAT["mistral-nemo:12b"]["vendor"] == "NVIDIA + Mistral AI"
    assert "NVIDIA Open Model License" in CAT["nemotron-3-nano:4b"]["desc"]
    assert not any("cloud" in n for n in CAT)                       # cloud-only tags are not downloadable
    assert len({c["name"] for c in CATALOG}) == len(CATALOG)       # no duplicates
def test_gtx1650_8gb_keeps_qwen3b_and_lists_nemotron_mini():
    r = recommend(hw(8, [NV(4, "NVIDIA GeForce GTX 1650")]))
    assert r["recommended"]["name"] == "huihui_ai/qwen2.5-abliterate:3b", r["recommended"]["name"]
    names = [a["name"] for a in r["alternatives"]] + [r["recommended"]["name"]]
    assert "qwen2.5:3b" in names and "nemotron-mini:4b" in names
    nm = next(a for a in r["alternatives"] if a["name"] == "nemotron-mini:4b")
    assert nm["ok"] and nm["vendor"] == "NVIDIA" and nm["tps"] >= 10
    assert "huihui_ai/qwen3-abliterated:4b" in names and "dolphin-phi:2.7b" in names
    assert "nemotron:70b" in [t["name"] for t in r["too_big"]]
def test_moe_speed_uses_active_params():
    from jiv.models_catalog import estimate
    h = hw(64, [NV(24)])
    moe, dense = estimate(CAT["nemotron-3-nano:30b"], h), estimate(CAT["qwen2.5:32b"], h)
    assert moe["fits"] and moe["tps"] > dense["tps"] * 2
def test_big_unified_mac_can_list_nemotron_70b():
    h = parse_mac("Apple M3 Ultra", str(128 * 1024**3), "28", "28", "", "15.0"); h["disk"] = {"free_gb": 900}
    r = recommend(h); names = [a["name"] for a in r["alternatives"]] + [r["recommended"]["name"]]
    assert "nemotron:70b" in names and r["recommended"]["tools"]
def test_multimodal_not_default():
    r = recommend(hw(128, [NV(80, "NVIDIA H100")], threads=64))
    assert r["recommended"]["kind"] == "general"

if __name__ == "__main__":
    fails = 0
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            try: f(); print("PASS", k)
            except Exception as e: fails += 1; print("FAIL", k, e)
    print("all passed" if not fails else f"{fails} failed"); sys.exit(1 if fails else 0)
