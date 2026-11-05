# Plan: this repository as the installer of the local-LLM environment

Date: 2026-11-04. Source of every fact about the current machine: `2026-11-04-description.md`
(referred to below as "the description"; section numbers cited without a document name are its).

Status on 2026-11-04: Phases 1 to 3 are done and Phase 4 is done except for its VM and CI runs. Nothing is
committed yet, and Phases 5 and 6 are open. Section 9 records what was run and what was not.

Goal: a person with a new Windows 11 Home laptop clones this repository, puts the secrets in place,
runs one command, and obtains the environment described in the description, isolated as described in
its section 6, plus the hardening of its section 8 that is inexpensive to apply on a fresh machine.

## 0. The three questions, answered

| Question | Decision | Section |
|---|---|---|
| 1. What drives the install on Windows 11 Home | A Python 3.14 program, `install.py`, standard library only, run through `uv` in isolated mode and wrapped by `install.cmd`. It calls `winget` (the preinstalled "App Installer") for Ollama and the optional cloud harnesses, downloads the two private harness binaries from pinned URLs with SHA-256 verification, and runs as the normal user. Only the firewall rules require Administrator rights; they form a separate phase that asks for one UAC consent, or none when the command is started from an elevated console. Every action is idempotent: an existing installation of Ollama, OpenCode or Claude Code is detected and left unchanged (2.8), and every change is journaled so that `uninstall.py` reverts exactly those changes and nothing else (2.9). PowerShell 5.1 is the alternative with the same step list. Ansible, mise, Salt, DSC, Chocolatey and WSL were considered and not selected | 2 |
| 2. Where the files with secrets live | Outside the repository, in `%USERPROFILE%\.secrets\my-opencode-ollama-environment\secrets.env`, a `KEY=value` file restricted to the user by an NTFS ACL. The repository tracks only `secrets.env.example`. The only secret the install can use is an Anthropic API key for the optional cloud profile; the air-gapped profile needs none | 3 |
| 3. The README | `README.md` at the repository root with the step-by-step path: prerequisites, install Git and uv, clone, secrets, review the configuration, run `install.cmd`, run `verify.cmd`, daily use, updates, uninstall. The full draft is Appendix A | 4 |

## 1. Scope

### 1.1 What the new laptop gets

| Component | Source in the description | How it is installed |
|---|---|---|
| Ollama 0.40.14, loopback only, cloud disabled | 2.1, 2.2, 2.4 | `winget install Ollama.Ollama --version 0.40.14` (per-user installer); `~/.ollama/server.json`; user environment variables |
| The preselected model `qwen3-coder-32k` (built from `qwen3-coder:30b`, `num_ctx 32768`) | 2.5 | `ollama pull` followed by `ollama create` from `local-llm/qwen3-coder-32k.Modelfile` |
| Optional second model `gpt-oss-64k` (from `gpt-oss:20b`) | 2.5 | same mechanism, enabled in `install.config.json` |
| The isolation layer: `egress_proxy.py`, the 500-entry `egress-denylist.txt`, `ensure-proxy.cmd`, uv and Python 3.14 for the proxy | 6.1 | files copied from `local-llm/` in the repository; uv is a README prerequisite; `uv python install 3.14` |
| The isolated OpenCode profile: private `opencode.exe` 1.18.34, `opencode-local.cmd`, `opencode.json`, `AGENTS.md` | 3.3 | pinned download from the OpenCode GitHub release (zip SHA-256 `8ec42ed1…`, executable SHA-256 `5283c2cd…`, both verified on 2026-11-04 against the copy in use); files from the repository |
| The isolated Claude Code profile: private `claude.exe` 2.1.292, `claude-local.cmd`, `settings.json`, minimal `.claude.json` | 4 | pinned download from `downloads.claude.ai/claude-code-releases/2.1.292/win32-x64/claude.exe` (SHA-256 `eb95bb65…` from the vendor manifest, identical to the copy in use); files from the repository |
| Windows Firewall outbound block rules for the private `opencode.exe`, the private `claude.exe` and `llama-server.exe` | 6.1 | `New-NetFirewallRule`, same display names as on the current laptop, admin phase |
| Hardening from section 8, Tier A: block rules for `ollama.exe` and `ollama app.exe`; app auto-update off and airplane mode on; `~/local-llm/bin` wrappers that select the local profile by directory | 8, items 4 and 5 | two more rules in the admin phase; a guarded SQLite update of the app settings; wrapper files from the repository plus a user PATH entry |
| Git Bash aliases `opencode-local`, `claude-local`, `claude-local-oss` | 3.3, 4 | a marked block appended to `~/.bashrc` |
| Optional cloud harnesses (OpenCode and Claude Code on PATH, for non-confidential work) with the Anthropic key | 3.1, 3.2 | `winget install SST.opencode` and `Anthropic.ClaudeCode` (portable, per-user); `auth.json` written from `secrets.env` |

### 1.2 Deliberate deviations from the current laptop

| Current state | New laptop | Reason |
|---|---|---|
| Chocolatey packages `opencode` 1.19.5 and `git`; the cloud `claude` on PATH is the vendor's native-installer copy in `~/.local/bin` | winget for everything that comes from a package manager | one package manager, preinstalled, with vendor-published installers and checksums |
| Desktop OpenCode 1.0.223 under `~/AppData/Local/opencode` | not installed | stale and unused (3.1) |
| App setting `context_length` 131072 | left at the app default | the `-32k` and `-64k` tags carry their own `num_ctx`; finding 9.3 |
| `gemma4:e4b-it-q8_0` selected in the app and in Continue | not reproduced; Continue and the other editor extensions are out of scope | they are not part of the isolated workflow (section 5) |
| gpt-oss stored for two runners (27.6 GB) | pulled once, only if enabled | finding 9.2 |
| `ollama.exe` and `ollama app.exe` free to reach ollama.com and github.com | blocked by firewall rules; `ollama-pull.cmd` lifts the server rule only for the duration of a pull | section 8, item 4 |
| Cloud binaries first on PATH, so a mistyped command sends code to the cloud | wrappers in `~/local-llm/bin` route confidential directories to the local profile | section 8, item 5 |
| Live `.claude.json`, `history.jsonl`, `projects/`, `opencode.db`, `egress.log` | not copied; the repository carries templates only | they hold session data from confidential projects |
| `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_VULKAN=0` | kept, as configuration values documented as GPU-specific | they were set for the RTX 3050; a different GPU may need other values |

