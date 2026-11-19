# Jiv Desktop

Your own AI agent for **Windows and macOS** (Linux works too). Jiv runs on your PC, connects to any open-source AI (Ollama, LM Studio, llama.cpp, vLLM, Jan, LocalAI, or cloud services like OpenRouter, Groq and Together), chats with you like a modern assistant, uses tools on your computer **only with your permission**, and can create and manage more agents when you ask.

## Features
- **⬆ Updates from the official repo (1.6.3).** *Check for updates* works with no setup: it checks the public [aamitojj/jiv-desktop](https://github.com/aamitojj/jiv-desktop/releases) releases, downloads the signed all-in-one `.jivupdate` if there is one, or offers `JivSetup.exe`. Also fixes Maven error highlighting in the Builder and makes the small-model prompt more compact.
- **✎ Clearer English (1.6.2).** Better reading/summaries (abbreviations, negation-aware extracts, readable polish) and a light polish on Jiv’s own replies. `grammar_check` tool for sentences you ask it to correct. Settings → Polish Jiv’s English.
- **🔎 Search & summarise (1.6.1).** Web search with DuckDuckGo (+ optional SearXNG, Wikipedia), readable page fetch, disk cache, and a grounded extractive summary with [n] citations that works with no AI.
- **🧱 Builder (1.6.0).** Create a static website or a Java app from a local template, with no AI and no model running. Editor, snippets, Build / Run / Package, and a local site preview. Writes stay in the project folder and use the same permission cards and audit log. Chat is optional and separate.

- **📎 File upload in chat (1.2.2).** Attach files with the button, by drag & drop, or by pasting; several at once, shown as chips before sending. Jiv reads text, code, CSV, JSON, HTML, XML, PDF, DOCX, XLSX and PPTX, and includes the text in the message (cut to fit small models, with a note when it is cut). The `read_file` tool pages through long files. Images show a preview; vision models (qwen2.5vl, llava) see them. Files are stored in `<data>/uploads`.
- **Chat**: streaming replies, markdown and code blocks with a copy button, a stop button, dark/light theme, conversation history (SQLite), and rename/delete for conversations.
- **Any OpenAI-compatible AI**: you can pick a provider, auto-detect local servers (ports 11434, 1234, 8080, 8000, 1337, 5000/5001), choose a model from a dropdown, set temperature and test the connection.
- **Tools**: PowerShell/cmd commands, read/write/delete/list files, web search (DuckDuckGo, no key needed), fetch web pages, HTTP POST, open apps/files/URLs, read the clipboard, system info, and memory notes.
- **Permissions**: anything that changes your PC shows a card with the exact command and three buttons: **Allow once / Always allow this tool / Deny**. By default Jiv asks every time. Read-only tools can run without asking (you can turn this off). You manage the always-allow list in Settings. Every action is written to the **Audit log**.
- **Multiple agents**: say *"create an agent called Scout that researches topics on the web"*. Jiv asks your permission, then creates it with its own persona, model and allowed tools. Scout appears in the sidebar and you can chat with it directly. Jiv can hand tasks to Scout (`delegate_to_agent`) and gets the answer back. You can edit, rename or delete agents with the ✎ button. The main agent can be renamed.
- **Tool calling**: Jiv uses the model's native function calling when the model supports it. When it doesn't, Jiv switches automatically to a JSON tool format and reads the tool call from the model's text (`<tool_call>`, ```json blocks, or bare JSON).

## 🧙 Setup Wizard (free local AI, automatic)
The wizard opens the first time you start Jiv. To run it again later, open **Settings → Re-scan my PC / Change model**.
1. **Your PC**: Jiv shows your RAM (total and free), CPU (model, cores/threads, AVX2), GPU (vendor, model, VRAM), free disk space and Windows version.
2. **Choose model**: Jiv recommends the best free model for your hardware and lists other models that fit. For each one you see the size, a speed estimate (tokens/s), whether it runs on the GPU or CPU, whether it supports tools, and why it was suggested. Models too big for your PC are listed separately. You can also type any model name from ollama.com/library.
3. **Install Ollama**: if Ollama is missing, a permission card asks you first. Jiv then downloads the official `OllamaSetup.exe` with a progress bar and runs it silently (`/VERYSILENT`). If the silent install fails, the normal installer opens instead. You can also use `winget install Ollama.Ollama`. If Ollama isn't running, Jiv starts it with `ollama serve`.
4. **Download**: a permission card asks first. Jiv then downloads the model through Ollama's `/api/pull` with a live bar (percent, MB, MB/s, ETA). You can **pause** and **resume**: parts already downloaded are kept.
5. **Finish**: Jiv sets itself to Ollama with that model and runs a test chat.

**📦 Models** (sidebar or Settings) lists the installed models with their size, parameters and quantization. From there you can use a model, delete it (asks permission) or download more.

### How hardware detection works
| What | Windows | Linux / macOS fallback |
|---|---|---|
| RAM | `GlobalMemoryStatusEx` (kernel32) | `/proc/meminfo` / `sysctl hw.memsize` + `vm_stat` |
| CPU | `Win32_Processor` (CIM) for name, cores, threads; `IsProcessorFeaturePresent(40)` for AVX2 | `/proc/cpuinfo` flags / `sysctl machdep.cpu.*` |
| NVIDIA GPU | `nvidia-smi --query-gpu=name,memory.total,memory.free` | same |
| AMD / Intel GPU | `Win32_VideoController` (`AdapterRAM`). That value tops out at 4 GB, so Jiv prefers `HardwareInformation.qwMemorySize` from the display-adapter registry key | `rocm-smi`, `lspci` / `system_profiler` (Apple unified memory) |
| Disk | free space on the drive with your models (`OLLAMA_MODELS` or your home folder) | same |
| Windows version | `Win32_OperatingSystem` caption and build | `platform` |

**How the recommendation works:** memory needed ≈ model size × 1.2 + 0.8 GB (for context). With a discrete NVIDIA/AMD GPU, Jiv's budget is VRAM − 0.5 GB. Without one, it's RAM minus 1.5–4 GB left for Windows. Integrated Intel GPUs are ignored. Disk needs model size × 1.1 + 2 GB free. Speed is estimated from model size, GPU/CPU/split mode, thread count and AVX2. Among general models with tool support, Jiv picks the smartest one that fits entirely on the GPU at ≥10 tokens/s, then any that reach ≥10 tokens/s, then ≥6. The estimates are rough guides, not benchmarks.

## Download JivSetup.exe from GitHub Releases
1. Open **https://github.com/aamitojj/jiv-desktop/releases/latest**. The repo is public, so you don't need a GitHub account. Direct link: https://github.com/aamitojj/jiv-desktop/releases/latest/download/JivSetup.exe
2. Under **Assets**, download **`JivSetup.exe`** (the installer). `JivDesktop-portable-win64.zip` is a no-install copy: unzip it and run `JivDesktop.exe`. `jiv-x.y.z-all-in-one.jivupdate` updates an existing install (1.5.1 or newer) in place through **⬆ Updates → Install update file…**.
3. Run `JivSetup.exe` as described in the next section.

Every build is also kept as a workflow artifact: **Actions → Build Windows installer → (a run) → Artifacts**.

### Making a new release (developers)
The workflow `.github/workflows/build-windows.yml` builds the installer on `windows-latest`: it downloads the python.org embeddable Python, installs the Windows wheels from `requirements.txt` into it, compiles the launcher with MinGW, runs `makensis installer/jiv.nsi`, and publishes `JivSetup.exe` and the portable zip to a GitHub Release. A separate job runs the `tests/test_*.py` unit tests on Ubuntu. It reports results only and does not block the build.
1. Bump the version in `jiv/__init__.py` (for example `__version__ = "1.6.3"`) and commit.
2. Tag and push. The tag must be `v` + the same version, otherwise the build stops:
   ```
   git tag v1.6.3
   git push origin main v1.6.3
   ```
3. Wait about 5–10 minutes. The release `v1.6.3` then appears with `JivSetup.exe` attached. To make a test build without a release, open **Actions → Build Windows installer → Run workflow**.
4. Signed `.jivupdate` packages are made separately with `tools/make_update.py` and your private key, which never goes into this repo. Upload one with `gh release upload vX.Y.Z jiv-X.Y.Z-all-in-one.jivupdate`. In-app *Check for updates* prefers an `all-in-one` package.

## Install on Windows: JivSetup.exe (recommended)
1. Download **`JivSetup.exe`** (see above) and double-click it.
2. **SmartScreen.** The installer isn't code-signed yet, so Windows may show *"Windows protected your PC"*. Click **More info**, then **Run anyway**. Your browser may also ask you to *Keep* the download.
3. Click through Welcome, License, Components (desktop shortcut on or off) and Folder, then Install. It installs **just for you** into `%LOCALAPPDATA%\Programs\JivDesktop`, so no administrator rights are needed. Tick **Launch Jiv Desktop** at the end.
4. Start Jiv later from the **Start menu** or the **desktop shortcut**. If Ollama is already installed, Jiv finds your downloaded models automatically. If not, the Setup Wizard installs it with a model for you.

What gets installed:
- `JivDesktop.exe`: a tiny launcher with no console window.
- `python\`: a private Python 3.12 (python.org embeddable) with the required packages.
- `app\`: Jiv itself as plain `.py` files.

Because the app is plain source in a folder you own, **the Updates page works exactly as in the script install**: `.jivupdate` files, GitHub releases, and updates Jiv writes itself. All of them get the backup and auto-rollback.

- **Upgrading.** Run a newer JivSetup.exe. It finds the existing install, closes Jiv, replaces the program files and keeps your data (`%APPDATA%\JivDesktop`: chats, agents, settings, keys, backups).
- **Uninstalling.** Use Windows Settings → Apps → *Jiv Desktop* → Uninstall, or `Uninstall.exe` in the install folder. Your data is **kept** unless you tick *Also delete my data*. Ollama and its models are never touched.
- **Silent options** (for admins and scripts):
  - `JivSetup.exe /S` installs silently; add `/D=C:\path` to choose the folder.
  - `Uninstall.exe /S` uninstalls silently and keeps your data; add `/DELETEDATA` to remove the data too.
- **Building the installer**: GitHub Actions builds it automatically (see above). To build locally on Linux, run `sh installer/build_installer.sh`. It needs `nsis`, `gcc-mingw-w64-x86-64`, `python3` and `curl`. It downloads the embeddable Python and the Windows wheels, compiles the launcher (`installer/launcher.c`) and runs `makensis installer/jiv.nsi`.

## Install on Windows from the zip (developer / script install)
1. Unzip `JivDesktop.zip` into a folder, for example `C:\Users\<you>\JivDesktop`.
2. Double-click **`install.bat`**.
   - If Python is missing, the installer shows `winget install -e --id Python.Python.3.12` and can run it for you. After Python installs, run `install.bat` again.
   - The installer creates a `.venv` folder and installs the requirements. It can also install **pywebview** for a native window and create a **desktop shortcut**.
3. Double-click **`start.bat`** (or the "Jiv Desktop" shortcut). Jiv opens at `http://127.0.0.1:8765` (or in its own window if you installed pywebview).
4. The **Setup Wizard** opens on first run and installs Ollama and a model for you. You can also skip it and set up any provider in Settings (Auto-detect → Use → model → Test → Save).

Optional: run `build_exe.bat` to build a single-file `dist\JivDesktop.exe` with PyInstaller. That build **cannot update itself**; use JivSetup.exe for a normal app that can.

## Install on macOS (Apple Silicon M1–M5 or Intel, macOS 12+; tested in simulation only)
1. Unzip `JivDesktop.zip`, for example into `~/JivDesktop`. Use Finder or `unzip` so the executable bits are kept.
2. **Gatekeeper:** files downloaded from the internet are quarantined, and macOS may say *"install.command cannot be opened because it is from an unidentified developer"*. Pick one fix:
   - Right-click `install.command` → **Open** → **Open**, or
   - In Terminal: `xattr -dr com.apple.quarantine ~/JivDesktop` (this clears the whole folder), or
   - System Settings → Privacy & Security → **Open Anyway**.
3. Double-click **`install.command`**. It finds Python 3.10+ and, if Python is missing, offers `brew install python@3.12`, the python.org installer (https://www.python.org/downloads/macos/), or `xcode-select --install`. Then it creates `.venv` and installs the requirements. It can also install **pywebview** for a native window and add **Jiv Desktop** to `~/Applications` (Launchpad/Spotlight) and the Dock.
4. Double-click **`start.command`**, or open Jiv Desktop from Launchpad. The Setup Wizard opens:
   - **Your Mac:** Apple chip, performance and efficiency cores, GPU core count, unified memory and the Metal budget.
   - **Recommended model:** on a **16 GB M-series Mac** (e.g. MacBook with M5) it recommends **`qwen2.5:7b`** (≈4.7 GB, about 15–25 tokens/s on the Metal GPU, reliable tool calls, leaves room for macOS and your apps). `llama3.1:8b` is an equal choice. **`mistral-nemo:12b`** is listed as *"higher quality, uses more memory"*. 14B+ models are marked too big, because going past the Metal budget makes the Mac swap.
   - **Install Ollama** (asks permission first): with Homebrew, `brew install ollama` then `brew services start ollama`. Without Homebrew, Jiv downloads `Ollama-darwin.zip` with a progress bar, unzips it into `/Applications` (or `~/Applications` if that isn't writable), runs `xattr -dr com.apple.quarantine` on it, launches **Ollama.app** and waits for the API.
   - **Download** the model with a progress bar, pause and resume.
5. Optional: run **`build_app.command`** to build `dist/Jiv Desktop.app` with PyInstaller. It is ad-hoc signed and can be copied to Applications and the Dock.

On a Mac, Jiv's tools run shell commands with **zsh** (the permission card shows `zsh > …`). Apps, files and URLs open with `open` / `open -a "App Name"`, the clipboard is read with `pbpaste`, and system info reports the chip, cores, GPU and memory. Data lives in `~/Library/Application Support/JivDesktop/`.

**How the Mac hardware check works:** `sysctl machdep.cpu.brand_string` gives the chip, `hw.memsize` the memory, `hw.physicalcpu`/`hw.logicalcpu` and `hw.perflevel0/1.physicalcpu` the cores. `system_profiler SPDisplaysDataType -json` gives the GPU model and `sppci_cores`. `sw_vers` gives the macOS version (26 = Tahoe) and `vm_stat` the free memory. The Metal budget follows macOS' default `recommendedMaxWorkingSetSize`: about ⅔ of unified memory up to 36 GB, about ¾ above that. Jiv keeps a further 1 GB in reserve. The recommended model must fit in half of unified memory so the Mac stays responsive.

## Install on Linux
`./install.sh` then `./start.sh`. The installer can add a menu entry. In the wizard, Ollama installs with the official `curl -fsSL https://ollama.com/install.sh | sh` (needs sudo).

## Model catalogue
These are the models the Setup Wizard knows about. Sizes are the default (Q4) download from ollama.com/library. "Tools" means Ollama lists the model with tool calling. **NVIDIA** models (Nemotron, plus Mistral NeMo, which Mistral AI built with NVIDIA) show a green **by NVIDIA** badge in the wizard and in Manage models. Cloud-only tags (`nemotron-3-super`, `nemotron-3-ultra`, `*-cloud`) are left out because they don't run locally. The 30B "A3B" Nemotron models are mixture-of-experts: only about 3B parameters are active per token, so they run much faster than dense models of the same size, but they still need about 24 GB of memory.

| Model tag | Params | Download | Tools | Notes | By |
|---|---|---|---|---|---|
| `qwen2.5:0.5b` | 0.5B | 0.4 GB | ✅ |  |  |
| `llama3.2:1b` | 1B | 1.3 GB | ✅ |  |  |
| `qwen2.5:1.5b` | 1.5B | 1 GB | ✅ |  |  |
| `qwen2.5-coder:1.5b` | 1.5B | 1 GB | ✅ | coder |  |
| `gemma2:2b` | 2B | 1.6 GB | — (JSON mode) |  |  |
| `llama3.2:3b` | 3B | 2 GB | ✅ |  |  |
| `qwen2.5:3b` | 3B | 1.9 GB | ✅ |  |  |
| `phi3:mini` | 3.8B | 2.2 GB | — (JSON mode) |  |  |
| `phi4-mini` | 3.8B | 2.5 GB | ✅ |  |  |
| `qwen3:4b` | 4B | 2.6 GB | ✅ | thinking |  |
| `qwen2.5:7b` | 7B | 4.7 GB | ✅ |  |  |
| `qwen2.5-coder:7b` | 7B | 4.7 GB | ✅ | coder |  |
| `llama3.1:8b` | 8B | 4.9 GB | ✅ |  |  |
| `qwen3:8b` | 8B | 5.2 GB | ✅ | thinking |  |
| `mistral-nemo:12b` | 12B | 7.1 GB | ✅ | Apache 2.0 | NVIDIA + Mistral AI |
| `qwen2.5:14b` | 14B | 9 GB | ✅ |  |  |
| `qwen2.5-coder:14b` | 14B | 9 GB | ✅ | coder |  |
| `qwen2.5:32b` | 32B | 20 GB | ✅ |  |  |
| `nemotron-mini:4b` | 4B | 2.7 GB | ✅ | 4K ctx, NVIDIA Community Model License | NVIDIA |
| `nemotron-3-nano:4b` | 4B | 2.8 GB | ✅ | thinking, 256K ctx, NVIDIA Open Model License | NVIDIA |
| `nemotron-3-nano:30b` | 30B (3.5B active, MoE) | 24 GB | ✅ | thinking, 1M ctx, NVIDIA Open Model License | NVIDIA |
| `nemotron-3.5-lightning:30b` | 30B (3B active, MoE) | 25 GB | ✅ | thinking, 1M ctx, NVIDIA open model licence (see model card) | NVIDIA |
| `nemotron-cascade-2:30b` | 30B (3B active, MoE) | 24 GB | ✅ | thinking, 256K ctx, NVIDIA open model licence (see model card) | NVIDIA |
| `nemotron3:33b` | 33B | 28 GB | ✅ | thinking, multimodal, 128K ctx, NVIDIA Open Model License | NVIDIA |
| `nemotron-3.5-bolt:74b-a9b` | 74B (9B active, MoE) | 25 GB | ✅ | thinking, multimodal, 256K ctx, OpenMDW License v1 | NVIDIA |
| `nemotron:70b` | 70B | 43 GB | ✅ | 128K ctx, Llama 3.1 Community License + NVIDIA terms | NVIDIA |

Licences vary: the Nemotron 3 family uses the **NVIDIA Open Model License**, Nemotron-Mini the NVIDIA Community Model License, Nemotron 70B the Llama 3.1 Community License, Nemotron 3.5 Bolt OpenMDW, and Mistral NeMo Apache 2.0. Check each model card before commercial use.

## Setting up Ollama (recommended, free, local)
1. The Setup Wizard does this for you. To do it by hand: Windows `winget install Ollama.Ollama`, macOS `brew install ollama` or the app from https://ollama.com/download, Linux the install script.
2. Open a terminal and download a model that supports tool calling:
   ```
   ollama pull llama3.1        # 8B, good all-rounder, supports tools
   ollama pull qwen2.5         # 7B, very good at tool calling
   ```
   Other models with tool support: `qwen2.5:14b`, `qwen3`, `llama3.2` (3B, for small PCs), `mistral-nemo`, `nemotron-mini` / `nemotron-3-nano:4b` (NVIDIA), `mistral`, `command-r`, `firefunction-v2`, `hermes3`. Models without native tools (for example `gemma2`, `phi3`, `deepseek-r1`) still work through JSON mode, but they are less reliable.
3. Ollama runs in the background on port 11434. In Jiv: Settings → Auto-detect → Use → pick `llama3.1` → Test → Save.

**Hardware:** 7–8B models need about 8 GB RAM (a GPU helps a lot). On weaker PCs use `llama3.2:3b` or `qwen2.5:3b`.

**LM Studio:** load a model, open the Developer tab, start the server on port 1234, then auto-detect in Jiv.
**Cloud (optional):** choose OpenRouter, Groq or Together in Settings and paste your API key.

## 🧠 Learning, skills and coding languages (1.3.0+)
Jiv gets better with use, without any model training:
- **Memory.** Jiv saves facts and preferences you mention ("my name is…", "I prefer…", "remember that…") and shows a 🧠 notice when it does. The model can also use `remember`, `recall` and `forget`. Before each reply, only the few most relevant memories (BM25 search) go into the prompt, about 900 characters for small models. Open **🧠 Memory** to view, edit or delete them, or to turn automatic memory off.
- **Skills.** When code runs successfully, Jiv offers to save it as a skill (name, description, steps, script). Relevant skills are suggested for new tasks, and `run_skill` runs them. You can edit them on the **🛠 Skills** page.
- **Feedback.** Every answer has 👍 and 👎 buttons. A 👎, optionally with a correction, becomes a *lesson* that Jiv uses for similar questions. Lessons are on Memory → Lessons.
- **Languages.** The **{ } Languages** page detects Python, Node.js, Java, C/C++ (gcc/g++), Go, Rust, .NET, PHP, Ruby and PowerShell. Missing ones install with winget using exact IDs (e.g. `OpenJS.NodeJS.LTS`, `EclipseAdoptium.Temurin.21.JDK`, `BrechtSanders.WinLibs.POSIX.UCRT`), and always ask first. On macOS they install with Homebrew. Python always works, because Jiv falls back to its own Python.
- **run_code.** Jiv writes a program to `%APPDATA%\JivDesktop\workspace\<run>`, compiles it if needed, and runs it with a time limit (30 s by default). It captures the output, and on an error it lets the model fix and retry up to 3 times. It asks permission the first time; "Always allow run_code" also covers `run_skill`.
- **Small models** (nemotron-mini:4b, qwen2.5:3b, etc.) get a compact tool set and a short how-to-use-tools guide. Jiv accepts native tool calls, `<tool_call>`/`<toolcall>` JSON, or bare JSON.

## ⚡ Batch jobs (1.4.0+)
Use Batch for big jobs on a small local model (qwen2.5:3b or nemotron-mini:4b, about 4k context). Each item goes to the model on its own, so the work fits the model's context.
- **Repeat N times:** a prompt plus a count from 1 to 10,000. `{i}` is the run number and `{total}` is the count. Tick "feed the previous output" to chain runs through `{prev}`. The result is `results.md`.
- **Run over a file:** upload JSON, CSV, JSONL, TXT, MD or HTML, a folder of pages, or a .zip of pages.
  - Jiv splits the file automatically: JSON array elements (or the list inside an object), CSV rows, JSONL lines, text lines or paragraphs, Markdown sections, or one item per HTML page or section. You can override the split.
  - Choose a start item and a limit. Long items are cut into chunks (3,500 characters by default) to fit the model's context.
  - Placeholders: `{item}`, `{i}`, `{total}`.
- **The output matches the input:**
  - JSON, CSV and JSONL keep the same fields, plus any added fields such as `explanation`.
  - For HTML, only the visible text changes. Tags, attributes, scripts, styles and `<pre>`/`<code>` blocks are copied exactly, and images and CSS from a folder or zip are copied unchanged.
- **Checks:**
  - JSON parsing and the required fields.
  - An "auto-mcq" JSON schema built from your fields, or a JSON schema you paste in.
  - MCQ rules: question, at least 2 distinct options, an answer key that matches an option, an explanation, and wording that has changed.
  - An optional **verify pass**: a second model call that solves the MCQ itself and compares its answer with the key, or checks a page for missing segments and prohibited content.
  - A failed item is retried up to 2 times, with the problems fed back to the model, and is then **flagged**.
- **Built-in templates:**
  - "Rewrite MCQ in original wording + verify answer + add explanation".
  - "Rewrite page for AdSense compliance (original, helpful, no copied text, no prohibited content)".
  - A text rewrite template and a repeat example.
  - You can save your own templates.
- **Running:**
  - Jobs live in SQLite (`jiv.db`). They have pause, resume and cancel, a progress bar with an ETA and tokens/s, and a per-item log.
  - A job interrupted by a restart or crash continues where it left off.
  - ⏱ **Estimate time** runs one test item and extrapolates from the measured speed.
- **Permission:** one card at the start of a job shows the prompt, the item count and the output folder (`%APPDATA%\JivDesktop\batch\jobs\<id>\output`). There are no cards for each item.
- **Downloads:** the result file, plus `review.xlsx` with "Flagged for review" and "All items" sheets showing original and new side by side, and `review_flagged.csv`.
- **From chat:**
  - Attach a file and say "rewrite all these MCQs"; Jiv calls `start_batch` after you approve.
  - Typing "… repeat this 50 times" opens a prefilled Batch page instead of flooding the chat.
  - Asking for more than 20 questions in chat ("make 60 NCLEX MCQs on sepsis") adds a button that opens the question generator with the count and topic filled in.
- **Generate NCLEX-style MCQs (topic, count)** (1.4.1): a repeat-mode template. Each question has a clinical scenario, 4 options, a lettered answer key and a 2–4 sentence rationale. A verify pass answers each question independently and flags it if its answer differs from the key. The result is `questions.json`, plus the review files.

## 💬 Jiv does any task the model can do (1.4.1+)
- Jiv's prompts no longer limit it to its tools. Jiv is a general assistant that writes things itself: MCQs and NCLEX/JEE/NEET questions, explanations, notes, articles, web pages, rewrites, stories, roleplay, plans, opinions and code. Jiv only mentions real technical limits, such as "small model: check the facts".
- The "I can't change myself" rule applies **only** to requests to change Jiv's own code or features.
- **Small models (3–8B):** the prompt includes a short NCLEX example. Pure writing requests are answered directly, without offering tools (`direct_writing`, on by default). Small models otherwise tend to detour through run_code or run_shell. Code requests, and anything that mentions files, the web, the PC, saving or running, still get the tools.
- **Refusal guard** (`refusal_guard`, on by default): if the reply to a writing request looks like a refusal ("unable to assist", "my capabilities are limited", "I don't have the ability"…), Jiv hides it, retries once with a firmer instruction and shows the second answer. Requests to change Jiv itself are never retried. Each retry is recorded in the Audit log.
- **Permissions** still apply to everything that touches the PC: shell, file writes, installs, batch jobs and updates. Each tool has "Always allow". **Settings → Permissions → "Auto-approve all actions (no permission prompts)"** is off by default. Turning it on shows a warning. While it is on, Jiv shows what it is doing in a card (no Allow/Deny buttons) and still records every action in the Audit log.
- **📦 Models → "Uncensored models"** lists open-weight Ollama models with fewer built-in refusals that fit a 4 GB GPU. The names were checked on ollama.com on 21 Oct 2026: `dolphin-phi:2.7b`, `huihui_ai/qwen2.5-abliterate:3b`, `huihui_ai/llama3.2-abliterate:3b` and `huihui_ai/qwen3-abliterated:4b`. Two 7B models, `llama2-uncensored:7b` and `dolphin-mistral:7b`, run partly on the CPU. The recommended defaults stay qwen2.5:3b and nemotron-mini:4b.

## ✨ Better answers on small models (1.5.0)
Tuned for 3–4B models such as qwen2.5:3b and nemotron-mini:4b on a 4 GB GPU (GTX 1650).
- **Stronger prompts:** a writing guide for articles, notes and explanations, and a stricter MCQ format (stem, A–D options, `Answer:` line, short rationale).
- **Think, then answer** (`think_mode`: `auto` by default, or `on` / `off`): for maths, logic and "compare/solve/prove" questions the model works it out first. The working is folded into a collapsed **🧠 Thinking** box, and only the answer is kept in the chat history. Thinking adds a few seconds.
- **Answer guard** (`quality_guard`, on by default): if a reply comes back empty, far too short, missing requested MCQs, or a refusal, Jiv asks once more with a clearer instruction and keeps the better answer.
- **Clarity hint** (`clarity_nudge`): very vague one-line requests get a small tip on what to add (topic, count, level, format).
- **Chat:** ↻ **Regenerate** the last answer, ✎ **Edit** your last message and resend it, ■ **Stop** keeps the partial answer, a live token meter while answering, and a stats line on each answer (tokens · tok/s · seconds; "≈" means the count is estimated from length because the server didn't report usage).
- **Tips strip** for small models above the chat. Click ✕ to hide it for good.
- **Memory:** better automatic memory, near-duplicates merged on save, a **🧹 Merge duplicates** button, and a **🧠 Used N memories** chip under answers that used memory.
- **Batch:** verify is only on by default for MCQ templates, so articles run faster. **Speed:** 1 at a time (default) or 2 (only helps if Ollama runs with `OLLAMA_NUM_PARALLEL=2` and the model fits twice in VRAM; keep 1 on a GTX 1650). There is a better ETA (warm-up + per item) and a **🧪 Quick 20** button for a trial run.
- **Models:** ⚡ **Quick switch** between qwen2.5:3b (★ best for writing and MCQs in our tests) and nemotron-mini:4b, in Settings and on the Models page. It downloads the model first if needed. The Uncensored list is still there.
- **Stability:** Settings no longer silently switches or clears the model when the list can't load; a missing model is shown as "(not found on this server)". A banner appears when Ollama or the model server is down, with ↻ Retry, ▶ Start Ollama (asks permission) and auto-reconnect every 10 s.

## ✎ Clearer English (1.6.2)
Improves **Jiv’s own** reading and writing — not a language-learning course.

- **Reading / summaries:** safer sentence splits (Dr., i.e., U.S., decimals, lists); key-sentence scoring that keeps negations and numbers; extractive summaries polished into readable English (no dangling “This/It”, no duplicate lines).
- **Replies:** short style rules in the system prompt; optional high-precision polish on Jiv’s answers (a/an, repeated words, spacing, its/it’s…). Never edits your message, code blocks, URLs or quotes. Settings → **Polish Jiv’s English** (on by default).
- **Rough / Hinglish input:** broader clarify lexicon (samjhao, grammer, sentance, plz…).
- **grammar_check** tool: rule-based corrections with simple explanations (and optional Hindi hints) when you ask Jiv to fix a sentence.

**From 1.5.1+:** `jiv-1.6.2-english-all-in-one.jivupdate` (min_version 1.5.1).

## 🔎 Search & summarise (1.6.1)
`web_search` now falls back across DuckDuckGo HTML → DuckDuckGo lite → optional **SearXNG** (Settings → Web search) → **Wikipedia** for factual queries. Results are deduped, tracker params stripped, redirect wrappers unwrapped, and ranked by query overlap + source quality.

`web_fetch` returns readable text (nav/ads/scripts removed; PDFs via pypdf) with size caps and a disk cache (TTL, Settings).

`web_summarize` searches (or uses URLs you pass), fetches the top pages concurrently, picks the most relevant chunks (BM25), and writes an **extractive** summary with numbered citations `[1]` and a source list — **no AI required**. If the pages do not support the answer it says **Not found in sources** instead of inventing. Conflicting figures across sources are flagged. Timing stats are included.

Read-only tools still follow Settings → "Run automatically (… web search/fetch …)". Builder and 1.5.4 safeguards are unchanged.

**From 1.5.1+:** use `jiv-1.6.1-search-all-in-one.jivupdate` (min_version 1.5.1).

## 🧱 Builder (1.6.0)
Build a website or Java app **without any AI**. Open **🧱 Builder** in the sidebar. Pick a template, enter a project name, folder and options, preview the files, then allow the write. Generation is local template expansion only (`{{ variables }}`, `{% if %}`, `{% for %}`) — it works with Ollama stopped or missing. Chat stays optional and separate; 1.5.4 safeguards (clarify question, technical-only fallback, no content-refusal retry, permissions) are unchanged.

**Templates:** (a) multi-page static HTML/CSS/JS site, with optional contact form, nav, and pages you type in the form; (b) Java console app, compiled with `javac` (optional `pom.xml` if you already have Maven — nothing is downloaded when the project is created); (c) Java Swing desktop app with N screens and a menu; (d) a small REST API on the JDK’s built-in `com.sun.net.httpserver` and a JSON file store (Spring Boot is not used, so compile stays offline); (e) HTML frontend + Java backend with the same JSON store (SQLite/H2 would need a JDBC driver, so they are not bundled). Built-in templates live in the app `templates` folder. Your own templates go in `%APPDATA%\JivDesktop\templates` (macOS/Linux: the Jiv data directory `/templates`). **Save project as template** copies the source files there.

**Editor and snippets.** A file tree and a plain text editor (no CDN). Insert reusable blocks: navbar, login form, table, card, `JsonStore`, and a JDBC note. **Build** / **Run** / **Package** detect a JDK (`javac`, `java`, `jar`) and Maven/Gradle if installed, show stdout/stderr, and jump to `file:line` errors. Java is packaged as a runnable `.jar`. An `.exe` is attempted with `jpackage` when this JDK has it (Windows often also needs the WiX toolset); otherwise the log explains how to run the jar. Web projects (and the fullstack `frontend/` folder) package a Netlify-ready zip. **Open site** serves the folder on `127.0.0.1`. If `javac` is missing, use **{ } Languages** to install Temurin / OpenJDK (`winget` on Windows, Homebrew on Mac) with the usual permission card.

Every file write, compile, run, package and local server start needs a permission card (**auto-approve stays off by default**) and is written to the Audit log. Writes cannot leave the chosen project folder or use `..`.

**From 1.5.1, 1.5.2, 1.5.3 or 1.5.4:** use `jiv-1.6.0-builder-all-in-one.jivupdate` (min_version 1.5.1) to install 1.6.0 in one step: Updates → Install update file… → Apply → Allow. Chats and settings are kept.

## ✎ Clarify question (1.5.4)
Before answering, Jiv can tidy rough wording (typos, shorthand, Hinglish) into a clearer question — **once**, with a short timeout. Your original message stays in the chat. An expandable card shows what changed, or that the original was used (timeout / skip / unchanged). The clarified text is only the user message for that answer; it is never a system instruction and never rephrases a request to evade safeguards. Attachments, code, URLs, paths, greetings and action commands are left alone. Content declines stay as-is. Settings → **Clarify question** (on by default).

**From 1.5.1 or 1.5.2:** use `jiv-1.5.4-all-in-one.jivupdate` (min_version 1.5.1) to install 1.5.4 in one step. The slim `jiv-1.5.4-prompt-improve.jivupdate` still requires 1.5.3+.

## 🧭 Response reliability (1.5.3)
- **Messy messages:** typos, shorthand (`wat`, `pls`, `b/w`) and Hinglish words (`kya`, `samjhao`) are read for their likely meaning. Jiv answers directly when the meaning is clear, and asks one short question only when two readings would give different answers.
- **Honest answers:** the prompt tells the model to answer in its own words, never invent sources, links, statistics or tool results, and say when it is unsure. Jiv cannot answer everything. Small local models make mistakes, so double-check facts.
- **Backup model (Settings → Backup model, on by default):** if the selected model errors, times out (`stall_timeout`, 180 s with no data) or returns an empty or garbled answer, Jiv answers **once** with another model that is **already installed** in Ollama. That is your preferred list, or else the smallest installed chat model up to 3.5 GB. It never downloads a model, does nothing with cloud servers, and never kicks in because a model declined something. The answer is labelled with the model that wrote it. A second model's answer is not a verification of the first.
- **Fewer false retries:** short, correct answers ("Paris.") are no longer retried. "Too short" now only applies to explicit long-form requests (essay, article, N words…). Declines that cite safety, harm, the law, or adult/sexual content are shown as they are — no rewrite, no other model. The old adult-topic "prudish" retry is removed. A fallback never re-runs a tool that already ran, and permission prompts are unchanged.
- **Sensitive topics:** the system prompt covers sexual health, relationships and consent helpfully and factually. It does not instruct the model to write erotic or pornographic fiction, and it drops the blanket "never refuse" wording.

## 🔓 Adult fix (1.5.2)
Short positive adult rules (no "guidelines" / "appropriate" wording that made small models refuse). Broader prudish-guard patterns. Small-model adult retries drop the hard-limits line and use a brief nudge. Settings → Custom system prompt (advanced). Existing chats pick up the new prompt every turn; stock personas upgrade on start.

## 🔓 Adult by default (1.5.1)
Jiv treats you as a consenting adult — no toggle. Sexual and adult topics, explicit language and mature fiction are answered without lecturing or refusals. Hard limits stay (stated briefly if triggered): nothing sexual involving minors or anyone under 18; no sexual content about real, identifiable people without consent; nothing that helps with clearly illegal harm. Prudish refusals on adult topics are retried once (hard-limit declines are not).
The recommended default for a 4 GB GPU is **`huihui_ai/qwen2.5-abliterate:3b`** (Quick switch + Models). Alternatives: `huihui_ai/qwen3-abliterated:4b`, `dolphin-phi:2.7b`. `qwen2.5:3b` and `nemotron-mini:4b` stay available. Your current model is unchanged until you pick the new one; a one-time banner suggests it. No jailbreak tooling is added; permission cards stay as they are. Abliterated models are community uploads and are less careful with facts.

## ⬆ Updates (version 1.1.0+)
Open **⬆ Updates** in the sidebar, or go to Settings → Updates & backups. An update can come in three ways:
1. **Update file.** Click *Install update file…* and choose a `.jivupdate` file.
2. **GitHub Releases.** Click *Check for updates*. By default (since 1.6.3) Jiv checks the official public repo `aamitojj/jiv-desktop`. To use your own fork, put `owner/repo` under *Source*. Jiv reads the latest release and downloads its `.jivupdate` asset (preferring an `all-in-one` package). If the release only has `JivSetup.exe`, Jiv offers *Download & run installer…*. Jiv only checks when you click, unless you turn on *Auto-install signed updates*, which is off by default. GitHub allows 60 checks per hour without sign-in.
3. **Jiv writes its own update.** Ask in chat, for example "add a feature that…". Jiv uses the `propose_update` tool, which needs your permission first. It syntax-checks every Python file and runs the test suite in a temporary copy of the app. Then it shows a card with the changelog, the check results and a diff for each file.

Whichever way an update arrives, nothing changes until you click **Apply update…** and allow the exact action on the permission card. After that, Jiv:
- verifies the package: every file's SHA-256, plus the ed25519 signature if there is one;
- refuses paths outside the app folder, and refuses downgrades;
- **backs up** the app folder to `<data>/backups/<version>-<timestamp>` (the 5 newest backups are kept);
- writes the new files next to the old ones, then swaps them in;
- restarts.

A small separate watchdog checks that the new version answers `/health` within 30 seconds. If it doesn't, the watchdog **restores the backup and restarts the previous version automatically**. You can also roll back to any backup in the list yourself.

Your settings, chats, agents, API keys and models live in the data folder and are never touched.

- **Signed vs unsigned.** Packages signed with the project key show **✔ Signed**. Anything else shows **⚠ Unsigned – created locally**.
- **Protected files.** Changes to the updater, the watchdog, the signing key, the permission system (`agent.py`, `server.py`) or `run.py` are refused unless you tick **Advanced**.
- **Self-written updates that fail their checks** can only be applied with Advanced ticked.
- **Auto-install** (off by default). *Auto-install signed updates from my GitHub* installs only **signed** releases. It shows a notice first and restarts only when Jiv has been idle for 2 minutes.
- **Installed app (JivSetup.exe).** Updates replace the `.py` files in `…\JivDesktop\app`, and Jiv restarts through `JivDesktop.exe`. The restart watchdog runs on the bundled Python.
- **New Python packages.** If an update needs one, its signed manifest lists it under `requirements`. `make_update.py` picks up new lines in `requirements.txt` automatically, or you can pass `-r name>=1.0`. The permission card shows the packages, and Jiv runs `pip install` into its own Python *before* changing any files. If that fails, nothing changes. Only plain PyPI names and versions are accepted: no URLs, paths or pip options.
- **Big updates** (for example a new bundled Python) ship as a new `JivSetup-x.y.z.exe` attached to the GitHub release. *Check for updates* then offers **Download & run installer…**. With your permission, Jiv backs itself up, downloads the installer, closes, and the installer upgrades in place and starts Jiv again. Your data is kept.
- **PyInstaller single-file builds** (`build_exe.bat`, `build_app.command`) can't patch themselves: rebuild them, or use JivSetup.exe or the script install.

**Making an update (developers).** Run `python tools/make_update.py --old <old folder or zip> --new <new folder> --version 1.1.1 --changelog "..." --key <private.pem> -o jiv-1.1.1.jivupdate`. The tool always stamps the version into `jiv/__init__.py`. Create a signing key with `python tools/gen_signing_key.py --out <folder outside the app> --embed`. Keep the private key out of the app folder and out of git.

## Where your data lives
Windows `%APPDATA%\JivDesktop\`, macOS `~/Library/Application Support/JivDesktop/`, Linux `~/.config/JivDesktop/`: `config.json` (settings and API key, stored only on your PC), `jiv.db` (agents, chats, memory, audit log), and `jiv.log`. Set the `JIV_DATA_DIR` environment variable to use a different folder.

## Security
- The server listens only on `127.0.0.1`. Other devices on your network can't reach it.
- Every API call needs a secret session token that is created each time Jiv starts. Requests with a foreign Origin or Host header are rejected, which protects against CSRF and DNS rebinding. A strict Content-Security-Policy is set.
- API keys are masked in the UI and API, and they are never written to logs or the audit log.
- Commands run with **your** Windows user rights. Read the permission card before you click Allow. Use "Always allow" only for tools you trust.

## Troubleshooting
| Problem | Fix |
|---|---|
| "No model selected" | Settings → Auto-detect / ↻ Models → choose one → Save |
| Auto-detect finds nothing | Make sure Ollama is running (`ollama list` in a terminal) or that the LM Studio server is started. For other hosts or ports, type the Base URL yourself, e.g. `http://192.168.1.10:11434/v1` |
| `model "x" not found` | Run `ollama pull x`. The model name must match `ollama list` |
| The model writes JSON instead of acting, or ignores tools | Use a tool-capable model (llama3.1, qwen2.5). You can also untick "native tool calling" to force JSON mode |
| Replies are very slow | Use a smaller model (`llama3.2:3b`), close other heavy apps, or check that Ollama is using your GPU |
| Port 8765 in use | Jiv picks the next free port automatically. You can also run `start.bat --port 9000` |
| Nothing opens / errors | Run `start_console.bat` to see the error messages, and check `%APPDATA%\JivDesktop\jiv.log` |
| PowerShell commands blocked | Jiv runs PowerShell with `-ExecutionPolicy Bypass` only for its own commands. Your system policy is not changed |
| Model download stuck or failed | Press Resume (already-downloaded parts are kept). Check your internet connection or firewall/proxy. You can also run `ollama pull <model>` in a terminal |
| Silent Ollama install fails | The normal installer opens; follow its steps. Or run `winget install Ollama.Ollama`, then press Re-check |
| Wrong GPU or VRAM shown | Update your GPU driver. For NVIDIA, make sure `nvidia-smi` works in a terminal |
| macOS: "cannot be opened… unidentified developer" | `xattr -dr com.apple.quarantine <JivDesktop folder>`, or right-click → Open |
| macOS: `permission denied` on .command | `chmod +x *.command *.sh scripts/*.sh` (your unzip tool dropped the executable bits) |
| macOS: Ollama.app won't launch | Open it once from Applications and approve the prompt. Or `brew install ollama && brew services start ollama` |
| macOS: everything slows down while chatting | The model is too big for your memory. Use the recommended 7B/8B model, or close memory-heavy apps |
| Language installed but still "not installed" | Press 🔄 Re-scan. If it still isn't found, restart Jiv (some installers update PATH only for new processes). winget may show an admin (UAC) prompt |
| Rust: "linker link.exe not found" | Install "Desktop development with C++" (Visual Studio Build Tools), or run `rustup default stable-gnu` with WinLibs installed |
| Batch job is slow | Check the measured tokens/s on the job card. Turn off the verify pass (about half the time), use a smaller chunk size, or split the file into ranges with start and limit. Make sure Ollama runs on the GPU (`ollama ps` shows "100% GPU") |
| Many batch items are flagged | Open the Review (Excel) file. Small models often miss JSON fields or answer keys; a 7B–8B model, if it fits, is more reliable. Flagged items keep the model's best attempt (or the original) so you can fix them by hand |
| Reset everything | Close Jiv and delete the data folder (see above) |

## Development
```
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python run.py            # --no-browser, --port N, --browser
```
Tests:
- `python tests/test_recommend.py`: unit tests for the recommender and hardware parsing.
- `python tests/e2e_test.py`: chat, tools, permissions and agents. Needs `python tests/mock_llm.py 11434` and Jiv running.
- `python tests/wizard_test.py`: fake installer download/run, service start, pull progress/cancel/resume, delete. Start Jiv with `JIV_INSTALL_MODE=windows JIV_OLLAMA_INSTALLER_URL=http://127.0.0.1:11434/OllamaSetup.exe JIV_OLLAMA_BIN=/tmp/fakeollama/ollama`.
- `python tests/real_ollama_test.py qwen2.5:0.5b`: against a real Ollama.
- `python tests/test_tools_platform.py`: macOS, Windows and Linux tool branches (zsh, `open`, `pbpaste`, PowerShell).
- `python tests/wizard_mac_test.py brew|zip <jiv_url> <ollama_url>`: simulated macOS install via Homebrew or the Ollama-darwin.zip download. Start Jiv with `JIV_INSTALL_MODE=mac` (plus `JIV_NO_BREW=1 JIV_MAC_APPS_DIR=… JIV_OLLAMA_MAC_ZIP_URL=…` for the zip path).
- `python tests/test_updater.py`: package verification, tampering, signatures, path traversal, downgrades, apply/backup/rollback, propose_update (44 tests).
- `python tests/update_integration_test.py`: real restarts on a temporary app copy. Covers a GitHub check against `tests/mock_github.py`, a signed 1.1.1 install, a broken update that is rolled back automatically, a manual rollback and idle auto-install.
- `python tests/installed_wine_test.py dist/JivSetup-1.2.0.exe [wineprefix]`: on Linux with Wine. Covers silent install, launch via JivDesktop.exe, /health, a signed feature update, auto-rollback, a pip requirement, manual rollback, a big update via JivSetup.exe from mock GitHub, upgrade keeping data, and uninstall with and without `/DELETEDATA`.
- `python tests/test_uploads.py` and `python tests/attach_ui_test.py`: attachment text extraction and the attach UI.
- `python tests/installed_wine_upload_test.py JivSetup-1.2.0.exe jiv-1.2.2-file-upload.jivupdate`: applies 1.2.2 to an installed 1.2.0 under Wine.
- `python tests/update_ui_test.py`: headless Chrome, propose_update end to end (chat card, permission, restart into 1.1.1) with screenshots 22–26 (27–28 show the installer running under Wine).
- `python tests/test_learning.py`: memory, skills, feedback, small-model tool parsing, languages (mock winget via `JIV_WINGET`) and real `run_code` for every language installed on the machine.
- `python tests/learning_ui_test.py`: headless Chrome for memory, run_code and skills, feedback and lessons, and the Languages page with a mocked install (screenshots 34–43).
- `python tests/installed_wine_learning_test.py JivSetup-1.2.0.exe jiv-1.2.2-file-upload.jivupdate jiv-1.3.0-learning-languages.jivupdate`: 1.2.0 → 1.2.2 → 1.3.0 under Wine, with winget mocked by `tests/fixtures/bin/winget.cmd`.
- `python tests/test_batch.py`: splitting, HTML round-trips, chunking, MCQ, answer-key and schema checks, plus the engine against a mock model: verify, retries, flags, outputs, pause/resume/cancel, restart resume and model-down handling.
- `python tests/batch_integration_test.py`: the HTTP API, the permission requirement, a `kill -9` of the server mid-job, the resume, and token-protected downloads.
- `python tests/batch_ui_test.py`: headless Chrome on a 200-MCQ JSON file and an HTML folder, the estimate, the single permission card, progress, pause, downloads, "repeat this 50 times", and `start_batch` from chat (screenshots 44–54).
- `python tests/installed_wine_batch_test.py JivSetup-1.2.0.exe <1.2.2> <1.3.0> <1.4.0 .jivupdate>`: upgrades through to 1.4.0 under Wine, then runs batch jobs in the installed app, including a restart mid-job.
- `python tests/test_refusals.py`: prompts for small and large models, refusal detection, retry once, Jiv-change scope, direct writing (no tools), batch suggestion, auto-approve, persona upgrade, and the NCLEX generator against the mock model.
- `python tests/refusal_ui_test.py`: headless Chrome for the guard in chat, the batch suggestion, the NCLEX generator job, the auto-approve toggle with its warning, and the Uncensored note (screenshots 55–60).
- `python tests/real_refusal_eval.py <jiv tree> <label> <model> <base_url> [runs]`: refusal rate against a **real** model, using llama.cpp `llama-server` or Ollama. The prompts are 10 content requests, 6 "allow any task" requests and 1 Jiv-change control. Permission cards are denied, so this measures whether Jiv answers in chat.
- `python tests/installed_wine_refusal_test.py JivSetup-1.2.0.exe <1.2.2> <1.3.0> <1.4.0> <1.4.1 .jivupdate>`: Wine, 1.4.0 → 1.4.1.
- `python tests/test_quality.py`: the 1.5.0 answer guard, think mode, clarity hint, regenerate/edit/stop, stats, memories used and merge, the Settings model fix, `/api/connection`, batch verify defaults and parallel.
- `python tests/quality_ui_test.py`: headless Chrome for the tips strip, meter and stats, the think box, regenerate, edit, stop, the memories chip, quick switch, Batch speed / Quick 20, merge, and the Ollama-down banner with reconnect (screenshots 62–69).
- `python tests/installed_wine_quality_test.py JivSetup-1.2.0.exe <1.2.2> <1.3.0> <1.4.0> <1.4.1> <1.5.0 .jivupdate>`: Wine, 1.4.1 → 1.5.0.
- `python tests/test_prompt_improve.py`: 1.5.4 clarify-question skip/validate/timeout/no-bypass.
- `python tests/test_english.py`: 1.6.2 segmentation, negation-aware extracts, summary/reply polish, grammar_check fixtures.
- `python tests/test_search.py`: 1.6.1 search parsing, ranking, extraction, citations, extractive no-AI summary, cache TTL (fixtures; optional live smoke).
- `python tests/test_builder.py`: 1.6.0 Builder template expansion, path safety, snippets, generate/compile/package (JDK used when present).
- `python tests/installed_wine_aio160_test.py`: Wine, direct 1.5.1 → 1.6.0 all-in-one, chats/settings kept, Builder API without a model.
- `python tests/test_reliability.py`: 1.5.3 understanding, fallback, bounded retries, no duplicate tools.
- `python tests/installed_wine_reliability_test.py JivSetup-1.2.0.exe <1.2.2> … <1.5.2> <1.5.3 .jivupdate>`: Wine, 1.5.2 → 1.5.3.
- `python tests/installed_wine_adult_test.py JivSetup-1.2.0.exe <1.2.2> <1.3.0> <1.4.0> <1.4.1> <1.5.0> <1.5.1> <1.5.2 .jivupdate>`: Wine, 1.5.1 → 1.5.2 adult-fix.
- `sh tests/start_stack.sh`: starts the mock LLM (11434) and Jiv (8765) for e2e and screenshots.
- `JIV_FAKE_HW=profile.json` makes the wizard use a hardware profile from a file (used for the M5 screenshots). For UI screenshots, `pip install playwright` and run `python tests/screenshots.py`.
