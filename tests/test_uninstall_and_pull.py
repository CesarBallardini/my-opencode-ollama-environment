"""uninstall.py reverts exactly what the journal records; ollama_pull.py against a faked machine."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest
from conftest import FakeContext, FakeRunner, FakeUserEnv

import envsetup as es
import install
import ollama_pull
import uninstall

JOURNALED_STEPS = ('ollama-config', 'local-llm', 'shell', 'cloud')


@pytest.fixture
def machine(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> FakeContext:
    """A context whose steps touch no real program: no cloud binaries, no Ollama to restart."""
    monkeypatch.setattr(es, 'which_outside', lambda _name, _exclude: None)
    monkeypatch.setattr(install, 'restart_ollama', lambda _ctx: None)
    monkeypatch.setattr(install, 'ollama_exe', lambda _ctx: None)
    ctx.user_env.values['Path'] = r'C:\One'
    return ctx


def _install_user_steps(ctx: FakeContext) -> None:
    for step in install.USER_STEPS:
        if step.name in JOURNALED_STEPS:
            install.run_step(ctx, step)


def test_fresh_install_is_reverted_completely(machine: FakeContext) -> None:
    ctx = machine
    _install_user_steps(ctx)
    kinds = sorted({entry['kind'] for entry in ctx.journal.entries})
    assert kinds == ['bashrc-block', 'dir-created', 'file', 'user-env', 'user-path']
    assert ctx.journal.has('bashrc-block', path=str(ctx.paths.bashrc), created=True, previous_block=None)
    assert ctx.journal.has('dir-created', path=str(ctx.paths.ollama_home), tree=False)
    assert ctx.journal.has('dir-created', path=str(ctx.paths.local_llm), tree=True)
    assert ctx.journal.has('file', path=str(ctx.paths.server_json), backup=None)
    assert not ctx.journal.has('file', path=str(ctx.paths.cloud_binaries_cmd))
    assert uninstall.user_phase(ctx, apply=False) == (0, 0)
    assert ctx.paths.local_llm.exists()
    read_only = ctx.paths.local_llm / 'opencode' / 'data' / 'locked.txt'
    read_only.write_text('x', encoding='utf-8')
    read_only.chmod(0o444)
    assert uninstall.user_phase(ctx, apply=True) == (7, 0)
    assert not ctx.paths.local_llm.exists()
    assert not ctx.paths.ollama_home.exists()
    assert not ctx.paths.bashrc.exists()
    assert ctx.user_env.values == {'Path': r'C:\One'}
    assert ctx.user_env.broadcasts == 3
    assert ctx.journal.entries == []
    assert not ctx.paths.journal.exists()


def test_update_templates_backs_up_what_it_replaces(machine: FakeContext) -> None:
    ctx = machine
    ctx.paths.local_llm.mkdir(parents=True)
    edited = ctx.paths.local_llm / 'egress-denylist.txt'
    edited.write_text('mine\n', encoding='utf-8')
    old_block = f'{es.BASHRC_BEGIN}\nalias old=1\n{es.BASHRC_END}\n'
    ctx.paths.bashrc.write_text('# top\n\n' + old_block, encoding='utf-8')
    ctx.update_templates = True
    install.do_local_llm(ctx)
    install.do_shell(ctx)
    assert edited.read_text(encoding='utf-8') != 'mine\n'
    assert es.block_state(ctx.paths.bashrc.read_text(encoding='utf-8'), install.bashrc_block(ctx)) == 'same'
    assert ctx.journal.has('bashrc-block', path=str(ctx.paths.bashrc), created=False, previous_block=old_block)
    uninstall.user_phase(ctx, apply=True)
    assert edited.read_text(encoding='utf-8') == 'mine\n'
    assert ctx.paths.bashrc.read_text(encoding='utf-8') == '# top\n\n' + old_block


def test_packages_models_settings_and_rules(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    db = ctx.paths.app_db
    db.parent.mkdir(parents=True)
    connection = sqlite3.connect(db)
    connection.execute('CREATE TABLE settings (auto_update_enabled INTEGER, airplane_mode INTEGER)')
    connection.execute('INSERT INTO settings VALUES (0, 1)')
    connection.commit()
    connection.close()
    auth = ctx.paths.opencode_cloud_auth
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({'other': {}, 'anthropic': {'type': 'api', 'key': 'sk'}}), encoding='utf-8')
    journal = ctx.journal
    journal.add({'kind': 'winget-install', 'id': 'Ollama.Ollama'})
    journal.add({'kind': 'uv-python-install', 'version': '3.14'})
    journal.add({'kind': 'app-settings', 'db': str(db), 'previous': {'auto_update_enabled': 1, 'airplane_mode': 0}})
    journal.add({'kind': 'model', 'tag': 'qwen3-coder:30b'})
    journal.add({'kind': 'winget-install', 'id': 'SST.opencode'})
    journal.add({'kind': 'auth-key', 'path': str(auth)})
    journal.add({'kind': 'firewall-rule', 'name': es.RULE_OPENCODE})
    stopped: list[int] = []
    monkeypatch.setattr(es, 'stop_ollama', lambda _runner: stopped.append(1))
    monkeypatch.setattr(install, 'ollama_exe', lambda _ctx: 'ollama.exe')
    monkeypatch.setattr(install, 'ensure_server', lambda _ctx: True)
    ctx.keep_models = True
    assert uninstall.user_phase(ctx, apply=True) == (4, 2)
    assert [entry['kind'] for entry in journal.entries] == ['uv-python-install', 'model', 'firewall-rule']
    uninstalled = [call[3] for call in ctx.runner.calls if call[:2] == ['winget', 'uninstall']]
    assert uninstalled == ['SST.opencode', 'Ollama.Ollama']
    assert len(stopped) == 2
    assert es.app_settings_read(db, ['auto_update_enabled', 'airplane_mode']) == {
        'auto_update_enabled': 1,
        'airplane_mode': 0,
    }
    assert json.loads(auth.read_text(encoding='utf-8')) == {'other': {}}
    ctx.keep_models = False
    assert uninstall.user_phase(ctx, apply=True) == (1, 1)
    assert ['ollama.exe', 'rm', 'qwen3-coder:30b'] in ctx.runner.calls
    assert uninstall.admin_phase(ctx) == (1, 0)
    assert ctx.runner.scripts[-1].startswith(f"Remove-NetFirewallRule -DisplayName '{es.RULE_OPENCODE}'")
    assert [entry['kind'] for entry in journal.entries] == ['uv-python-install']


def test_reverts_cope_with_what_vanished_or_changed_since(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    home = ctx.paths.home
    home.mkdir(parents=True)
    assert uninstall.revert_file(ctx, {'kind': 'file', 'path': str(home / 'f'), 'backup': str(home / 'nope')})
    (home / 'd' / 'x').mkdir(parents=True)
    assert uninstall.revert_dir_created(ctx, {'kind': 'dir-created', 'path': str(home / 'd'), 'tree': False})
    assert (home / 'd' / 'x').is_dir()
    assert uninstall.revert_dir_created(ctx, {'kind': 'dir-created', 'path': str(home / 'gone'), 'tree': True})
    assert uninstall.revert_bashrc_block(
        ctx, {'kind': 'bashrc-block', 'path': str(home / '.bashrc'), 'created': False, 'previous_block': None}
    )
    assert uninstall.revert_app_settings(ctx, {'kind': 'app-settings', 'db': str(home / 'db'), 'previous': {}})
    auth = home / 'auth.json'
    assert uninstall.revert_auth_key(ctx, {'kind': 'auth-key', 'path': str(auth)})
    auth.write_text(json.dumps({'anthropic': {'key': 'changed'}}), encoding='utf-8')
    ctx.secrets_file = home / 's.env'
    ctx.secrets_file.write_text('ANTHROPIC_API_KEY=sk\n', encoding='utf-8')
    assert uninstall.revert_auth_key(ctx, {'kind': 'auth-key', 'path': str(auth)})
    assert json.loads(auth.read_text(encoding='utf-8')) == {'anthropic': {'key': 'changed'}}
    monkeypatch.setattr(install, 'ollama_exe', lambda _ctx: None)
    assert uninstall.revert_model(ctx, {'kind': 'model', 'tag': 't'})
    monkeypatch.setattr(install, 'ollama_exe', lambda _ctx: 'ollama.exe')
    monkeypatch.setattr(install, 'ensure_server', lambda _ctx: False)
    assert not uninstall.revert_model(ctx, {'kind': 'model', 'tag': 't'})
    assert sum(record.levelno == logging.WARNING for record in caplog.records) == 6


def test_user_phase_restarts_ollama_when_it_is_still_installed(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx.journal.add({'kind': 'user-env', 'name': 'OLLAMA_VULKAN', 'previous': None})
    ctx.paths.ollama_app_exe.parent.mkdir(parents=True)
    ctx.paths.ollama_app_exe.write_bytes(b'')
    restarted: list[int] = []
    monkeypatch.setattr(install, 'restart_ollama', lambda _ctx: restarted.append(1))
    assert uninstall.user_phase(ctx, apply=True) == (1, 0)
    assert restarted == [1]
    assert ctx.user_env.broadcasts == 1

    def failing(_ctx: es.Context) -> None:
        raise RuntimeError('did not answer')

    ctx.journal.add({'kind': 'user-env', 'name': 'OLLAMA_VULKAN', 'previous': None})
    monkeypatch.setattr(install, 'restart_ollama', failing)
    assert uninstall.user_phase(ctx, apply=True) == (1, 0)


def test_remove_rules_inline_or_through_uac(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx.journal.add({'kind': 'firewall-rule', 'name': es.RULE_RUNNER})
    monkeypatch.setattr(es, 'is_admin', lambda: True)
    uninstall.remove_rules(ctx, uninstall.parse_args([]), ctx.paths.home / 'u.log')
    assert ctx.runner.scripts[-1].startswith(f"Remove-NetFirewallRule -DisplayName '{es.RULE_RUNNER}'")
    assert ctx.journal.entries == []
    ctx.journal.add({'kind': 'firewall-rule', 'name': es.RULE_RUNNER})
    monkeypatch.setattr(es, 'is_admin', lambda: False)
    elevated: list[list[str]] = []

    def child(args: list[str], _cwd: Path) -> int:
        elevated.append(args)
        journal = es.Journal(ctx.paths.journal)  # the elevated child reverts and rewrites the journal
        journal.remove(journal.entries[0])
        return 0

    monkeypatch.setattr(es, 'run_elevated', child)
    uninstall.remove_rules(ctx, uninstall.parse_args(['--home', 'H']), Path('L'))
    assert elevated[0][3:] == [
        '--phase',
        'admin',
        '--home',
        'H',
        '--config',
        str(es.REPO / 'install.config.json'),
        '--log-file',
        'L',
    ]
    assert ctx.journal.entries == []
    ctx.journal.add({'kind': 'firewall-rule', 'name': es.RULE_RUNNER})
    monkeypatch.setattr(es, 'run_elevated', lambda _args, _cwd: 1)
    with pytest.raises(RuntimeError, match='exited with code 1'):
        uninstall.remove_rules(ctx, uninstall.parse_args([]), Path('L'))
    monkeypatch.setattr(es, 'run_elevated', lambda _args, _cwd: 0)
    with pytest.raises(RuntimeError, match='still in the journal'):
        uninstall.remove_rules(ctx, uninstall.parse_args([]), Path('L'))


def test_uninstall_main(
    machine: FakeContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ctx = machine
    runner = FakeRunner()
    monkeypatch.setattr(es, 'Runner', lambda _log: runner)
    monkeypatch.setattr(es, 'UserEnv', lambda: ctx.user_env)
    args = ['--home', str(ctx.paths.home), '--log-file', str(tmp_path / 'u.log')]
    assert uninstall.main(args) == 0
    assert 'nothing to revert' in capsys.readouterr().out
    _install_user_steps(ctx)
    assert uninstall.main(args) == 2
    assert 'none recorded' in capsys.readouterr().out
    ctx.journal.add({'kind': 'firewall-rule', 'name': es.RULE_CLAUDE})
    assert uninstall.main(args) == 2
    out = capsys.readouterr().out
    assert f"remove the firewall rule '{es.RULE_CLAUDE}'" in out
    assert f'delete the directory {ctx.paths.local_llm} and everything in it' in out
    assert 'nothing reverted' in out
    assert ctx.paths.local_llm.exists()
    monkeypatch.setattr(es, 'is_admin', lambda: True)
    assert uninstall.main([*args, '--yes']) == 0
    out = capsys.readouterr().out
    assert 'uninstall finished: 8 entries reverted, 0 kept' in out
    assert not ctx.paths.local_llm.exists()
    assert not ctx.paths.state_dir.exists()
    assert any(script.startswith('Remove-NetFirewallRule') for script in runner.scripts)
    monkeypatch.setattr(es, 'is_admin', lambda: False)
    assert uninstall.main([*args, '--phase', 'admin']) == 1
    monkeypatch.setattr(es, 'is_admin', lambda: True)
    assert uninstall.main([*args, '--phase', 'admin']) == 0
    assert uninstall.main([*args, '--config', str(tmp_path / 'absent.json')]) == 1


def test_pull_with_rule_lifted(ctx: FakeContext) -> None:
    runner = ctx.runner
    runner.answer_powershell(lambda script: 'Get-NetFirewallRule' in script, '{"enabled": "True"}')
    runner.answer(lambda args: args[1:] == ['pull', 'bad:tag'], '', 1)
    assert ollama_pull.pull_with_rule_lifted(runner, 'ollama.exe', ['good:tag', 'bad:tag'], ctx.log) == 1
    toggles = [
        script
        for script in runner.scripts
        if script.endswith(f"-NetFirewallRule -DisplayName '{es.RULE_OLLAMA_SERVER}'")
    ]
    assert toggles == [
        f"Disable-NetFirewallRule -DisplayName '{es.RULE_OLLAMA_SERVER}'",
        f"Enable-NetFirewallRule -DisplayName '{es.RULE_OLLAMA_SERVER}'",
    ]
    runner.powershell_handlers.clear()
    runner.handlers.clear()
    runner.scripts.clear()
    assert ollama_pull.pull_with_rule_lifted(runner, 'ollama.exe', ['good:tag'], ctx.log) == 0
    assert not any(script.startswith(('Disable-', 'Enable-')) for script in runner.scripts)


def test_ollama_pull_main(
    ctx: FakeContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ['x:tag', '--home', str(ctx.paths.home), '--log-file', str(tmp_path / 'p.log')]
    monkeypatch.setattr(es, 'is_admin', lambda: False)
    elevated: list[list[str]] = []
    monkeypatch.setattr(es, 'run_elevated', lambda child, _cwd: elevated.append(child) or 0)
    assert ollama_pull.main(args) == 0
    assert elevated[0][2:] == [
        str(es.REPO / 'ollama_pull.py'),
        'x:tag',
        '--home',
        str(ctx.paths.home),
        '--log-file',
        str(tmp_path / 'p.log'),
        '--elevated-child',
    ]

    def declined(_child: list[str], _cwd: Path) -> int:
        raise es.ElevationError('the UAC request was declined')

    monkeypatch.setattr(es, 'run_elevated', declined)
    assert ollama_pull.main(args) == 1
    assert 'declined' in capsys.readouterr().out
    monkeypatch.setattr(es, 'is_admin', lambda: True)
    monkeypatch.setattr(ollama_pull.shutil, 'which', lambda _command: None)
    prompts: list[str] = []
    monkeypatch.setattr('builtins.input', prompts.append)
    assert ollama_pull.main([*args, '--elevated-child']) == 1
    assert prompts == ['Press Enter to close this window']
    ctx.paths.ollama_exe.parent.mkdir(parents=True)
    ctx.paths.ollama_exe.write_bytes(b'')
    runner = FakeRunner()
    monkeypatch.setattr(es, 'Runner', lambda _log: runner)
    assert ollama_pull.main(args) == 0
    assert runner.calls[-1] == [str(ctx.paths.ollama_exe), 'pull', 'x:tag']
    assert isinstance(FakeUserEnv(), es.UserEnv)
