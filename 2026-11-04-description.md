# OpenCode and Ollama environment: inventory and isolation review

Date of the inventory: 2026-11-04 (local time, UTC-3).
Host: the Windows laptop of user `cesar`. Everything below was read from the files, registry
keys, running processes and command output of this machine; nothing was changed.
This file is a snapshot and is not kept up to date. The state of the installer is in section 9 of
`2026-11-04-plan-repo-to-install.md`.

Paths are written with forward slashes. `~` is `C:/Users/YOUR_USERNAME`.

## 1. Host

| Item | Value |
|---|---|
| OS | Windows 11 Home Single Language, build 10.0.26300 |
| CPU | AMD Ryzen 9 5900HX |
| RAM | 27.9 GiB |
| GPU | NVIDIA GeForce RTX 3050 Laptop GPU, 4 GiB VRAM, driver 617.42, CUDA compute 8.6 |
| Shells | Git Bash (MSYS), PowerShell 7, cmd |
| Package managers seen | Chocolatey, nvm, uv 0.13.7, mise, bun |

The 4 GiB of VRAM is the limiting factor for every local model; the 13 to 18 GB models below run mostly on
the CPU and system RAM, with a few layers on the GPU. (This is my current laptop.)

## 2. Ollama

### 2.1 Installation and runtime

