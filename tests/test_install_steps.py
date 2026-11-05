from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from typing import TYPE_CHECKING

import pytest
from conftest import REPO, FakeContext, fake_download

import envsetup as es
import install

if TYPE_CHECKING:
    from pathlib import Path

    from envtypes import InstallConfigDto


def test_local_llm_step_copies_once(ctx: FakeContext) -> None:
    pending = install.check_local_llm(ctx)
    assert any(action.startswith('copy egress_proxy.py') for action in pending)
    assert 'write confidential-roots.txt from shell.confidential_roots' in pending
    install.do_local_llm(ctx)
    assert install.check_local_llm(ctx) == []
    assert (ctx.paths.local_llm / 'bin' / 'opencode.cmd').exists()
    assert json.loads(ctx.paths.claude_json.read_text(encoding='utf-8')) == {'hasCompletedOnboarding': True}
    roots = ctx.paths.confidential_roots.read_bytes()
    assert b'%USERPROFILE%\\YOUR_CONFIDENTIAL_FOLDER\r\n' in roots
    for directory in ctx.paths.profile_dirs:
        assert directory.is_dir()


def test_local_llm_step_leaves_edited_files_unless_asked(ctx: FakeContext) -> None:
    install.do_local_llm(ctx)
    edited = ctx.paths.local_llm / 'egress-denylist.txt'
    edited.write_text('example.com\n', encoding='utf-8')
    ctx.paths.confidential_roots.write_bytes(b'D:\\secret\r\n')
    assert install.check_local_llm(ctx) == []
    ctx.update_templates = True
    pending = install.check_local_llm(ctx)
    assert pending == ['overwrite egress-denylist.txt (--update-templates)']
    install.do_local_llm(ctx)
    assert not es.files_differ(REPO / 'local-llm' / 'egress-denylist.txt', edited)
    assert ctx.paths.confidential_roots.read_bytes() == b'D:\\secret\r\n'


def test_binaries_step_never_replaces_without_flag(ctx: FakeContext) -> None:
    pending = install.check_binaries(ctx)
    assert len(pending) == 2
    assert all(action.startswith('download') for action in pending)
    for exe in (ctx.paths.opencode_private_exe, ctx.paths.claude_private_exe):
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'not the pinned binary')
    assert install.check_binaries(ctx) == []
    ctx.replace_binaries = True
    pending = install.check_binaries(ctx)
    assert len(pending) == 2
    assert all('--replace-binaries' in action for action in pending)


