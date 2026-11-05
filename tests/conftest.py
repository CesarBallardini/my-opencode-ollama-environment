"""pytest configuration: make the repository root importable and provide the fakes.

Three levels of fake. FakeRunner answers from registered handlers and records what was asked.
FakeMachine keeps the state the real commands would change (winget packages, uv pythons, model
tags, firewall rules, the cloud binaries on PATH), so that each step's own detection sees what the
previous command did. The machine fixture wires a FakeMachine into a Context whose every other
system touch (downloads, Ollama's API, elevation, reachability, free space) is patched away.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, TypeVar

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import envsetup as es
import install

if TYPE_CHECKING:
    from envtypes import FirewallRuleDto, InstallConfigDto, JsonObject, JsonValue

REPO = Path(__file__).resolve().parent.parent
PINNED = es.load_config(REPO / 'install.config.json')
Matcher = Callable[[list[str]], bool]
Downloader = Callable[[str, Path, str, logging.Logger], None]
# winget package id -> the command it puts on PATH, for the cloud harnesses.
CLOUD_PACKAGES = {'SST.opencode': 'opencode', 'Anthropic.ClaudeCode': 'claude'}
# What the fake downloads deliver as the two private binaries.
FAKE_OPENCODE_EXE = b'opencode binary'
FAKE_CLAUDE_EXE = b'claude binary'


class MachineStateDto(TypedDict):
    """What a FakeMachine holds, as plain data."""

    packages: list[str]
    pythons: list[str]
    tags: list[str]
    rules: dict[str, FirewallRuleDto]
    cloud: dict[str, str]


class MachineSnapshotDto(TypedDict):
    """The observable state of a fake laptop."""

    files: dict[str, str]
    env: dict[str, str]
    app_settings: dict[str, int] | None
    machine: MachineStateDto | None


class FakeRunner(es.Runner):
    """Records commands; answers from registered handlers, otherwise with empty success."""

    def __init__(self, log: logging.Logger | None = None) -> None:
        super().__init__(log or logging.getLogger('test'))
        self.calls: list[list[str]] = []
        self.scripts: list[str] = []
        self.handlers: list[tuple[Matcher, str, int, Exception | None]] = []
        self.powershell_handlers: list[tuple[Callable[[str], bool], str]] = []

    def answer(self, matcher: Matcher, stdout: str = '', returncode: int = 0, raises: Exception | None = None) -> None:
        self.handlers.append((matcher, stdout, returncode, raises))

    def answer_powershell(self, matcher: Callable[[str], bool], stdout: str) -> None:
        self.powershell_handlers.append((matcher, stdout))

    def run(  # noqa: PLR0913  # the signature mirrors Runner.run
        self,
        args: list[str],
        *,
        check: bool = True,
        stream: bool = False,  # noqa: ARG002
        env: dict[str, str] | None = None,  # noqa: ARG002
        cwd: Path | None = None,  # noqa: ARG002
        timeout: float | None = None,  # noqa: ARG002
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        for matcher, stdout, returncode, raises in self.handlers:
            if matcher(args):
                if raises is not None:
                    raise raises
                completed = subprocess.CompletedProcess(args, returncode, stdout, '')
                if check and returncode != 0:
                    raise es.CommandError(list(args), completed)
                return completed
        return subprocess.CompletedProcess(args, 0, '', '')

    def powershell(self, script: str, *, check: bool = True) -> str:  # noqa: ARG002
        self.scripts.append(script)
        for matcher, stdout in self.powershell_handlers:
            if matcher(script):
                return stdout
        return ''


class FakeMachine(FakeRunner):
    """A runner that keeps the state the real commands would change.

    With paths given, installing Ollama creates its binaries under the profile root and
    uninstalling it removes them, so that the steps' own detection sees the change. Rule names and
    program paths are taken from the scripts between their single quotes, which is enough for the
    constant names the installer uses.
    """

    def __init__(self, log: logging.Logger | None = None, paths: es.Paths | None = None) -> None:
        super().__init__(log)
        self.paths = paths
        self.packages: set[str] = set()
        self.pythons: set[str] = set()
        self.tags: set[str] = set()
        self.rules: dict[str, FirewallRuleDto] = {}
        self.cloud: dict[str, str] = {}

    def state(self) -> MachineStateDto:
        """The machine state as plain data, for snapshots."""
        return {
            'packages': sorted(self.packages),
            'pythons': sorted(self.pythons),
            'tags': sorted(self.tags),
            'rules': {name: rule.copy() for name, rule in sorted(self.rules.items())},
            'cloud': dict(sorted(self.cloud.items())),
        }

    def install_ollama_files(self) -> None:
        """Create the Ollama binaries under the profile root, as the winget install would."""
        if self.paths is None:
            return
        for exe in (self.paths.ollama_exe, self.paths.ollama_app_exe, self.paths.llama_server_exe):
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_bytes(b'')

    def run(  # noqa: PLR0913  # the signature mirrors Runner.run
        self,
        args: list[str],
        *,
        check: bool = True,
        stream: bool = False,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        simulated = self._simulate(args)
        if simulated is not None:
            self.calls.append(list(args))
            return simulated
        return super().run(args, check=check, stream=stream, env=env, cwd=cwd, timeout=timeout)

    def _simulate(self, args: list[str]) -> subprocess.CompletedProcess[str] | None:
        ok = subprocess.CompletedProcess(args, 0, '', '')
        if args[:2] == ['winget', 'install']:
            package = args[args.index('--id') + 1]
            self.packages.add(package)
            if package == PINNED['ollama']['winget_id']:
                self.install_ollama_files()
            elif package in CLOUD_PACKAGES:
                self.cloud[CLOUD_PACKAGES[package]] = f'C:/cloud/{CLOUD_PACKAGES[package]}.exe'
            return ok
        if args[:2] == ['winget', 'uninstall']:
            package = args[args.index('--id') + 1]
            self.packages.discard(package)
            if package == PINNED['ollama']['winget_id'] and self.paths is not None:
                shutil.rmtree(self.paths.ollama_dir, ignore_errors=True)
            elif package in CLOUD_PACKAGES:
                self.cloud.pop(CLOUD_PACKAGES[package], None)
            return ok
        if args[:2] == ['uv', 'python']:
            if args[2] == 'find':
                return subprocess.CompletedProcess(args, 0 if args[3] in self.pythons else 2, '', '')
            if args[2] == 'install':
                self.pythons.add(args[3])
            elif args[2] == 'uninstall':
                self.pythons.discard(args[3])
            return ok
        if args[0].lower().endswith('ollama.exe') and len(args) > 1:
            return self._simulate_ollama(args)
        return None

    def _simulate_ollama(self, args: list[str]) -> subprocess.CompletedProcess[str] | None:
        command = args[1]
        if command == '--version':
            return subprocess.CompletedProcess(args, 0, f'ollama version is {PINNED["ollama"]["version"]}', '')
        if command == 'list':
            lines = ['NAME ID SIZE MODIFIED', *(f'{tag} abc 1 GB now' for tag in sorted(self.tags))]
            return subprocess.CompletedProcess(args, 0, '\n'.join(lines) + '\n', '')
        if command in ('pull', 'create'):
            self.tags.add(es.normalize_tag(args[2]))
        elif command == 'rm':
            self.tags.discard(es.normalize_tag(args[2]))
        elif command == 'show':
            return subprocess.CompletedProcess(args, 0, 'FROM base\nPARAMETER num_ctx 32768\n', '')
        else:
            return None
        return subprocess.CompletedProcess(args, 0, '', '')

    def powershell(self, script: str, *, check: bool = True) -> str:
        self.scripts.append(script)
        if script.startswith('New-NetFirewallRule'):
            name = script.split("'", 2)[1]
            program = script.split("-Program '", 1)[1].split("'", 1)[0]
            self.rules[name] = {'enabled': 'True', 'direction': 'Outbound', 'action': 'Block', 'program': program}
            return ''
        if script.startswith('$r = Get-NetFirewallRule'):
            name = script.split("'", 2)[1]
            return json.dumps(self.rules[name]) if name in self.rules else ''
        for verb in ('Remove', 'Enable', 'Disable'):
            if script.startswith(f'{verb}-NetFirewallRule'):
                name = script.split("'", 2)[1]
                if verb == 'Remove':
                    self.rules.pop(name, None)
                elif name in self.rules:
                    self.rules[name]['enabled'] = 'True' if verb == 'Enable' else 'False'
                return ''
        return super().powershell(script, check=check)


class FakeUserEnv(es.UserEnv):
    """An in-memory HKCU\\Environment."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.broadcasts = 0

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str, expandable: bool = False) -> None:  # noqa: ARG002
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)

    def broadcast(self) -> None:
        self.broadcasts += 1

    def refresh_process_path(self) -> None:
        pass


