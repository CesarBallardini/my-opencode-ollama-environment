# Development

The programs are Python 3.14, standard library only, and run through `uv` in isolated mode. The tests and
the quality gates use the locked tool environment:

    uv sync --frozen
    make qa

`make qa` runs ruff (format and lint), pyright and pyrefly, pytest with coverage (floor in `.coveragerc`),
bandit, pip-audit and osv-scanner, and gitleaks. It needs `make`, `gitleaks` and `osv-scanner` on the
PATH; `uv` provides the rest. `make install` also installs the pre-commit hooks into `.git/hooks` of the
clone it runs in. Single pieces:

    uv run --frozen pytest
    uv run --frozen ruff check .
    uv run --frozen pyright

`install.cmd --dry-run` and `verify.cmd` are the two commands that touch the real machine without
changing it.

## Types

`envtypes.py` holds the shared types. A name with the `Dto` suffix only carries data. `typing.Any` is banned
by ruff, and pyright runs in strict mode on the programs, so a JSON value is `JsonValue` until it has been
validated into a DTO. To record a new kind of change, add it to `JournalKind` and `JournalEntryDto`, to the
two tables next to `Journal`, and to the two `match` statements in `uninstall.py`; the type checkers and
`tests/test_types.py` report whichever of these is missing. The tests stay in standard mode.

## Test layers

| Layer | Where | Run with | What it touches |
| --- | --- | --- | --- |
| Unit and properties | `tests/test_*.py` | `make test` | Nothing: a temporary `--home` and a simulated laptop |
| Behaviour (pytest-bdd) | `tests/features/` and `tests/test_bdd.py` | `make test` | Nothing, same simulated laptop, through the real entry points |
| Integration | `tests/integration/` | `make integration` | The real OS, with throwaway names, cleaned up afterwards |
| End to end, CI | `tests/e2e/ci-e2e.ps1` | `.github/workflows/e2e.yml` | The real user environment and firewall rules of a disposable runner |
| End to end, VM | `tests/e2e/vm-e2e.ps1` | by hand, inside a VM | A whole disposable Windows 11 guest |

The first two layers are the default `pytest` run. `tests/test_laws.py` holds the rules every step
obeys: once done it stays done, a dry run changes nothing, and install followed by uninstall restores
the machine.

`make integration` is safe on a laptop in daily use. It writes one throwaway variable under
`HKCU\Environment` and deletes it, and its firewall rule and UAC tests skip unless the console is
elevated. In an elevated console it creates and removes one throwaway firewall rule.

Never run the end-to-end scripts on a laptop in daily use. They change the real user environment and
the real firewall rules, and `ci-e2e.ps1` refuses to start when `local-llm:` rules already exist.

The VM run needs a Windows 11 guest with a snapshot taken after steps 1 to 3 of the README. Inside the
guest, restoring that snapshot between runs:

    powershell -ExecutionPolicy Bypass -File tests\e2e\vm-e2e.ps1 -Scenario fresh
    powershell -ExecutionPolicy Bypass -File tests\e2e\vm-e2e.ps1 -Scenario preexisting

Run `fresh` once from a plain console, accepting the UAC prompts, and once from an elevated console.
The `preexisting` scenario installs Ollama, OpenCode and Claude Code first, and checks that all three
are still installed after the uninstall. The script header lists the host commands for the snapshot.