### 1.3 Out of scope

Continue, CodeGPT and VS Code; the cloud-profile skills symlinks; the allow-list rewrite of the proxy
(section 8, item 1) and the `bash` restrictions (item 3), both of which modify files that this repository
will carry and therefore become ordinary follow-up changes once the repository exists; Tier B (dedicated
account) and Tier C (VM).

## 2. Question 1: the tool that drives the install

### 2.1 Which steps need Administrator rights

This determines the shape of any tool, so it is settled first. Each row was checked against what the
installer does on the current machine and what Windows requires.

| Step | Administrator | Why |
|---|---|---|
| Install Git for Windows (`winget install Git.Git`) | **yes** | machine-scope installer. It is a README prerequisite before the clone, one UAC prompt, not part of the installer |
| Install uv, Ollama, OpenCode, Claude Code through winget | no | per-user installers: Ollama's Inno setup installs under `%LOCALAPPDATA%\Programs`; uv and the two harnesses are winget "portable" packages that install under the profile (verified with `winget show` on 2026-11-04) |
| `uv python install 3.14` | no | under the profile |
| `~/.ollama/server.json`, user environment variables (`HKCU\Environment`), the app `db.sqlite` update | no | the user's own files and registry hive |
| `ollama pull`, `ollama create` | no | user process; once the `ollama.exe` block rule exists, lifting it for a pull is the admin case below |
| `~/local-llm` files, the two private binaries, `~/.bashrc`, the user `Path` | no | under the profile |
| Firewall rules: add, enable, disable (`netsh advfirewall` or `New-NetFirewallRule`) | **yes** | always, on every edition |
| Machine `Path` (`HKLM`) | yes | avoided: the wrappers go into the user `Path`, which is sufficient on the new laptop because every cloud binary that winget installs is also in the user `Path` |

The installer therefore runs as the normal user and elevates once, for the five firewall rules. Keeping
the elevated part small is a correctness requirement as much as a safety one: per-user installers must
run as the account that will use them, and an installer that ran entirely under another administrator's
credentials would place Ollama and the profiles in the wrong home directory. On a Home laptop the first
account is an administrator, so the elevation is a UAC consent prompt for the same account.

### 2.2 Candidates

| Tool | Native on Windows 11 Home | Bootstrap before the first run | Language of the install logic | Administrator | Covers the custom steps (files, registry, firewall, SQLite, `ollama pull`) | Assessment |
|---|---|---|---|---|---|---|
| Python 3.14, standard library, run through `uv` | yes | `winget install astral-sh.uv` (per-user), after which `uv` fetches Python once; both are required by the proxy in any case | Python | only the firewall phase | yes: `subprocess` for `winget` and for PowerShell with the NetSecurity cmdlets, `winreg` for environment and `Path`, `sqlite3`, `hashlib`, `zipfile`, `urllib.request`, `ctypes` for the elevation check | **selected** |
| Windows PowerShell 5.1 script | yes, preinstalled | none | PowerShell | only the firewall phase | yes, with native cmdlets | the alternative: no bootstrap, but a second language next to the Python proxy and no test tooling in the current toolbox |
| mise-en-place (`jdx.mise`, version 2026.10.12 on winget; already installed on the current laptop) | yes | `winget install jdx.mise` | TOML pins plus tasks that run shell or PowerShell | none for mise itself | partially: it pins and installs CLI tools (uv, Python, OpenCode through its `ubi` or `aqua` backends) and runs tasks; firewall rules, registry, SQLite and the Ollama GUI installer remain scripts | optional wrapper, not required: the pins live in `install.config.json` and `mise run install` would only call `install.py` |
| Ansible | no control node on Windows: WSL on the new laptop, or a Linux or macOS machine reaching it over WinRM or OpenSSH | WSL install (reboot) or a second machine; OpenSSH Server or WinRM enabled on the target (admin) | YAML; `ansible.windows` provides `win_get_url` (with checksum), `win_firewall_rule`, `win_environment`, `win_package`, `win_shell`; neither official Windows collection has a winget module (checked 2026-11-04), so winget runs through `win_shell` | the connection user must be an administrator for the firewall tasks (`become_method: runas`) | yes | appropriate for several machines managed from a Linux host; disproportionate for one laptop configured by its owner |
| Salt, masterless (`salt-call --local`) | yes, native minion | installs a service of several hundred MB (admin) | Python-based states, mature `win_*` modules | yes, for the agent install | yes | disproportionate |
| pyinfra | only as a WinRM target; no local execution on Windows | a control host | Python | not applicable | partial | not applicable |
| `winget configure` (DSC) | yes | none | YAML; custom steps are PowerShell `Script` resources | elevated run expected | the custom steps become the same PowerShell inside YAML | additional structure around the same code |
| Chocolatey with Boxstarter | no, bootstrapped with a remote script | remote script | PowerShell | yes, for everything | yes | second package manager; reboot handling that this install does not need |
| Scoop | no | remote script | PowerShell | no | no firewall support | no advantage over plain PowerShell |
| Nix, Homebrew | no | | | | | not available on Windows |

winget facts verified on 2026-11-04 on the current machine (winget 1.29.380, `winget search --id … -e` and
`winget show`): `Ollama.Ollama` 0.40.14 (Inno, per-user, installer 1.68 GB), `Git.Git` 2.56.0.4,
`astral-sh.uv` 0.13.7 (portable zip), `SST.opencode` 1.19.5 (portable zip), `Anthropic.ClaudeCode` 2.1.302
(portable). winget keeps the version history (210 Ollama versions, 590 OpenCode, 333 Claude Code), so
`--version` pins work.

### 2.3 Decision

`install.py`, Python 3.14 with the standard library only, started with the same command shape as the
proxy:

```text
uv run --no-project --python 3.14 python -I install.py
```

wrapped by `install.cmd` so that the README has one command. Reasons: it is the language of the proxy and
of the rest of the toolbox (uv, ruff, ty and pytest are on the current laptop), so the repository stays
single-language apart from the `.cmd` launchers; it can be unit-tested against a temporary home directory;
the `sqlite3` module handles the app settings without an extra tool; and the bootstrap (uv plus one Python
download) is incurred once and reused by the proxy at every launch. Windows-specific calls are few:
`winget` through `subprocess`, the NetSecurity cmdlets (`Get-`, `New-`, `Remove-NetFirewallRule`,
`Get-NetTCPConnection`) through a `powershell -NoProfile -Command` wrapper, `winreg` plus a
`WM_SETTINGCHANGE` broadcast for the user environment, `ctypes` for the elevation check and for
`ShellExecuteEx` with the `runas` verb.

