"""Shared code for install.py, verify.py, uninstall.py and ollama_pull.py.

Windows only, standard library only. The entry scripts run through
``uv run --no-project --python 3.14 python -I <script>``; because ``-I`` keeps the script
directory off ``sys.path``, each entry script inserts its own directory before importing this
module.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess  # nosec B404
import sys
import time
import urllib.error
import urllib.request
import winreg
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import UnionType
from typing import TYPE_CHECKING, Generic, TypeVar, cast

from envtypes import CommandRunner, HarnessBinaryDto, StepName, UserEnvironment

if TYPE_CHECKING:
    from envtypes import (
        AppSettingsState,
        BlockState,
        FirewallRuleDto,
        InstallConfigDto,
        JournalDocumentDto,
        JournalEntryDto,
        JournalKind,
        JsonObject,
        JsonScalar,
        JsonValue,
    )

REPO = Path(__file__).resolve().parent
SECRETS_DIR_NAME = 'my-opencode-ollama-environment'
STATE_DIR_NAME = 'my-opencode-ollama-environment'
JOURNAL_FILE_NAME = 'install-journal.json'
JOURNAL_VERSION = 1
BASHRC_BEGIN = '# >>> local-llm >>>'
BASHRC_END = '# <<< local-llm <<<'
WRAPPERS_PATH_ENTRY = r'%USERPROFILE%\local-llm\bin'
OLLAMA_API = 'http://127.0.0.1:11434'
PROXY_PORT = 8899
USER_AGENT = 'my-opencode-ollama-environment installer'
TEMPLATE_SOURCE_SUFFIX = '.template'
# Files under local-llm/ that install.py writes once and never overwrites.
GENERATED_ONCE = ('claude/config/.claude.json', 'confidential-roots.txt')
BROAD_SIDS = {
    'S-1-1-0': 'Everyone',
    'S-1-5-32-545': 'Users',
    'S-1-5-11': 'Authenticated Users',
}

RULE_OPENCODE = 'local-llm: opencode direct outbound'
RULE_CLAUDE = 'local-llm: claude direct outbound'
RULE_RUNNER = 'local-llm: ollama runner direct outbound'
RULE_OLLAMA_SERVER = 'local-llm: ollama server direct outbound'
RULE_OLLAMA_APP = 'local-llm: ollama app direct outbound'

# robocopy exit codes below 8 report what was copied; 8 and above report failures.
ROBOCOPY_FAILURE_THRESHOLD = 8
# Win32: ERROR_CANCELLED, what ShellExecuteEx reports when the UAC prompt is declined.
ERROR_CANCELLED = 1223
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_REPORT_STEP_PERCENT = 10


class ConfigError(Exception):
    """install.config.json is invalid."""


class CommandError(Exception):
    """An external command exited with a non-zero status."""

    def __init__(self, args: list[str], completed: subprocess.CompletedProcess[str]) -> None:
        """Keep the completed process and summarise its last output lines in the message."""
        self.completed = completed
        tail = (completed.stderr or completed.stdout or '').strip().splitlines()[-5:]
        super().__init__(
            f'command failed ({completed.returncode}): {subprocess.list2cmdline(args)}\n' + '\n'.join(tail)
        )


class IntegrityError(Exception):
    """A downloaded file does not match its pinned SHA-256."""


class ElevationError(Exception):
    """The elevated child process could not be started."""


# ----------------------------------------------------------------------------- configuration


@dataclass(frozen=True)
class ListOf:
    """A schema: a JSON array whose items all have the type item."""

    item: type


@dataclass(frozen=True)
class MapOf:
    """A schema: a JSON object with free keys, whose values all have the type value."""

    value: type | UnionType


# A leaf type or union of types, a homogeneous container, or an object with exactly the given keys.
type Schema = type | UnionType | ListOf | MapOf | dict[str, Schema]

JSON_SCALAR = str | int | float | bool | None

CONFIG_SCHEMA: dict[str, Schema] = {
    'minimum_free_gb': int,
    'minimum_free_gb_with_optional': int,
    'tools': {'python_for_proxy': str},
    'ollama': {
        'winget_id': str,
        'version': str,
        'installer_sha256': str,
        'server_json': MapOf(JSON_SCALAR),
        'user_env': MapOf(str),
        'app_settings': MapOf(int),
    },
    'models': {
        'pull': ListOf(str),
        'create': MapOf(str),
        'optional_pull': ListOf(str),
        'optional_create': MapOf(str),
        'install_optional': bool,
    },
    'harness': {
        'opencode': {'version': str, 'url': str, 'zip_sha256': str, 'exe_sha256': str},
        'claude': {'version': str, 'url': str, 'exe_sha256': str},
    },
    'firewall': {'block_harness_and_runner': bool, 'block_ollama_server_and_app': bool},
    'shell': {'bashrc_aliases': bool, 'path_wrappers': bool, 'confidential_roots': ListOf(str)},
    'cloud': {'install_harnesses': bool, 'winget': MapOf(str)},
}


def _validate_object(value: JsonObject, schema: dict[str, Schema], prefix: str) -> None:
    unknown = sorted(set(value) - set(schema))
    if unknown:
        raise ConfigError(f'{prefix or "config"}: unknown key(s) {unknown}')
    missing = sorted(set(schema) - set(value))
    if missing:
        raise ConfigError(f'{prefix or "config"}: missing key(s) {missing}')
    for key, sub in schema.items():
        validate_config(value[key], sub, f'{prefix}.{key}' if prefix else key)


def _validate_leaf(value: JsonValue, schema: type | UnionType, prefix: str) -> None:
    if schema is bool:
        if not isinstance(value, bool):
            raise ConfigError(f'{prefix}: expected true or false')
    elif schema is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f'{prefix}: expected an integer')
    elif not isinstance(value, schema):
        raise ConfigError(f'{prefix}: expected {schema.__name__ if isinstance(schema, type) else schema}')


def validate_config(value: JsonValue, schema: Schema, prefix: str = '') -> None:
    """Raise ConfigError on unknown keys, missing keys or wrong types, naming the offending key."""
    if isinstance(schema, ListOf):
        if not isinstance(value, list):
            raise ConfigError(f'{prefix}: expected an array')
        for index, item in enumerate(value):
            validate_config(item, schema.item, f'{prefix}[{index}]')
    elif isinstance(schema, MapOf):
        if not isinstance(value, dict):
            raise ConfigError(f'{prefix}: expected an object')
        for key, item in value.items():
            validate_config(item, schema.value, f'{prefix}.{key}')
    elif isinstance(schema, dict):
        if not isinstance(value, dict):
            raise ConfigError(f'{prefix or "config"}: expected an object')
        _validate_object(value, schema, prefix)
    else:
        _validate_leaf(value, schema, prefix)


def load_config(path: Path) -> InstallConfigDto:
    """Read and validate install.config.json."""
    try:
        config: JsonValue = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ConfigError(f'cannot read {path}: {exc}') from exc
    validate_config(config, CONFIG_SCHEMA)
    return cast('InstallConfigDto', config)  # the validation above is what makes this true


def read_secrets(path: Path) -> dict[str, str]:
    """Parse a KEY=value file; blank lines and # comments are ignored."""
    secrets: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key.strip()):
            raise ConfigError(f'{path}:{number}: expected KEY=value')
        secrets[key.strip()] = value.strip()
    return secrets