def test_binaries_step_downloads_verifies_and_keeps_a_backup(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    opencode_exe, claude_exe = b'opencode binary', b'claude binary'
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('opencode.exe', opencode_exe)
    harness = ctx.config['harness']
    harness['opencode']['zip_sha256'] = hashlib.sha256(archive.getvalue()).hexdigest()
    harness['opencode']['exe_sha256'] = hashlib.sha256(opencode_exe).hexdigest()
    harness['claude']['exe_sha256'] = hashlib.sha256(claude_exe).hexdigest()
    payloads = {harness['opencode']['url']: archive.getvalue(), harness['claude']['url']: claude_exe}
    monkeypatch.setattr(es, 'download_verified', fake_download(payloads))
    ctx.paths.claude_private_exe.parent.mkdir(parents=True)
    ctx.paths.claude_private_exe.write_bytes(b'older claude')
    ctx.replace_binaries = True
    install.do_binaries(ctx)
    assert ctx.paths.opencode_private_exe.read_bytes() == opencode_exe
    assert ctx.paths.claude_private_exe.read_bytes() == claude_exe
    backups = list(ctx.paths.journal_backups.iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == b'older claude'
    assert ctx.journal.has('file', path=str(ctx.paths.claude_private_exe), backup=str(backups[0]))
    assert ctx.journal.has('dir-created', path=str(ctx.paths.opencode_private_exe.parent.parent), tree=True)
    assert not ctx.journal.has('file', path=str(ctx.paths.opencode_private_exe))
    assert install.check_binaries(ctx) == []
    harness['claude']['exe_sha256'] = '0' * 64
    ctx.paths.claude_private_exe.unlink()
    with pytest.raises(es.IntegrityError):
        install.do_binaries(ctx)


def test_ollama_config_step(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install, 'restart_ollama', lambda _ctx: None)
    monkeypatch.setattr(install, 'ollama_exe', lambda _ctx: None)
    pending = install.check_ollama_config(ctx)
    assert any('disable_ollama_cloud' in action for action in pending)
    assert any('OLLAMA_FLASH_ATTENTION=1' in action for action in pending)
    ctx.paths.server_json.parent.mkdir(parents=True)
    ctx.paths.server_json.write_text('{"other": 1}', encoding='utf-8')
    install.do_ollama_config(ctx)
    assert json.loads(ctx.paths.server_json.read_text(encoding='utf-8')) == {'other': 1, 'disable_ollama_cloud': True}
    assert ctx.user_env.values == {'OLLAMA_FLASH_ATTENTION': '1', 'OLLAMA_VULKAN': '0'}
    assert install.check_ollama_config(ctx) == []


def test_shell_step(ctx: FakeContext) -> None:
    ctx.user_env.values['Path'] = r'C:\One;C:\Two'
    pending = install.check_shell(ctx)
    assert len(pending) == 2
    install.do_shell(ctx)
    assert install.check_shell(ctx) == []
    assert ctx.user_env.path_list() == [es.WRAPPERS_PATH_ENTRY, r'C:\One', r'C:\Two']
    text = ctx.paths.bashrc.read_text(encoding='utf-8')
    assert 'alias opencode-local=\'"$HOME/local-llm/opencode-local.cmd"\'' in text
    assert ctx.user_env.broadcasts == 1
    ctx.paths.bashrc.write_text(text.replace('gpt-oss-64k', 'x'), encoding='utf-8')
    assert install.check_shell(ctx) == []
    ctx.update_templates = True
    assert len(install.check_shell(ctx)) == 1
    install.do_shell(ctx)
    assert install.check_shell(ctx) == []
    ctx.user_env.values['Path'] = f'C:\\One;{es.WRAPPERS_PATH_ENTRY}'
    assert install.check_shell(ctx) == []


def test_cloud_step_writes_cloud_binaries(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = {'opencode': r'C:\ProgramData\chocolatey\bin\opencode.exe', 'claude': None}
    monkeypatch.setattr(es, 'which_outside', lambda name, _exclude: resolved[name])
    pending = install.check_cloud(ctx)
    assert pending == [f'write {ctx.paths.cloud_binaries_cmd}']
    install.do_cloud(ctx)
    text = ctx.paths.cloud_binaries_cmd.read_bytes()
    assert b'@set "OPENCODE_CLOUD_EXE=C:\\ProgramData\\chocolatey\\bin\\opencode.exe"\r\n' in text
    assert b'@set "CLAUDE_CLOUD_EXE="\r\n' in text
    assert install.check_cloud(ctx) == []


def test_cloud_step_with_secrets_and_existing_auth(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(es, 'which_outside', lambda name, _exclude: f'C:\\bin\\{name}.exe')
    monkeypatch.setattr(install, 'load_secrets', lambda _ctx: {'ANTHROPIC_API_KEY': 'sk-new'})
    ctx.config['cloud']['install_harnesses'] = True
    pending = install.check_cloud(ctx)
    assert any('Anthropic key' in action for action in pending)
    ctx.paths.opencode_cloud_auth.parent.mkdir(parents=True)
    ctx.paths.opencode_cloud_auth.write_text('{"other": {}}', encoding='utf-8')
    install.do_cloud(ctx)
    auth = json.loads(ctx.paths.opencode_cloud_auth.read_text(encoding='utf-8'))
    assert auth == {'other': {}, 'anthropic': {'type': 'api', 'key': 'sk-new'}}
    assert ctx.journal.has('auth-key', path=str(ctx.paths.opencode_cloud_auth))
    assert not ctx.journal.has('file', path=str(ctx.paths.opencode_cloud_auth))
    assert install.check_cloud(ctx) == []
    auth['anthropic']['key'] = 'sk-old'
    ctx.paths.opencode_cloud_auth.write_text(json.dumps(auth), encoding='utf-8')
    assert install.check_cloud(ctx) == []
    assert json.loads(ctx.paths.opencode_cloud_auth.read_text(encoding='utf-8'))['anthropic']['key'] == 'sk-old'
    ctx.paths.opencode_cloud_auth.unlink()
    install.do_cloud(ctx)
    assert ctx.journal.has('file', path=str(ctx.paths.opencode_cloud_auth), backup=None)


def test_cloud_step_installs_missing_harnesses(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(es, 'which_outside', lambda _name, _exclude: None)
    ctx.config['cloud']['install_harnesses'] = True
    pending = install.check_cloud(ctx)
    assert 'winget install SST.opencode' in pending
    assert 'winget install Anthropic.ClaudeCode' in pending
    install.do_cloud(ctx)
    installed = [call[3] for call in ctx.runner.calls if call[:2] == ['winget', 'install']]
    assert installed == ['SST.opencode', 'Anthropic.ClaudeCode']


def test_load_secrets_restricts_broad_acls(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    assert install.load_secrets(ctx) == {}
    ctx.secrets_file = ctx.paths.home / 's.env'
    ctx.secrets_file.parent.mkdir(parents=True)
    ctx.secrets_file.write_text('ANTHROPIC_API_KEY=sk\n', encoding='utf-8')
    monkeypatch.setenv('USERNAME', 'tester')
    answers = iter([{'S-1-1-0'}, set()])
    monkeypatch.setattr(es, 'acl_sids', lambda _runner, _path: next(answers))
    assert install.load_secrets(ctx) == {'ANTHROPIC_API_KEY': 'sk'}
    assert ctx.runner.calls[-1][:2] == ['icacls', str(ctx.secrets_file)]
    ctx.dry_run = True
    monkeypatch.setattr(es, 'acl_sids', lambda _runner, _path: {'S-1-5-32-545'})
    assert install.load_secrets(ctx) == {'ANTHROPIC_API_KEY': 'sk'}
    ctx.dry_run = False
    with pytest.raises(es.ConfigError, match='still readable'):
        install.load_secrets(ctx)


def test_firewall_check_uses_rule_queries(ctx: FakeContext) -> None:
    runner = ctx.runner
    good = json.dumps(
        {'enabled': 'True', 'direction': 'Outbound', 'action': 'Block', 'program': str(ctx.paths.opencode_private_exe)}
    )
    runner.answer_powershell(lambda script: es.RULE_OPENCODE in script, good)
    runner.answer_powershell(lambda script: f"'{es.RULE_CLAUDE}'" in script, good)
    pending = install.check_firewall(ctx)
    assert len(pending) == 3
    assert not any(es.RULE_OPENCODE in action for action in pending)
    install.do_firewall(ctx)
    added = [script for script in runner.scripts if script.startswith('New-NetFirewallRule')]
    assert len(added) == 3
    assert not any(es.RULE_OPENCODE in script for script in added)


def test_ollama_check_skips_existing_install(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install.shutil, 'which', lambda _command: None)
    ctx.paths.ollama_exe.parent.mkdir(parents=True)
    ctx.paths.ollama_exe.write_bytes(b'')
    ctx.runner.answer(lambda args: args[-1] == '--version', 'ollama version is 0.40.13')
    assert install.check_ollama(ctx) == []
    ctx.paths.ollama_exe.unlink()
    pending = install.check_ollama(ctx)
    assert pending == ['winget install Ollama.Ollama --version 0.40.14']
    install.do_ollama(ctx)
    assert ctx.runner.calls[-1][:4] == ['winget', 'install', '--id', 'Ollama.Ollama']


def test_python_step(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install.shutil, 'which', lambda _command: None)
    assert install.check_python(ctx) == ['uv python install 3.14 (uv is not on PATH)']
    monkeypatch.setattr(install.shutil, 'which', lambda _command: 'C:/uv.exe')
    ctx.runner.answer(lambda args: args[:3] == ['uv', 'python', 'find'], '', 2)
    assert install.check_python(ctx) == ['uv python install 3.14']
    install.do_python(ctx)
    assert ctx.runner.calls[-1] == ['uv', 'python', 'install', '3.14']
    ctx.runner.handlers.clear()
    assert install.check_python(ctx) == []


def test_models_check_lists_missing_tags(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx.paths.ollama_exe.parent.mkdir(parents=True)
    ctx.paths.ollama_exe.write_bytes(b'')
    listing = 'NAME ID SIZE MODIFIED\nqwen3-coder:30b abc 18 GB now\ngpt-oss-64k:latest b1 13 GB now\n'
    ctx.runner.answer(lambda args: args[-1] == 'list', listing)
    monkeypatch.setattr(install, 'ensure_server', lambda _ctx: True)
    assert install.check_models(ctx) == ['ollama create qwen3-coder-32k']
    ctx.config['models']['install_optional'] = True
    assert install.check_models(ctx) == ['ollama create qwen3-coder-32k', 'ollama pull gpt-oss:20b']
    monkeypatch.setattr(install, 'ensure_server', lambda _ctx: False)
    with pytest.raises(RuntimeError, match='not running'):
        install.check_models(ctx)
    monkeypatch.setattr(install, 'ollama_exe', lambda _ctx: None)
    assert len(install.check_models(ctx)) == 4


def test_models_do_pulls_and_creates(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx.paths.ollama_exe.parent.mkdir(parents=True)
    ctx.paths.ollama_exe.write_bytes(b'')
    exe = str(ctx.paths.ollama_exe)
    listings = {'count': 0}

    def after_the_copy(args: list[str]) -> bool:
        if args[-1] != 'list':
            return False
        listings['count'] += 1
        return listings['count'] >= 2

    ctx.runner.answer(after_the_copy, 'NAME ID SIZE MODIFIED\ncopied:latest a 1 GB now\n')
    ctx.runner.answer(lambda args: args[-1] == 'list', 'NAME ID SIZE MODIFIED\n')
    monkeypatch.setattr(install, 'restart_ollama', lambda _ctx: None)
    ctx.models_from = ctx.paths.home / 'models-from'
    install.do_models(ctx)
    commands = [call[1:] for call in ctx.runner.calls if call[0] == exe]
    assert ['pull', 'qwen3-coder:30b'] in commands
    assert ['create', 'qwen3-coder-32k', '-f', str(REPO / 'local-llm' / 'qwen3-coder-32k.Modelfile')] in commands
    assert any(call[0] == 'robocopy' for call in ctx.runner.calls)
    assert [entry['tag'] for entry in ctx.journal.entries if entry['kind'] == 'model'] == [
        'copied:latest',
        'qwen3-coder:30b',
        'qwen3-coder-32k:latest',
    ]
    assert ctx.journal.has('dir-created', path=str(ctx.paths.ollama_home), tree=False)
    ctx.runner.answer_powershell(lambda script: es.RULE_OLLAMA_SERVER in script, '{"enabled": "True"}')
    with pytest.raises(RuntimeError, match=r'ollama-pull.cmd'):
        install.do_models(ctx)


def test_app_settings_step(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    assert install.check_app_settings(ctx) == []
    ctx.paths.app_db.parent.mkdir(parents=True)
    connection = sqlite3.connect(ctx.paths.app_db)
    connection.execute('CREATE TABLE settings (auto_update_enabled INTEGER, airplane_mode INTEGER)')
    connection.execute('INSERT INTO settings VALUES (1, 0)')
    connection.commit()
    connection.close()
    assert len(install.check_app_settings(ctx)) == 2
    monkeypatch.setattr(es, 'stop_ollama', lambda _runner: None)
    monkeypatch.setattr(install, 'restart_ollama', lambda _ctx: None)
    install.do_app_settings(ctx)
    assert install.check_app_settings(ctx) == []


def test_ensure_server_and_restart(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(es, 'api_version', lambda: '0.40.14')
    assert install.ensure_server(ctx)
    monkeypatch.setattr(es, 'api_version', lambda: None)
    assert not install.ensure_server(ctx)
    ctx.paths.ollama_app_exe.parent.mkdir(parents=True)
    ctx.paths.ollama_app_exe.write_bytes(b'')
    started: list[Path] = []
    monkeypatch.setattr(es, 'start_ollama_app', lambda app_exe, _env: started.append(app_exe))
    monkeypatch.setattr(es, 'wait_for_api', lambda _seconds: '0.40.14')
    assert install.ensure_server(ctx)
    assert started == [ctx.paths.ollama_app_exe]
    monkeypatch.setattr(es, 'stop_ollama', lambda _runner: None)
    install.restart_ollama(ctx)
    monkeypatch.setattr(es, 'wait_for_api', lambda _seconds: None)
    with pytest.raises(RuntimeError, match='did not answer'):
        install.restart_ollama(ctx)
    ctx.paths.ollama_app_exe.unlink()
    install.restart_ollama(ctx)
    monkeypatch.setattr(install.shutil, 'which', lambda _command: None)
    with pytest.raises(RuntimeError, match=r'ollama.exe not found'):
        install.ollama_run(ctx, 'list')


def test_second_run_performs_no_actions(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(es, 'which_outside', lambda _name, _exclude: None)
    for step in (install.USER_STEPS[5], install.USER_STEPS[7], install.USER_STEPS[8]):
        install.run_step(ctx, step)
    for step in (install.USER_STEPS[5], install.USER_STEPS[7], install.USER_STEPS[8]):
        assert step.check(ctx) == [], step.name


def test_run_step_dry_run_and_incomplete_step(ctx: FakeContext) -> None:
    ctx.dry_run = True
    install.run_step(ctx, install.USER_STEPS[5])
    assert not ctx.paths.local_llm.exists()
    ctx.dry_run = False
    broken = install.Step('firewall', lambda _ctx: ['always pending'], lambda _ctx: None)
    with pytest.raises(RuntimeError, match='still pending'):
        install.run_step(ctx, broken)


def test_selected_and_parse_names(ctx: FakeContext) -> None:
    assert install.parse_names(' shell, cloud ') == {'shell', 'cloud'}
    with pytest.raises(es.ConfigError, match='unknown step'):
        install.parse_names('nope')
    assert install.selected(ctx, 'shell')
    ctx.only = {'shell'}
    assert install.selected(ctx, 'shell')
    assert not install.selected(ctx, 'cloud')
    ctx.only = set()
    ctx.skip = {'shell'}
    assert not install.selected(ctx, 'shell')


def test_confidential_roots_text_and_cloud_text() -> None:
    assert install.confidential_roots_text(['C:\\a']).endswith('C:\\a\r\n')
    assert install.cloud_binaries_text({'x': None}).endswith('@set "X_CLOUD_EXE="\r\n')
    assert isinstance(install.read_json(REPO / 'install.config.json'), dict)
    assert install.read_json(REPO / 'missing.json') is None


def test_config_fixture_is_fresh(config: InstallConfigDto) -> None:
    assert config['models']['install_optional'] is False