PowerShell 5.1 remains a valid alternative if the maintainer prefers it; the step list below is identical.
This is decision D1.

### 2.4 Elevation

- `install.py --phase user` (the default) performs every step marked "no" in 2.1. `install.py --phase
  admin` creates the five firewall rules and nothing else.
- `install.cmd` runs `install.py` once. After the user phase, `install.py` checks the rules: when some
  are missing and the process is not elevated it starts `install.py --phase admin` through
  `ShellExecuteEx` with the `runas` verb (one UAC consent prompt) and waits for it; when the process is
  already elevated it runs the firewall step inline with no prompt, which is the fully unattended mode.
  The verify checks then run in the same process. `--dry-run` never elevates; it prints the rules the
  admin phase would create.
- In both phases the preflight compares the process user with the user logged on to the console session
  (WTS session information) and stops on a mismatch.
- `ollama-pull.cmd <tag>` elevates in the same way: disable the `ollama.exe` rule, pull, re-enable the rule
  in a `finally` block.
- `verify.cmd` never elevates: `Get-NetFirewallRule` works without elevation. `uninstall.cmd` elevates
  once to remove the rules, and only when the journal holds rules the installer added (2.9).

### 2.5 Program design

- Parameters: `--config` (default `install.config.json`), `--secrets-file` (default in section 3),
  `--dry-run` (report what each step would do, change nothing), `--only step[,step]`, `--skip step[,step]`,
  `--models-from <path>` (offline model import, 2.6 step 5), `--home <dir>` (the profile root, default
  `%USERPROFILE%`; tests point it at a temporary directory), `--phase user|admin`, `--replace-binaries` and
  `--update-templates` (2.8), `--log-file` (set by the elevated child and the tests).
- Every step is a function in three parts: check, do, verify. A satisfied check logs "already done" and
  returns, so a second run changes nothing. Steps never prompt. The detection rule of each step and the
  behaviour on a pre-existing but different installation are in 2.8.
- Logging goes to `logs\install-<started_datetime_local_as_str>.log` (`yyyyMMdd-HHmmss`) and to the
  console; the folder is git-ignored. Secrets are never logged: the program reads `secrets.env` into a
  dictionary and writes the target file; no value is formatted into any message.
- The preflight stops when: the system is not 64-bit Windows 11; the process user is not the console
  user; `winget` is missing; free space on the system drive is below the configured minimum; or
  `github.com`, `ollama.com` and `downloads.claude.ai` are unreachable. A missing NVIDIA driver produces a
  warning, not a stop (Ollama runs on the CPU).
- Output from `winget` is decoded with `errors="replace"` and used for logging only; decisions rest on
  exit codes. Every winget call carries `--disable-interactivity --accept-package-agreements
  --accept-source-agreements`.
- `install.config.json` holds pins, checksums, the model list and the feature switches (Appendix B).
  Unknown keys, missing keys and wrong types are errors that name the key, down to list items and map
  values (`models.pull[0]: expected str`).
- Shared code lives in `envsetup.py` and shared types in `envtypes.py`, next to the entry scripts (`install.py`, `verify.py`, `uninstall.py`,
  `ollama_pull.py`). Because `-I` keeps the script directory off `sys.path`, each entry script inserts its
  own directory explicitly, as the proxy verification script of 2026-11-04 already does.

### 2.6 Steps of `install.py`, in order

Steps 1 to 10 form the user phase, step 11 is the admin phase, and step 12 runs after both.

- [x] 1. Preflight (2.5).
- [x] 2. Ollama: skipped when `ollama.exe` is already present (2.8); otherwise `winget install --id Ollama.Ollama
      -e --version 0.40.14` (per-user installer). Refresh the process `PATH` from the registry afterwards so
      that the following steps find `ollama`.
- [x] 3. `uv python install 3.14`, a no-op after the bootstrap and kept as an explicit check, so that
      `ensure-proxy.cmd` never downloads a Python build at launch time (section 7, "bootstrap" row).
- [x] 4. Ollama configuration: write `~/.ollama/server.json`; set the user environment variables from the
      configuration; stop `ollama app.exe` and `ollama.exe` if they are running; start `ollama app.exe`,
      which starts the server with the new environment and creates `db.sqlite` with its settings row; wait
      for `GET /api/version` on `127.0.0.1:11434`.
- [x] 5. Models: `ollama pull` for each tag in `models.pull`, then `ollama create` for each Modelfile in
      `models.create`; the optional pair when `models.install_optional` is true. With `--models-from <path>`
      the step instead copies the `models` directory tree from the external disk into `~/.ollama/models`
      (`robocopy /E`, no purge) and skips the pulls. Pulls resume after an interruption, so a re-run
      continues where the previous one stopped.
- [x] 6. App settings: stop `ollama app.exe` and `ollama.exe`; if `db.sqlite` has a table `settings` with
      the columns `auto_update_enabled` and `airplane_mode`, update them with `sqlite3`; otherwise log the
      two manual toggles and continue. Restart the app.
- [x] 7. `~/local-llm`: copy each template file of the repository's `local-llm/` tree only if it is absent in
      `~/local-llm` (2.8); never touch `data`, `state`, `cache` or logs; create the empty profile directories;
      write the minimal `claude/config/.claude.json` and `confidential-roots.txt` (from
      `shell.confidential_roots`) only if absent, so that later manual edits survive re-runs.
- [x] 8. Private harness binaries: download the two pinned files to a temporary directory, verify the
      SHA-256 against the configuration, extract or copy into `~/local-llm/opencode/bin/` and
      `~/local-llm/claude/bin/`. Skipped when a binary is already in place, whatever its hash (2.8).
- [x] 9. Shell integration: append the marked alias block to `~/.bashrc` (created if missing); when
      `shell.path_wrappers` is true, prepend `%USERPROFILE%\local-llm\bin` to the user `Path`
      (`HKCU\Environment`, kept as an expandable value) and broadcast the change.
