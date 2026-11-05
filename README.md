# my-opencode-ollama-environment

I first installed Ollama, OpenCode, Claude Code and an egress proxy by hand to keep a local model and its
tools off the Internet while working on source code that a non-disclosure agreement forbids sending to
cloud-based LLMs. This repository turns that setup into an unattended installer. The code in this repository
was written by Claude. The resulting setup reduces the ways code can leave the laptop but is not yet
equivalent to an air gap: the proxy uses a deny list, and commands run by the `bash` tool of OpenCode or
Claude Code bypass both the proxy and the firewall rules. See section 7 of `2026-11-04-description.md` before
relying on it under an NDA.

Installs a local, network-restricted coding-assistant setup on a Windows 11 Home laptop: Ollama with a
preselected model, private copies of OpenCode and Claude Code configured to use only that model, an egress
proxy with a 500-domain deny list, and Windows Firewall rules that block direct internet access for Ollama
and the private binaries. One command performs the whole install unattended, can be re-run safely, and
records every change it makes so that `uninstall.cmd` reverts exactly those changes and nothing else.

Background and the reasoning behind every setting: `2026-11-04-description.md`. The plan this repository
implements: `2026-11-04-plan-repo-to-install.md`.

Status on 2026-11-04: the tests and quality gates pass, and the real wrappers ran against a temporary
`--home`. The full install has not yet run on a new laptop, in a VM or on CI. Section 9 of the plan has the
details.

## What you get

- Ollama 0.40.14, listening on 127.0.0.1 only, cloud features off, auto-update off, airplane mode on.
- Model `qwen3-coder-32k` (Qwen3-Coder 30B-A3B, 32k context). Optional: `gpt-oss-64k`.
- `opencode-local` and `claude-local` commands (Git Bash) that use the local model and nothing else.
- `opencode` and `claude` commands that select the local profile automatically inside the directories
  you list as confidential, and the cloud profile elsewhere, if you install it.
- Firewall rules that block direct internet access for the model runner, the Ollama server and app, and
  the two private harness binaries. Web access from the harness goes only through the local proxy, which
  refuses every vendor, LLM API, model hub and upload sink on its list.

## Prerequisites

- Windows 11 Home or Pro, 64-bit, up to date.
- Your own Windows account must be an Administrator (the first account on a new laptop is). The install
  runs as you; it asks for one UAC confirmation to create the firewall rules.