# ----------------------------------------------------------------------------- paths


@dataclass(frozen=True)
class Paths:
    """Every location the programs touch, derived from the profile root."""

    home: Path

    @property
    def local_llm(self) -> Path:
        """The isolated environment root, ~/local-llm."""
        return self.home / 'local-llm'

    @property
    def ollama_home(self) -> Path:
        """Ollama's per-user directory, ~/.ollama."""
        return self.home / '.ollama'

    @property
    def ollama_models(self) -> Path:
        """Where Ollama stores model blobs and manifests."""
        return self.ollama_home / 'models'

    @property
    def server_json(self) -> Path:
        """Ollama's server settings file."""
        return self.ollama_home / 'server.json'

    @property
    def localappdata(self) -> Path:
        """%LOCALAPPDATA% for the profile root."""
        return self.home / 'AppData' / 'Local'

    @property
    def ollama_dir(self) -> Path:
        """The per-user Ollama installation directory."""
        return self.localappdata / 'Programs' / 'Ollama'

    @property
    def ollama_exe(self) -> Path:
        """The Ollama server and command-line binary."""
        return self.ollama_dir / 'ollama.exe'

    @property
    def ollama_app_exe(self) -> Path:
        """The Ollama tray application."""
        return self.ollama_dir / 'ollama app.exe'

    @property
    def llama_server_exe(self) -> Path:
        """The inference runner that Ollama starts per model."""
        return self.ollama_dir / 'lib' / 'ollama' / 'llama-server.exe'

    @property
    def app_db(self) -> Path:
        """The SQLite database holding the Ollama app settings."""
        return self.localappdata / 'Ollama' / 'db.sqlite'

    @property
    def bashrc(self) -> Path:
        """Git Bash start-up file."""
        return self.home / '.bashrc'

    @property
    def opencode_private_exe(self) -> Path:
        """The pinned OpenCode binary used only through the proxy."""
        return self.local_llm / 'opencode' / 'bin' / 'opencode.exe'

    @property
    def claude_private_exe(self) -> Path:
        """The pinned Claude Code binary used only through the proxy."""
        return self.local_llm / 'claude' / 'bin' / 'claude.exe'

    @property
    def wrappers_bin(self) -> Path:
        """The directory of the opencode/claude wrappers that pick a profile by working directory."""
        return self.local_llm / 'bin'

    @property
    def confidential_roots(self) -> Path:
        """The per-machine list of confidential directories read by the wrappers."""
        return self.local_llm / 'confidential-roots.txt'

    @property
    def cloud_binaries_cmd(self) -> Path:
        """The generated file that tells the wrappers where the cloud binaries are."""
        return self.local_llm / 'cloud-binaries.cmd'

    @property
    def claude_json(self) -> Path:
        """Claude Code's onboarding state for the isolated profile."""
        return self.local_llm / 'claude' / 'config' / '.claude.json'

    @property
    def opencode_cloud_auth(self) -> Path:
        """The credentials file of the cloud OpenCode profile."""
        return self.home / '.local' / 'share' / 'opencode' / 'auth.json'

    @property
    def secrets_file(self) -> Path:
        """The default location of secrets.env (plan, section 3)."""
        return self.home / '.secrets' / SECRETS_DIR_NAME / 'secrets.env'

    @property
    def state_dir(self) -> Path:
        """The installer's own state: the journal of its changes and the files it replaced."""
        return self.localappdata / STATE_DIR_NAME

    @property
    def journal(self) -> Path:
        """The install journal that uninstall.py reverts from."""
        return self.state_dir / JOURNAL_FILE_NAME

    @property
    def journal_backups(self) -> Path:
        """Where the installer moves the files it replaces, for uninstall.py to restore."""
        return self.state_dir / 'backups'

    @property
    def profile_dirs(self) -> list[Path]:
        """The directories the isolated harness profiles need to exist."""
        return [
            self.local_llm / 'opencode' / 'data',
            self.local_llm / 'opencode' / 'cache',
            self.local_llm / 'opencode' / 'state',
            self.local_llm / 'opencode' / 'bin',
            self.local_llm / 'claude' / 'bin',
            self.local_llm / 'claude' / 'config',
        ]