- [x] 10. Optional cloud harnesses: `winget install SST.opencode` and `Anthropic.ClaudeCode`, each skipped when
      the command already resolves on PATH outside `~/local-llm/bin` (2.8), otherwise the latest version
      (both have their own update mechanism and winget can upgrade them); write
      `~/.local/share/opencode/auth.json` as `{"anthropic":{"type":"api","key":…}}` from `ANTHROPIC_API_KEY`
      in `secrets.env` when the key is present and the file has no `anthropic` entry; an existing credential
      store is never overwritten. Always write `~/local-llm/cloud-binaries.cmd` with the resolved paths
      of the cloud binaries, or empty values when they are not installed (2.7). Claude Code's cloud login is
      OAuth in a browser and remains manual (README, step 9).
- [x] 11. Admin phase: five firewall rules, outbound, block, all profiles, one program each, created only if
      no rule with that display name exists: `local-llm: opencode direct outbound`,
      `local-llm: claude direct outbound`, `local-llm: ollama runner direct outbound` (the existing names),
      `local-llm: ollama server direct outbound` (`ollama.exe`), `local-llm: ollama app direct outbound`
      (`ollama app.exe`). The last two only when `firewall.block_ollama_server_and_app` is true. This phase
      runs after step 5 by design: the pulls must complete before `ollama.exe` loses its network access.
- [x] 12. `verify.py`, run by `install.cmd` after the admin phase.

### 2.7 The wrappers (step 9 detail)

`local-llm/bin/opencode.cmd` and `claude.cmd` read `~/local-llm/confidential-roots.txt` (one directory per
line, forward or backward slashes). If `%CD%` is under one of them, they `call` the local launcher;
otherwise they run the cloud binary whose full path step 10 writes into `~/local-llm/cloud-binaries.cmd`.
With no cloud binary installed they print "cloud profile not installed; use opencode-local" and exit
with status 1. `opencode-cloud.cmd` and `claude-cloud.cmd` run the cloud binary unconditionally, for
non-confidential work inside a confidential tree.

### 2.8 Idempotence and pre-existing installations

Requirement: every action of `install.py` is idempotent, and an existing installation of Ollama, OpenCode
or Claude Code is never reinstalled, replaced or upgraded unless an explicit flag requests it. Each step
detects its target state from the artefact itself (a file, a hash, a registry value, a rule name, a model
tag), never from a marker file or from winget's package list, so the detection also covers installations
that predate this repository, such as the current laptop. `--dry-run` prints, per step, either "already
done" or the action it would take.

| Step | "Already done" when | Present but different from the configuration | Default action | Explicit override |
|---|---|---|---|---|
| 2 Ollama | `ollama.exe` exists under `%LOCALAPPDATA%\Programs\Ollama` or resolves on PATH | version differs from the pin | skip; log the version found; `verify` reports WARN | none: the owner upgrades or downgrades with winget or the app |
| 3 Python | `uv python find 3.14` succeeds | not applicable | skip | |
| 4 `server.json` | the file parses and `disable_ollama_cloud` is true | other keys present | set only that key, keep the rest | |
| 4 environment variables | the value is equal | a different value | set, logging the previous value (configuration, not an installation) | |
| 5 models | the tag appears in `ollama list` | derived tag present but its Modelfile differs | skip; `verify` compares `ollama show --modelfile` with the repository Modelfile and reports WARN | `ollama rm` by hand, then re-run |
| 6 app settings | both columns already hold the target values | not applicable | skip | |
| 7 template files | the file exists in `~/local-llm` | content differs from the repository | skip; `verify` reports WARN per file | `--update-templates` overwrites the template files (proxy, deny list, launchers, wrappers, Modelfiles, profile configuration) and leaves generated-once files, data, state and logs untouched |
| 7 generated-once files (`.claude.json`, `confidential-roots.txt`) | the file exists | any | skip | none |
| 8 private binaries | the file exists | hash differs from the pin | skip; log the version found; `verify` reports WARN | `--replace-binaries` downloads the pinned version and replaces the file, moving the previous one to the journal backups, from where `uninstall.cmd` puts it back (2.9) |
| 9 `.bashrc` block | the marker lines exist | content between the markers differs | skip; `verify` reports WARN | `--update-templates` replaces the block |
| 9 user `Path` | the entry is present (case-insensitive comparison) | present but not first | skip; `verify` reports WARN | none: the owner reorders the `Path` |
| 10 cloud harnesses | `opencode` or `claude` resolves on PATH to a location outside `~/local-llm/bin` | any version | skip the winget install; regenerate `cloud-binaries.cmd` from the resolved path | none |
| 10 `auth.json` | the file has an `anthropic` entry | the key differs from `secrets.env` | skip; log that the key was not applied | none: `opencode auth login` or `logout` by hand |
| 11 firewall rules | a rule with the display name exists | program path differs | skip; `verify` reports WARN | delete the rule by hand, then re-run |

On the current laptop a run therefore installs nothing: Ollama, the private binaries and the cloud
harnesses are detected and skipped, and the only actions are the ones listed in Phase 4.

### 2.9 Reverting: the install journal

Requirement (added 2026-11-04): `uninstall.cmd` reverts only the changes `install.cmd` effectively made.
A program that was installed before the installer ran, such as an existing Claude Code, must remain
installed; so must an existing `~/local-llm`, existing variables, models and rules.

- `install.py` records every change in `%LOCALAPPDATA%\my-opencode-ollama-environment\install-journal.json`
  (`envsetup.Journal`), one entry per change in the order they happen, written at once, so an
  interrupted run leaves a complete record. `--dry-run` records nothing. The elevated phase writes the
  same file; the user phase reloads it afterwards.
- The first record of an item wins: a later run that touches the same item again adds nothing
  (`Journal.add_once`), so the journal always holds the state from before the installer first ran.
- Each entry is checked against the schema of its kind when the journal is loaded. An unknown kind or a
  wrong field stops the program with an error naming the entry, before anything is reverted, and is a FAIL
  in `verify.cmd`.
