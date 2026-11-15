"""Hardware detection (Windows first; Linux/macOS fallbacks). Every probe is best-effort."""
import ctypes, json, os, platform, re, shutil, subprocess, sys
from pathlib import Path

IS_WIN, IS_MAC = sys.platform == "win32", sys.platform == "darwin"
NOWIN = 0x08000000 if IS_WIN else 0

def _run(args, timeout=15):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           errors="replace", **({"creationflags": NOWIN} if IS_WIN else {}))
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""

def _ps(script):
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], 25)

def _ps_json(script):
    out = _ps(script + " | ConvertTo-Json -Compress -Depth 3").strip()
    if not out: return []
    try:
        d = json.loads(out); return d if isinstance(d, list) else [d]
    except Exception: return []

GB = 1024 ** 3

# ---------------- RAM ----------------
def ram():
    if IS_WIN:
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                        ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
        m = MS(); m.dwLength = ctypes.sizeof(MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys / GB, m.ullAvailPhys / GB
    if IS_MAC:
        total = int(_run(["sysctl", "-n", "hw.memsize"]) or 0)
        vm = _run(["vm_stat"]); page = 4096
        free = sum(int(x) for x in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)", vm)) * page
        return total / GB, free / GB
    info = Path("/proc/meminfo").read_text()
    g = lambda k: int(re.search(rf"{k}:\s+(\d+)", info).group(1)) * 1024
    return g("MemTotal") / GB, g("MemAvailable") / GB

# ---------------- CPU ----------------
def cpu():
    name, cores, threads, avx2 = platform.processor(), None, os.cpu_count(), None
    if IS_WIN:
        d = _ps_json("Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors")
        if d:
            name = d[0].get("Name", name).strip(); cores = sum(int(x.get("NumberOfCores") or 0) for x in d) or None
            threads = sum(int(x.get("NumberOfLogicalProcessors") or 0) for x in d) or threads
        try: avx2 = bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))  # PF_AVX2_INSTRUCTIONS_AVAILABLE
        except Exception: pass
    elif IS_MAC:
        name = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or platform.machine()
        cores = int(_run(["sysctl", "-n", "hw.physicalcpu"]) or 0) or None
        feats = _run(["sysctl", "-n", "machdep.cpu.leaf7_features"])
        avx2 = ("AVX2" in feats) if feats else (True if platform.machine() == "arm64" else None)  # Apple Silicon: n/a (Metal)
    else:
        txt = Path("/proc/cpuinfo").read_text(errors="ignore")
        m = re.search(r"model name\s*:\s*(.+)", txt); name = m.group(1).strip() if m else name
        ids = set(re.findall(r"physical id\s*:\s*(\d+)\n(?:.*\n)*?core id\s*:\s*(\d+)", txt))
        cores = len(ids) or None
        fl = re.search(r"^flags\s*:\s*(.+)$", txt, re.M); avx2 = ("avx2" in fl.group(1).split()) if fl else None
    return {"name": name or platform.machine(), "cores": cores, "threads": threads, "avx2": avx2, "arch": platform.machine()}

# ---------------- GPU ----------------
def _vendor(name):
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "rtx" in n or "quadro" in n: return "NVIDIA"
    if "amd" in n or "radeon" in n: return "AMD"
    if "intel" in n or "arc" in n: return "Intel"
    if "apple" in n: return "Apple"
    return "Other"

def parse_nvidia_smi(out):
    gpus = []
    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 3:
            try: gpus.append({"vendor": "NVIDIA", "name": p[0], "vram_gb": round(float(p[1]) / 1024, 1),
                              "vram_free_gb": round(float(p[2]) / 1024, 1), "source": "nvidia-smi"})
            except ValueError: pass
    return gpus