def firewall_rules_for(config: InstallConfigDto, paths: Paths) -> dict[str, Path]:
    """Display name -> program, for the rules the configuration enables."""
    rules: dict[str, Path] = {}
    if config['firewall']['block_harness_and_runner']:
        rules[RULE_OPENCODE] = paths.opencode_private_exe
        rules[RULE_CLAUDE] = paths.claude_private_exe
        rules[RULE_RUNNER] = paths.llama_server_exe
    if config['firewall']['block_ollama_server_and_app']:
        rules[RULE_OLLAMA_SERVER] = paths.ollama_exe
        rules[RULE_OLLAMA_APP] = paths.ollama_app_exe
    return rules


def required_tags(config: InstallConfigDto) -> list[str]:
    """Every model tag the configuration asks for, optional ones included when enabled."""
    models = config['models']
    tags = list(models['pull']) + list(models['create'])
    if models['install_optional']:
        tags += list(models['optional_pull']) + list(models['optional_create'])
    return tags


def harness_binaries(config: InstallConfigDto, paths: Paths) -> list[HarnessBinaryDto]:
    """The two private harness binaries: their pins and destinations."""
    harness = config['harness']
    return [
        HarnessBinaryDto('opencode', harness['opencode'], paths.opencode_private_exe),
        HarnessBinaryDto('claude', harness['claude'], paths.claude_private_exe),
    ]


def template_files(repo: Path) -> list[Path]:
    """Relative paths of the template files under repo/local-llm (sources of generated files excluded)."""
    root = repo / 'local-llm'
    files: list[Path] = []
    for path in sorted(root.rglob('*')):
        if path.is_file() and not path.name.endswith(TEMPLATE_SOURCE_SUFFIX) and '__pycache__' not in path.parts:
            files.append(path.relative_to(root))
    return files


# ----------------------------------------------------------------------------- logging


def started_datetime_local_as_str() -> str:
    """Local wall-clock time, formatted for a log file name."""
    return datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')


def setup_logging(log_file: Path | None) -> logging.Logger:
    """Log INFO and above to stdout and, when given, everything to log_file."""
    logger = logging.getLogger('envsetup')
    logger.setLevel(logging.DEBUG)
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(console)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, encoding='utf-8')
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
    return logger


# ----------------------------------------------------------------------------- commands