- Entry kinds and their reverts, applied newest first:

  | Kind | Recorded by | Revert |
  |---|---|---|
  | `winget-install` | steps 2 and 10 | `winget uninstall --id … -e --silent` (Ollama is stopped first) |
  | `uv-python-install` | step 3 | kept: `uninstall.py` runs on that Python; the command to run by hand is printed |
  | `dir-created` (with `tree`) | every step that creates a directory: the topmost one that did not exist, never the profile root | under `~/local-llm` (`tree` true) the whole tree is deleted, session data included; elsewhere the directory is removed once empty, otherwise kept and reported |
  | `file` (with `backup`) | every file written outside a created tree: a replaced file is first moved to `…\backups\` | delete the file, then put the backup back |
  | `user-env` (with `previous`) | step 4 | delete the variable, or set the previous value back |
  | `user-path` | step 9 | remove the entry from the user `Path` |
  | `bashrc-block` (with `created`, `previous_block`) | step 9 | delete the created file, put the previous block back, or remove the block |
  | `app-settings` (with `previous`) | step 6 | write the previous row back (Ollama stopped and restarted) |
  | `model` | step 5: each pulled, created or copied tag | `ollama rm`, unless `--keep-models` (the entry then stays for a later run) |
  | `auth-key` | step 10, when `auth.json` existed | remove the `anthropic` entry, unless its key no longer matches `secrets.env` |
  | `firewall-rule` | step 11 | `Remove-NetFirewallRule` (admin phase, elevated once) |

- `uninstall.cmd` lists the reverts; `--yes` applies them, drops each reverted entry from the journal at
  once and deletes the file when nothing is left, so an interrupted uninstall resumes. An empty or absent
  journal means there is nothing to revert. Not reverted on purpose: the ACL restriction of `secrets.env`.
- `verify.cmd` reports the journal (Appendix D, row 13). This is decision D9.

## 3. Question 2: where the secrets live

### 3.1 Inventory of secrets

| Secret | Needed by | Needed for the air-gapped profile | Transfer between laptops |
|---|---|---|---|
| Anthropic API key | the cloud OpenCode profile (`~/.local/share/opencode/auth.json`) | no | do not copy the key: create a new key per laptop in the vendor console, so that each one can be revoked independently |
| Claude Code cloud login | the cloud Claude Code profile | no | OAuth in the browser, one manual `claude` run after the install |
| GitHub credentials | cloning the repository if it is private | no | Git Credential Manager (bundled with Git for Windows) prompts for a browser login on the first clone and stores the token in Windows Credential Manager |
| `~/.ollama/id_ed25519` | only `ollama push` | no | generated per machine on first start; do not copy |

Nothing in `~/local-llm` is a secret. The isolated profiles blank every API key variable (3.3, 4).

### 3.2 Decision

- Path: `%USERPROFILE%\.secrets\my-opencode-ollama-environment\secrets.env`. Outside every repository,
  so that `git add -A`, `git clean -x` and a public push cannot reach it.
- Format: one `KEY=value` per line, UTF-8, no quotes, `#` comments. Keys: `ANTHROPIC_API_KEY` (optional).
  The repository tracks `secrets.env.example` (Appendix C) with every key, empty, and a comment stating
  where to obtain it.
- Protection: the README has the user run `icacls <file> /inheritance:r /grant:r "<user>:(R,W)"` after
  creating the file; `install.py` repeats that command (idempotent) and refuses a file whose ACL grants
  access to `Users` or `Everyone`.
- Consumption: `install.py --secrets-file` (that default). A missing file or an empty key skips the cloud
  steps with a notice; it never stops the air-gapped install.
- The installer writes only the tools' own stores (`auth.json`). It never sets a key as an environment
  variable, which would expose it to every process of the account.

### 3.3 Alternatives considered

| Alternative | Reason for not selecting it |
|---|---|
| A git-ignored `secrets/` folder inside the clone | one `git add -f` away from the remote; a fresh clone does not contain it; `git clean -xdf` deletes it |
| Windows Credential Manager or `Microsoft.PowerShell.SecretManagement` | the tools cannot read it, so the installer would decrypt it into `auth.json` anyway and the plaintext copy would exist either way; it adds a module install and an interactive unlock. Reasonable later if more secrets appear |
| Encrypted in the repository (age, sops) | moves the problem to the decryption key; adds a tool |
| User environment variable `ANTHROPIC_API_KEY` | visible to every process of the account, including the local profile, which blanks it but still receives it |

## 4. Question 3: the README

`README.md` at the repository root; the draft is Appendix A. Rules for it: one path, numbered steps, each
step ending with what the person should observe; every command in a code block; the user name written as
`$env:USERPROFILE` or `<you>`; no step depends on knowledge of the description, which is linked for
reference.

Sections: what you get; prerequisites; step 1 open a console and check winget; step 2 install Git and uv;
step 3 clone; step 4 secrets (optional); step 5 review `install.config.json`; step 6 run `install.cmd`;
step 7 verify; step 8 daily use; step 9 cloud profile sign-in (optional); updating pins; pulling another
model; uninstall; troubleshooting (the failures most likely on a fresh laptop).

## 5. Repository layout

```text
my-opencode-ollama-environment/
  README.md                          the step-by-step path (Phase 3; Appendix A was its draft)
  development.md                     quality gates, type conventions and test layers
  2026-11-04-description.md          inventory and isolation review, a snapshot of that date
  2026-11-04-plan-repo-to-install.md this plan
  install.cmd                        user phase, elevated admin phase, verify (2.4); arguments pass through
  verify.cmd                         runs verify.py, never elevates
  uninstall.cmd                      runs uninstall.py, elevates once for the rules
  ollama-pull.cmd                    runs ollama_pull.py, elevates once for the rule
  install.py                         entry point, user and admin phases (2.4, 2.6)
  verify.py                          read-only checks, Appendix D
  uninstall.py
  ollama_pull.py                     pull with the ollama.exe rule lifted
  envtypes.py                        shared types: the Dto classes, the closed Literal sets, the Protocols
  envsetup.py                        shared code: configuration, logging, winget, NetSecurity cmdlets, winreg,
                                     hashes, the install journal (2.9)
  tests/                             pytest; run with: uv run --frozen pytest (or make qa for every gate)
    test_*.py                        unit tests, the step laws (test_laws.py), hypothesis properties,
                                     documentation drift (test_docs.py), schemas against DTOs (test_types.py)
    features/, test_bdd.py           the install contract in pytest-bdd, through the real entry points
    integration/                     the real OS, throwaway names; pytest -m integration
    e2e/                             ci-e2e.ps1 (workflow e2e.yml), vm-e2e.ps1 (by hand, in a VM), and the
                                     helpers both share, e2e-common.ps1
  pyproject.toml, uv.lock,           the locked tool environment (Python 3.14, .python-version) and the
  .python-version                    quality gates, mirrored from ../localenv-python: ruff.toml,
  ruff.toml, pyrightconfig.json,     pyrightconfig.json, pyrefly.toml, bandit.yaml, .coveragerc (floor 85),
  pyrefly.toml, bandit.yaml,         .gitleaks.toml, .pre-commit-config.yaml, .pre-commit-hooks/, Makefile,
  .coveragerc, .gitleaks.toml,       .github/ (check, pytest on windows-latest, security, PR-title workflows,
  .pre-commit-config.yaml, Makefile, Dependabot with Conventional Commits prefixes)
  .github/
  .gitattributes                     LF for text, CRLF for *.cmd, local-llm/** untouched by git
  install.config.json                pins, checksums, switches, Appendix B
  secrets.env.example                Appendix C
  LICENSE                            MIT
  .gitignore                         logs/, *.bak, *.log, secrets.env, the tool caches and .venv/, node_modules/,
                                     local-llm/opencode/{data,cache,state,bin}/, the runtime state under
                                     local-llm/claude/, and the two generated files of section 5
  local-llm/                         template of ~/local-llm, copied verbatim by step 7
    egress_proxy.py
    egress-denylist.txt              the 500-entry list of 2026-11-04
    ensure-proxy.cmd
    opencode-local.cmd
    claude-local.cmd
    qwen3-coder-32k.Modelfile
    gpt-oss-64k.Modelfile
    bin/opencode.cmd, bin/claude.cmd, bin/opencode-cloud.cmd, bin/claude-cloud.cmd
    opencode/config/opencode/opencode.json
    opencode/config/opencode/AGENTS.md
    claude/config/settings.json
    claude/config/.claude.json.template   {"hasCompletedOnboarding": true}; extended if Phase 4 shows that
                                          the first start still asks onboarding questions
  ollama/server.json                 {"disable_ollama_cloud": true}
  home/bashrc.local-llm              the alias block, between "# >>> local-llm >>>" and "# <<< local-llm <<<"
```

