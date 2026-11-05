"""verify.py checks, each against a faked machine state."""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from email.message import Message
from typing import TYPE_CHECKING

import pytest
from conftest import REPO, FakeContext, FakeRunner, FakeUserEnv

import envsetup as es
import install
import verify
from envtypes import ProxyProbeDto

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType


def statuses(outcomes: list[verify.OutcomeDto]) -> list[str]:
    return [outcome.status for outcome in outcomes]


def test_check_ollama(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify.shutil, 'which', lambda _command: None)
    assert statuses(verify.check_ollama(ctx)) == ['FAIL']
    ctx.paths.ollama_exe.parent.mkdir(parents=True)
    ctx.paths.ollama_exe.write_bytes(b'')
    ctx.runner.answer(lambda args: args[-1] == '--version', 'ollama version is 0.40.14')
    monkeypatch.setattr(es, 'api_version', lambda: None)
    assert statuses(verify.check_ollama(ctx)) == ['PASS', 'WARN']
    monkeypatch.setattr(es, 'api_version', lambda: '0.40.14')
    ctx.runner.answer_powershell(lambda script: 'Get-NetTCPConnection' in script, '127.0.0.1\n')
    assert statuses(verify.check_ollama(ctx)) == ['PASS', 'PASS']
    ctx.runner.powershell_handlers.clear()
    ctx.runner.answer_powershell(lambda script: 'Get-NetTCPConnection' in script, '0.0.0.0\n')
    outcomes = verify.check_ollama(ctx)
    assert statuses(outcomes) == ['PASS', 'FAIL']
    assert 'loopback only' in outcomes[1].detail
    ctx.runner.handlers.clear()
    ctx.runner.answer(lambda args: args[-1] == '--version', 'ollama version is 0.39.0')
    assert statuses(verify.check_ollama(ctx))[0] == 'WARN'


def test_check_server_json_and_user_env(ctx: FakeContext) -> None:
    assert statuses(verify.check_server_json(ctx)) == ['FAIL']
    ctx.paths.server_json.parent.mkdir(parents=True)
    ctx.paths.server_json.write_text('{"disable_ollama_cloud": false}', encoding='utf-8')
    assert statuses(verify.check_server_json(ctx)) == ['FAIL']
    ctx.paths.server_json.write_text('{"disable_ollama_cloud": true}', encoding='utf-8')
    assert statuses(verify.check_server_json(ctx)) == ['PASS']
    assert statuses(verify.check_user_env(ctx)) == ['FAIL']
    ctx.user_env.values.update(ctx.config['ollama']['user_env'])
    assert statuses(verify.check_user_env(ctx)) == ['PASS']


