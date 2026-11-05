r"""Revert what install.py did on this machine, and nothing else.

install.py records every change it makes in a journal (``%LOCALAPPDATA%\my-opencode-ollama-
environment\install-journal.json``): packages it installed, directories and files it created,
files it replaced (kept in a backups directory next to the journal), user environment variables
and the Path entry, the .bashrc alias block, the Ollama app settings, the models it pulled or
created, the firewall rules. This program walks that journal backwards, undoes each entry and
drops it from the journal. Whatever existed before the installer ran is not in the journal and
stays as it is: an Ollama, OpenCode or Claude Code that was already installed, a ~/local-llm
that was already there, variables that were already set, models that were already pulled.

Kept on purpose: the ACL restriction of secrets.env (never recorded), the models when
--keep-models is given (their entries stay for a later run), and the uv-managed Python the
proxy runs on, because this program itself runs on it (the command to run by hand is printed).

Without --yes the program only lists what it would revert.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envsetup as es
import install
from envtypes import RevertCountsDto

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from envtypes import (
        AppSettingsEntryDto,
        AuthKeyEntryDto,
        BashrcBlockEntryDto,
        DirCreatedEntryDto,
        FileEntryDto,
        FirewallRuleEntryDto,
        JournalEntryDto,
        JournalKind,
        ModelEntryDto,
        Phase,
        UserEnvEntryDto,
        UserPathEntryDto,
        UvPythonInstallEntryDto,
        WingetInstallEntryDto,
    )

EXIT_NOTHING_REMOVED = 2
# The journal kinds that only an elevated process can revert.
ADMIN_KINDS: frozenset[JournalKind] = frozenset({'firewall-rule'})
# The kinds whose revert changes what a running Ollama has read at start-up.
OLLAMA_RESTART_KINDS: frozenset[JournalKind] = frozenset({'user-env', 'app-settings'})


class UninstallArgsDto(argparse.Namespace):
    """The parsed command line of uninstall.py."""

    config: Path
    home: Path
    secrets_file: Path | None
    yes: bool
    keep_models: bool
    phase: Phase
    log_file: Path | None


def _writable(function: Callable[[str], None], path: str, _exc_info: BaseException) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


# ----------------------------------------------------------------------------- reverts, one per kind


def revert_firewall_rule(ctx: es.Context, entry: FirewallRuleEntryDto) -> bool:
    """Remove the rule the installer added."""
    es.firewall_remove(ctx.runner, entry['name'])
    return True


def revert_winget_install(ctx: es.Context, entry: WingetInstallEntryDto) -> bool:
    """Uninstall the package the installer installed; Ollama is stopped first."""
    if entry['id'] == ctx.config['ollama']['winget_id']:
        es.stop_ollama(ctx.runner)
    es.winget_uninstall(ctx.runner, entry['id'])
    return True


def revert_uv_python(ctx: es.Context, entry: UvPythonInstallEntryDto) -> bool:
    """Leave the uv-managed Python in place: this program runs on it."""
    ctx.log.warning(
        '  WARN left in place because this program runs on it; run by hand: uv python uninstall %s', entry['version']
    )
    return False


def describe_file(entry: FileEntryDto) -> str:
    """'delete' for a file the installer created, 'restore' for one it replaced."""
    if entry['backup']:
        return f'restore {entry["path"]} from {entry["backup"]}'
    return f'delete {entry["path"]}'


def revert_file(ctx: es.Context, entry: FileEntryDto) -> bool:
    """Delete the file the installer wrote and put the replaced one back when there was one."""
    path = Path(entry['path'])
    if path.is_file():
        path.unlink()
    if entry['backup']:
        backup = Path(entry['backup'])
        if backup.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup), str(path))
        else:
            ctx.log.warning('  WARN the backup %s is missing; %s was not restored', backup, path)
    return True


def describe_dir_created(entry: DirCreatedEntryDto) -> str:
    """Whether the whole tree goes or only the directory, once empty."""
    if entry['tree']:
        return f'delete the directory {entry["path"]} and everything in it'
    return f'remove the directory {entry["path"]} (once empty)'


def revert_dir_created(ctx: es.Context, entry: DirCreatedEntryDto) -> bool:
    """Delete the directory the installer created.

    Under ~/local-llm the whole tree goes, session data included: it is the environment this
    repository manages. Elsewhere only an empty directory is removed; one that other programs
    filled in the meantime is kept and reported.
    """
    path = Path(entry['path'])
    if not path.exists():
        return True
    if entry['tree']:
        shutil.rmtree(path, onexc=_writable)
        return True
    try:
        path.rmdir()
    except OSError:
        ctx.log.warning('  WARN %s kept: it is not empty (delete it by hand if nothing else uses it)', path)
    return True


def describe_user_env(entry: UserEnvEntryDto) -> str:
    """'delete' for a variable the installer created, 'set back' for one it changed."""
    if entry['previous'] is None:
        return f'delete the user environment variable {entry["name"]}'
    return f'set the user environment variable {entry["name"]} back to {entry["previous"]!r}'


def revert_user_env(ctx: es.Context, entry: UserEnvEntryDto) -> bool:
    """Delete the variable, or set it back to the value it had."""
    if entry['previous'] is None:
        ctx.user_env.delete(entry['name'])
    else:
        ctx.user_env.set(entry['name'], entry['previous'])
    return True


def revert_user_path(ctx: es.Context, entry: UserPathEntryDto) -> bool:
    """Remove the entry the installer prepended to the user Path."""
    ctx.user_env.set_path_list(es.path_remove(ctx.user_env.path_list(), entry['entry']))
    return True


def describe_bashrc_block(entry: BashrcBlockEntryDto) -> str:
    """What happens to the .bashrc the installer wrote the alias block into."""
    if entry['created']:
        return f'delete {entry["path"]} (created by the installer)'
    if entry['previous_block']:
        return f'put the previous alias block back into {entry["path"]}'
    return f'remove the local-llm alias block from {entry["path"]}'


def revert_bashrc_block(ctx: es.Context, entry: BashrcBlockEntryDto) -> bool:
    """Delete the .bashrc the installer created, or take its block out of the one that existed."""
    path = Path(entry['path'])
    if entry['created']:
        path.unlink(missing_ok=True)
        return True
    if not path.is_file():
        ctx.log.warning('  WARN %s no longer exists', path)
        return True
    text = path.read_text(encoding='utf-8')
    previous = entry['previous_block']
    restored = es.block_apply(text, previous) if previous else es.block_remove(text)
    path.write_text(restored, encoding='utf-8', newline='\n')
    return True


def revert_app_settings(ctx: es.Context, entry: AppSettingsEntryDto) -> bool:
    """Write the Ollama app settings the installer found back into its database."""
    db = Path(entry['db'])
    if not db.exists():
        return True
    es.stop_ollama(ctx.runner)
    es.app_settings_apply(db, entry['previous'])
    return True


def revert_model(ctx: es.Context, entry: ModelEntryDto) -> bool:
    """Remove the model tag the installer pulled, created or copied, unless --keep-models."""
    if ctx.keep_models:
        ctx.log.info('  kept (--keep-models)')
        return False
    exe = install.ollama_exe(ctx)
    if exe is None:
        ctx.log.warning('  WARN ollama.exe not found; %s cannot be removed and is forgotten', entry['tag'])
        return True
    if not install.ensure_server(ctx):
        ctx.log.warning('  WARN the Ollama server is not running; %s is kept for a later run', entry['tag'])
        return False
    ctx.runner.run([exe, 'rm', entry['tag']], check=False, stream=True)
    return True


def revert_auth_key(ctx: es.Context, entry: AuthKeyEntryDto) -> bool:
    """Take the anthropic entry out of the credentials file, unless the key was changed since."""
    path = Path(entry['path'])
    auth = install.read_json(path)
    if auth is None or 'anthropic' not in auth:
        return True
    expected = es.read_secrets(ctx.secrets_path).get('ANTHROPIC_API_KEY') if ctx.secrets_path.exists() else None
    if expected is not None and install.anthropic_key(auth) != expected:
        ctx.log.warning('  WARN the anthropic entry in %s carries another key than secrets.env; left in place', path)
        return True
    del auth['anthropic']
    path.write_text(json.dumps(auth, indent=2) + '\n', encoding='utf-8')
    return True


def describe_revert(entry: JournalEntryDto) -> str:  # noqa: PLR0911  (one return per journal kind)
    """A one-line description of how entry is undone. A new journal kind fails type checking here."""
    match entry['kind']:
        case 'firewall-rule':
            return f"remove the firewall rule '{entry['name']}'"
        case 'winget-install':
            return f'winget uninstall {entry["id"]}'
        case 'uv-python-install':
            return f'uv python uninstall {entry["version"]} (by hand)'
        case 'file':
            return describe_file(entry)
        case 'dir-created':
            return describe_dir_created(entry)
        case 'user-env':
            return describe_user_env(entry)
        case 'user-path':
            return f'remove {entry["entry"]} from the user Path'
        case 'bashrc-block':
            return describe_bashrc_block(entry)
        case 'app-settings':
            return f'set the Ollama app settings back to {entry["previous"]}'
        case 'model':
            return f'ollama rm {entry["tag"]}'
        case 'auth-key':
            return f'remove the anthropic entry from {entry["path"]}'


def apply_revert(ctx: es.Context, entry: JournalEntryDto) -> bool:  # noqa: PLR0911  (one return per journal kind)
    """Undo entry; true when it is done with. A new journal kind fails type checking here."""
    match entry['kind']:
        case 'firewall-rule':
            return revert_firewall_rule(ctx, entry)
        case 'winget-install':
            return revert_winget_install(ctx, entry)
        case 'uv-python-install':
            return revert_uv_python(ctx, entry)
        case 'file':
            return revert_file(ctx, entry)
        case 'dir-created':
            return revert_dir_created(ctx, entry)
        case 'user-env':
            return revert_user_env(ctx, entry)
        case 'user-path':
            return revert_user_path(ctx, entry)
        case 'bashrc-block':
            return revert_bashrc_block(ctx, entry)
        case 'app-settings':
            return revert_app_settings(ctx, entry)
        case 'model':
            return revert_model(ctx, entry)
        case 'auth-key':
            return revert_auth_key(ctx, entry)


# ----------------------------------------------------------------------------- phases


def entries_for(ctx: es.Context, admin: bool) -> list[JournalEntryDto]:
    """The journal entries of the admin phase, or of the user phase, oldest first."""
    return [entry for entry in ctx.journal.entries if (entry['kind'] in ADMIN_KINDS) == admin]


def revert_entries(ctx: es.Context, entries: list[JournalEntryDto], apply: bool) -> RevertCountsDto:
    """Describe, and with apply undo, entries newest first."""
    reverted = kept = 0
    for entry in reversed(entries):
        ctx.log.info('  %s', describe_revert(entry))
        if not apply:
            continue
        if apply_revert(ctx, entry):
            ctx.journal.remove(entry)
            reverted += 1
        else:
            kept += 1
    return RevertCountsDto(reverted, kept)


def user_phase(ctx: es.Context, apply: bool) -> RevertCountsDto:
    """Revert everything that needs no elevation, then let running programs know."""
    entries = entries_for(ctx, admin=False)
    counts = revert_entries(ctx, entries, apply)
    if not apply:
        return counts
    kinds = {entry['kind'] for entry in entries}
    if kinds & {'user-env', 'user-path'}:
        ctx.user_env.broadcast()
    if kinds & OLLAMA_RESTART_KINDS and ctx.paths.ollama_app_exe.exists():
        try:
            install.restart_ollama(ctx)
        except RuntimeError as exc:
            ctx.log.warning('  WARN %s; start the Ollama app by hand', exc)
    return counts


def admin_phase(ctx: es.Context) -> RevertCountsDto:
    """Remove the firewall rules the installer added (needs elevation)."""
    return revert_entries(ctx, entries_for(ctx, admin=True), apply=True)


def remove_rules(ctx: es.Context, args: UninstallArgsDto, log_file: Path) -> None:
    """Run the admin phase inline when elevated, otherwise through an elevated child process."""
    if es.is_admin():
        admin_phase(ctx)
        return
    ctx.log.info('  starting the admin phase through UAC')
    child = [
        *es.python_args(ctx.repo / 'uninstall.py'),
        '--phase',
        'admin',
        '--home',
        str(args.home),
        '--config',
        str(args.config),
        '--log-file',
        str(log_file),
    ]
    code = es.run_elevated(child, ctx.repo)
    ctx.reload_journal()
    if code != 0:
        raise RuntimeError(f'the admin phase exited with code {code}; see {log_file}')
    remaining = entries_for(ctx, admin=True)
    if remaining:
        raise RuntimeError(f'firewall rules still in the journal after the admin phase: {remaining}')


def tidy_state_dir(ctx: es.Context) -> None:
    """Remove the backups directory and the state directory once they are empty."""
    for directory in (ctx.paths.journal_backups, ctx.paths.state_dir):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


# ----------------------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    """The command line of uninstall.py."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, default=es.REPO / 'install.config.json')
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('USERPROFILE', Path.home())))
    parser.add_argument('--secrets-file', type=Path)
    parser.add_argument('--yes', action='store_true', help='actually revert; without it, only list')
    parser.add_argument('--keep-models', action='store_true', help='leave the models the installer pulled or created')
    parser.add_argument('--phase', choices=('user', 'admin'), default='user')
    parser.add_argument('--log-file', type=Path)
    return parser