The `.cmd` launchers already use `%USERPROFILE%` throughout (checked on 2026-11-04), so they need no
templating. The current `~/.bashrc` aliases, in contrast, contain the absolute path
`/c/Users/cesar/local-llm/…`; the block in `home/bashrc.local-llm` uses `$HOME/local-llm/…` instead.
`confidential-roots.txt` is generated from the configuration (step 7), not tracked. The
`opencode/config/opencode/package.json` and lockfile are not tracked: OpenCode recreates them on first
start, which is the `registry.npmjs.org` traffic the proxy log shows (3.3), and that host stays allowed.

## 6. Work plan

### Phase 0: repository bootstrap (user)

- [x] Initialise the repository in this folder. It sits on `master` with no commits and no remote.
- [ ] Create `main`, then commit everything on a branch. Any note that must stay local goes in
      `.git/info/exclude` first, because `git add -A` takes every file that is not ignored:

      ```text
      git checkout -b main
      git commit --allow-empty -e -m "chore: start the repository"
      git checkout -b feat/installer
      git add -A
      git status
      git commit -e -m "feat: add the local-LLM environment installer"
      ```

- [ ] Decide public or private (decision D6), create the repository, add it as `origin` and push `main`
      before the branch. The description names private project directories and the REPL history; review it
      before any public push.

### Phase 1: templates from the live `~/local-llm`

- [x] Copy the files listed in section 5 from `~/local-llm` into `local-llm/` of the repository; exclude
      binaries, `data`, `state`, `cache`, `__pycache__`, `egress.log`, `*.bak`, `.claude.json`,
      `history.jsonl`, `projects/`, `sessions/`, `session-env/`, `shell-snapshots/`, `backups/`.
- [x] Write the four wrappers, `.claude.json.template`, `home/bashrc.local-llm` (with `$HOME`, section 5),
      `ollama/server.json`, `secrets.env.example`, `.gitignore`.
- [x] Compare the copied files with `~/local-llm` byte for byte.

### Phase 2: programs

- [x] `install.config.json` with the pins of Appendix B.
- [x] `envsetup.py`, then `install.py` with the twelve steps of 2.6; `--dry-run` first.
- [x] `verify.py` with the checks of Appendix D.
- [x] `ollama_pull.py`, `uninstall.py`, the four `.cmd` wrappers.
- [x] Tests in `tests/`: configuration loading and unknown-key rejection, hash verification, the deny-list
      parser, `.bashrc` block idempotence, the user `Path` edit, every step's check function against a
      temporary `--home`; a second run against the same temporary home that performs zero actions;
      `ruff check`, `pyright` and `pyrefly check` report nothing (ty was replaced by the two checkers the example repository uses).

### Phase 3: README

- [x] `README.md` from Appendix A, updated with whatever Phase 2 changed.

### Phase 4: tests on the current laptop

- [x] `install.cmd --dry-run` reports every step as already satisfied except the items the current laptop
      lacks: the two new firewall rules, the app settings, the wrappers with their PATH entry and
      `confidential-roots.txt`. Ollama, the private binaries and the cloud harnesses must be reported as
      present and skipped (2.8). That list is the expected output and is recorded in the review section.
      Done 2026-11-04; the output is in section 9.
- [x] `verify.cmd` passes on the current laptop for everything that exists on it (section 9: 16 PASS; the
      6 FAIL lines are exactly the pending items of the dry run).
- [ ] Full unattended run in a fresh Windows 11 VM (VirtualBox is installed, section 8 Tier C; a Windows 11
      evaluation image), with `models.pull` overridden to a small tag so that the run finishes in minutes;
      `verify.cmd` passes; a second run changes nothing; `uninstall.cmd` followed by `install.cmd` again;
      one run from a plain console (one UAC prompt) and one from an elevated console (no prompt); the first
      `claude-local` start shows no onboarding questions (section 5, `.claude.json.template`).
      `tests/e2e/vm-e2e.ps1` drives all of this except the `claude-local` start and the snapshot restore;
      it has not been run yet.
- [ ] The same run with Ollama, OpenCode and Claude Code installed through winget beforehand
      (`vm-e2e.ps1 -Scenario preexisting`): all three must still be installed after the uninstall.
- [ ] First run of `.github/workflows/e2e.yml` and of the integration step in `pytest.yml`, which need the
      repository pushed; then set the timeout in `e2e.yml` from the measured duration.

### Phase 5: the new laptop

- [ ] Follow the README literally, as the only instructions. Every deviation required becomes a README fix.
- [ ] `verify.cmd` passes; `opencode-local` answers a prompt; `egress.log` shows only the first-start
      traffic (`registry.npmjs.org`).
- [ ] Record the elapsed time and the download volume in the review section.

### Phase 6: review

- [ ] Complete section 9 of this file.

## 7. Decisions to confirm before Phase 1