| Item | Value |
|---|---|
| Version | 0.40.14 (upgraded today, 2026-11-04 10:26 to 10:28, from 0.40.13 by the desktop app's own updater) |
| Install directory | `~/AppData/Local/Programs/Ollama` (`ollama.exe`, `ollama app.exe`, `lib/ollama/` with `llama-server.exe`, CPU backends, `cuda_v12`, `cuda_v13`, `rocm_v7_1`, `vulkan`) |
| On PATH | yes, via the user `Path` registry value |
| Autostart | Startup-folder shortcut `~/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/Ollama.lnk` (not in the `Run` registry key) |
| Running now | `ollama.exe` (PID 17956, the API server) and `ollama app.exe` (PID 42340, tray app and UI server on a random port) |
| API endpoint | `http://127.0.0.1:11434` (loopback only); `GET /api/version` answers `0.40.14` |
| Logs | `~/AppData/Local/Ollama/server.log`, `app.log`, rotated `server-1..5.log`, `app-1..5.log`, `upgrade.log` |
| First install | about 2024-11-25 (date of the registry key pair `~/.ollama/id_ed25519`) |

### 2.2 Effective server configuration

Taken from the `server config` line that the server writes at start (`server.log`, 2026-11-04 10:28).
The "source" column says where each value comes from.

| Setting | Effective value | Source |
|---|---|---|
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | default |
| `OLLAMA_MODELS` | `C:/Users/cesar/.ollama/models` | default (also stored in the app settings DB) |
| `OLLAMA_NO_CLOUD` | `true` | `~/.ollama/server.json` = `{"disable_ollama_cloud": true}`; log confirms `Ollama cloud disabled: true` |
| `OLLAMA_CONTEXT_LENGTH` | `131072` | desktop app setting `context_length` (see 2.3). Overrides the "vram-based default" of 4096 that the server computes for a 4 GiB GPU |
| `OLLAMA_FLASH_ATTENTION` | `true` | user environment variable `OLLAMA_FLASH_ATTENTION=1` (HKCU\Environment) |
| `OLLAMA_VULKAN` | `false` | user environment variable `OLLAMA_VULKAN=0` (HKCU\Environment) |
| `OLLAMA_KV_CACHE_TYPE` | unset (`f16`) | default |
| `OLLAMA_KEEP_ALIVE` | `5m0s` | default |
| `OLLAMA_NUM_PARALLEL` | `1` | default |
| `OLLAMA_MAX_LOADED_MODELS` | `0` (auto) | default |
| `OLLAMA_MAX_QUEUE` | `512` | default |
| `OLLAMA_LOAD_TIMEOUT` | `5m0s` | default |
| `OLLAMA_ORIGINS` | localhost, 127.0.0.1, 0.0.0.0, `app://`, `file://`, `tauri://`, `vscode-webview://`, `vscode-file://` | default |
| `OLLAMA_REMOTES` | `ollama.com` | default |
| `OLLAMA_DEBUG` | `INFO` | default |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` | empty | the server is not started through the local-llm proxy |

No machine-wide `OLLAMA_*` variables exist (HKLM environment checked).

GPU discovery: the 10:26 start (version 0.40.13) hit `llama-server GPU discovery watchdog timed out` for
`cuda_v12`, `cuda_v13` and `rocm_v7_1` and fell back to CPU-only (`inference compute id=cpu`). The 10:28
start, right after the upgrade, found `CUDA0 NVIDIA GeForce RTX 3050 Laptop GPU total=4.0 GiB
available=3.2 GiB`. So the currently running server does see the GPU.

### 2.3 Desktop app settings

`~/AppData/Local/Ollama/db.sqlite`, table `settings` (one row). Tables present: `settings`, `chats` (1),
`messages` (6), `tool_calls` (0), `attachments` (0), `users` (1).

| Column | Value | Meaning |
|---|---|---|
| `expose` | 0 | not exposed on the network (loopback only) |
| `browser` | 0 | browser integration off |
| `agent`, `tools` | 0, 0 | app-side agent and tools off (app log: `initialized tools registry tool_count=0`) |
| `airplane_mode` | 0 | the app's airplane mode is OFF |
| `turbo_enabled` | 0 | cloud "turbo" off |
| `websearch_enabled` | 0 | cloud web search off |
| `auto_update_enabled` | 1 | the app checks `https://ollama.com/api/update` every hour and downloads installers from GitHub |
| `context_length` | 131072 | becomes `OLLAMA_CONTEXT_LENGTH` for the server |
| `selected_model` | `gemma4:e4b-it-q8_0` | this model is NOT installed (see 2.6) |
| `think_enabled` | 0 | |
| `models` | `C:\Users\cesar\.ollama\models` | |
| `remote` | empty | |
| `claude_desktop_used`, `codex_desktop_used` | 0, 0 | |

`~/AppData/Local/Ollama/config.json` only holds a device id and `first-time-run`.

Update activity observed in `app-2.log`: hourly `New update available at
https://github.com/ollama/ollama/releases/download/v0.40.14/OllamaSetup.exe` on 2026-11-03, bundle cached
under `updates_v2/`, installed silently on 2026-11-04 10:26 (`upgrade.log`). At that moment the check itself
failed with `lookup ollama.com: no such host` (no DNS at that instant), but the already downloaded bundle was
used.

### 2.4 Contents of `~/.ollama`

| Path | Purpose |
|---|---|
| `server.json` | `{"disable_ollama_cloud": true}` |
| `id_ed25519`, `id_ed25519.pub` | the key pair Ollama uses to sign registry requests (2024-11-25) |
| `history` | plain-text history of ...  |
| `onboarding-v1.completed` | marker, 2025-09-20 |
| `cache/model-recommendations.json` | list fetched from ollama.com (cloud models `kimi-k2.6:cloud`, `glm-5.1:cloud`, `qwen3.5:cloud`, `nemotron-3-super:cloud`, `gemma4:31b-cloud`, and local suggestions `gemma4`, `qwen3.6`). The server schedules this fetch every 3.5 to 4.5 hours (`model_recommendations.go` lines in `server.log`, `consecutive_failures=0`) |
| `models/blobs/` | 23 blobs, 46,143,571,647 bytes (46.1 GB) |
| `models/manifests/registry.ollama.ai/library/` | legacy manifests: `gpt-oss/20b`, `gpt-oss-64k/latest`, `qwen3-coder/30b`, plus `llamacpp/<digest>` variant manifests |
| `models/manifests-v2/ollama.com/library/` | 0.40-style manifests: `gpt-oss/20b`, `gpt-oss-64k/latest`, `qwen3-coder/30b`, `qwen3-coder-32k/latest` |
| `models/metadata/` | GGUF key/value dumps for the three model blobs (written by 0.40.0) |

### 2.5 Installed models

`ollama list` on 2026-11-04:

| Tag | Base | Architecture | Parameters | Quantization | Native context | `num_ctx` in Modelfile | Capabilities | Disk | Pulled / created |
|---|---|---|---|---|---|---|---|---|---|
| `qwen3-coder:30b` | Qwen3-Coder-30B-A3B-Instruct (Alibaba) | qwen3moe, 48 layers, 128 experts, 8 active | 30.5 B | Q4_K_M | 262,144 | none (server default applies: 131,072) | completion, tools | 18.56 GB (blob `1194192c…`) | 2026-10-07 09:13 |
| `qwen3-coder-32k:latest` | `FROM qwen3-coder:30b` | same | same | same | same | **32768** | completion, tools | shares the blob (no extra disk) | 2026-10-07 15:56 |
| `gpt-oss:20b` | gpt-oss-20b (OpenAI) | gpt-oss, 24 layers, 32 experts, 4 active, sliding window 128 | 20.9 B | MXFP4 | 131,072 | none (server default: 131,072) | completion, tools, thinking (low / medium / high, default medium) | 13.79 GB **twice** (blobs `e7b273f9…` for the `ggml` runner and `8ecb5d4a…` for the `llamacpp` runner) | 2026-10-07 13:07 and 2026-10-08 22:40 |
| `gpt-oss-64k:latest` | `FROM gpt-oss:20b` | same | same | same | same | **65536** | same | shares the blobs | 2026-10-07 13:09 |

Sampling parameters: the Qwen tags set `temperature 0.7`, `top_k 20`, `top_p 0.8`, `repeat_penalty 1.05`
and the three stop tokens `<|im_start|>`, `<|im_end|>`, `<|endoftext|>`; the gpt-oss tags set
`temperature 1`. The Qwen tags use Ollama's built-in `qwen3-coder` renderer and parser (template
`{{ .Prompt }}`), the gpt-oss tags use the Harmony template, which carries the text "You are ChatGPT, a
large language model trained by OpenAI".