def parse_args(argv: list[str] | None) -> UninstallArgsDto:
    """The command line of uninstall.py, parsed."""
    return build_parser().parse_args(argv, namespace=UninstallArgsDto())


def build_context(args: UninstallArgsDto, log: logging.Logger, log_file: Path) -> es.Context:
    """The Context for the parsed command line."""
    return es.Context(
        repo=es.REPO,
        paths=es.Paths(args.home),
        config=es.load_config(args.config),
        log=log,
        runner=es.Runner(log),
        user_env=es.UserEnv(),
        secrets_file=args.secrets_file,
        log_file=log_file,
        keep_models=args.keep_models,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the selected phase; 0 on success, 2 when only listing, 1 on any error."""
    args = parse_args(argv)
    log_file = args.log_file or es.REPO / 'logs' / f'uninstall-{es.started_datetime_local_as_str()}.log'
    log = es.setup_logging(log_file)
    try:
        ctx = build_context(args, log, log_file)
        if args.phase == 'admin':
            if not es.is_admin():
                log.error('the admin phase requires elevation')
                return 1
            admin_phase(ctx)
            return 0
        log.info('uninstall.py home=%s apply=%s journal=%s', args.home, args.yes, ctx.paths.journal)
        if not ctx.journal.entries:
            log.info('the journal is empty: install.py recorded no change on this machine, nothing to revert')
            return 0
        log.info('what existed before install.py ran is not in the journal and stays')
        log.info('[firewall rules]')
        rules = entries_for(ctx, admin=True)
        if rules:
            if args.yes:
                remove_rules(ctx, args, log_file)
            else:
                revert_entries(ctx, rules, apply=False)
        else:
            log.info('  none recorded')
        log.info('[everything else]')
        reverted, kept = user_phase(ctx, apply=args.yes)
        if not args.yes:
            log.info('nothing reverted; re-run with --yes to apply')
            return EXIT_NOTHING_REMOVED
        tidy_state_dir(ctx)
        log.info('uninstall finished: %d entries reverted, %d kept (see above)', reverted + len(rules), kept)
        return 0
    except (es.ConfigError, es.CommandError, es.ElevationError, RuntimeError, OSError) as exc:
        log.error('ERROR %s', exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