class Runner:
    """Runs external commands; tests replace it with a fake."""

    def __init__(self, log: logging.Logger) -> None:
        """Keep the logger that records every command line at DEBUG level."""
        self.log = log

    def run(  # noqa: PLR0913  (mirrors the subprocess.run options the callers need)
        self,
        args: list[str],
        *,
        check: bool = True,
        stream: bool = False,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run args without a shell; capture its output unless stream is true."""
        self.log.debug('run: %s', subprocess.list2cmdline(args))
        # The argument list is built from constants, install.config.json and resolved program
        # paths; nothing here comes from an untrusted source and no shell is involved.
        completed = subprocess.run(  # nosec B603  # noqa: S603
            args,
            text=True,
            encoding='utf-8',
            errors='replace',
            capture_output=not stream,
            env=env,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            check=False,
        )
        if check and completed.returncode != 0:
            raise CommandError(args, completed)
        return completed

    def powershell(self, script: str, *, check: bool = True) -> str:
        """Run a PowerShell snippet in Windows PowerShell and return its standard output.

        Cmdlet errors become terminating and exit 2, except where the snippet says
        -ErrorAction SilentlyContinue; a "not found" query therefore exits 0 with no output
        (PowerShell itself would exit 1 whenever the last pipeline failed).
        """
        wrapped = (
            "$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            'try { ' + script + ' } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 2 }; exit 0'
        )
        completed = self.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', wrapped],
            check=check,
            env=powershell_env(),
        )
        return completed.stdout


def powershell_env() -> dict[str, str]:
    """This process's environment without PSModulePath, for starting Windows PowerShell.

    Started from PowerShell 7, it inherits a module path that lists the PowerShell 7 modules first and then
    cannot load its own: Get-Acl fails with "the module could not be loaded". Without the variable it builds
    its default path.
    """
    return {name: value for name, value in os.environ.items() if name.upper() != 'PSMODULEPATH'}


def winget_install(runner: CommandRunner, package_id: str, version: str | None = None) -> None:
    """Install one winget package silently, pinned to version when given."""
    args = ['winget', 'install', '--id', package_id, '-e', '--source', 'winget', '--silent']
    if version:
        args += ['--version', version]
    args += ['--disable-interactivity', '--accept-package-agreements', '--accept-source-agreements']
    runner.run(args, stream=True)


def winget_uninstall(runner: CommandRunner, package_id: str) -> None:
    """Remove one winget package silently."""
    args = ['winget', 'uninstall', '--id', package_id, '-e', '--source', 'winget', '--silent']
    args += ['--disable-interactivity', '--accept-source-agreements']
    runner.run(args, stream=True)


# ----------------------------------------------------------------------------- firewall (NetSecurity cmdlets)


def ps_quote(value: str) -> str:
    """Return value as a single-quoted PowerShell literal: an apostrophe inside it is doubled.

    Rule names are constants, but program paths and file paths derive from the profile root, and a
    Windows user name may contain an apostrophe.
    """
    return "'" + value.replace("'", "''") + "'"


def firewall_rule(runner: CommandRunner, name: str) -> FirewallRuleDto | None:
    """The enabled/direction/action/program of the rule called name, or None when absent."""
    script = (
        f'$r = Get-NetFirewallRule -DisplayName {ps_quote(name)} -ErrorAction SilentlyContinue '
        '| Select-Object -First 1; '
        'if ($r) { $f = $r | Get-NetFirewallApplicationFilter; [pscustomobject]@{ '
        'enabled = [string]$r.Enabled; direction = [string]$r.Direction; action = [string]$r.Action; '
        'program = [string]$f.Program } | ConvertTo-Json -Compress }'
    )
    out = runner.powershell(script).strip()
    if not out:
        return None
    rule: FirewallRuleDto = json.loads(out)  # the shape the script above emits
    return rule


def firewall_add(runner: CommandRunner, name: str, program: Path) -> None:
    """Add an enabled outbound block rule for program, on every profile."""
    runner.powershell(
        f'New-NetFirewallRule -DisplayName {ps_quote(name)} -Direction Outbound -Action Block '
        f'-Program {ps_quote(str(program))} -Profile Any -Enabled True | Out-Null'
    )


def firewall_set_enabled(runner: CommandRunner, name: str, enabled: bool) -> None:
    """Enable or disable the rule called name."""
    verb = 'Enable' if enabled else 'Disable'
    runner.powershell(f'{verb}-NetFirewallRule -DisplayName {ps_quote(name)}')


def firewall_remove(runner: CommandRunner, name: str) -> None:
    """Remove the rule called name; absent rules are not an error."""
    runner.powershell(f'Remove-NetFirewallRule -DisplayName {ps_quote(name)} -ErrorAction SilentlyContinue')


def rule_matches(rule: FirewallRuleDto | None, program: Path) -> bool:
    """Whether rule is an enabled outbound block of exactly program."""
    if rule is None:
        return False
    return (
        rule['enabled'] == 'True'
        and rule['direction'] == 'Outbound'
        and rule['action'] == 'Block'
        and os.path.normcase(os.path.normpath(rule['program'])) == os.path.normcase(os.path.normpath(str(program)))
    )


# ----------------------------------------------------------------------------- user environment (HKCU)


class UserEnv:
    r"""The user's environment variables in HKCU\Environment; tests replace it with a fake."""

    KEY = r'Environment'

    def _open(self, write: bool = False) -> winreg.HKEYType:  # pragma: no cover  (touches the live registry)
        access = winreg.KEY_READ | (winreg.KEY_WRITE if write else 0)
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.KEY, 0, access)

    def get(self, name: str) -> str | None:  # pragma: no cover  (touches the live registry)
        """The value of the user variable name, or None when unset."""
        with self._open() as key:
            try:
                value, _ = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                return None
        return str(value)

    def set(self, name: str, value: str, expandable: bool = False) -> None:  # pragma: no cover  (live registry)
        """Set the user variable name; expandable keeps %VAR% references unexpanded (REG_EXPAND_SZ)."""
        kind = winreg.REG_EXPAND_SZ if expandable else winreg.REG_SZ
        with self._open(write=True) as key:
            winreg.SetValueEx(key, name, 0, kind, value)

    def delete(self, name: str) -> None:  # pragma: no cover  (touches the live registry)
        """Delete the user variable name; an unset variable is not an error."""
        with self._open(write=True) as key, contextlib.suppress(FileNotFoundError):
            winreg.DeleteValue(key, name)

    def path_list(self) -> list[str]:
        """The user Path split into entries, empty ones kept so that writing it back changes nothing else."""
        raw = self.get('Path') or ''
        return raw.split(';') if raw else []

    def set_path_list(self, entries: list[str]) -> None:
        """Write the user Path from entries, as REG_EXPAND_SZ, or delete it when none remain."""
        if entries:
            self.set('Path', ';'.join(entries), expandable=True)
        else:
            self.delete('Path')

    def broadcast(self) -> None:  # pragma: no cover  (sends a window message to the live desktop)
        """Tell running programs that the environment changed (WM_SETTINGCHANGE)."""
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        result = wt.DWORD()
        user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, 'Environment', 0x0002, 5000, ctypes.byref(result))

    def refresh_process_path(self) -> None:  # pragma: no cover  (reads the live registry)
        """Bring the registry's PATH into this process, see the function of the same name."""
        refresh_process_path(self)


def _path_key(entry: str) -> str:
    return os.path.normcase(os.path.normpath(entry.strip().rstrip('\\')))


def path_position(entries: list[str], entry: str) -> int | None:
    """Index of entry among the non-empty entries, comparing expanded and normalised forms, or None."""
    wanted = {_path_key(entry), _path_key(os.path.expandvars(entry))}
    for index, candidate in enumerate(filter(None, entries)):
        if _path_key(candidate) in wanted or _path_key(os.path.expandvars(candidate)) in wanted:
            return index
    return None


def path_prepend(entries: list[str], entry: str) -> list[str]:
    """Entries with entry first and any other spelling of it removed."""
    return [entry, *path_remove(entries, entry)]


def path_remove(entries: list[str], entry: str) -> list[str]:
    """Entries without any spelling of entry."""
    wanted = {_path_key(entry), _path_key(os.path.expandvars(entry))}
    return [e for e in entries if _path_key(e) not in wanted and _path_key(os.path.expandvars(e)) not in wanted]