Runner variants: `ollama show` reports `runner llamacpp` for both gpt-oss tags, with `available runners
llamacpp:… , ggml:…`. Ollama 0.40 keeps one model blob per runner, which is why gpt-oss occupies 27.6 GB
instead of 13.8 GB.

The two derived tags were built from these Modelfiles in `~/local-llm/`:

```text
# ~/local-llm/qwen3-coder-32k.Modelfile
FROM qwen3-coder:30b
PARAMETER num_ctx 32768

# ~/local-llm/gpt-oss-64k.Modelfile
FROM gpt-oss:20b
PARAMETER num_ctx 65536
```

The licence layer of all four tags is Apache 2.0.

### 2.6 Models referenced somewhere but not installed

| Name | Referenced by | Effect |
|---|---|---|
| `gemma4:e4b-it-q8_0` | Ollama desktop app `selected_model`; `~/.continue/config.yaml` (the only model in the Continue config) | the Ollama app chat and the Continue extension point at a model that does not exist on disk |
| `qwen2.5:32b` | `~/.ollama/history` | removed at some point |
| `code-review-for-python` | `~/.ollama/history` (`/save`) | removed at some point |

## 3. OpenCode

### 3.1 Binaries present

| Binary | Version | Path | Origin |
|---|---|---|---|
| `opencode.exe` (the one on PATH) | 1.19.5 | `C:/ProgramData/chocolatey/bin/opencode.exe` (shim) to `C:/ProgramData/chocolatey/lib/opencode/tools/opencode.exe` | Chocolatey package `opencode` 1.19.5, maintainer `itsrainingmani`, installed 2026-10-29 |
| `opencode.exe` (private copy) | 1.18.34 | `~/local-llm/opencode/bin/opencode.exe` | copied 2026-10-06 for the local-llm profile |
| `OpenCode.exe`, `opencode-cli.exe` | 1.0.223 | `~/AppData/Local/opencode/` | desktop app installer from 2026-01-01; no activity since |

Helper installs made by OpenCode itself: `~/.local/share/opencode/bin/vscode-eslint`,
`~/.cache/opencode/bin/vscode-eslint`, `~/.cache/opencode/packages/{@vue, pyright, typescript-language-server}`
(LSP servers), `~/.cache/opencode/package.json` (`opencode-anthropic-auth 0.0.13`, `pyright ^1.1.408`),
`~/.cache/opencode/version` = `21`.

### 3.2 Default profile (cloud providers) under the XDG directories

| Location | Content |
|---|---|
| `~/.config/opencode/opencode.jsonc` | `{"$schema": "https://opencode.ai/config.json"}` only, every setting is the default |
| `~/.config/opencode/package.json`, `bun.lock`, `package-lock.json` | `@opencode-ai/plugin` pinned at **1.4.1**, **1.3.6** and **1.4.0** respectively (the three files disagree); no plugin is configured, so the dependency is unused |
| `~/.config/opencode/skills/` | five symlinks into `~/.agents/skills/`: `document-writer`, `motion`, `ts-library`, `vue`, `vueuse`. `~/.agents/.skill-lock.json` (the `skills` CLI from vercel-labs) also tracks `find-skills`, `web-design-guidelines`, `writing-guidelines`, which are not linked into OpenCode |
| `~/.local/share/opencode/auth.json` | one provider, `anthropic`, type `api` (an Anthropic API key; value not copied here) |
| `~/.local/share/opencode/opencode.db` (+ `-wal`, `-shm`) | 521 MB + 27 MB SQLite session store, last written 2026-10-09 |
| `~/.local/share/opencode/storage/` | the pre-SQLite JSON store: `message/` (84 sessions), `part/` (4,556 files), `project/` (4), `session/`, `session_diff/` (117), `todo/` (6), `migration` marker |
| `~/.local/share/opencode/snapshot/` | git-style snapshots for 7 project ids |
| `~/.local/share/opencode/log/` | logs from 2026-01-27 to 2026-10-09; versions seen 1.1.34, 1.4.1, 1.14.29, 1.17.8, 1.18.26, 1.18.30, 1.18.31, 1.18.34 |
| `~/.local/state/opencode/model.json` | recent: `opencode/big-pickle`, `anthropic/claude-opus-4-5`, `opencode/glm-4.7-free`, `opencode/grok-code`; favourite `anthropic/claude-opus-4-5`; variant `high` for it |
| `~/.local/state/opencode/kv.json` | TUI preferences: theme `cursor`, sidebar hidden, timestamps hidden, thinking shown, diff wrap `word`, upsell dismissed |
| `~/.local/state/opencode/prompt-history.jsonl` | 50 prompts, 12 MB (it embeds pasted content) |
| `~/.cache/opencode/models.json` | 5.4 MB snapshot of the models.dev catalogue, 225 providers, fetched 2026-10-09; it has **no** `ollama` entry, so any Ollama provider must be declared by hand |

