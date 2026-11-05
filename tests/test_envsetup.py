from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import logging
import sqlite3
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import PINNED, REPO, FakeRunner, FakeUserEnv, nested, raw_config

import envsetup as es

if TYPE_CHECKING:
    from collections.abc import Iterator

    from envtypes import FirewallRuleDto, InstallConfigDto, JsonObject, JsonValue


def test_config_loads_and_validates(config: InstallConfigDto) -> None:
    assert config['harness']['opencode']['version'] == '1.18.34'
    assert es.required_tags(config) == ['qwen3-coder:30b', 'qwen3-coder-32k']
    config['models']['install_optional'] = True
    assert es.required_tags(config) == ['qwen3-coder:30b', 'qwen3-coder-32k', 'gpt-oss:20b', 'gpt-oss-64k']


def write_json(path: Path, document: JsonObject) -> Path:
    path.write_text(json.dumps(document), encoding='utf-8')
    return path


def test_config_rejects_unknown_key(tmp_path: Path) -> None:
    config = raw_config()
    config['unexpected'] = 1
    with pytest.raises(es.ConfigError, match='unknown key'):
        es.load_config(write_json(tmp_path / 'c.json', config))


@pytest.mark.parametrize(
    ('keys', 'value', 'message'),
    [
        (('models', 'install_optional'), 1, 'models.install_optional: expected true or false'),
        (('minimum_free_gb',), True, 'minimum_free_gb: expected an integer'),
        (('ollama', 'version'), 1, 'ollama.version: expected str'),
        (('ollama',), 'not an object', 'ollama: expected an object'),
        (('models', 'pull'), 'qwen3-coder:30b', 'models.pull: expected an array'),
        (('models', 'pull'), ['qwen3-coder:30b', 7], r'models.pull\[1\]: expected str'),
        (('ollama', 'user_env'), [], 'ollama.user_env: expected an object'),
        (('ollama', 'user_env'), {'OLLAMA_VULKAN': 0}, 'ollama.user_env.OLLAMA_VULKAN: expected str'),
        (
            ('ollama', 'app_settings'),
            {'airplane_mode': True},
            'ollama.app_settings.airplane_mode: expected an integer',
        ),
        (('ollama', 'server_json'), {'nested': {}}, 'ollama.server_json.nested: expected'),
        (('cloud', 'winget'), {'opencode': None}, 'cloud.winget.opencode: expected str'),
    ],
)
def test_config_rejects_wrong_types(tmp_path: Path, keys: tuple[str, ...], value: JsonValue, message: str) -> None:
    config = raw_config()
    nested(config, *keys[:-1])[keys[-1]] = value
    with pytest.raises(es.ConfigError, match=message):
        es.load_config(write_json(tmp_path / 'c.json', config))


def test_config_rejects_missing_key_and_unreadable_file(tmp_path: Path) -> None:
    config = raw_config()
    del nested(config, 'firewall')['block_ollama_server_and_app']
    with pytest.raises(es.ConfigError, match='missing key'):
        es.load_config(write_json(tmp_path / 'c.json', config))
    with pytest.raises(es.ConfigError, match='cannot read'):
        es.load_config(tmp_path / 'absent.json')


def test_read_secrets(tmp_path: Path) -> None:
    path = tmp_path / 'secrets.env'
    path.write_text('# comment\n\nANTHROPIC_API_KEY = sk-test \nOTHER=x=y\n', encoding='utf-8')
    assert es.read_secrets(path) == {'ANTHROPIC_API_KEY': 'sk-test', 'OTHER': 'x=y'}
    path.write_text('not a pair\n', encoding='utf-8')
    with pytest.raises(es.ConfigError):
        es.read_secrets(path)


def test_secrets_example_parses() -> None:
    assert es.read_secrets(REPO / 'secrets.env.example') == {'ANTHROPIC_API_KEY': ''}


def test_bashrc_block_is_idempotent() -> None:
    block = (REPO / 'home' / 'bashrc.local-llm').read_text(encoding='utf-8')
    original = "export FOO=1\nalias ll='ls -l'\n"
    assert es.block_state(original, block) == 'absent'
    once = es.block_apply(original, block)
    assert es.block_state(once, block) == 'same'
    assert es.block_apply(once, block) == once
    changed = once.replace('gpt-oss-64k', 'other')
    assert es.block_state(changed, block) == 'different'
    assert es.block_apply(changed, block) == once
    assert es.block_remove(once) == original
    assert es.block_remove(original) == original
    assert es.block_remove(es.block_apply('', block)) == ''