def merged_path(registry_path: str, process_path: str) -> str:
    """The registry's entries in order, then the entries only this process has, without duplicates.

    A process can hold entries the registry lacks, such as a tool that a CI runner or a console added for
    itself. Replacing PATH with the registry value would hide those tools from every later step.
    """
    merged: dict[str, str] = {}
    for entry in os.path.expandvars(registry_path).split(os.pathsep) + process_path.split(os.pathsep):
        if entry.strip():
            merged.setdefault(_path_key(entry), entry)
    return os.pathsep.join(merged.values())


def refresh_process_path(user_env: UserEnv) -> None:  # pragma: no cover  (reads the live registry)
    """Add the registry's PATH (machine part, then user part) in front of this process's PATH."""
    machine = ''
    with (
        winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
        ) as key,
        contextlib.suppress(FileNotFoundError),
    ):
        machine = str(winreg.QueryValueEx(key, 'Path')[0])
    combined = ';'.join(part for part in (machine, user_env.get('Path') or '') if part)
    os.environ['PATH'] = merged_path(combined, os.environ.get('PATH', ''))


# ----------------------------------------------------------------------------- .bashrc block


def _block_bounds(lines: list[str]) -> tuple[int, int] | None:
    try:
        start = lines.index(BASHRC_BEGIN)
        end = lines.index(BASHRC_END, start)
    except ValueError:
        return None
    return start, end


def block_state(text: str, block: str) -> BlockState:
    """'absent', 'same' or 'different'."""
    lines = text.splitlines()
    bounds = _block_bounds(lines)
    if bounds is None:
        return 'absent'
    start, end = bounds
    return 'same' if lines[start : end + 1] == block.strip().splitlines() else 'different'


def block_apply(text: str, block: str) -> str:
    """Text with block appended, or with the existing marked block replaced by it."""
    lines = text.splitlines()
    new_block = block.strip().splitlines()
    bounds = _block_bounds(lines)
    if bounds is None:
        if lines and lines[-1] != '':
            lines.append('')
        lines += new_block
    else:
        start, end = bounds
        lines[start : end + 1] = new_block
    return '\n'.join(lines) + '\n'


def block_remove(text: str) -> str:
    """Text without the marked block and the blank line that separated it."""
    lines = text.splitlines()
    bounds = _block_bounds(lines)
    if bounds is None:
        return text
    start, end = bounds
    del lines[start : end + 1]
    # Drop the blank separator line that block_apply added before the block.
    if 0 < start <= len(lines) and lines[start - 1] == '' and (start == len(lines) or lines[start] == ''):
        del lines[start - 1]
    return ('\n'.join(lines) + '\n') if lines else ''


def block_text(text: str) -> str | None:
    """The marked block in text, markers included, or None when there is none."""
    lines = text.splitlines()
    bounds = _block_bounds(lines)
    if bounds is None:
        return None
    start, end = bounds
    return '\n'.join(lines[start : end + 1]) + '\n'


# ----------------------------------------------------------------------------- files, hashes, downloads


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(DOWNLOAD_CHUNK_BYTES), b''):
            digest.update(chunk)
    return digest.hexdigest()


def files_differ(a: Path, b: Path) -> bool:
    """Whether two files differ, by size first and by SHA-256 when the sizes match."""
    if a.stat().st_size != b.stat().st_size:
        return True
    return sha256_file(a) != sha256_file(b)


def http_request(url: str, method: str = 'GET') -> urllib.request.Request:
    """A request for an http(s) URL; any other scheme is refused before a socket is opened."""
    if not url.startswith(('https://', 'http://')):
        raise ValueError(f'refusing to open {url!r}: only http and https are allowed')
    return urllib.request.Request(url, method=method, headers={'User-Agent': USER_AGENT})  # noqa: S310