Projects registered in this profile: `~/cesar/blog/CesarBallardini.github.io`, later log entries also
show `~/cesar/github/CesarBallardini/llm-for-dummies-study-plan`.
The log never mentions Ollama; the providers used were `opencode` (free models) and `anthropic`.

No `OPENCODE_*` environment variable is set globally, and this repository has no `opencode.json` of its own.

### 3.3 The local-llm profile (Ollama only)

Everything lives under `~/local-llm/` and is entered through `~/local-llm/opencode-local.cmd`
(alias `opencode-local` in `~/.bashrc`).

What the launcher does:

1. Calls `ensure-proxy.cmd` (section 6) and aborts if the proxy cannot start.
2. Sets `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, `XDG_STATE_HOME` to
   `~/local-llm/opencode/{config,data,cache,state}` and `OPENCODE_CONFIG_DIR` to
   `~/local-llm/opencode/config/opencode`, so the cloud profile's `auth.json` is never read.
3. Sets `OPENCODE_DISABLE_MODELS_FETCH`, `OPENCODE_DISABLE_AUTOUPDATE`, `OPENCODE_DISABLE_SHARE`,
   `OPENCODE_DISABLE_DEFAULT_PLUGINS`, `OPENCODE_DISABLE_LSP_DOWNLOAD`, `OPENCODE_DISABLE_EXTERNAL_SKILLS`,
   `OPENCODE_DISABLE_CLAUDE_CODE` and `OPENCODE_PURE` to true.
4. Blanks `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENCODE_API_KEY`.
5. Runs the private `~/local-llm/opencode/bin/opencode.exe` (1.18.34).

Configuration, `~/local-llm/opencode/config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "enabled_providers": ["ollama"],
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://127.0.0.1:11434/v1" },
      "models": {
        "qwen3-coder-32k": {
          "name": "Qwen3-Coder 30B-A3B, 32k (local)",
          "tool_call": true,
          "limit": { "context": 32768, "output": 8192 }
        },
        "gpt-oss-64k": {
          "name": "gpt-oss 20B, 64k (local)",
          "tool_call": true,
          "reasoning": true,
          "limit": { "context": 65536, "output": 8192 }
        }
      }
    }
  },
  "model": "ollama/qwen3-coder-32k",
  "small_model": "ollama/qwen3-coder-32k",
  "share": "disabled",
  "autoupdate": false,
  "permission": {
    "webfetch": "allow",
    "websearch": "deny"
  }
}
```

Other files of the profile:

| Path | Content |
|---|---|
| `config/opencode/AGENTS.md` | one global instruction: always write Windows paths with forward slashes in tool calls |
| `config/opencode/package.json`, `package-lock.json` | `@opencode-ai/plugin 1.18.34` (matches the binary); the install is what produced the `registry.npmjs.org` lines in the proxy log |
| `state/opencode/model.json` | recent `ollama/gpt-oss-64k`; variants `default` for both models |
| `state/opencode/prompt-history.jsonl` | 38 prompts (2026-10-06 to 2026-10-12): writing `AGENTS.md` for a confidential codebase, read-only code review, "fix this: Provider response headers timed out after 300000ms", "fix this: SSE read timed out" |
| `data/opencode/opencode.db` | 26.6 MB, last written 2026-10-12 |
| `data/opencode/log/opencode.log` | 2026-10-06 to 2026-10-13; `providerID=ollama` 621 lines; `modelID=qwen3-coder-32k` 523, `modelID=gpt-oss-64k` 94 |
| `data/opentui/tree-sitter/` | parsers downloaded by the TUI |

Errors in that log, grouped:

| Count | Error |
|---|---|
| 37 | `ProviderHeaderTimeoutError: Provider response headers timed out after 300000ms` (qwen3-coder-32k, in the build, explore and compaction agents): Ollama took more than 5 minutes to start answering, which is what a 30 B model with a 32 k prompt does on this GPU |
| 24 | `AI_APICallError: error parsing tool call: raw='{"filePath":"C:\\Users\\cesar\AppData\\L…` (gpt-oss-64k): the model emitted backslash paths that broke the tool-call JSON; this is the reason for the forward-slash rule in `AGENTS.md` |
| 6 | `ProviderResponseStreamError: SSE read timed out` |
| 3 | `Aborted` (user interrupt) |

## 4. Claude Code local profile (the second Ollama consumer)

`~/local-llm/claude-local.cmd` (aliases `claude-local` and `claude-local-oss` in `~/.bashrc`) runs a
private `~/local-llm/claude/bin/claude.exe` (2.1.292) against Ollama's Anthropic-compatible endpoint:

