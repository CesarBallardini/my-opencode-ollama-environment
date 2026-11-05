"""Integration tests against the real operating system: no fakes, throwaway names, everything cleaned up.

Excluded from the default run (pyproject: -m "not integration"); run with `pytest -m integration`.
The firewall and elevation tests need an elevated process and skip otherwise; the rest run as any
user. The registry test writes a throwaway variable under HKCU\\Environment and deletes it again.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess  # nosec B404
import sys
import time
import uuid
import winreg
from typing import TYPE_CHECKING

import pytest
from conftest import REPO, FakeContext

import envsetup as es
import install
import verify

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

LOG = logging.getLogger('integration')
PROXY_START_SECONDS = 20


@pytest.fixture
def runner() -> es.Runner:
    return es.Runner(LOG)


def requires_elevation() -> None:
    if not es.is_admin():
        pytest.skip('needs an elevated process (the windows-latest runner is one; this console is not)')


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return int(probe.getsockname()[1])


def test_firewall_rule_round_trip_with_apostrophes(runner: es.Runner, tmp_path: Path) -> None:
    requires_elevation()
    name = f"local-llm test {uuid.uuid4()} O'Neil"
    program = tmp_path / "O'Neil" / 'opencode.exe'
    try:
        assert es.firewall_rule(runner, name) is None
        es.firewall_add(runner, name, program)
        rule = es.firewall_rule(runner, name)
        assert es.rule_matches(rule, program), rule
        es.firewall_set_enabled(runner, name, enabled=False)
        assert (es.firewall_rule(runner, name) or {}).get('enabled') == 'False'
        es.firewall_set_enabled(runner, name, enabled=True)
        assert (es.firewall_rule(runner, name) or {}).get('enabled') == 'True'
    finally:
        es.firewall_remove(runner, name)
    assert es.firewall_rule(runner, name) is None
    es.firewall_remove(runner, name)  # absent is not an error


def test_absent_firewall_rule_needs_no_elevation(runner: es.Runner) -> None:
    assert es.firewall_rule(runner, f"local-llm: absent {uuid.uuid4()} O'Neil") is None


def test_user_environment_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    env = es.UserEnv()
    name = f'LOCAL_LLM_TEST_{uuid.uuid4().hex}'
    try:
        assert env.get(name) is None
        env.set(name, 'plain')
        assert env.get(name) == 'plain'
        env.set(name, r'%USERPROFILE%\x', expandable=True)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, es.UserEnv.KEY) as key:
            value, kind = winreg.QueryValueEx(key, name)
        assert (value, kind) == (r'%USERPROFILE%\x', winreg.REG_EXPAND_SZ)
        env.broadcast()
        monkeypatch.setenv('PATH', 'nothing')
        env.refresh_process_path()
        assert os.environ['PATH'] != 'nothing'
        assert 'system32' in os.environ['PATH'].lower()
        assert 'nothing' in os.environ['PATH'].split(os.pathsep)  # what only this process had is kept
    finally:
        env.delete(name)
    assert env.get(name) is None
    env.delete(name)  # unset is not an error
    assert isinstance(env.path_list(), list)


def test_listening_addresses_sees_a_real_loopback_listener(runner: es.Runner) -> None:
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = int(listener.getsockname()[1])
        assert es.listening_addresses(runner, port) == ['127.0.0.1']
        assert verify.port_open(port)
    assert not verify.port_open(port)


def test_powershell_ignores_a_module_path_inherited_from_powershell_7(
    runner: es.Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # What a console or a CI step running PowerShell 7 hands to its children.
    monkeypatch.setenv('PSModulePath', r'C:\Program Files\PowerShell\7\Modules')
    target = tmp_path / 'file.txt'
    target.write_text('x', encoding='utf-8')
    assert es.acl_sids(runner, target)


def test_acl_round_trip_restricts_a_broadly_readable_file(runner: es.Runner, tmp_path: Path) -> None:
    secrets = tmp_path / "O'Neil secrets.env"
    secrets.write_text('ANTHROPIC_API_KEY=\n', encoding='utf-8')
    runner.run(['icacls', str(secrets), '/grant', '*S-1-1-0:R'])
    assert es.broad_acl_entries(es.acl_sids(runner, secrets)) == ['Everyone']
    es.restrict_acl(runner, secrets, os.environ['USERNAME'])
    assert es.broad_acl_entries(es.acl_sids(runner, secrets)) == []
    assert secrets.read_text(encoding='utf-8') == 'ANTHROPIC_API_KEY=\n'


def test_the_egress_proxy_template_refuses_a_denied_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ('egress_proxy.py', 'egress-denylist.txt'):
        shutil.copy2(REPO / 'local-llm' / name, tmp_path / name)
    port = free_port()
    # The template under test, run by this interpreter on a free port; no shell, no user input.
    child = subprocess.Popen(  # nosec B603  # noqa: S603
        [sys.executable, '-I', str(tmp_path / 'egress_proxy.py'), '--port', str(port)],
        cwd=str(tmp_path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + PROXY_START_SECONDS
        while not verify.port_open(port) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert verify.port_open(port), 'the proxy did not start'
        monkeypatch.setattr(es, 'PROXY_PORT', port)
        kind, detail = verify.through_proxy(verify.PROXY_DENIED_PROBE)
        assert kind == 'denied', detail
        allowed_kind, _ = verify.through_proxy(verify.PROXY_ALLOWED_PROBE)
        assert allowed_kind in ('allowed', 'offline')
        log_text = (tmp_path / 'egress.log').read_text(encoding='utf-8')
        assert 'DENY CONNECT api.openai.com:443' in log_text
    finally:
        child.terminate()
        child.wait(timeout=10)


@pytest.mark.parametrize('wrapper', ['install.cmd', 'verify.cmd', 'uninstall.cmd', 'ollama-pull.cmd'])
def test_the_cmd_wrappers_start_their_program(wrapper: str) -> None:
    # The repository's own batch files, run by cmd with --help; no user input reaches them.
    completed = subprocess.run(  # nosec B603 B607  # noqa: S603
        ['cmd', '/c', str(REPO / wrapper), '--help'],  # noqa: S607
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        cwd=str(REPO),
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert 'usage:' in completed.stdout.lower()


@pytest.mark.parametrize('script', sorted((REPO / 'tests' / 'e2e').glob('*.ps1')), ids=lambda path: path.name)
def test_the_end_to_end_scripts_parse_and_stay_ascii(runner: es.Runner, script: Path) -> None:
    # Windows PowerShell 5.1 reads a file without a byte order mark in the ANSI code page.
    script.read_text(encoding='ascii')
    parser = '[System.Management.Automation.Language.Parser]::ParseFile'
    errors = runner.powershell(
        f'$errors = $null; $tokens = $null; [void]{parser}({es.ps_quote(str(script))}, [ref]$tokens, [ref]$errors); '
        '$errors | ForEach-Object { $_.Message }'
    )
    assert errors.strip() == ''


def test_uv_finds_the_pinned_python(ctx: FakeContext) -> None:
    real = es.Context(REPO, ctx.paths, ctx.config, LOG, es.Runner(LOG), ctx.user_env)
    assert shutil.which('uv') is not None
    assert install.check_python(real) == []
    assert verify.check_uv_python(real)[0].status == 'PASS'


def test_run_elevated_returns_the_child_exit_code() -> None:
    requires_elevation()
    assert es.run_elevated([sys.executable, '-c', 'raise SystemExit(3)'], REPO) == 3


def test_session_queries_answer() -> None:
    assert es.is_windows()
    assert es.windows_build() >= install.WINDOWS_11_FIRST_BUILD
    assert es.free_gb(REPO) > 0
    console = es.console_user()
    assert console is None or isinstance(console, str)
    assert isinstance(es.is_admin(), bool)