FakeRunnerT_co = TypeVar('FakeRunnerT_co', bound=FakeRunner, covariant=True, default=FakeRunner)


@dataclass
class FakeContext(es.Context[FakeRunnerT_co, FakeUserEnv]):
    """A Context whose runner and user environment are the fakes, so tests can read what was recorded."""


def fake_download(payloads: dict[str, bytes]) -> Downloader:
    """A stand-in for envsetup.download_verified that writes payloads[url] and checks the hash."""

    def download_verified(url: str, dest: Path, sha256: str, _log: logging.Logger) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payloads[url])
        if es.sha256_file(dest) != sha256:
            raise es.IntegrityError(dest.name)

    return download_verified


def pin_fake_binaries(config: InstallConfigDto) -> dict[str, bytes]:
    """Point the harness pins of config at two tiny fake binaries; returns url -> payload."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('opencode.exe', FAKE_OPENCODE_EXE)
    harness = config['harness']
    harness['opencode']['zip_sha256'] = hashlib.sha256(archive.getvalue()).hexdigest()
    harness['opencode']['exe_sha256'] = hashlib.sha256(FAKE_OPENCODE_EXE).hexdigest()
    harness['claude']['exe_sha256'] = hashlib.sha256(FAKE_CLAUDE_EXE).hexdigest()
    return {harness['opencode']['url']: archive.getvalue(), harness['claude']['url']: FAKE_CLAUDE_EXE}


def create_app_db(path: Path, auto_update_enabled: int, airplane_mode: int) -> None:
    """An Ollama app database with one settings row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute('CREATE TABLE settings (auto_update_enabled INTEGER, airplane_mode INTEGER)')
        connection.execute('INSERT INTO settings VALUES (?, ?)', (auto_update_enabled, airplane_mode))
        connection.commit()
    finally:
        connection.close()


