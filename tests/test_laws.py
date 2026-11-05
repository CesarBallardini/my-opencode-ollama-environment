"""Laws every step obeys, checked on the stateful fake machine rather than on hand-picked examples.

Idempotence: once a step has run, its check is empty and running it again changes nothing.
Dry run: a dry run of a pending step changes nothing. Revertibility: installing and then
uninstalling leaves the machine as it was, whether it started fresh or with everything already
in place, and every kind of change the installer records has a revert.
"""

from __future__ import annotations

import pytest
from conftest import FakeContext, FakeMachine, snapshot

import envsetup as es
import install
import uninstall

STEPS = [*install.USER_STEPS, install.FIREWALL_STEP]
STEP_IDS = [step.name for step in STEPS]


def run_steps(ctx: FakeContext, steps: list[install.Step]) -> None:
    for step in steps:
        install.run_step(ctx, step)


@pytest.mark.parametrize('index', range(len(STEPS)), ids=STEP_IDS)
def test_once_done_a_step_is_done_and_a_second_run_changes_nothing(
    machine: FakeContext[FakeMachine], index: int
) -> None:
    ctx = machine
    run_steps(ctx, STEPS[:index])
    step = STEPS[index]
    assert step.check(ctx), f'{step.name} is expected to be pending on a fresh machine'
    install.run_step(ctx, step)
    assert step.check(ctx) == []
    after_first = snapshot(ctx)
    entries = list(ctx.journal.entries)
    install.run_step(ctx, step)
    assert snapshot(ctx) == after_first
    assert ctx.journal.entries == entries


@pytest.mark.parametrize('index', range(len(STEPS)), ids=STEP_IDS)
def test_a_dry_run_of_a_pending_step_changes_nothing(machine: FakeContext[FakeMachine], index: int) -> None:
    ctx = machine
    run_steps(ctx, STEPS[:index])
    before = snapshot(ctx)
    entries = list(ctx.journal.entries)
    ctx.dry_run = True
    install.run_step(ctx, STEPS[index])
    assert snapshot(ctx) == before
    assert ctx.journal.entries == entries


@pytest.mark.parametrize('preinstalled', [False, True], ids=['fresh', 'preinstalled'])
def test_install_then_uninstall_restores_the_machine(machine: FakeContext[FakeMachine], preinstalled: bool) -> None:
    ctx = machine
    if preinstalled:
        run_steps(ctx, STEPS)
        ctx.paths.journal.unlink()  # everything is in place, as if set up by hand before the installer
        ctx.reload_journal()
    before = snapshot(ctx)
    run_steps(ctx, STEPS)
    if preinstalled:
        assert snapshot(ctx) == before
        assert ctx.journal.entries == []
    else:
        assert snapshot(ctx) != before
    uninstall.admin_phase(ctx)
    uninstall.user_phase(ctx, apply=True)
    expected = before.copy()
    if not preinstalled:
        # The uv-managed Python is the one thing the uninstaller leaves, because it runs on it.
        machine_before = before['machine']
        assert machine_before is not None
        expected['machine'] = {**machine_before, 'pythons': [ctx.config['tools']['python_for_proxy']]}
    assert snapshot(ctx) == expected
    remaining = [entry['kind'] for entry in ctx.journal.entries]
    assert remaining == ([] if preinstalled else ['uv-python-install'])


def test_every_kind_the_installer_records_has_a_revert(machine: FakeContext[FakeMachine]) -> None:
    ctx = machine
    ctx.config['cloud']['install_harnesses'] = True
    ctx.secrets_file = ctx.paths.home / 'secrets.env'
    ctx.secrets_file.parent.mkdir(parents=True, exist_ok=True)
    ctx.secrets_file.write_text('ANTHROPIC_API_KEY=sk-test\n', encoding='utf-8')
    ctx.paths.opencode_cloud_auth.parent.mkdir(parents=True)
    ctx.paths.opencode_cloud_auth.write_text('{}\n', encoding='utf-8')
    run_steps(ctx, STEPS)
    recorded = {entry['kind'] for entry in ctx.journal.entries}
    assert recorded == set(es.JOURNAL_SCHEMAS)
    assert 'firewall-rule' in uninstall.ADMIN_KINDS
    assert set(es.JOURNAL_SCHEMAS) >= uninstall.ADMIN_KINDS


def test_the_fake_machine_detects_what_it_installed(machine: FakeContext[FakeMachine]) -> None:
    runner = machine.runner
    assert install.ollama_exe(machine) is None
    es.winget_install(runner, machine.config['ollama']['winget_id'], '0.40.14')
    assert install.ollama_exe(machine) == str(machine.paths.ollama_exe)
    assert install.version_of(machine, str(machine.paths.ollama_exe)) == machine.config['ollama']['version']
    es.winget_uninstall(runner, machine.config['ollama']['winget_id'])
    assert install.ollama_exe(machine) is None
    assert es.ollama_modelfile(runner, 'x') == ''  # a plain `ollama` on PATH is not simulated


@pytest.mark.parametrize(
    'user_path',
    [r'C:\One;C:\Two;', r'C:\One;;C:\Two', r';C:\One', ';', None],
    ids=['trailing-separator', 'empty-entry', 'leading-separator', 'only-a-separator', 'unset'],
)
def test_uninstall_restores_the_user_path_as_it_was_written(
    machine: FakeContext[FakeMachine], user_path: str | None
) -> None:
    ctx = machine
    if user_path is None:
        ctx.user_env.values.pop('Path', None)
    else:
        ctx.user_env.values['Path'] = user_path
    for _ in ('first cycle', 'second cycle'):
        run_steps(ctx, STEPS)
        assert es.path_position(ctx.user_env.path_list(), es.WRAPPERS_PATH_ENTRY) == 0
        uninstall.admin_phase(ctx)
        uninstall.user_phase(ctx, apply=True)
        assert ctx.user_env.get('Path') == user_path


def test_uninstall_keeps_what_the_user_added_to_the_path_after_the_install(machine: FakeContext[FakeMachine]) -> None:
    ctx = machine
    run_steps(ctx, STEPS)
    ctx.user_env.values['Path'] += r';C:\AddedLater;'
    uninstall.admin_phase(ctx)
    uninstall.user_phase(ctx, apply=True)
    assert ctx.user_env.get('Path') == r'C:\One;C:\AddedLater;'
