"""install.py orchestration: preflight, the firewall phase and main, with every system call faked."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from conftest import REPO, FakeContext, FakeMachine, FakeRunner, FakeUserEnv

import envsetup as es
import install

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def quiet_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine where every precondition holds and nothing is installed."""
    monkeypatch.setattr(es, 'console_user', lambda: None)
    monkeypatch.setattr(es, 'reachable', lambda _url: True)
    monkeypatch.setattr(es, 'free_gb', lambda _path: 1000.0)
    monkeypatch.setattr(es, 'api_version', lambda: '0.40.14')
    monkeypatch.setattr(es, 'which_outside', lambda _name, _exclude: None)
    monkeypatch.setattr(install.shutil, 'which', lambda _command: 'C:/tools/any.exe')
    monkeypatch.setattr(install.platform, 'machine', lambda: 'AMD64')


def test_preflight_reports_every_problem(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(es, 'windows_build', lambda: 19045)
    monkeypatch.setattr(install.platform, 'machine', lambda: 'x86')
    monkeypatch.setattr(es, 'console_user', lambda: 'someone-else')
    monkeypatch.setenv('USERNAME', 'tester')
    monkeypatch.setattr(install.shutil, 'which', lambda _command: None)
    monkeypatch.setattr(es, 'reachable', lambda _url: False)
    monkeypatch.setattr(es, 'free_gb', lambda _path: 1.0)
    monkeypatch.setattr(es, 'which_outside', lambda _name, _exclude: None)
    with pytest.raises(install.PreflightError) as info:
        install.preflight(ctx)
    text = str(info.value)
    assert 'Windows 11' in text
    assert '64-bit' in text
    assert 'console user' in text
    assert 'winget' in text
    assert 'https://github.com is unreachable' in text
    assert 'GB are required' in text


@pytest.mark.usefixtures('quiet_machine')
def test_preflight_passes_and_warns_on_a_quiet_machine(ctx: FakeContext) -> None:
    install.preflight(ctx)
    ctx.dry_run = True
    install.preflight(ctx)


@pytest.mark.usefixtures('quiet_machine')
def test_preflight_warns_instead_of_failing_when_ollama_is_present(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx.paths.ollama_exe.parent.mkdir(parents=True)
    ctx.paths.ollama_exe.write_bytes(b'')
    monkeypatch.setattr(es, 'free_gb', lambda _path: 1.0)
    monkeypatch.setattr(install.shutil, 'which', lambda _command: None)
    install.preflight(ctx)
    ctx.config['cloud']['install_harnesses'] = True
    with pytest.raises(install.PreflightError, match='winget'):
        install.preflight(ctx)


@pytest.mark.usefixtures('quiet_machine')
def test_preflight_ignores_the_sources_of_skipped_steps(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install.shutil, 'which', lambda _command: None)  # no winget, no uv
    ctx.config['cloud']['install_harnesses'] = True
    with pytest.raises(install.PreflightError, match='winget'):
        install.preflight(ctx)
    ctx.skip = {'ollama', 'cloud'}
    install.preflight(ctx)
    ctx.skip = set()
    ctx.only = {'local-llm', 'shell'}
    install.preflight(ctx)


def test_firewall_phase_elevates_when_needed(ctx: FakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeMachine()
    ctx.runner = runner
    ctx.skip = {'firewall'}
    install.firewall_phase(ctx, REPO / 'install.config.json')
    assert runner.scripts == []
    ctx.skip = set()
    ctx.dry_run = True
    install.firewall_phase(ctx, REPO / 'install.config.json')
    assert runner.rules == {}
    ctx.dry_run = False
    monkeypatch.setattr(es, 'is_admin', lambda: True)
    install.firewall_phase(ctx, REPO / 'install.config.json')
    assert set(runner.rules) == set(es.firewall_rules_for(ctx.config, ctx.paths))
    install.firewall_phase(ctx, REPO / 'install.config.json')
    runner.rules.clear()
    monkeypatch.setattr(es, 'is_admin', lambda: False)
    elevated: list[list[str]] = []

    def run_elevated(args: list[str], _cwd: Path) -> int:
        elevated.append(args)
        for name, program in es.firewall_rules_for(ctx.config, ctx.paths).items():
            es.firewall_add(runner, name, program)
        es.Journal(ctx.paths.journal).add({'kind': 'firewall-rule', 'name': 'from the child'})
        return 0

    monkeypatch.setattr(es, 'run_elevated', run_elevated)
    ctx.log_file = ctx.paths.home / 'x.log'
    assert not ctx.journal.has('firewall-rule', name='from the child')
    install.firewall_phase(ctx, REPO / 'install.config.json')
    assert elevated[0][1:4] == ['-I', str(REPO / 'install.py'), '--phase']
    assert '--log-file' in elevated[0]
    assert ctx.journal.has('firewall-rule', name='from the child')
    runner.rules.clear()
    monkeypatch.setattr(es, 'run_elevated', lambda _args, _cwd: 5)
    with pytest.raises(RuntimeError, match='exited with code 5'):
        install.firewall_phase(ctx, REPO / 'install.config.json')
    monkeypatch.setattr(es, 'run_elevated', lambda _args, _cwd: 0)
    with pytest.raises(RuntimeError, match='still missing'):
        install.firewall_phase(ctx, REPO / 'install.config.json')


@pytest.mark.usefixtures('quiet_machine')
def test_main_dry_run_on_a_quiet_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(es, 'Runner', FakeRunner)
    monkeypatch.setattr(es, 'UserEnv', FakeUserEnv)
    home = tmp_path / 'home'
    log_file = tmp_path / 'install.log'
    code = install.main(['--home', str(home), '--dry-run', '--log-file', str(log_file), '--skip', 'python'])
    assert code == 0
    out = capsys.readouterr().out
    assert '[preflight]' in out
    assert '[python] skipped' in out
    assert 'dry run: no change' in out
    assert 'dry run: no elevation, no change' in out
    assert 'dry run finished' in out
    assert not home.exists()
    assert 'install.py phase=user' in log_file.read_text(encoding='utf-8')


@pytest.mark.usefixtures('quiet_machine')
def test_main_reports_errors_and_runs_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(es, 'Runner', FakeRunner)
    monkeypatch.setattr(es, 'UserEnv', FakeUserEnv)
    home = tmp_path / 'home'
    log_file = tmp_path / 'install.log'
    assert install.main(['--home', str(home), '--only', 'nope', '--log-file', str(log_file)]) == 1
    assert 'unknown step name' in capsys.readouterr().out
    monkeypatch.setattr(es, 'console_user', lambda: 'someone-else')
    assert install.main(['--home', str(home), '--log-file', str(log_file)]) == 1
    assert 'ERROR run as the console user' in capsys.readouterr().out
    monkeypatch.setattr(es, 'console_user', lambda: None)
    monkeypatch.setattr(install.verify, 'run_checks', lambda _ctx: 7)
    code = install.main(['--home', str(home), '--only', 'local-llm', '--log-file', str(log_file)])
    assert code == 7
    assert (home / 'local-llm' / 'egress_proxy.py').exists()
    assert '[verify]' in capsys.readouterr().out


def test_main_admin_phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(es, 'Runner', FakeMachine)
    monkeypatch.setattr(es, 'UserEnv', FakeUserEnv)
    args = ['--home', str(tmp_path), '--phase', 'admin', '--log-file', str(tmp_path / 'a.log')]
    monkeypatch.setattr(es, 'is_admin', lambda: False)
    assert install.main(args) == 1
    assert 'requires elevation' in capsys.readouterr().out
    monkeypatch.setattr(es, 'is_admin', lambda: True)
    assert install.main(args) == 0
    out = capsys.readouterr().out
    assert "adding 'local-llm: opencode direct outbound'" in out
    assert out.strip().endswith('done')