def download(url: str, dest: Path, log: logging.Logger) -> None:
    """Fetch url into dest, logging progress every ten percent."""
    request = http_request(url)
    log.info('  downloading %s', url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # http_request() has already limited the scheme to http(s).
    with urllib.request.urlopen(request, timeout=60) as response, dest.open('wb') as out:  # nosec B310  # noqa: S310
        total = int(response.headers.get('Content-Length') or 0)
        done = 0
        next_report = DOWNLOAD_REPORT_STEP_PERCENT
        for chunk in iter(lambda: response.read(DOWNLOAD_CHUNK_BYTES), b''):
            out.write(chunk)
            done += len(chunk)
            if total and done * 100 // total >= next_report:
                log.info('  %d%% of %.1f MB', done * 100 // total, total / 1e6)
                next_report += DOWNLOAD_REPORT_STEP_PERCENT
    log.info('  saved %s (%d bytes)', dest.name, dest.stat().st_size)


def download_verified(url: str, dest: Path, sha256: str, log: logging.Logger) -> None:
    """Download url into dest and delete it again unless its SHA-256 is sha256."""
    download(url, dest, log)
    actual = sha256_file(dest)
    if actual != sha256.lower():
        dest.unlink(missing_ok=True)
        raise IntegrityError(f'{dest.name}: SHA-256 {actual} differs from the pinned {sha256}')
    log.info('  SHA-256 verified')


def reachable(url: str, timeout: float = 10) -> bool:
    """Whether a HEAD request to url gets any HTTP answer at all."""
    try:
        # http_request() has already limited the scheme to http(s).
        urllib.request.urlopen(http_request(url, 'HEAD'), timeout=timeout).close()  # nosec B310  # noqa: S310
    except urllib.error.HTTPError as exc:
        exc.close()
        return True
    except urllib.error.URLError, OSError, ValueError:
        return False
    return True


def which_outside(command: str, exclude_dir: Path) -> str | None:
    """shutil.which over PATH minus one directory (the wrappers directory)."""
    excluded = _path_key(str(exclude_dir))
    entries = [
        entry
        for entry in os.environ.get('PATH', '').split(os.pathsep)
        if entry and _path_key(os.path.expandvars(entry)) != excluded
    ]
    return shutil.which(command, path=os.pathsep.join(entries))


def robocopy(runner: CommandRunner, source: Path, dest: Path) -> None:
    """Mirror the tree source into dest with robocopy /E."""
    completed = runner.run(
        ['robocopy', str(source), str(dest), '/E', '/NFL', '/NDL', '/NJH', '/NP'], check=False, stream=True
    )
    if completed.returncode >= ROBOCOPY_FAILURE_THRESHOLD:
        raise CommandError(['robocopy', str(source), str(dest)], completed)


# ----------------------------------------------------------------------------- Windows session


def is_windows() -> bool:
    """Whether this process runs on Windows."""
    return sys.platform == 'win32'


def windows_build() -> int:
    """The Windows build number, 0 elsewhere."""
    return sys.getwindowsversion().build if is_windows() else 0


def is_admin() -> bool:  # pragma: no cover  (asks the live token)
    """Whether this process is elevated."""
    try:
        return bool(ctypes.WinDLL('shell32').IsUserAnAdmin())
    except AttributeError, OSError:
        return False


def console_user() -> str | None:  # pragma: no cover  (asks the live session)
    """Name of the user logged on to the interactive session of this process."""
    wtsapi32 = ctypes.WinDLL('wtsapi32', use_last_error=True)
    query = wtsapi32.WTSQuerySessionInformationW
    query.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_int, ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(wt.DWORD)]
    query.restype = wt.BOOL
    buffer = ctypes.c_wchar_p()
    size = wt.DWORD()
    if not query(None, 0xFFFFFFFF, 5, ctypes.byref(buffer), ctypes.byref(size)):
        return None
    try:
        return buffer.value
    finally:
        wtsapi32.WTSFreeMemory(buffer)


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ('cbSize', wt.DWORD),
        ('fMask', wt.ULONG),
        ('hwnd', wt.HWND),
        ('lpVerb', wt.LPCWSTR),
        ('lpFile', wt.LPCWSTR),
        ('lpParameters', wt.LPCWSTR),
        ('lpDirectory', wt.LPCWSTR),
        ('nShow', ctypes.c_int),
        ('hInstApp', wt.HINSTANCE),
        ('lpIDList', ctypes.c_void_p),
        ('lpClass', wt.LPCWSTR),
        ('hkeyClass', wt.HKEY),
        ('dwHotKey', wt.DWORD),
        ('hIconOrMonitor', wt.HANDLE),
        ('hProcess', wt.HANDLE),
    ]


def run_elevated(args: list[str], cwd: Path) -> int:  # pragma: no cover  (shows a UAC prompt)
    """Run args[0] with args[1:] through UAC ('runas'), wait, return its exit code."""
    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = 'runas'
    info.lpFile = args[0]
    info.lpParameters = subprocess.list2cmdline(args[1:])
    info.lpDirectory = str(cwd)
    info.nShow = 1  # SW_SHOWNORMAL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == ERROR_CANCELLED:
            raise ElevationError('the UAC request was declined')
        raise ElevationError(f'ShellExecuteEx failed with error {error}')
    kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wt.DWORD()
    kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(info.hProcess)
    return int(code.value)


def free_gb(path: Path) -> float:
    """Free space of the volume holding path, in GB."""
    return shutil.disk_usage(path).free / 1e9


# ----------------------------------------------------------------------------- ACL


def acl_sids(runner: CommandRunner, path: Path) -> set[str]:
    """The SIDs that have an access entry on path."""
    script = (
        f'(Get-Acl -LiteralPath {ps_quote(str(path))}).Access | ForEach-Object {{ '
        'try { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value } '
        'catch { [string]$_.IdentityReference } }'
    )
    return {line.strip() for line in runner.powershell(script).splitlines() if line.strip()}


def broad_acl_entries(sids: set[str]) -> list[str]:
    """The names of the well-known broad groups (Everyone, Users, ...) among sids."""
    return [BROAD_SIDS[sid] for sid in sorted(sids) if sid in BROAD_SIDS]


def restrict_acl(runner: CommandRunner, path: Path, username: str) -> None:
    """Leave path readable and writable by username, and by none of the broad groups.

    /inheritance:r drops only the inherited entries, so an explicit grant to a broad group has to be
    removed by name; the SIDs make that independent of the Windows display language.
    """
    broad = [f'*{sid}' for sid in sorted(BROAD_SIDS)]
    runner.run(['icacls', str(path), '/inheritance:r', '/remove:g', *broad, '/grant:r', f'{username}:(R,W)'])


# ----------------------------------------------------------------------------- Ollama


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api_version(timeout: float = 3, base_url: str = OLLAMA_API) -> str | None:
    """The version the Ollama server reports, or None when it does not answer."""
    try:
        with _no_proxy_opener().open(http_request(f'{base_url}/api/version'), timeout=timeout) as response:
            return str(json.load(response).get('version'))
    except urllib.error.URLError, OSError, ValueError:
        return None


