"""Pull Ollama tags while the 'ollama server direct outbound' firewall rule is lifted.

The rule is restored afterwards, whatever the outcome of the pulls. The program elevates
itself through UAC when needed, because changing a rule requires it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envsetup as es

if TYPE_CHECKING:
    import logging

    from envtypes import CommandRunner


def build_parser() -> argparse.ArgumentParser:
    """The command line of ollama_pull.py."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('tags', nargs='+', help='model tags to pull')
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('USERPROFILE', Path.home())))
    parser.add_argument('--log-file', type=Path)
    parser.add_argument('--elevated-child', action='store_true', help=argparse.SUPPRESS)
    return parser


class PullArgsDto(argparse.Namespace):
    """The parsed command line of ollama_pull.py."""

    tags: list[str]
    home: Path
    log_file: Path | None
    elevated_child: bool


def pull_with_rule_lifted(runner: CommandRunner, exe: str, tags: list[str], log: logging.Logger) -> int:
    """Pull each tag with the server rule disabled; 1 when any pull failed."""
    rule = es.firewall_rule(runner, es.RULE_OLLAMA_SERVER)
    lifted = False
    code = 0
    try:
        if rule is not None and rule['enabled'] == 'True':
            log.info("disabling '%s'", es.RULE_OLLAMA_SERVER)
            es.firewall_set_enabled(runner, es.RULE_OLLAMA_SERVER, False)
            lifted = True
        for tag in tags:
            log.info('ollama pull %s', tag)
            completed = runner.run([exe, 'pull', tag], check=False, stream=True)
            if completed.returncode != 0:
                log.error('ERROR ollama pull %s exited with %d', tag, completed.returncode)
                code = 1
    finally:
        if lifted:
            log.info("re-enabling '%s'", es.RULE_OLLAMA_SERVER)
            es.firewall_set_enabled(runner, es.RULE_OLLAMA_SERVER, True)
    return code


def main(argv: list[str] | None = None) -> int:
    """Elevate when needed, then pull; the elevated window stays open on failure."""
    args = build_parser().parse_args(argv, namespace=PullArgsDto())
    log_file = args.log_file or es.REPO / 'logs' / f'ollama-pull-{es.started_datetime_local_as_str()}.log'
    log = es.setup_logging(log_file)
    paths = es.Paths(args.home)
    if not es.is_admin():
        log.info('starting an elevated window through UAC; progress is shown there and in %s', log_file)
        child = [
            *es.python_args(es.REPO / 'ollama_pull.py'),
            *args.tags,
            '--home',
            str(args.home),
            '--log-file',
            str(log_file),
            '--elevated-child',
        ]
        try:
            return es.run_elevated(child, es.REPO)
        except es.ElevationError as exc:
            log.error('ERROR %s', exc)
            return 1
    exe = str(paths.ollama_exe) if paths.ollama_exe.exists() else shutil.which('ollama')
    if exe is None:
        log.error('ERROR ollama.exe not found')
        code = 1
    else:
        code = pull_with_rule_lifted(es.Runner(log), exe, args.tags, log)
    if args.elevated_child and code != 0:
        input('Press Enter to close this window')
    return code


if __name__ == '__main__':
    sys.exit(main())