| # | Decision | Proposed default |
|---|---|---|
| D1 | Driver: Python 3.14 through uv, a user phase plus a small admin phase for the firewall rules; PowerShell 5.1 is the alternative with the same step list; Ansible, mise, Salt, DSC and Chocolatey not selected for this task (2.2) | Python |
| D2 | Secrets: `%USERPROFILE%\.secrets\my-opencode-ollama-environment\secrets.env`, only `ANTHROPIC_API_KEY`, new key per laptop | yes |
| D3 | Preselected model: `qwen3-coder-32k` from `qwen3-coder:30b`; `gpt-oss-64k` optional, off by default | yes |
| D4 | Hardening on by default: `ollama.exe` and `ollama app.exe` block rules, app auto-update off and airplane mode on, PATH wrappers | yes |
| D5 | Cloud harnesses: installed by the program when `cloud.install_harnesses` is true | off by default |
| D6 | Repository visibility | to be decided by the repository owner; private until the description has been reviewed |
| D7 | Keep `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_VULKAN=0` as defaults although they are GPU-specific | yes, documented as such |
| D8 | Pre-existing installations are never reinstalled, replaced or upgraded by default; a differing version is reported by `verify`; replacement only with `--replace-binaries` or `--update-templates` (2.8) | yes |
| D9 | `uninstall.cmd` reverts only what `install.cmd` journaled (2.9); `--keep-models` keeps the models; the uv-managed Python is left with the command printed | yes (user, 2026-11-04) |
| D10 | Python 3.14 and the quality gates of `../localenv-python` (ruff, pyright, pyrefly, bandit, pytest with coverage, pip-audit, osv-scanner, gitleaks, pre-commit, CI); coverage floor 85 instead of 90 because the lines that start the real Ollama app or sleep in polling loops are not unit-testable | yes (user, 2026-11-04) |

## 8. Risks and open points

- Fresh laptops sometimes ship an App Installer that needs one Microsoft Store update before `winget`
  responds; the README's first step checks `winget --version` and states what to do.
- `uv` appears on the `Path` only in new consoles; the README closes and reopens the console after step 2.
- The Ollama Inno installer run by winget may open the app window and the tray icon; the program tolerates
  both. If a future Ollama changes the `settings` table, step 6 degrades to logged instructions.
- `ollama pull` of 18.6 GB over a slow connection is the longest step; `--models-from` with the current
  laptop's `~/.ollama/models` on an external disk avoids it (46 GB in total on 2026-11-04; 18.6 GB if only the
  Qwen blob and its manifests are copied).
- Pinned binaries do not update themselves (autoupdate is off in both profiles by design); updating means
  changing three values in `install.config.json` and re-running, with the new hashes taken from the vendor
  manifest (Claude Code) or computed from the release zip (OpenCode).
- The minimal `.claude.json` template may not suppress every first-start question of Claude Code; Phase 4
  verifies this and extends the template if needed.
- `uninstall.cmd` cannot remove the uv-managed Python 3.14 it runs on; the command is printed for the
  owner to run by hand. Removing that Python also affects every other project that uses it through uv.
- On the current laptop `egress_proxy.py` and `ensure-proxy.cmd` under `~/local-llm` differ from the
  repository templates by their Python version line only (3.13 there, 3.14 here); `verify` reports WARN
  until `install.cmd --update-templates` is run, which journals the two replaced files.
- testinfra was assessed for the machine checks and not adopted: its local backend fails on Windows
  (`bytes args is not allowed on Windows`) and its Windows modules only work over WinRM. `verify.py`
  performs the checks itself; a pytest-marked suite that reuses those checks remains possible.

## 9. Review

Phases 1 to 4 (except the VM and CI runs) were completed on 2026-11-04 on the current laptop (Windows 11 Home
build 26300). Phase 5 (the new laptop), the VM and CI runs, the elapsed-time and download figures and the first commit
are still open.

What was run on 2026-11-04:

- The quality gates, all green on the final code: `ruff check` and `ruff format --check`, `pyright` and
  `pyrefly check` (0 errors), `bandit`, 166 tests, coverage 99.53 % (floor 85), `pip-audit`, `osv-scanner`,
  `gitleaks`, `uv lock --check`. Of the 16 integration tests, 14 passed and 2 skipped.
- `install.cmd --dry-run`, exit 0. Preflight: WARN 2 GB free (Ollama present, so no stop). The laptop would
  need room freed before a real run that downloads anything. Already done:
  ollama (0.40.14 found), python, ollama-config, models, binaries. Pending: app-settings (both values),
  local-llm (copy the four `bin/*.cmd`, write `confidential-roots.txt`; WARN `egress_proxy.py` and
  `ensure-proxy.cmd` differ, left unchanged), shell (append the `.bashrc` block, prepend the Path entry),
  cloud (write `cloud-binaries.cmd`), firewall (the two Ollama rules; no elevation in dry run).
- `verify.cmd`: 16 PASS, 2 WARN, 2 SKIP, 6 FAIL. The six FAIL lines are the pending items above (app
  settings, the four wrappers, the two Ollama rules, the Path entry, the `.bashrc` block); the WARN lines
  are the wrappers resolving to the machine-wide `opencode` and `claude` while the Path entry is absent;
  SKIP: no `secrets.env`, no install journal yet.

Deviations from the plan and their reasons:

- Python 3.14 instead of 3.13 (user decision), everywhere including the proxy's `ensure-proxy.cmd`.
- The elevated phase is started by `install.py` itself through `ShellExecuteEx` (`runas`) and the verify
  checks run in-process, instead of three commands in `install.cmd` (2.4): one interpreter start, one
  log, one exit status.
- The firewall and the listener checks use the NetSecurity cmdlets through PowerShell instead of `netsh`
  and `netstat`; on build 26300 the Ollama listener is reported with TCP state 0, so listeners are
  selected by `RemotePort -eq 0` rather than `-State Listen`.
- `uninstall.py` was redesigned around the install journal (2.9) instead of removing a fixed list;
  `--models` became `--keep-models`, and a replaced binary goes to the journal backups instead of `.bak`.
- `cloud.winget` is an object (`{"opencode": "SST.opencode", "claude": "Anthropic.ClaudeCode"}`) so that
  the command name and the package id are paired.
- `verify.py` has 13 checks (Appendix D), the install journal being the last.
- The quality gates were copied from `../localenv-python` (D10); pyright and pyrefly replaced `ty`.