| Setting | Value |
|---|---|
| `CLAUDE_CONFIG_DIR` | `~/local-llm/claude/config` (own `.claude.json`, `settings.json`, `history.jsonl`, `projects/`) |
| `ANTHROPIC_BASE_URL` | `http://127.0.0.1:11434` (Ollama serves `/v1/messages`) |
| `ANTHROPIC_AUTH_TOKEN` | `ollama`; `ANTHROPIC_API_KEY` blanked |
| Model | every slot (`ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `_SONNET_`, `_HAIKU_`, `ANTHROPIC_SMALL_FAST_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`) = `LOCAL_LLM_MODEL`, default `qwen3-coder-32k`; the `claude-local-oss` alias selects `gpt-oss-64k` |
| Context | `CLAUDE_CODE_AUTO_COMPACT_WINDOW` = 32768, or 65536 for `gpt-oss-64k`; `CLAUDE_CODE_MAX_OUTPUT_TOKENS` = 8192 |
| Disabled | non-essential traffic, telemetry, error reporting, autoupdater, `/bug`, prompt caching, feedback survey, marketplace auto-install |
| `settings.json` | `permissions.deny: ["WebSearch"]`, `blockReadsOutsideWorkingDirectories: true`, `skipWebFetchPreflight: true`, theme dark |
| Usage | 4 startups on 2026-10-07 in some folder; last session: model `qwen3-coder-32k`, 59,794 input tokens, 370 output tokens. The `lastCost 0.33 USD` figure is Claude Code's own price table applied to a free local model, not a bill |

## 5. Other Ollama consumers on the machine

| Consumer | Configuration | State |
|---|---|---|
| Continue (VS Code extension 2.0.0) | `~/.continue/config.yaml`: one model, provider `ollama`, `gemma4:e4b-it-q8_0` at `http://localhost:11434`, roles chat / edit / apply, contextLength 32768, maxTokens 4096, temperature 0.2; autocomplete (`qwen2.5-coder:1.5b-base`) and embeddings (`nomic-embed-text`) left commented out | broken until `gemma4:e4b-it-q8_0` is pulled or the model name is changed |
| Ollama desktop app chat | `selected_model gemma4:e4b-it-q8_0` | same gap |
| CodeGPT (VS Code extension 3.8.35) | ships an Ollama provider; no user configuration referencing Ollama found in `~/.codegpt` | unused |
| VS Code user settings, main Claude Code (`~/.claude`), Twinny, cagent, Gemini CLI | no Ollama reference | not configured |

## 6. The isolation layer ("the proxy used to isolate the model")

All of it is in `~/local-llm/` plus three Windows Firewall rules.

### 6.1 Components

| Component | Role |
|---|---|
| `ensure-proxy.cmd` | If nothing listens on `127.0.0.1:8899`, starts `egress_proxy.py` minimised with `uv run --no-project --python 3.13 python -I`, waits up to about 20 seconds for the port, then exports `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy`, `https_proxy` = `http://127.0.0.1:8899` and `NO_PROXY` = `127.0.0.1,localhost,::1` for the launcher's shell. Loopback (Ollama) therefore bypasses the proxy |
| `egress_proxy.py` | Standard-library asyncio HTTP proxy bound to `127.0.0.1` only. Handles HTTPS through `CONNECT` and plain HTTP through absolute-URI requests. For each request it takes the host, refuses it with `403` when the host equals or is a subdomain of any entry in `egress-denylist.txt`, otherwise opens the upstream connection and pipes bytes both ways. It does not inspect or decrypt TLS. The deny list is re-read whenever the file's mtime changes. Every decision is appended to `egress.log` as `<time> <ALLOW or DENY> <method> <host>:<port>` |
| `egress-denylist.txt` | Domains refused, with all their subdomains: Anthropic (`anthropic.com`, `claude.ai`, `claude.com`, `claudeusercontent.com`); Claude Code telemetry (`datadoghq.com`, `statsig.com`, `statsigapi.net`, `featureassets.org`, `prodregistryv2.org`, `sentry.io`); Qwen / Alibaba (`qwen.ai`, `qwenlm.ai`, `aliyun.com`, `aliyuncs.com`, `alibabacloud.com`, `modelscope.cn`, `modelscope.ai`); the Ollama registry (`ollama.com`); OpenCode (`opencode.ai`, `models.dev`, `sst.dev`, `anoma.ly`). That was 22 entries. **Extended later on 2026-11-04** to 500 entries in 11 blocks: model producers (OpenAI added), harness and runtime vendors, telemetry, hosted LLM APIs worldwide (with the deliberately broad `googleapis.com`, `amazonaws.com` and `core.windows.net`), coding assistants, GPU clouds, model hubs, search APIs, exfiltration sinks (pastebins, code-hosting write paths, file drops, cloud storage, tunnels, webhook catchers, messaging, mail, deployment targets, decentralised storage), DNS-over-HTTPS resolvers, social and SaaS posting sinks. The previous file is kept as `egress-denylist.txt.2026-11-04.bak` |
| `egress.log` | Audit trail. 36 lines, 2026-10-07 to 2026-10-12, all `ALLOW CONNECT`: `registry.npmjs.org:443` (18, the plugin install), `docs.python.org:443`, `peps.python.org:443`, `github.com:443`, `raw.githubusercontent.com:443`, `release-assets.githubusercontent.com:443`. No `DENY` was ever recorded |
| Windows Firewall rule `local-llm: opencode direct outbound` | Outbound, Block, all profiles, program `~/local-llm/opencode/bin/opencode.exe`, any remote address, enabled |
| Windows Firewall rule `local-llm: claude direct outbound` | same for `~/local-llm/claude/bin/claude.exe` |
| Windows Firewall rule `local-llm: ollama runner direct outbound` | same for `~/AppData/Local/Programs/Ollama/lib/ollama/llama-server.exe` (the process that actually holds the model weights) |
| Launcher environment | the `OPENCODE_DISABLE_*` and `CLAUDE_CODE_DISABLE_*` variables listed in sections 3.3 and 4 turn off the vendor-facing features at the source |

The firewall profiles (Domain, Private, Public) are enabled with the default outbound action
"not configured", which means allow; only the three explicit rules block anything. No process was
listening on `127.0.0.1:8899` during this inventory, which is expected: the launchers start the proxy on
demand and it dies with the console that started it.

### 6.2 How a request travels

1. The model never opens a socket. It runs inside `llama-server.exe`, which is firewall-blocked outbound
   and only talks to `ollama.exe` over loopback.
2. `ollama.exe` serves `127.0.0.1:11434` to the harness. Cloud features are disabled.
3. The harness (`opencode.exe` or `claude.exe`, private copies) is the process that would reach the
   internet when the model calls a tool such as `webfetch`. Its direct connections are blocked by the
   firewall, so the only way out is the proxy address in `HTTPS_PROXY`, and the proxy applies the deny list.
4. Anything the harness spawns from a `bash` tool call (curl, python, git, uv, npm, PowerShell) is a
   separate executable: it inherits the proxy variables, but it is **not** covered by the firewall rules,
   and it is free to ignore the variables.

Evidence the design works as intended: the local-llm OpenCode log shows 621 successful Ollama requests
while the block rule was enabled (loopback is not filtered by Windows Firewall), and the proxy log shows
the plugin install and documentation fetches going through the proxy rather than directly.

## 7. Air-gap equivalence assessment

Goal as stated: using the selected Ollama model must be equivalent to being air-gapped. The model must not
be able to (a) reach its home, (b) use an LLM outside the laptop, (c) send any message to its producer or
anywhere else.

Two clarifications first. The model weights have no network ability of their own; every outbound byte
comes from one of three pieces of software: the harness driven by the model (OpenCode or Claude Code and
whatever their tools spawn), the Ollama server, and the Ollama desktop app. And "its producer" means
Alibaba for the Qwen tags and OpenAI for the gpt-oss tags; neither has any channel on this machine other
than what those three programs could open.

| Path | Current control | Status |
|---|---|---|
| Model process (`llama-server.exe`) to internet | firewall block | closed |
| Harness binaries direct to internet | firewall block | closed |
| Harness through the proxy | deny list, default allow | **partly open**: any host not on the list is reachable |
| Harness `webfetch` tool | OpenCode `webfetch: allow`; Claude Code WebFetch allowed | open to every non-denied host (data can leave in URL query strings) |
| Harness `bash` tool and child processes | none (OpenCode `bash` at its default `allow`; Claude Code permission prompts only) | **open** |
| Harness `websearch` | OpenCode `deny`; Claude Code `WebSearch` denied | closed |
| Harness vendor traffic (OpenCode: opencode.ai, models.dev; Claude Code: anthropic.com, statsig, datadog, sentry) | disabled by environment variables and refused by the deny list | closed |
| MCP servers | none configured in either local profile | closed |
| Ollama server (`ollama.exe`) to ollama.com | cloud disabled; registry pulls and the periodic model-recommendations fetch still go out; not firewall-blocked | partly open (no model content travels on it) |
| Ollama desktop app (`ollama app.exe`) to ollama.com and github.com | hourly update check, auto-update on; not firewall-blocked | partly open (software phoning its own vendor, not the model's) |
| Accidental use of the unrestricted binaries (`opencode` 1.19.5 on PATH with the Anthropic key in `~/.local/share/opencode/auth.json`, the Chocolatey `claude`, the desktop OpenCode 1.0.223) | only the habit of typing `opencode-local` / `claude-local` | **open** |
| Model producer domains | at inventory time Alibaba / Qwen were listed and OpenAI was not; the deny list extended later the same day adds OpenAI (`openai.com`, `chatgpt.com`, `oaiusercontent.com`, `oaistatic.com`, `sora.com`) and the model hubs | closed for the known producer domains |
| External LLM APIs (Google, Azure, Bedrock, Groq, OpenRouter, Mistral, DeepSeek, Together, Fireworks, xAI, Cohere, the Chinese providers, …) | not listed at inventory time; listed since the extension of the same day, including the broad `googleapis.com`, `amazonaws.com` and `core.windows.net` entries that cover regional endpoints | closed for every listed vendor; a deny list still cannot name a host that does not exist yet, nor a raw IP address |
| DNS | Windows resolves names in the DNS Client service, so a blocked program's lookups still happen | negligible side channel, no practical content exfiltration |
| `ensure-proxy.cmd` bootstrap | `uv` runs before the proxy variables are set; it would fetch a Python build from GitHub if 3.13 were missing (3.13.18 and 3.13.3 are present, so it does not today) | minor |

Verdict: the current layer protects well against the harness vendors and against the Qwen producer,
and it is a sound design (block the binary, allow only the proxy, filter at the proxy). It is not
air-gap-equivalent yet, for two reasons: the proxy is a deny list, so any host that is not listed is
reachable (the extensive list of the same day removes the known vendors and sinks, not unknown or future
ones, nor raw IP addresses); and the `bash` tool escapes both the firewall rules and the proxy.

## 8. What would make it air-gap-equivalent

Recommended, in order. Tier A is configuration only and reversible. Tier B closes the child-process hole
properly. Item 2 of Tier A was applied later on 2026-11-04; the rest have not been.

### Tier A: configuration

1. **Make the proxy default-deny.** Replace the deny list with an allow list (`egress-allowlist.txt`, for
   example `docs.python.org`, `peps.python.org`, and nothing else), and refuse everything else. The change
   in `egress_proxy.py` is a few lines: load the allow list the same way the deny list is loaded and make
   `is_denied` return true when the host is not on it. Keep the deny file as documentation of why the
   vendors are excluded. If no web access at all is wanted, the simplest form is to set
   `"webfetch": "deny"` in the local `opencode.json` and not start the proxy; the firewall rule then leaves
   the harness with no route out.
2. **Extend the deny list: done.** `egress-denylist.txt` now carries 500 entries in 11 blocks (section 6.1);
   A deny list can never be complete, which is why item 1 is still the real fix.
3. **Restrict the `bash` tool.** In the local `opencode.json`:

   ```json
   "permission": {
     "webfetch": "deny",
     "websearch": "deny",
     "bash": {
       "*": "ask",
       "curl *": "deny", "wget *": "deny", "Invoke-WebRequest*": "deny", "iwr *": "deny",
       "git push*": "deny", "git fetch*": "deny", "git pull*": "deny",
       "ssh *": "deny", "scp *": "deny", "pip *": "deny", "npm *": "deny", "uv *": "deny"
     }
   }
   ```

   and in `~/local-llm/claude/config/settings.json` the equivalent `permissions.deny` entries
   (`Bash(curl *)`, `Bash(git push*)`, …). Pattern lists are leaky (`python -c`, `powershell -c`), so this
   is defence in depth, not the fence.
4. **Cover Ollama's own software.** Add outbound block rules for `~/AppData/Local/Programs/Ollama/ollama.exe`
   and `ollama app.exe`; disable the rule for `ollama.exe` only while running `ollama pull`. Turn off
   auto-update in the app (`auto_update_enabled`) and turn on its airplane mode. `OLLAMA_NO_CLOUD` is
   already in force.
   Does this traffic carry code? No evidence of that: the update check is a `GET` whose query string holds
   only `arch`, `nonce`, `os`, `ts` and `version` (visible in `app-1.log`), and the recommendations fetch
   returns a model list (the cached file holds nothing else). Prompts and files leave Ollama only through
   cloud models, `ollama push` or the app's web search, and all three are off. The rules above turn that
   observation into an enforced guarantee, one that also survives a future version changing its behaviour.
5. **Remove the accident path: pick the profile by directory, not by command name.** Put a small
   `~/local-llm/bin` first on PATH with wrappers `opencode.cmd` and `claude.cmd`. Each wrapper reads the
   confidential roots from `~/local-llm/confidential-roots.txt` and, when
   the current directory is under one of them, runs the local launcher; otherwise it runs the Chocolatey
   binary. The familiar command inside a confidential tree then always gets the local model, and the cloud
   profile is reachable only outside those trees (or through an explicit `opencode-cloud` / `claude-cloud`
   name). Complement it with: uninstall the stale desktop OpenCode 1.0.223
   (`~/AppData/Local/opencode/uninstall.exe`); run `opencode auth logout` for `anthropic` when the key is
   not needed (the free `opencode/*` models are cloud too, so the key is not the only path); and, if Tier B
   is adopted, do the confidential work under the network-less account, where the wrong binary simply
   cannot connect.

### Tier B: run the local profile as a dedicated Windows account

Windows Firewall rules can be scoped to a local user (`New-NetFirewallRule -LocalUser <SDDL>`, available on
this machine). Create a standard local account (for example `llmlocal`), give it read access to the
project folder, and add one rule: outbound, block, any program, any address, local user = that account.
Every process the account starts, including anything the `bash` tool spawns, loses outbound network,
while loopback to Ollama on `127.0.0.1:11434` keeps working because Windows Firewall does not filter
loopback. Launch the harness with `runas /user:llmlocal` or a scheduled task. The proxy becomes optional:
run it as the main account only if a few documentation sites must stay reachable, with the allow list of
Tier A item 1.

This is the configuration that matches the stated goal literally: nothing the model can drive has a
route to the internet, and the only software that still talks to a vendor is Ollama's updater, which
Tier A item 4 silences.

### Tier C: a virtual machine with no network adapter

VirtualBox is installed. A VM with no NIC, or with a host-only adapter that only reaches the host's
Ollama port, is the strongest form; it costs GPU access and disk, so it is listed for completeness only.

## 9. Other findings and housekeeping

| # | Finding | Suggestion |
|---|---|---|
| 1 | `gemma4:e4b-it-q8_0` is the selected model of the Ollama app and the only model in the Continue config, but it is not installed | pull it, or point both at an installed tag |
| 2 | gpt-oss is stored twice (ggml and llamacpp runner variants), 13.8 GB of duplicate blobs | expected with Ollama 0.40; accept, or `ollama rm` and re-pull once the preferred runner is settled |
| 3 | `OLLAMA_CONTEXT_LENGTH=131072` comes from the app setting and applies to every tag without its own `num_ctx` (`qwen3-coder:30b`, `gpt-oss:20b`); on a 4 GiB GPU that KV cache is huge and slow | keep using the `-32k` and `-64k` tags (they are the ones wired into both launchers), or lower the app's context length to 32768. `OLLAMA_KV_CACHE_TYPE=q8_0` would halve KV memory with flash attention already on |
| 4 | the 300-second header timeouts in the local OpenCode log are the model's prompt-processing time on this hardware | prefer `gpt-oss-64k` for long prompts (it is the lighter model), or raise the provider timeout in `opencode.json` if a newer OpenCode exposes it |
| 5 | `~/.config/opencode` carries three lockfiles that disagree, for a plugin package nothing uses | delete `package.json`, `bun.lock`, `package-lock.json`, `node_modules` from that folder |
| 6 | cloud-profile `opencode.db` is 521 MB and `prompt-history.jsonl` 12 MB with pasted content | prune old sessions if disk or privacy matters |
| 7 | the 0.40.13 start this morning fell back to CPU after the GPU discovery watchdog; the 0.40.14 start found the GPU | if a session feels slow, check `server.log` for `inference compute id=cpu` and restart Ollama |
| 8 | `~/.ollama/history` is a plain-text record of every REPL prompt | set `OLLAMA_NOHISTORY=1` if that matters |
| 9 | the Ollama model-recommendations fetch and the hourly update check are the only periodic outbound traffic from Ollama's own processes | covered by Tier A item 4 |

## 10. File inventory

| Path | What it is |
|---|---|
| `~/.ollama/server.json`, `history`, `id_ed25519*`, `cache/`, `models/` | Ollama home (section 2.4) |
| `~/AppData/Local/Ollama/{config.json, db.sqlite, *.log, updates_v2/, ollama.pid}` | Ollama desktop app data and logs |
| `~/AppData/Local/Programs/Ollama/` | Ollama binaries and runtime libraries |
| `HKCU\Environment: OLLAMA_FLASH_ATTENTION=1, OLLAMA_VULKAN=0` | user environment variables |
| `~/.config/opencode/` | OpenCode cloud-profile config and skills |
| `~/.local/share/opencode/` | OpenCode cloud-profile auth, database, snapshots, logs |
| `~/.local/state/opencode/` | OpenCode cloud-profile model choice, TUI prefs, prompt history |
| `~/.cache/opencode/` | OpenCode cloud-profile models.dev cache and LSP packages |
| `C:/ProgramData/chocolatey/lib/opencode/` | Chocolatey OpenCode 1.19.5 |
| `~/AppData/Local/opencode/` | desktop OpenCode 1.0.223 (stale) |
| `~/local-llm/opencode-local.cmd`, `claude-local.cmd` | launchers of the isolated profiles |
| `~/local-llm/ensure-proxy.cmd`, `egress_proxy.py`, `egress-denylist.txt`, `egress.log` | the egress proxy |
| `~/local-llm/*.Modelfile` | sources of the `-32k` and `-64k` tags |
| `~/local-llm/opencode/{bin,config,data,cache,state}` | isolated OpenCode profile |
| `~/local-llm/claude/{bin,config}` | isolated Claude Code profile |
| Windows Firewall rules `local-llm: *` | per-binary outbound blocks |
| `~/.continue/config.yaml` | Continue extension, Ollama provider |
| `~/.bashrc` | aliases `opencode-local`, `claude-local`, `claude-local-oss` |

## 11. Commands used for this inventory

```text
ollama --version; ollama list; ollama ps; ollama show <tag> [--modelfile]
curl http://127.0.0.1:11434/api/version
opencode --version; <private>/opencode.exe --version; <private>/claude.exe --version
Get-ItemProperty HKCU:\Environment; HKLM ...\Session Manager\Environment
Get-Process *ollama*; Get-NetTCPConnection
Get-NetFirewallRule | Get-NetFirewallApplicationFilter; Get-NetFirewallProfile
Get-ItemProperty HKCU:\...\CurrentVersion\Run; Startup folder listing
nvidia-smi --query-gpu=...; Get-CimInstance Win32_ComputerSystem / Win32_Processor
sqlite3 (via uv python) on a copy of ~/AppData/Local/Ollama/db.sqlite
find / ls / cat / grep over the directories listed in section 10
```
