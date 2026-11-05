"""Step definitions for tests/features: the install contract, run through the real entry points."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from conftest import FAKE_OPENCODE_EXE, FakeContext, FakeMachine, snapshot
from pytest_bdd import given, parsers, scenarios, then, when

import envsetup as es
import install
import uninstall

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from conftest import MachineSnapshotDto

    from envtypes import JournalEntryDto

scenarios('features/install_contract.feature')

API_KEY = 'not-a-real-key'
CLAUDE_PACKAGE = 'Anthropic.ClaudeCode'
OTHER_OPENCODE_EXE = b'another opencode binary'
MY_BASHRC = '# mine\n'
MY_DENY_LIST = 'mine\n'
MY_VARIABLE = 'OLLAMA_VULKAN'


@dataclass
class World:
    """What the steps share: the fake laptop, the last exit code and the snapshots taken."""

    ctx: FakeContext[FakeMachine]
    tmp: Path
    exit_code: int | None = None
    machine_taken: MachineSnapshotDto | None = None
    journal_taken: list[JournalEntryDto] | None = None

    @property
    def log_file(self) -> Path:
        return self.tmp / 'run.log'

    @property
    def secrets_file(self) -> Path:
        return self.tmp / 'secrets.env'

    def common_args(self) -> list[str]:
        config_path = self.tmp / 'config.json'
        config_path.write_text(json.dumps(self.ctx.config), encoding='utf-8')
        return [
            '--home',
            str(self.ctx.paths.home),
            '--config',
            str(config_path),
            '--log-file',
            str(self.log_file),
            '--secrets-file',
            str(self.secrets_file),
        ]

    def run_installer(self, *flags: str) -> None:
        self.exit_code = install.main([*self.common_args(), *flags])
        self.ctx.reload_journal()

    def run_uninstaller(self, *flags: str) -> None:
        self.exit_code = uninstall.main([*self.common_args(), *flags])
        self.ctx.reload_journal()

    def log_text(self) -> str:
        return self.log_file.read_text(encoding='utf-8')

    def fail_first_download(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real = es.download_verified
        failed = {'already': False}

        def download_verified(url: str, dest: Path, sha256: str, log: logging.Logger) -> None:
            if not failed['already']:
                failed['already'] = True
                raise es.IntegrityError(f'{dest.name}: SHA-256 differs from the pinned value (simulated)')
            real(url, dest, sha256, log)

        monkeypatch.setattr(es, 'download_verified', download_verified)

    def calls_with(self, *prefix: str) -> list[list[str]]:
        return [call for call in self.ctx.runner.calls if call[: len(prefix)] == list(prefix)]


@pytest.fixture
def world(machine: FakeContext[FakeMachine], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setattr(install.verify, 'run_checks', lambda _ctx: 0)
    return World(ctx=machine, tmp=tmp_path)


# ----------------------------------------------------------------------------- given


@given('Claude Code is installed outside the local-llm directory')
def claude_code_installed(world: World) -> None:
    world.ctx.runner.cloud['claude'] = 'C:/Program Files/Claude/claude.exe'


@given('the configuration asks for the cloud harnesses')
def cloud_harnesses_wanted(world: World) -> None:
    world.ctx.config['cloud']['install_harnesses'] = True


@given('Ollama is already installed')
def ollama_installed(world: World) -> None:
    world.ctx.runner.install_ollama_files()


@given('the Ollama server rule exists')
def ollama_server_rule_exists(world: World) -> None:
    world.ctx.runner.rules[es.RULE_OLLAMA_SERVER] = {
        'enabled': 'True',
        'direction': 'Outbound',
        'action': 'Block',
        'program': str(world.ctx.paths.ollama_exe),
    }


@given('the first download will fail')
def first_download_fails(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.fail_first_download(monkeypatch)


@given('another opencode binary is in place')
def another_opencode_binary(world: World) -> None:
    exe = world.ctx.paths.opencode_private_exe
    exe.parent.mkdir(parents=True)
    exe.write_bytes(OTHER_OPENCODE_EXE)


@given('a secrets file with an Anthropic key')
def secrets_file(world: World) -> None:
    world.secrets_file.write_text(f'ANTHROPIC_API_KEY={API_KEY}\n', encoding='utf-8')


@given('my own lines in .bashrc')
def own_bashrc(world: World) -> None:
    world.ctx.paths.bashrc.parent.mkdir(parents=True, exist_ok=True)
    world.ctx.paths.bashrc.write_text(MY_BASHRC, encoding='utf-8')


@given('my own value for a variable the installer sets')
def own_variable(world: World) -> None:
    world.ctx.user_env.values[MY_VARIABLE] = '1'


@given('my own key in the Ollama server.json')
def own_server_json(world: World) -> None:
    world.ctx.paths.server_json.parent.mkdir(parents=True, exist_ok=True)
    world.ctx.paths.server_json.write_text('{"a": 1}\n', encoding='utf-8')


@given('my own deny list in the local-llm directory')
def own_deny_list(world: World) -> None:
    world.ctx.paths.local_llm.mkdir(parents=True)
    (world.ctx.paths.local_llm / 'egress-denylist.txt').write_text(MY_DENY_LIST, encoding='utf-8')


# ----------------------------------------------------------------------------- when


@when('I run the installer')
def run_installer(world: World) -> None:
    world.run_installer()


@when(parsers.parse('I run the installer with {flag}'))
def run_installer_with(world: World, flag: str) -> None:
    world.run_installer(flag)


@when('I run the uninstaller with --yes')
def run_uninstaller(world: World) -> None:
    world.run_uninstaller('--yes')


@when('I take a snapshot of the machine')
def take_snapshot(world: World) -> None:
    world.machine_taken = snapshot(world.ctx)
    world.journal_taken = list(world.ctx.journal.entries)


# ----------------------------------------------------------------------------- then


@then('the installer succeeds')
def installer_succeeds(world: World) -> None:
    assert world.exit_code == 0, world.log_text()


@then(parsers.parse('the installer reports an error mentioning "{text}"'))
def installer_reports_error(world: World, text: str) -> None:
    assert world.exit_code == 1
    assert 'ERROR' in world.log_text()
    assert text in world.log_text()


@then('the installer does not install Claude Code')
def claude_not_installed_by_us(world: World) -> None:
    assert not any(CLAUDE_PACKAGE in call for call in world.calls_with('winget', 'install'))


@then('the journal has no entry for Claude Code')
def journal_without_claude(world: World) -> None:
    assert not world.ctx.journal.has('winget-install', id=CLAUDE_PACKAGE)


@then('Claude Code is still installed')
def claude_still_installed(world: World) -> None:
    assert world.ctx.runner.cloud['claude'] == 'C:/Program Files/Claude/claude.exe'
    assert not any(CLAUDE_PACKAGE in call for call in world.calls_with('winget', 'uninstall'))


@then('the installer does not install Ollama')
def ollama_not_installed_by_us(world: World) -> None:
    assert world.calls_with('winget', 'install') == []
    assert not world.ctx.journal.has('winget-install')


@then('Ollama is still installed')
def ollama_still_installed(world: World) -> None:
    assert world.ctx.paths.ollama_exe.exists()
    assert world.calls_with('winget', 'uninstall') == []


@then('the machine is unchanged since the snapshot')
def machine_unchanged(world: World) -> None:
    assert snapshot(world.ctx) == world.machine_taken


@then('the journal is unchanged since the snapshot')
def journal_unchanged(world: World) -> None:
    assert world.ctx.journal.entries == world.journal_taken


@then('the journal is empty')
def journal_empty(world: World) -> None:
    assert world.ctx.journal.entries == []
    assert not world.ctx.paths.journal.exists()


@then('the journal keeps only the Python the uninstaller runs on')
def journal_keeps_python(world: World) -> None:
    assert [entry['kind'] for entry in world.ctx.journal.entries] == ['uv-python-install']


@then('no change is recorded twice')
def no_duplicate_records(world: World) -> None:
    identities = [
        json.dumps(
            {key: value for key, value in entry.items() if key != 'recorded_datetime_local_as_str'}, sort_keys=True
        )
        for entry in world.ctx.journal.entries
    ]
    assert len(identities) == len(set(identities))
    assert identities, 'the first, interrupted run must have recorded the steps that completed'


@then('the opencode binary is unchanged')
def opencode_unchanged(world: World) -> None:
    assert world.ctx.paths.opencode_private_exe.read_bytes() == OTHER_OPENCODE_EXE


@then('the opencode binary is the pinned one')
def opencode_pinned(world: World) -> None:
    assert world.ctx.paths.opencode_private_exe.read_bytes() == FAKE_OPENCODE_EXE
    assert any(path.read_bytes() == OTHER_OPENCODE_EXE for path in world.ctx.paths.journal_backups.iterdir())


@then('the other opencode binary is back')
def opencode_restored(world: World) -> None:
    assert world.ctx.paths.opencode_private_exe.read_bytes() == OTHER_OPENCODE_EXE


@then('the installer has set the variable and the server.json key')
def installer_set_values(world: World) -> None:
    assert world.ctx.user_env.values[MY_VARIABLE] == '0'
    current = json.loads(world.ctx.paths.server_json.read_text(encoding='utf-8'))
    assert current == {'a': 1, 'disable_ollama_cloud': True}


@then('my deny list is unchanged')
def deny_list_unchanged(world: World) -> None:
    assert (world.ctx.paths.local_llm / 'egress-denylist.txt').read_text(encoding='utf-8') == MY_DENY_LIST


@then('my .bashrc, my variable and my server.json are as they were')
def own_things_restored(world: World) -> None:
    assert world.ctx.paths.bashrc.read_text(encoding='utf-8') == MY_BASHRC
    assert world.ctx.user_env.values[MY_VARIABLE] == '1'
    assert json.loads(world.ctx.paths.server_json.read_text(encoding='utf-8')) == {'a': 1}


@then('what the installer added to the local-llm directory is gone')
def installer_files_gone(world: World) -> None:
    local_llm = world.ctx.paths.local_llm
    assert sorted(path.name for path in local_llm.iterdir()) == ['egress-denylist.txt']


@then('the key is in the OpenCode credentials')
def key_in_credentials(world: World) -> None:
    auth = json.loads(world.ctx.paths.opencode_cloud_auth.read_text(encoding='utf-8'))
    assert auth['anthropic'] == {'type': 'api', 'key': API_KEY}


@then('the key is not in the log')
def key_not_in_log(world: World) -> None:
    assert API_KEY not in world.log_text()
    assert 'wrote the Anthropic key into' in world.log_text()