def raw_config() -> JsonObject:
    """install.config.json as plain JSON, for the tests that break it on purpose."""
    value: JsonValue = json.loads((REPO / 'install.config.json').read_text(encoding='utf-8'))
    assert isinstance(value, dict)
    return value


def nested(document: JsonObject, *keys: str) -> JsonObject:
    """The JSON object found by following keys down from document."""
    for key in keys:
        value = document[key]
        assert isinstance(value, dict)
        document = value
    return document


def snapshot(ctx: FakeContext) -> MachineSnapshotDto:
    """The observable state of the fake machine: files under the profile root, variables, settings, machine.

    The app database is compared by its values (SQLite rewrites pages on every update) and the
    installer's own state directory is left out: the journal is what the tests inspect separately.
    """
    files: dict[str, str] = {}
    if ctx.paths.home.exists():
        for path in sorted(ctx.paths.home.rglob('*')):
            if path.is_file() and path != ctx.paths.app_db and not path.is_relative_to(ctx.paths.state_dir):
                files[path.relative_to(ctx.paths.home).as_posix()] = es.sha256_file(path)
    columns = list(ctx.config['ollama']['app_settings'])
    settings = es.app_settings_read(ctx.paths.app_db, columns) if ctx.paths.app_db.exists() else None
    machine = ctx.runner.state() if isinstance(ctx.runner, FakeMachine) else None
    return {'files': files, 'env': dict(ctx.user_env.values), 'app_settings': settings, 'machine': machine}


@pytest.fixture
def config() -> InstallConfigDto:
    return es.load_config(REPO / 'install.config.json')


@pytest.fixture
def ctx(tmp_path: Path, config: InstallConfigDto) -> FakeContext:
    return FakeContext(
        repo=REPO,
        paths=es.Paths(tmp_path / 'home'),
        config=config,
        log=logging.getLogger('test'),
        runner=FakeRunner(),
        user_env=FakeUserEnv(),
    )


@pytest.fixture
def machine(tmp_path: Path, config: InstallConfigDto, monkeypatch: pytest.MonkeyPatch) -> FakeContext[FakeMachine]:
    """A fresh laptop: nothing installed, every step pending, every system touch simulated."""
    paths = es.Paths(tmp_path / 'home')
    runner = FakeMachine(paths=paths)
    ctx = FakeContext(
        repo=REPO, paths=paths, config=config, log=logging.getLogger('test'), runner=runner, user_env=FakeUserEnv()
    )
    ctx.user_env.values['Path'] = r'C:\One'
    create_app_db(ctx.paths.app_db, auto_update_enabled=1, airplane_mode=0)
    monkeypatch.setattr(es, 'download_verified', fake_download(pin_fake_binaries(ctx.config)))
    monkeypatch.setattr(
        install.shutil, 'which', lambda command: 'C:/tools/uv.exe' if command in ('uv', 'winget') else None
    )
    monkeypatch.setattr(es, 'which_outside', lambda name, _exclude: runner.cloud.get(name))
    monkeypatch.setattr(es, 'api_version', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(es, 'start_ollama_app', lambda _exe, _env: None)
    monkeypatch.setattr(es, 'wait_for_api', lambda _seconds: PINNED['ollama']['version'])
    monkeypatch.setattr(es, 'is_admin', lambda: True)
    monkeypatch.setattr(es, 'console_user', lambda: None)
    monkeypatch.setattr(es, 'reachable', lambda _url: True)
    monkeypatch.setattr(es, 'free_gb', lambda _path: 1000.0)
    monkeypatch.setattr(es, 'Runner', lambda _log: runner)
    monkeypatch.setattr(es, 'UserEnv', lambda: ctx.user_env)
    return ctx