def test_path_list_helpers() -> None:
    entries = [r'C:\Tools', r'%USERPROFILE%\local-llm\bin\\', r'C:\Other']
    assert es.path_position(entries, es.WRAPPERS_PATH_ENTRY) == 1
    assert es.path_position(entries, r'%userprofile%\LOCAL-LLM\bin') == 1
    prepended = es.path_prepend(entries, es.WRAPPERS_PATH_ENTRY)
    assert prepended == [es.WRAPPERS_PATH_ENTRY, r'C:\Tools', r'C:\Other']
    assert es.path_prepend(prepended, es.WRAPPERS_PATH_ENTRY) == prepended
    assert es.path_remove(prepended, es.WRAPPERS_PATH_ENTRY) == [r'C:\Tools', r'C:\Other']
    assert es.path_position([], es.WRAPPERS_PATH_ENTRY) is None


def test_user_env_path_helpers_use_get_and_set() -> None:
    env = FakeUserEnv({'Path': r'C:\A;;C:\B'})
    assert env.path_list() == [r'C:\A', r'C:\B']
    env.set_path_list([r'C:\B'])
    assert env.values['Path'] == r'C:\B'


def test_sha256_and_files_differ(tmp_path: Path) -> None:
    a, b, c = tmp_path / 'a', tmp_path / 'b', tmp_path / 'c'
    a.write_bytes(b'abc')
    b.write_bytes(b'abc')
    c.write_bytes(b'abd')
    assert es.sha256_file(a) == 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    assert not es.files_differ(a, b)
    assert es.files_differ(a, c)
    c.write_bytes(b'longer')
    assert es.files_differ(a, c)


def test_template_files() -> None:
    files = {path.as_posix() for path in es.template_files(REPO)}
    assert 'egress_proxy.py' in files
    assert 'egress-denylist.txt' in files
    assert 'bin/opencode.cmd' in files
    assert 'opencode/config/opencode/opencode.json' in files
    assert 'claude/config/settings.json' in files
    assert 'claude/config/.claude.json.template' not in files
    assert not any(name.startswith('opencode/data') for name in files)


def test_denylist_parses_with_the_proxy_loader() -> None:
    spec = importlib.util.spec_from_file_location('repo_egress_proxy', REPO / 'local-llm' / 'egress_proxy.py')
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entries = module.denied_domains()
    assert len(entries) == 500
    assert module.is_denied('api.openai.com')
    assert module.is_denied('huggingface.co')
    assert not module.is_denied('github.com')
    assert not module.is_denied('docs.python.org')


def test_ollama_list_parsing_normalises_latest() -> None:
    stdout = 'NAME  ID  SIZE  MODIFIED\nqwen3-coder-32k:latest  c6  18 GB  now\nqwen3-coder:30b  06  18 GB  now\n'
    present = es.parse_ollama_list(stdout)
    assert present == {'qwen3-coder-32k:latest', 'qwen3-coder:30b'}
    assert es.missing_tags(['qwen3-coder:30b', 'qwen3-coder-32k', 'gpt-oss:20b'], present) == ['gpt-oss:20b']
    assert es.normalize_tag('x') == 'x:latest'
    assert es.normalize_tag('x:1') == 'x:1'


def test_firewall_rules_for(config: InstallConfigDto) -> None:
    paths = es.Paths(REPO / 'fakehome')
    rules = es.firewall_rules_for(config, paths)
    assert set(rules) == {es.RULE_OPENCODE, es.RULE_CLAUDE, es.RULE_RUNNER, es.RULE_OLLAMA_SERVER, es.RULE_OLLAMA_APP}
    assert rules[es.RULE_OLLAMA_APP].name == 'ollama app.exe'
    config['firewall']['block_ollama_server_and_app'] = False
    assert len(es.firewall_rules_for(config, paths)) == 3


def test_rule_matches() -> None:
    program = REPO / 'x.exe'
    good: FirewallRuleDto = {
        'enabled': 'True',
        'direction': 'Outbound',
        'action': 'Block',
        'program': str(program).upper(),
    }
    assert es.rule_matches(good, program)
    assert not es.rule_matches({**good, 'enabled': 'False'}, program)
    assert not es.rule_matches(None, program)