def _nvidia_smi_path():
    p = shutil.which("nvidia-smi")
    if not p and IS_WIN:
        for c in (r"C:\Windows\System32\nvidia-smi.exe", r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            if os.path.exists(c): return c
    return p

WIN_GPU_PS = r"""
$regs = Get-ItemProperty 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*' -ErrorAction SilentlyContinue |
  Select-Object DriverDesc, @{n='qw';e={$_.'HardwareInformation.qwMemorySize'}}
Get-CimInstance Win32_VideoController | ForEach-Object {
  $n = $_.Name; $r = $regs | Where-Object { $_.DriverDesc -eq $n } | Select-Object -First 1
  [pscustomobject]@{ Name = $n; AdapterRAM = $_.AdapterRAM; QwMemorySize = $(if ($r) { [uint64]$r.qw } else { $null }) }
}"""

def merge_win_gpus(rows):
    """Win32_VideoController.AdapterRAM is uint32 (caps at 4 GB) -> prefer registry qwMemorySize."""
    out = []
    for r in rows:
        name = (r.get("Name") or "").strip()
        if not name or "basic display" in name.lower() or "remote" in name.lower(): continue
        qw, ar = r.get("QwMemorySize") or 0, r.get("AdapterRAM") or 0
        try: qw, ar = int(qw), int(ar)
        except Exception: qw, ar = 0, 0
        if ar < 0: ar += 2 ** 32
        vram = qw if qw > 0 else ar
        out.append({"vendor": _vendor(name), "name": name, "vram_gb": round(vram / GB, 1) if vram else None,
                    "source": "registry qwMemorySize" if qw > 0 else "Win32_VideoController"})
    return out

def gpus():
    found = []
    smi = _nvidia_smi_path()
    if smi:
        found = parse_nvidia_smi(_run([smi, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"]))
    if IS_WIN:
        for g in merge_win_gpus(_ps_json(WIN_GPU_PS)):
            if not any(g["name"] in f["name"] or f["name"] in g["name"] for f in found): found.append(g)
    elif IS_MAC:
        sp = _run(["system_profiler", "SPDisplaysDataType", "-json"])
        try:
            for g in json.loads(sp).get("SPDisplaysDataType", []):
                found.append({"vendor": _vendor(g.get("sppci_model", "")), "name": g.get("sppci_model", "GPU"),
                              "vram_gb": None, "unified_memory": platform.machine() == "arm64", "source": "system_profiler"})
        except Exception: pass
    elif not found:
        if shutil.which("rocm-smi"):
            m = re.search(r"Total Memory \(B\):\s*(\d+)", _run(["rocm-smi", "--showmeminfo", "vram"]))
            if m: found.append({"vendor": "AMD", "name": "AMD GPU (ROCm)", "vram_gb": round(int(m.group(1)) / GB, 1), "source": "rocm-smi"})
        if not found and shutil.which("lspci"):
            for l in _run(["lspci"]).splitlines():
                if re.search(r"VGA|3D controller", l):
                    nm = l.split(":", 2)[-1].strip(); found.append({"vendor": _vendor(nm), "name": nm, "vram_gb": None, "source": "lspci"})
    return found

def windows_version():
    if not IS_WIN: return platform.platform()
    d = _ps_json("Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber")
    if d: return f"{d[0].get('Caption','Windows')} (build {d[0].get('BuildNumber','?')})"
    v = sys.getwindowsversion(); return f"Windows {platform.release()} (build {v.build})"

def disk(path=None):
    path = path or os.environ.get("OLLAMA_MODELS") or str(Path.home())
    p = Path(path)
    while not p.exists() and p != p.parent: p = p.parent
    du = shutil.disk_usage(p); return {"path": str(p), "free_gb": round(du.free / GB, 1), "total_gb": round(du.total / GB, 1)}

def detect():
    if IS_MAC: return detect_mac()
    tot, free = ram()
    return {"os": windows_version(), "platform": sys.platform, "ram_total_gb": round(tot, 1), "ram_free_gb": round(free, 1),
            "cpu": cpu(), "gpus": gpus(), "disk": disk()}

# ---------------- macOS (Apple Silicon + Intel Macs) ----------------
MAC_NAMES = {26: "Tahoe", 15: "Sequoia", 14: "Sonoma", 13: "Ventura", 12: "Monterey", 11: "Big Sur"}

def metal_budget_gb(ram_gb):
    """Approximates macOS' default MTLDevice.recommendedMaxWorkingSetSize: ~2/3 of unified memory on
    machines up to 36 GB, ~3/4 above that."""
    return round(ram_gb * (0.67 if ram_gb <= 36 else 0.75), 1)

def parse_mac(brand, memsize, physicalcpu, logicalcpu, sp_displays_json, product_version="", perf_cores="", eff_cores="",
              vm_stat="", page_size=16384, leaf7=""):
    total = int(memsize or 0) / GB
    free = sum(int(x) for x in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)", vm_stat or "")) * int(page_size or 16384) / GB
    brand = (brand or "").strip()
    apple = brand.startswith("Apple")
    cpu = {"name": brand or "Mac", "cores": int(physicalcpu) if str(physicalcpu).strip().isdigit() else None,
           "threads": int(logicalcpu) if str(logicalcpu).strip().isdigit() else None, "arch": "arm64" if apple else "x86_64",
           "avx2": None if apple else ("AVX2" in (leaf7 or "")), "apple_silicon": apple}
    if str(perf_cores).strip().isdigit(): cpu["performance_cores"] = int(perf_cores)
    if str(eff_cores).strip().isdigit(): cpu["efficiency_cores"] = int(eff_cores)
    gpus = []
    try: disp = json.loads(sp_displays_json or "{}").get("SPDisplaysDataType", [])
    except Exception: disp = []
    for g in disp:
        name = g.get("sppci_model") or g.get("_name") or "GPU"
        cores = g.get("sppci_cores")
        entry = {"vendor": "Apple" if "apple" in name.lower() else _vendor(name), "name": name, "source": "system_profiler"}
        if cores: entry["gpu_cores"] = int(re.sub(r"\D", "", str(cores)) or 0) or None
        vr = g.get("spdisplays_vram") or g.get("spdisplays_vram_shared") or ""
        m = re.match(r"(\d+)\s*(GB|MB)", str(vr))
        if entry["vendor"] == "Apple" or apple:
            entry.update(unified_memory=True, vram_gb=None, metal_budget_gb=metal_budget_gb(total))
        elif m:
            entry["vram_gb"] = float(m.group(1)) / (1 if m.group(2) == "GB" else 1024)
        gpus.append(entry)
    if apple and not gpus:
        gpus.append({"vendor": "Apple", "name": brand, "unified_memory": True, "vram_gb": None,
                     "metal_budget_gb": metal_budget_gb(total), "source": "sysctl"})
    pv = (product_version or "").strip()
    try: major = int(pv.split(".")[0])
    except Exception: major = 0
    osname = f"macOS {MAC_NAMES.get(major, '')} {pv}".replace("  ", " ").strip() if pv else "macOS"
    return {"os": osname, "platform": "darwin", "ram_total_gb": round(total, 1), "ram_free_gb": round(free, 1),
            "cpu": cpu, "gpus": gpus}

def detect_mac():
    s = lambda k: _run(["sysctl", "-n", k]).strip()
    d = parse_mac(s("machdep.cpu.brand_string"), s("hw.memsize") or 0, s("hw.physicalcpu"), s("hw.logicalcpu"),
                  _run(["system_profiler", "SPDisplaysDataType", "-json"], 30), _run(["sw_vers", "-productVersion"]),
                  s("hw.perflevel0.physicalcpu"), s("hw.perflevel1.physicalcpu"), _run(["vm_stat"]),
                  s("hw.pagesize") or 16384, s("machdep.cpu.leaf7_features"))
    d["disk"] = disk(); return d