def test_check_models(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(es, 'api_version', lambda: None)
    assert statuses(verify.check_models(ctx)) == ['SKIP']
    monkeypatch.setattr(es, 'api_version', lambda: '0.40.14')
    ctx.paths.ollama_exe.parent.mkdir(parents=True)
    ctx.paths.ollama_exe.write_bytes(b'')
    ctx.runner.answer(lambda args: args[-1] == 'list', 'NAME ID SIZE MODIFIED\nqwen3-coder:30b a 1 GB now\n')
    outcomes = verify.check_models(ctx)
    assert statuses(outcomes) == ['FAIL']
    assert 'qwen3-coder-32k' in outcomes[0].detail
    ctx.runner.handlers.clear()
    listing = 'NAME ID SIZE MODIFIED\nqwen3-coder:30b a 1 GB now\nqwen3-coder-32k:latest b 1 GB now\n'
    ctx.runner.answer(lambda args: args[-1] == 'list', listing)
    ctx.runner.answer(lambda args: args[-2] == '--modelfile', 'FROM qwen3-coder:30b\nPARAMETER num_ctx 1\n')
    outcomes = verify.check_models(ctx)
    assert statuses(outcomes) == ['PASS', 'WARN']
    modelfile = (REPO / 'local-llm' / 'qwen3-coder-32k.Modelfile').read_text(encoding='utf-8')
    ctx.runner.handlers.pop()
    ctx.runner.answer(lambda args: args[-2] == '--modelfile', modelfile)
    assert statuses(verify.check_models(ctx)) == ['PASS']


def test_check_app_settings(ctx: FakeContext) -> None:
    assert statuses(verify.check_app_settings(ctx)) == ['SKIP']
    ctx.paths.app_db.parent.mkdir(parents=True)
    connection = es.sqlite3.connect(ctx.paths.app_db)
    connection.execute('CREATE TABLE settings (auto_update_enabled INTEGER, airplane_mode INTEGER)')
    connection.execute('INSERT INTO settings VALUES (1, 0)')
    connection.commit()
    connection.close()
    assert statuses(verify.check_app_settings(ctx)) == ['FAIL']
    es.app_settings_apply(ctx.paths.app_db, ctx.config['ollama']['app_settings'])
    assert statuses(verify.check_app_settings(ctx)) == ['PASS']


def test_check_local_llm_files(ctx: FakeContext) -> None:
    outcomes = verify.check_local_llm_files(ctx)
    assert statuses(outcomes) == ['FAIL', 'FAIL', 'FAIL', 'FAIL']
    install.do_local_llm(ctx)
    for exe, spec in (
        (ctx.paths.opencode_private_exe, ctx.config['harness']['opencode']),
        (ctx.paths.claude_private_exe, ctx.config['harness']['claude']),
    ):
        exe.write_bytes(b'binary')
        spec['exe_sha256'] = es.sha256_file(exe)
    outcomes = verify.check_local_llm_files(ctx)
    assert statuses(outcomes) == ['PASS', 'PASS', 'PASS', 'PASS']
    assert '500 entries' in outcomes[3].detail
    ctx.paths.claude_private_exe.write_bytes(b'other')
    denylist = ctx.paths.local_llm / 'egress-denylist.txt'
    denylist.write_text('api.openai.com\napi.openai.com\n', encoding='utf-8')
    outcomes = verify.check_local_llm_files(ctx)
    assert statuses(outcomes) == ['WARN', 'PASS', 'WARN', 'FAIL']
    assert 'duplicates' in outcomes[3].detail
    denylist.write_text('example.com\n', encoding='utf-8')
    assert verify.check_local_llm_files(ctx)[3].detail == 'api.openai.com is not denied'
    with pytest.raises(RuntimeError, match='cannot load'):
        verify.load_proxy_module(ctx.paths.local_llm / 'not-python.txt')


def test_check_firewall(ctx: FakeContext) -> None:
    good = json.dumps(
        {'enabled': 'True', 'direction': 'Outbound', 'action': 'Block', 'program': str(ctx.paths.opencode_private_exe)}
    )
    ctx.runner.answer_powershell(lambda script: es.RULE_OPENCODE in script, good)
    ctx.runner.answer_powershell(lambda script: f"'{es.RULE_CLAUDE}'" in script, good)
    outcomes = verify.check_firewall(ctx)
    assert statuses(outcomes) == ['PASS', 'WARN', 'FAIL', 'FAIL', 'FAIL']


def test_through_proxy_and_proxy_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(
            self, _type: type[BaseException] | None, _value: BaseException | None, _traceback: TracebackType | None
        ) -> None:
            pass

    class Opener:
        def __init__(self, outcome: Exception | None) -> None:
            self.outcome = outcome

        def open(self, _request: urllib.request.Request, timeout: float) -> Response:  # noqa: ARG002
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return Response()

    outcomes: list[Exception | None] = [
        None,
        urllib.error.HTTPError('u', 403, 'forbidden', Message(), None),
        urllib.error.HTTPError('u', 503, 'busy', Message(), None),
        urllib.error.URLError('Tunnel connection failed: 403 Forbidden'),
        OSError('no route'),
    ]
    monkeypatch.setattr(verify.urllib.request, 'build_opener', lambda _handler: Opener(outcomes.pop(0)))
    assert verify.through_proxy('https://x')[0] == 'allowed'
    assert verify.through_proxy('https://x') == ('denied', '403')
    assert verify.through_proxy('https://x') == ('allowed', '503')
    assert verify.through_proxy('https://x')[0] == 'denied'
    assert verify.through_proxy('https://x')[0] == 'offline'
    assert statuses(verify.proxy_outcomes(ProxyProbeDto('offline', 'x'), ProxyProbeDto('offline', 'x'))) == ['SKIP']
    assert statuses(verify.proxy_outcomes(ProxyProbeDto('denied', '403'), ProxyProbeDto('allowed', '200'))) == [
        'PASS',
        'PASS',
    ]
    assert statuses(verify.proxy_outcomes(ProxyProbeDto('allowed', '200'), ProxyProbeDto('offline', 'x'))) == [
        'FAIL',
        'SKIP',
    ]
    assert statuses(verify.proxy_outcomes(ProxyProbeDto('denied', '403'), ProxyProbeDto('denied', '403'))) == [
        'PASS',
        'FAIL',
    ]


def test_check_proxy_starts_the_proxy_when_needed(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    assert statuses(verify.check_proxy(ctx)) == ['FAIL']
    install.do_local_llm(ctx)
    monkeypatch.setattr(verify.shutil, 'which', lambda _command: None)
    assert verify.check_proxy(ctx)[0].detail == 'uv is not on PATH'
    monkeypatch.setattr(verify.shutil, 'which', lambda _command: 'C:/uv.exe')

    class Child:
        pid = 4242
        waited = False

        def wait(self, timeout: float) -> int:
            self.waited = timeout > 0
            return 0

    child = Child()
    started: list[list[str]] = []

    def start(_ctx: FakeContext, proxy: Path, uv: str) -> Child:
        started.append([uv, str(proxy)])
        return child

    monkeypatch.setattr(verify, 'start_proxy', start)
    answers = iter([False, True, True])
    monkeypatch.setattr(verify, 'port_open', lambda _port: next(answers))
    monkeypatch.setattr(
        verify,
        'through_proxy',
        lambda url: ProxyProbeDto('denied', '403') if 'openai' in url else ProxyProbeDto('allowed', '200'),
    )
    assert statuses(verify.check_proxy(ctx)) == ['PASS', 'PASS']
    assert started == [['C:/uv.exe', str(ctx.paths.local_llm / 'egress_proxy.py')]]
    assert ['taskkill', '/T', '/F', '/PID', '4242'] in ctx.runner.calls
    assert child.waited
    monkeypatch.setattr(verify, 'port_open', lambda _port: False)
    monkeypatch.setattr(verify, 'PROXY_START_SECONDS', 0)
    assert 'did not listen' in verify.check_proxy(ctx)[0].detail


def test_start_proxy_builds_the_uv_command(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[list[str], str]] = []

    class Popen:
        def __init__(self, args: list[str], *, cwd: str, stdin: int, stdout: int, stderr: int) -> None:  # noqa: ARG002
            captured.append((args, cwd))

    monkeypatch.setattr(verify.subprocess, 'Popen', Popen)
    verify.start_proxy(ctx, ctx.paths.local_llm / 'egress_proxy.py', 'C:/uv.exe')
    args, cwd = captured[0]
    assert args[:5] == ['C:/uv.exe', 'run', '--no-project', '--python', '3.14']
    assert cwd == str(ctx.paths.local_llm)


def test_server_json_that_is_not_an_object_fails(ctx: FakeContext) -> None:
    ctx.paths.server_json.parent.mkdir(parents=True)
    ctx.paths.server_json.write_text('[]', encoding='utf-8')
    assert statuses(verify.check_server_json(ctx)) == ['FAIL']


def test_port_open_on_a_closed_port() -> None:
    assert not verify.port_open(1)


def test_check_binaries_run(ctx: FakeContext) -> None:
    assert statuses(verify.check_binaries_run(ctx)) == ['FAIL', 'FAIL']
    for exe in (ctx.paths.opencode_private_exe, ctx.paths.claude_private_exe):
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'')
    opencode, claude = str(ctx.paths.opencode_private_exe), str(ctx.paths.claude_private_exe)
    ctx.runner.answer(lambda args: args[0] == opencode, '1.18.34\n')
    ctx.runner.answer(lambda args: args[0] == claude, '9.9.9 (Claude Code)\n')
    outcomes = verify.check_binaries_run(ctx)
    assert statuses(outcomes) == ['PASS', 'WARN']
    ctx.runner.handlers.clear()
    ctx.runner.answer(lambda args: args[0] == opencode, 'boom', 3)
    ctx.runner.answer(lambda args: args[0] == claude, raises=subprocess.TimeoutExpired(claude, 120))
    outcomes = verify.check_binaries_run(ctx)
    assert statuses(outcomes) == ['FAIL', 'FAIL']
    assert 'exit 3' in outcomes[0].detail
    assert 'timed out' in outcomes[1].detail


def test_check_path_and_wrappers(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify.shutil, 'which', lambda _command: None)
    outcomes = verify.check_path_and_wrappers(ctx)
    assert statuses(outcomes) == ['FAIL', 'WARN', 'WARN', 'FAIL']
    ctx.user_env.values['Path'] = f'C:\\x;{es.WRAPPERS_PATH_ENTRY}'
    assert statuses(verify.check_path_and_wrappers(ctx))[0] == 'WARN'
    ctx.user_env.values['Path'] = 'C:\\x'
    install.do_shell(ctx)
    monkeypatch.setattr(verify.shutil, 'which', lambda command: str(ctx.paths.wrappers_bin / f'{command}.cmd'))
    assert statuses(verify.check_path_and_wrappers(ctx)) == ['PASS', 'PASS', 'PASS', 'PASS']


def test_check_uv_python_and_secrets(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify.shutil, 'which', lambda _command: None)
    assert statuses(verify.check_uv_python(ctx)) == ['FAIL']
    monkeypatch.setattr(verify.shutil, 'which', lambda _command: 'C:/uv.exe')
    ctx.runner.answer(lambda args: args[:3] == ['uv', 'python', 'find'], 'C:/py/python.exe\n')
    assert verify.check_uv_python(ctx)[0].detail == 'C:/py/python.exe'
    ctx.runner.handlers.clear()
    ctx.runner.answer(lambda args: args[:3] == ['uv', 'python', 'find'], '', 2)
    assert statuses(verify.check_uv_python(ctx)) == ['FAIL']
    assert statuses(verify.check_secrets(ctx)) == ['SKIP']
    ctx.secrets_file = ctx.paths.home / 's.env'
    ctx.secrets_file.parent.mkdir(parents=True)
    ctx.secrets_file.write_text('', encoding='utf-8')
    ctx.runner.answer_powershell(lambda script: 'Get-Acl' in script, 'S-1-1-0\n')
    assert statuses(verify.check_secrets(ctx)) == ['FAIL']
    ctx.runner.powershell_handlers.clear()
    assert statuses(verify.check_secrets(ctx)) == ['PASS']


def test_check_journal(ctx: FakeContext) -> None:
    assert statuses(verify.check_journal(ctx)) == ['SKIP']
    ctx.journal.add({'kind': 'file', 'path': 'x', 'backup': None})
    ctx.journal.add({'kind': 'user-env', 'name': 'A', 'previous': None})
    outcome = verify.check_journal(ctx)[0]
    assert outcome.status == 'PASS'
    assert outcome.detail.startswith('2 entries')
    assert '1 file, 1 user-env' in outcome.detail
    ctx.paths.journal.write_text('[]', encoding='utf-8')
    assert statuses(verify.check_journal(ctx)) == ['FAIL']


def test_run_checks_and_main(
    ctx: FakeContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(_ctx: FakeContext) -> list[verify.OutcomeDto]:
        raise ValueError('boom')

    ctx.log = es.setup_logging(None)
    monkeypatch.setattr(
        verify,
        'CHECKS',
        [lambda _ctx: [verify.OutcomeDto('PASS', 'a', 'ok'), verify.OutcomeDto('WARN', 'b', 'hm')], broken],
    )
    assert verify.run_checks(ctx) == 1
    out = capsys.readouterr().out
    assert 'FAIL broken: ValueError: boom' in out
    assert 'verify: 1 PASS, 1 WARN, 0 SKIP, 1 FAIL' in out
    monkeypatch.setattr(verify, 'CHECKS', [lambda _ctx: [verify.OutcomeDto('PASS', 'a', 'ok')]])
    monkeypatch.setattr(es, 'Runner', FakeRunner)
    monkeypatch.setattr(es, 'UserEnv', FakeUserEnv)
    assert verify.main(['--home', str(tmp_path)]) == 0
    assert verify.main(['--home', str(tmp_path), '--config', str(tmp_path / 'absent.json')]) == 1
    assert 'ERROR cannot read' in capsys.readouterr().out