def test_firewall_helpers_build_the_expected_scripts() -> None:
    runner = FakeRunner()
    runner.answer_powershell(lambda script: 'present' in script, '{"enabled": "True"}')
    assert es.firewall_rule(runner, 'present') == {'enabled': 'True'}
    assert es.firewall_rule(runner, 'absent') is None
    es.firewall_add(runner, 'r', Path('C:/x.exe'))
    es.firewall_set_enabled(runner, 'r', False)
    es.firewall_set_enabled(runner, 'r', True)
    es.firewall_remove(runner, 'r')
    tail = runner.scripts[-4:]
    assert tail[0].startswith("New-NetFirewallRule -DisplayName 'r' -Direction Outbound -Action Block")
    assert tail[1] == "Disable-NetFirewallRule -DisplayName 'r'"
    assert tail[2] == "Enable-NetFirewallRule -DisplayName 'r'"
    assert tail[3].startswith("Remove-NetFirewallRule -DisplayName 'r'")


def test_listening_addresses_and_acl_helpers() -> None:
    runner = FakeRunner()
    runner.answer_powershell(lambda script: 'Get-NetTCPConnection' in script, '::1\r\n127.0.0.1\r\n127.0.0.1\r\n')
    assert es.listening_addresses(runner, 11434) == ['127.0.0.1', '::1']
    runner.answer_powershell(lambda script: 'Get-Acl' in script, 'S-1-1-0\nS-1-5-21-1-2-3-1001\n')
    sids = es.acl_sids(runner, Path('C:/s.env'))
    assert es.broad_acl_entries(sids) == ['Everyone']
    es.restrict_acl(runner, Path('C:/s.env'), 'cesar')
    assert runner.calls[-1] == [
        'icacls',
        'C:\\s.env',
        '/inheritance:r',
        '/remove:g',
        '*S-1-1-0',
        '*S-1-5-11',
        '*S-1-5-32-545',
        '/grant:r',
        'cesar:(R,W)',
    ]


def test_winget_install_and_robocopy_and_ollama_helpers() -> None:
    runner = FakeRunner()
    es.winget_install(runner, 'Ollama.Ollama', '0.40.14')
    assert runner.calls[-1][:4] == ['winget', 'install', '--id', 'Ollama.Ollama']
    assert '--version' in runner.calls[-1]
    runner.answer(lambda args: args[0] == 'robocopy', '', 1)
    es.robocopy(runner, Path('C:/src'), Path('C:/dst'))
    runner.handlers.clear()
    runner.answer(lambda args: args[0] == 'robocopy', 'failed', 16)
    with pytest.raises(es.CommandError):
        es.robocopy(runner, Path('C:/src'), Path('C:/dst'))
    runner.answer(lambda args: args[:2] == ['ollama', 'show'], 'FROM x\nPARAMETER num_ctx 32768\n')
    assert 'num_ctx' in es.ollama_modelfile(runner, 'qwen3-coder-32k')


def test_ollama_version_and_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeRunner()
    monkeypatch.setattr(es.shutil, 'which', lambda _command: None)
    assert es.ollama_version(runner) is None
    monkeypatch.setattr(es.shutil, 'which', lambda _command: 'C:/ollama.exe')
    runner.answer(lambda args: args == ['ollama', '--version'], 'ollama version is 0.40.14')
    assert es.ollama_version(runner) == '0.40.14'
    monkeypatch.setattr(es, 'api_version', lambda _timeout=3: None)
    es.stop_ollama(runner)
    assert ['taskkill', '/F', '/IM', 'ollama.exe'] in runner.calls


def test_runner_runs_real_commands(tmp_path: Path) -> None:
    runner = es.Runner(logging.getLogger('test'))
    completed = runner.run([sys.executable, '-c', 'print("hi")'], cwd=tmp_path)
    assert completed.stdout.strip() == 'hi'
    with pytest.raises(es.CommandError, match='command failed'):
        runner.run([sys.executable, '-c', 'import sys; sys.exit(3)'])
    assert runner.run([sys.executable, '-c', 'import sys; sys.exit(3)'], check=False).returncode == 3


def test_runner_powershell_exit_codes() -> None:
    runner = es.Runner(logging.getLogger('test'))
    assert runner.powershell('Write-Output hi').strip() == 'hi'
    assert runner.powershell("Get-Item -LiteralPath 'C:\\no\\such' -ErrorAction SilentlyContinue") == ''
    with pytest.raises(es.CommandError, match=r'command failed \(2\)'):
        runner.powershell("Get-Item -LiteralPath 'C:\\no\\such'")