- Internet during the install: about 21 GB of downloads with the default model, 35 GB with the optional
  one. Alternatively, an external disk with the models of another laptop (see "Install without downloading
  the models").
- Free space on C: 40 GB for the default model, 70 GB for both.
- If the laptop has an NVIDIA GPU, install its driver first (Windows Update or the GeForce app). Without
  it everything still works, on the CPU.

## Step 1: open a console and check winget

Press Win+X, choose "Terminal" (or "Windows PowerShell"), then:

    winget --version

A version number appears. If the command is not recognised, open Microsoft Store, go to Library, update
"App Installer", then close and reopen the console.

## Step 2: install Git and uv

    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    winget install --id astral-sh.uv -e --source winget --accept-package-agreements --accept-source-agreements

Git asks for one UAC confirmation. Close the console and open a new one, so that `git` and `uv` are on
the PATH.

## Step 3: clone the repository

    New-Item -ItemType Directory -Force $env:USERPROFILE\github | Out-Null
    cd $env:USERPROFILE\github
    git clone https://github.com/CesarBallardini/my-opencode-ollama-environment.git
    cd my-opencode-ollama-environment

A private repository asks you to log in to GitHub in the browser once; Git Credential Manager stores the
token in Windows Credential Manager.

## Step 4 (optional): secrets for the cloud profile

Skip this step if the laptop will only use the local model.

1. In the Anthropic console create a new API key for this laptop (do not reuse a key from another machine).
2. Create the secrets file outside the repository and restrict it to your account:

       New-Item -ItemType Directory -Force $env:USERPROFILE\.secrets\my-opencode-ollama-environment | Out-Null
       copy secrets.env.example $env:USERPROFILE\.secrets\my-opencode-ollama-environment\secrets.env
       notepad $env:USERPROFILE\.secrets\my-opencode-ollama-environment\secrets.env
       icacls $env:USERPROFILE\.secrets\my-opencode-ollama-environment\secrets.env /inheritance:r /grant:r "${env:USERNAME}:(R,W)"

3. Put the key after `ANTHROPIC_API_KEY=` and save. Then set `"install_harnesses": true` under `cloud` in
   `install.config.json`.

## Step 5: review install.config.json

Open it and check: the model list (`models`), whether you want the optional second model
(`install_optional`), the hardening switches (`firewall`, `shell`), and the directories that must always
use the local model (`confidential_roots`). An unknown key, a missing key or a wrong type stops the installer
with an error naming the key.

**Required:** `confidential_roots` under `shell` ships with a template value. Replace it before installing
with the directories that hold code that must never reach a cloud model:

    "confidential_roots": ["%USERPROFILE%\\YOUR_CONFIDENTIAL_FOLDER"]

Add one entry per directory, and double each backslash, as JSON requires. `%VAR%` references are expanded. Inside
a listed directory, `opencode` and `claude` use the local model. Anywhere else they use the cloud profile when
one is installed, so the template value left in place protects nothing.

To keep your folder names out of the repository, copy `install.config.json` outside the clone, edit the copy, and
pass it to `install.cmd`, `verify.cmd` and `uninstall.cmd` with `--config`.

The installer writes this list to `~/local-llm/confidential-roots.txt` only when that file is absent. After the
first install, edit that file directly: one directory per line, with single backslashes.

The other defaults reproduce the reference laptop.

## Step 6: run the installer

Inside the repository:

    .\install.cmd

The first run downloads Python 3.14 for the installer itself (about 30 MB). It needs no input except one
UAC confirmation near the end, for the firewall rules; run it from an elevated console ("Terminal
(Admin)") to get no prompt at all. The tools install in minutes; the model download (18.6 GB) depends on
the connection. The console shows one line per step; the full log is in `logs\`. If it stops, fix what it
reports and run the same command again: finished steps are skipped and an interrupted model download
resumes.

If Ollama, OpenCode or Claude Code are already on this laptop, the installer leaves them unchanged and
only adds what is missing; `verify.cmd` reports versions that differ from the pinned ones. To replace the
private harness copies with the pinned versions, or to refresh the template files in `~/local-llm` from
the repository, add `--replace-binaries` or `--update-templates`. A replaced file is kept, so the
uninstaller can put it back.

Every change the installer makes is written to the install journal,
`%LOCALAPPDATA%\my-opencode-ollama-environment\install-journal.json`: the packages it installed, the
directories and files it created or replaced, the variables and the Path entry it set, the models it
pulled, the firewall rules it added. Nothing that already existed is written there. That journal is what
`uninstall.cmd` works from.

To see what it would do without changing anything:

    .\install.cmd --dry-run

`.\install.cmd --help` lists the other options (`--only`, `--skip`, `--models-from`, `--home`).

## Step 7: verify

    .\verify.cmd

Every line starts with PASS, with SKIP and a reason, with WARN for something present that differs from the
configuration and was left unchanged, or with FAIL. The last line counts them; the exit status is 1 when
anything FAILs. One line reports the install journal: how many changes `uninstall.cmd` would revert.

Then open Git Bash (Start menu, "Git Bash"), go to any project folder and run:

    opencode-local

Submit a prompt. Check `~/local-llm/egress.log`: the first start shows `ALLOW CONNECT
registry.npmjs.org:443` lines (OpenCode installs its plugin package) and nothing else.

## Step 8: daily use

| Command (Git Bash) | What it runs |
|---|---|
| `opencode-local` | OpenCode, local model, isolated profile |
| `claude-local` | Claude Code, local model `qwen3-coder-32k` |
| `claude-local-oss` | Claude Code, local model `gpt-oss-64k` (needs the optional model) |
| `opencode`, `claude` | the local profile inside a confidential directory, the cloud profile elsewhere |
| `opencode-cloud`, `claude-cloud` | the cloud profile unconditionally |

Confidential directories are listed in `~/local-llm/confidential-roots.txt`, one per line.

## Step 9 (optional): sign in to the cloud Claude Code profile

Outside a confidential directory run `claude`; it opens the browser for the sign-in. OpenCode's cloud
profile already has the key from step 4.

## Pulling another model

The firewall blocks `ollama.exe`, so a plain `ollama pull` fails by design. Use:

    .\ollama-pull.cmd <tag>

It asks for one UAC confirmation, lifts the rule for the duration of the pull and restores it.

## Install without downloading the models

Copy `C:\Users\<other>\.ollama\models` from a laptop that has them to an external disk, then:

    .\install.cmd --models-from E:\models

## Updating

Change the pinned versions and checksums in `install.config.json` (the Claude Code hash comes from
`https://downloads.claude.ai/claude-code-releases/<version>/manifest.json`, the OpenCode hash from the
release zip), commit, pull on the laptop, then re-run step 6 with `--replace-binaries` so that the private
copies are replaced; without that flag a binary already in place is kept whatever its version.

## Uninstall

    .\uninstall.cmd

lists, newest first, every change recorded in the install journal and changes nothing. Then:

    .\uninstall.cmd --yes

reverts them in that order: removes the firewall rules the installer added (one UAC confirmation, only
when there are any), removes the models it pulled or created, deletes the files and directories it
created, puts back the files it replaced, restores the variables, the Path entry, the `.bashrc` block and
the Ollama app settings to what they were, and uninstalls the packages it installed. Whatever was on the
laptop before the installer ran is not in the journal and stays: an Ollama, OpenCode or Claude Code that
was already installed, a `~/local-llm` that already existed, variables that were already set.

- `--keep-models` leaves the models in place; their entries stay in the journal for a later run.
- The uv-managed Python the installer added is left in place, because `uninstall.cmd` itself runs on it;
  the program prints the command to run by hand (`uv python uninstall 3.14`).
- The ACL restriction of `secrets.env` is never reverted.
- `~/local-llm`, when created by the installer, is deleted whole, session data included; back up
  `~/local-llm/opencode/data` first if you want to keep it.

Each reverted entry is dropped from the journal at once, so an interrupted uninstall continues where it
stopped when run again. When the journal is empty the program reports that there is nothing to revert.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `winget` not recognised | App Installer not yet updated on a new laptop | Microsoft Store, Library, update App Installer |
| `uv` not recognised | console opened before step 2 finished | close and reopen the console |
| installer stops at preflight: "run as the console user" | started with another administrator's credentials | log in with your own administrator account and run from there |
| `ERROR models.pull[0]: expected str`, or another named key | `install.config.json` has an unknown key, a missing key or a wrong type | correct the named key |
| `unknown entry kind`, `is not an install journal`, or a named journal entry | the install journal was edited by hand or damaged | repair that entry; deleting the file leaves `uninstall.cmd` nothing to revert |
| `egress proxy did not start` | Python 3.14 missing for uv in this console | run `uv python install 3.14` |
| `ollama pull` hangs or fails after the install | the `ollama.exe` block rule | use `ollama-pull.cmd` |
| verify: `WARN ... differs from the repository template` | a template in `~/local-llm` was edited by hand or predates the repository | keep it, or `.\install.cmd --update-templates` to take the repository version (the old file goes to the journal backups) |
| verify: `WARN opencode wrapper: resolves to ...` | another `opencode` or `claude` comes first on the machine-wide PATH, or the console predates the install | open a new console; otherwise move `%USERPROFILE%\local-llm\bin` before that entry |
| verify: `FAIL app settings` | the Ollama app was reinstalled or its settings changed by hand | re-run `.\install.cmd`, or in the app's Settings turn auto-update off and airplane mode on |
| model answers after minutes, or "headers timed out" | 30B model on a small GPU | expected on 4 GiB VRAM; prefer `gpt-oss-64k` for long prompts |

## Development

The quality gates, the type conventions and the test layers are described in `development.md`.