Not run yet: the firewall and elevation integration tests (they need an elevated process, and skip in the
plain console used here), both end-to-end drivers in full (CI needs a push, the VM needs a guest). The
drivers' helpers and the wrappers ran for real against a temporary `--home` with
`--only local-llm,app-settings,cloud`: the second run had nothing pending, the listing removed nothing, and
the uninstall removed `local-llm` and left the user environment unchanged. This was repeated after the
typing work, because `envtypes.py` is now imported at run time and the journal validates what it loads.

## Appendix A: README.md draft

The draft that stood here was turned into `README.md` at the repository root on 2026-11-04 (Phase 3) and
is maintained there; the README adds the install journal, the uninstall contract of 2.9, Python 3.14, a
development section with the quality gates, since moved to `development.md`, and the verify-related troubleshooting rows.

## Appendix B: install.config.json draft

```json
{
  "minimum_free_gb": 40,
  "minimum_free_gb_with_optional": 70,
  "tools": {
    "python_for_proxy": "3.14"
  },
  "ollama": {
    "winget_id": "Ollama.Ollama",
    "version": "0.40.14",
    "installer_sha256": "e069c7cc7d4808f77684e81efa0b1f853a5190b4b8f070bc8fd63041180e3eba",
    "server_json": { "disable_ollama_cloud": true },
    "user_env": { "OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_VULKAN": "0" },
    "app_settings": { "auto_update_enabled": 0, "airplane_mode": 1 }
  },
  "models": {
    "pull": ["qwen3-coder:30b"],
    "create": { "qwen3-coder-32k": "local-llm/qwen3-coder-32k.Modelfile" },
    "optional_pull": ["gpt-oss:20b"],
    "optional_create": { "gpt-oss-64k": "local-llm/gpt-oss-64k.Modelfile" },
    "install_optional": false
  },
  "harness": {
    "opencode": {
      "version": "1.18.34",
      "url": "https://github.com/sst/opencode/releases/download/v1.18.34/opencode-windows-x64.zip",
      "zip_sha256": "8ec42ed1ad8db108052394b83ab69d0331398f761fbfff5fe50f91d65bdd3548",
      "exe_sha256": "5283c2cd5b4285855daa97e6f7d17bf48146227cdf2a940e79e0f2b7869fd06e"
    },
    "claude": {
      "version": "2.1.292",
      "url": "https://downloads.claude.ai/claude-code-releases/2.1.292/win32-x64/claude.exe",
      "exe_sha256": "eb95bb65955f8b1702e800815f9a2c0388a5de0f196c354c7ee1dd5cb9a2ba23"
    }
  },
  "firewall": {
    "block_harness_and_runner": true,
    "block_ollama_server_and_app": true
  },
  "shell": {
    "bashrc_aliases": true,
    "path_wrappers": true,
    "confidential_roots": ["%USERPROFILE%\\YOUR_CONFIDENTIAL_FOLDER"]
  },
  "cloud": {
    "install_harnesses": false,
    "winget": { "opencode": "SST.opencode", "claude": "Anthropic.ClaudeCode" }
  }
}
```

Provenance of the hashes, all verified on 2026-11-04: the Ollama installer hash is the one in the winget
manifest for 0.40.14 (installer size 1,675,999,728 bytes); the OpenCode zip was downloaded from the URL
above (62,158,551 bytes) and hashed, and the `opencode.exe` inside it hashes to the same value as the
private copy in use; the Claude Code hash is the `win32-x64` entry of the vendor manifest for 2.1.292
(254,858,400 bytes) and equals the private copy in use. The OpenCode download URL redirects to the
project's new organisation (`anomalyco`); the pinned URL still resolves.

Download volume behind the README figures: Ollama installer 1.68 GB, Git about 70 MB, uv about 15 MB,
Python 3.14 about 30 MB, OpenCode 62 MB, Claude Code 255 MB, `qwen3-coder:30b` 18.56 GB; `gpt-oss:20b`
adds 13.79 GB.

## Appendix C: secrets.env.example

```text
# Copy to %USERPROFILE%\.secrets\my-opencode-ollama-environment\secrets.env and fill in.
# Never place this file inside a repository. Create a new key per laptop.

# Anthropic API key for the OPTIONAL cloud OpenCode profile (console.anthropic.com, "API keys").
# Leave empty to install the air-gapped profile only.
ANTHROPIC_API_KEY=
```

## Appendix D: what verify.py checks

Outcomes per line: PASS; SKIP (not applicable, with the reason); WARN (present but different from the
configuration, left unchanged by the installer, 2.8); FAIL. Exit status 1 when any FAIL.

| # | Check | Expected |
|---|---|---|
| 1 | `ollama --version`; `GET http://127.0.0.1:11434/api/version` | the pinned version (WARN otherwise); the listener is bound to loopback only (`Get-NetTCPConnection`, listeners selected by `RemotePort -eq 0`) |
| 2 | `~/.ollama/server.json` | `disable_ollama_cloud` true |
| 3 | `ollama list`; `ollama show --modelfile` | every required tag; each created tag carries its Modelfile parameters (WARN when they differ); SKIP when the server is not running |
| 4 | app `settings` row | `auto_update_enabled` 0, `airplane_mode` 1 (SKIP with a note if the schema differs) |
| 5 | user environment variables | as configured |
| 6 | `~/local-llm` files | every template present (WARN per file that differs); the two binaries hash to the pinned values; `egress-denylist.txt` parses with the proxy's own loader and has no duplicates |
| 7 | firewall rules | every configured display name exists, enabled, outbound, block, correct program (`Get-NetFirewallRule`, no elevation needed) |
| 8 | the proxy | started through `ensure-proxy.cmd` when not running; a CONNECT to `https://api.openai.com` is refused with 403 and one to `https://docs.python.org` succeeds (SKIP when offline) |
| 9 | private binaries | `opencode.exe --version` and `claude.exe --version` print the pinned versions |
| 10 | PATH, wrappers, `.bashrc` | `%USERPROFILE%\local-llm\bin` first in the user Path; `opencode` and `claude` resolve to the wrappers (WARN when another copy comes first); the `.bashrc` block present (WARN when it differs) |
| 11 | uv and Python | `uv python find 3.14` succeeds |
| 12 | secrets | if `secrets.env` exists, its ACL grants access to the user only (SKIP when absent) |
| 13 | install journal | readable when present, with the count of entries per kind that `uninstall.cmd` would revert (SKIP when absent: nothing recorded) |