class _Handler(http.server.BaseHTTPRequestHandler):
    body = b'payload'

    def do_GET(self) -> None:
        if self.path == '/api/version':
            data = b'{"version": "9.9.9"}'
        elif self.path == '/missing':
            self.send_error(404)
            return
        else:
            data = self.body
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self) -> None:
        self.send_response(404 if self.path == '/missing' else 200)
        self.end_headers()

    def log_message(self, format: str, *args: str) -> None:
        pass


@pytest.fixture
def local_server() -> Iterator[str]:
    server = http.server.HTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()


def test_download_verified_and_reachable(local_server: str, tmp_path: Path) -> None:
    log = logging.getLogger('test')
    dest = tmp_path / 'out' / 'file.bin'
    digest = hashlib.sha256(b'payload').hexdigest()
    es.download_verified(f'{local_server}/file', dest, digest.upper(), log)
    assert dest.read_bytes() == b'payload'
    with pytest.raises(es.IntegrityError):
        es.download_verified(f'{local_server}/file', dest, '0' * 64, log)
    assert not dest.exists()
    assert es.reachable(f'{local_server}/')
    assert es.reachable(f'{local_server}/missing')
    assert not es.reachable('http://127.0.0.1:1/')
    assert not es.reachable('ftp://127.0.0.1/')
    with pytest.raises(ValueError, match='only http and https'):
        es.http_request('file:///C:/secret')


def test_api_version_and_wait(local_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert es.api_version(base_url=local_server) == '9.9.9'
    assert es.api_version(base_url='http://127.0.0.1:1') is None
    monkeypatch.setattr(es, 'api_version', lambda: '1.2.3')
    assert es.wait_for_api(1) == '1.2.3'
    monkeypatch.setattr(es, 'api_version', lambda: None)
    monkeypatch.setattr(es.time, 'sleep', lambda _seconds: None)
    assert es.wait_for_api(0.01) is None


def test_which_outside_skips_the_wrappers_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wrappers = tmp_path / 'bin'
    other = tmp_path / 'other'
    wrappers.mkdir()
    other.mkdir()
    (wrappers / 'tool.cmd').write_text('@echo off', encoding='utf-8')
    (other / 'tool.cmd').write_text('@echo off', encoding='utf-8')
    monkeypatch.setenv('PATH', f'{wrappers};{other}')
    monkeypatch.setenv('PATHEXT', '.CMD')
    found = es.which_outside('tool', wrappers)
    assert found is not None
    assert Path(found).parent == other
    monkeypatch.setenv('PATH', str(wrappers))
    assert es.which_outside('tool', wrappers) is None


def test_app_settings_state_and_apply(tmp_path: Path) -> None:
    db = tmp_path / 'db.sqlite'
    wanted = {'auto_update_enabled': 0, 'airplane_mode': 1}
    assert es.app_settings_state(db, wanted) == 'no-db'
    connection = sqlite3.connect(db)
    connection.execute('CREATE TABLE other (x)')
    connection.commit()
    assert es.app_settings_state(db, wanted) == 'no-table'
    with pytest.raises(es.ConfigError, match='settings table'):
        es.app_settings_apply(db, wanted)
    connection.execute(
        'CREATE TABLE settings (auto_update_enabled INTEGER, airplane_mode INTEGER, context_length INTEGER)'
    )
    connection.commit()
    assert es.app_settings_state(db, wanted) == 'no-row'
    connection.execute('INSERT INTO settings VALUES (1, 0, 131072)')
    connection.commit()
    connection.close()
    assert es.app_settings_state(db, wanted) == 'different'
    es.app_settings_apply(db, wanted)
    assert es.app_settings_state(db, wanted) == 'same'
    assert es.app_settings_state(db, {'missing_column': 1}) == 'no-columns'


def test_misc_helpers(tmp_path: Path) -> None:
    assert es.is_windows()
    assert es.windows_build() > 0
    assert es.free_gb(tmp_path) > 0
    assert es.python_args(Path('x.py'))[1:] == ['-I', 'x.py']
    assert len(es.started_datetime_local_as_str()) == len('20260101-120000')
    log = es.setup_logging(tmp_path / 'logs' / 'a.log')
    log.info('line')
    assert 'line' in (tmp_path / 'logs' / 'a.log').read_text(encoding='utf-8')
    context = es.Context(REPO, es.Paths(tmp_path), PINNED, log, FakeRunner(), FakeUserEnv())
    assert context.secrets_path == tmp_path / '.secrets' / es.SECRETS_DIR_NAME / 'secrets.env'
    context.secrets_file = tmp_path / 's.env'
    assert context.secrets_path == tmp_path / 's.env'