def wait_for_api(seconds: float) -> str | None:
    """Poll the Ollama API for up to seconds; its version, or None on timeout."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        version = api_version()
        if version:
            return version
        time.sleep(2)
    return None


def ollama_version(runner: CommandRunner) -> str | None:
    """The version of the ollama command on PATH, or None."""
    if not shutil.which('ollama'):
        return None
    completed = runner.run(['ollama', '--version'], check=False)
    match = re.search(r'(\d+\.\d+\.\d+)', completed.stdout + completed.stderr)
    return match.group(1) if match else None


def normalize_tag(tag: str) -> str:
    """'name' and 'name:latest' denote the same model; ollama list prints the latter."""
    return tag if ':' in tag else f'{tag}:latest'


def parse_ollama_list(stdout: str) -> set[str]:
    """Normalised tags from the output of `ollama list`."""
    return {normalize_tag(line.split()[0]) for line in stdout.splitlines()[1:] if line.strip()}


def missing_tags(wanted: list[str], present: set[str]) -> list[str]:
    """The tags in wanted that present does not contain, in order."""
    return [tag for tag in wanted if normalize_tag(tag) not in present]


def ollama_modelfile(runner: CommandRunner, tag: str) -> str:
    """The Modelfile of an installed tag."""
    return runner.run(['ollama', 'show', '--modelfile', tag]).stdout


def stop_ollama(runner: CommandRunner) -> None:
    """Kill the Ollama app, server and runner, then wait until the API stops answering."""
    for image in ('ollama app.exe', 'ollama.exe', 'llama-server.exe'):
        runner.run(['taskkill', '/F', '/IM', image], check=False)
    deadline = time.monotonic() + 15
    while api_version(1) and time.monotonic() < deadline:
        time.sleep(1)


def start_ollama_app(app_exe: Path, env: dict[str, str]) -> None:
    """Start the Ollama tray app detached from this console, with env as its environment."""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    # app_exe is the Ollama app under the profile root; no shell, no user-supplied arguments.
    subprocess.Popen(  # nosec B603  # noqa: S603
        [str(app_exe)],
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        close_fds=True,
        cwd=str(app_exe.parent),
    )


def _settings_columns(connection: sqlite3.Connection) -> set[str] | None:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'settings' not in tables:
        return None
    return {row[1] for row in connection.execute('PRAGMA table_info(settings)')}


def app_settings_state(db_path: Path, wanted: dict[str, int]) -> AppSettingsState:
    """'no-db', 'no-table', 'no-columns', 'no-row', 'same' or 'different'."""
    if not db_path.exists():
        return 'no-db'
    connection = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        columns = _settings_columns(connection)
        if columns is None:
            return 'no-table'
        if not set(wanted) <= columns:
            return 'no-columns'
        # The column names were just checked against the table, so they are safe to interpolate.
        query = 'SELECT ' + ', '.join(wanted) + ' FROM settings LIMIT 1'  # nosec B608  # noqa: S608
        row = connection.execute(query).fetchone()
    finally:
        connection.close()
    if row is None:
        return 'no-row'
    return 'same' if all(int(row[index]) == int(value) for index, value in enumerate(wanted.values())) else 'different'


def app_settings_apply(db_path: Path, wanted: dict[str, int]) -> None:
    """Write wanted into the single settings row of the Ollama app database."""
    connection = sqlite3.connect(db_path)
    try:
        columns = _settings_columns(connection)
        if columns is None or not set(wanted) <= columns:
            raise ConfigError(f'{db_path}: the settings table does not have the columns {sorted(wanted)}')
        # The column names were just checked against the table; the values are bound parameters.
        assignments = ', '.join(f'{column} = ?' for column in wanted)
        connection.execute(f'UPDATE settings SET {assignments}', tuple(int(v) for v in wanted.values()))  # nosec B608  # noqa: S608
        connection.commit()
    finally:
        connection.close()


def app_settings_read(db_path: Path, columns: list[str]) -> dict[str, int]:
    """The values of columns in the single settings row of the Ollama app database."""
    connection = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        have = _settings_columns(connection)
        if have is None or not set(columns) <= have:
            raise ConfigError(f'{db_path}: the settings table does not have the columns {sorted(columns)}')
        # The column names were just checked against the table, so they are safe to interpolate.
        query = 'SELECT ' + ', '.join(columns) + ' FROM settings LIMIT 1'  # nosec B608  # noqa: S608
        row = connection.execute(query).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ConfigError(f'{db_path}: the settings table has no row')
    return {column: int(row[index]) for index, column in enumerate(columns)}


def listening_addresses(runner: CommandRunner, port: int) -> list[str]:
    """Local addresses of the sockets bound to port that have no remote endpoint (listeners).

    Selected by RemotePort rather than -State Listen: on Windows 11 build 26300 the Ollama listener
    is reported with TCP state 0, which -State Listen does not match.
    """
    out = runner.powershell(
        f'Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue '
        '| Where-Object { $_.RemotePort -eq 0 } | ForEach-Object { $_.LocalAddress }'
    )
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


# ----------------------------------------------------------------------------- install journal


_RECORDED: dict[str, Schema] = {'kind': str, 'recorded_datetime_local_as_str': str}
JOURNAL_SCHEMAS: dict[JournalKind, dict[str, Schema]] = {
    'winget-install': {**_RECORDED, 'id': str},
    'uv-python-install': {**_RECORDED, 'version': str},
    'dir-created': {**_RECORDED, 'path': str, 'tree': bool},
    'file': {**_RECORDED, 'path': str, 'backup': str | None},
    'user-env': {**_RECORDED, 'name': str, 'previous': str | None},
    'user-path': {**_RECORDED, 'entry': str},
    'bashrc-block': {**_RECORDED, 'path': str, 'created': bool, 'previous_block': str | None},
    'app-settings': {**_RECORDED, 'db': str, 'previous': MapOf(int)},
    'model': {**_RECORDED, 'tag': str},
    'auth-key': {**_RECORDED, 'path': str},
    'firewall-rule': {**_RECORDED, 'name': str},
}
# The key that names the changed item: a second change to the same item is not a new record.
JOURNAL_IDENTITY: dict[JournalKind, str] = {
    'winget-install': 'id',
    'uv-python-install': 'version',
    'dir-created': 'path',
    'file': 'path',
    'user-env': 'name',
    'user-path': 'entry',
    'bashrc-block': 'path',
    'app-settings': 'db',
    'model': 'tag',
    'auth-key': 'path',
    'firewall-rule': 'name',
}


class Journal:
    """What install.py changed on this machine, so that uninstall.py reverts exactly that.

    One JSON file under %LOCALAPPDATA%: an entry per change, in the order the changes happened,
    each with a kind and the data its revert needs (a backup location, a previous value). Every
    add writes the file at once, so a run that stops half-way still leaves a complete record.
    Whatever existed before the installer ran is never recorded, and is therefore never touched
    by the uninstaller.
    """

    def __init__(self, path: Path) -> None:
        """Load the entries recorded at path; a missing file is an empty journal."""
        self.path = path
        self.entries: list[JournalEntryDto] = self._load()

    def _load(self) -> list[JournalEntryDto]:
        try:
            data: JsonValue = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            raise ConfigError(f'cannot read the install journal {self.path}: {exc}') from exc
        entries = data.get('entries') if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ConfigError(f'{self.path} is not an install journal')
        return [self._checked(entry) for entry in entries]

    def _checked(self, entry: JsonValue) -> JournalEntryDto:
        """The entry as read from the file, once it has been checked against the schema of its kind."""
        if not isinstance(entry, dict) or 'kind' not in entry:
            raise ConfigError(f'{self.path} is not an install journal')
        kind = entry['kind']
        schema = next((schema for name, schema in JOURNAL_SCHEMAS.items() if name == kind), None)
        if schema is None:
            raise ConfigError(f'{self.path}: unknown entry kind {kind!r}')
        validate_config(entry, schema, f'{self.path}: {kind} entry')
        return cast('JournalEntryDto', entry)  # the validation above is what makes this true

    def save(self) -> None:
        """Write the entries through a temporary file; no entries means no file."""
        if not self.entries:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        document: JournalDocumentDto = {'version': JOURNAL_VERSION, 'entries': self.entries}
        temporary.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
        temporary.replace(self.path)

    def add(self, entry: JournalEntryDto) -> None:
        """Stamp one entry with the current time, append it and write the file."""
        entry['recorded_datetime_local_as_str'] = datetime.now().astimezone().isoformat(timespec='seconds')
        self.entries.append(entry)
        self.save()

    def add_once(self, entry: JournalEntryDto) -> None:
        """Record a change unless the same item already has an entry of this kind: the first record wins.

        The first record holds the state from before the installer ran, which is the one the
        uninstaller must restore; a later run that touches the same item again adds nothing.
        """
        key = JOURNAL_IDENTITY[entry['kind']]
        if not any(other['kind'] == entry['kind'] and other.get(key) == entry.get(key) for other in self.entries):
            self.add(entry)

    def remove(self, entry: JournalEntryDto) -> None:
        """Drop one reverted entry and write the file."""
        self.entries.remove(entry)
        self.save()

    def of_kind(self, kind: JournalKind) -> list[JournalEntryDto]:
        """The entries of one kind, oldest first."""
        return [entry for entry in self.entries if entry['kind'] == kind]

    def has(self, kind: JournalKind, **data: JsonScalar) -> bool:
        """Whether an entry of kind carries every given value."""
        return any(all(entry.get(key) == value for key, value in data.items()) for entry in self.of_kind(kind))

    def covers_path(self, path: Path) -> bool:
        """Whether path is journaled as a file, or lies in a tree the installer created and deletes whole."""
        if self.has('file', path=str(path)):
            return True
        return any(
            entry['kind'] == 'dir-created' and entry['tree'] and path.is_relative_to(entry['path'])
            for entry in self.entries
        )


# ----------------------------------------------------------------------------- shared context


# Covariant so that a step written against the protocols accepts a Context holding any implementation
# of them. The programs never reassign these two attributes after construction.
RunnerT_co = TypeVar('RunnerT_co', bound=CommandRunner, covariant=True, default=CommandRunner)
UserEnvT_co = TypeVar('UserEnvT_co', bound=UserEnvironment, covariant=True, default=UserEnvironment)


@dataclass
class Context(Generic[RunnerT_co, UserEnvT_co]):
    """Everything a step or a check needs: locations, configuration, log, command runners and options."""

    repo: Path
    paths: Paths
    config: InstallConfigDto
    log: logging.Logger
    runner: RunnerT_co
    user_env: UserEnvT_co
    dry_run: bool = False
    replace_binaries: bool = False
    update_templates: bool = False
    models_from: Path | None = None
    secrets_file: Path | None = None
    log_file: Path | None = None
    only: set[StepName] = field(default_factory=set[StepName])
    skip: set[StepName] = field(default_factory=set[StepName])
    keep_models: bool = False
    _journal: Journal | None = field(default=None, init=False, repr=False)

    @property
    def secrets_path(self) -> Path:
        """The secrets file given on the command line, or the default location."""
        return self.secrets_file or self.paths.secrets_file

    @property
    def journal(self) -> Journal:
        """The install journal, loaded on first use."""
        if self._journal is None:
            self._journal = Journal(self.paths.journal)
        return self._journal

    def reload_journal(self) -> None:
        """Forget the loaded journal, after another process (the elevated phase) has written it."""
        self._journal = None


def python_args(script: Path) -> list[str]:
    """Command line that re-runs a repository script with this interpreter, isolated."""
    return [sys.executable, '-I', str(script)]
