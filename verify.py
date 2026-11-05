"""Read-only verification of the local-LLM environment (plan, Appendix D).

Outcomes per line: PASS; SKIP (not applicable, with the reason); WARN (present but different
from the configuration, left unchanged by the installer); FAIL. Exit status 1 when any FAIL.
"""

from __future__ import annotations

import argparse
import contextlib
import http
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess  # nosec B404
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envsetup as es
from envtypes import ProxyProbeDto

if TYPE_CHECKING:
    from collections.abc import Callable

    from envtypes import BlockState, CommandRunner, EgressProxyModule, JsonValue, OutcomeStatus

PROXY_DENIED_PROBE = 'https://api.openai.com'
PROXY_ALLOWED_PROBE = 'https://docs.python.org'
PROXY_START_SECONDS = 30
PROXY_STOP_SECONDS = 10
VERSION_TIMEOUT_SECONDS = 120
OLLAMA_PORT = 11434
LOOPBACK_ADDRESSES = ('127.0.0.1', '::1')


@dataclass
class OutcomeDto:
    """One verification line: PASS, SKIP, WARN or FAIL, the check name and the detail."""

    status: OutcomeStatus
    name: str
    detail: str


class VerifyArgsDto(argparse.Namespace):
    """The parsed command line of verify.py."""

    config: Path
    home: Path
    secrets_file: Path | None
    log_file: Path | None


def ollama_exe(ctx: es.Context) -> str | None:
    """The Ollama binary under the profile root, else the one on PATH, else None."""
    if ctx.paths.ollama_exe.exists():
        return str(ctx.paths.ollama_exe)
    return shutil.which('ollama')


def version_in(text: str) -> str | None:
    """The first x.y.z in text, or None."""
    match = re.search(r'(\d+\.\d+\.\d+)', text)
    return match.group(1) if match else None


# ----------------------------------------------------------------------------- checks


def check_ollama(ctx: es.Context) -> list[OutcomeDto]:
    """The Ollama version against the pin, and the server listening on loopback only."""
    exe = ollama_exe(ctx)
    pinned = ctx.config['ollama']['version']
    if exe is None:
        return [OutcomeDto('FAIL', 'ollama', 'ollama.exe not found')]
    completed = ctx.runner.run([exe, '--version'], check=False)
    version = version_in((completed.stdout or '') + (completed.stderr or ''))
    results: list[OutcomeDto] = []
    if version == pinned:
        results.append(OutcomeDto('PASS', 'ollama version', f'{version} at {exe}'))
    else:
        results.append(OutcomeDto('WARN', 'ollama version', f'{version or "unknown"} at {exe}; pinned {pinned}'))
    api = es.api_version()
    if api is None:
        results.append(OutcomeDto('WARN', 'ollama server', 'not answering on 127.0.0.1:11434 (start the Ollama app)'))
        return results
    addresses = es.listening_addresses(ctx.runner, OLLAMA_PORT)
    foreign = [address for address in addresses if address not in LOOPBACK_ADDRESSES]
    if foreign:
        results.append(
            OutcomeDto('FAIL', 'ollama server', f'listening on {", ".join(addresses)}; loopback only is expected')
        )
    else:
        results.append(OutcomeDto('PASS', 'ollama server', f'{api} on {", ".join(addresses) or "127.0.0.1"}'))
    return results


def check_server_json(ctx: es.Context) -> list[OutcomeDto]:
    """server.json carries the configured keys."""
    try:
        current: JsonValue = json.loads(ctx.paths.server_json.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return [OutcomeDto('FAIL', 'server.json', f'{ctx.paths.server_json}: {exc}')]
    if not isinstance(current, dict):
        return [OutcomeDto('FAIL', 'server.json', f'{ctx.paths.server_json}: not a JSON object')]
    wanted = ctx.config['ollama']['server_json']
    wrong = {key: current.get(key) for key, value in wanted.items() if current.get(key) != value}
    if wrong:
        return [OutcomeDto('FAIL', 'server.json', f'expected {wanted}, found {wrong}')]
    return [OutcomeDto('PASS', 'server.json', str(wanted))]


def check_models(ctx: es.Context) -> list[OutcomeDto]:
    """Every required tag is installed and the created tags carry the Modelfile parameters."""
    exe = ollama_exe(ctx)
    if exe is None or es.api_version() is None:
        return [OutcomeDto('SKIP', 'models', 'Ollama server not running')]
    present = es.parse_ollama_list(ctx.runner.run([exe, 'list']).stdout)
    tags = es.required_tags(ctx.config)
    missing = es.missing_tags(tags, present)
    results: list[OutcomeDto] = []
    if missing:
        results.append(OutcomeDto('FAIL', 'models', 'missing: ' + ', '.join(missing)))
    else:
        results.append(OutcomeDto('PASS', 'models', ', '.join(tags)))
    creates = dict(ctx.config['models']['create'])
    if ctx.config['models']['install_optional']:
        creates.update(ctx.config['models']['optional_create'])
    for name, modelfile in creates.items():
        if name in missing:
            continue
        wanted = (ctx.repo / modelfile).read_text(encoding='utf-8')
        actual = ctx.runner.run([exe, 'show', '--modelfile', name]).stdout
        wanted_params = {tuple(line.split()) for line in wanted.splitlines() if line.startswith('PARAMETER')}
        actual_params = {tuple(line.split()) for line in actual.splitlines() if line.startswith('PARAMETER')}
        if wanted_params - actual_params:
            results.append(OutcomeDto('WARN', f'model {name}', f'Modelfile parameters differ from {modelfile}'))
    return results


def check_app_settings(ctx: es.Context) -> list[OutcomeDto]:
    """The Ollama app settings database holds the configured values."""
    wanted = ctx.config['ollama']['app_settings']
    state = es.app_settings_state(ctx.paths.app_db, wanted)
    if state == 'same':
        return [OutcomeDto('PASS', 'app settings', str(wanted))]
    if state == 'different':
        return [OutcomeDto('FAIL', 'app settings', f'expected {wanted}')]
    return [OutcomeDto('SKIP', 'app settings', f'{state}: {ctx.paths.app_db}')]


def check_user_env(ctx: es.Context) -> list[OutcomeDto]:
    """The configured user environment variables are set."""
    wanted = ctx.config['ollama']['user_env']
    wrong = {name: ctx.user_env.get(name) for name, value in wanted.items() if ctx.user_env.get(name) != value}
    if wrong:
        return [OutcomeDto('FAIL', 'user environment', f'expected {wanted}, found {wrong}')]
    return [OutcomeDto('PASS', 'user environment', str(wanted))]


def load_proxy_module(path: Path) -> EgressProxyModule:
    """Import the installed egress_proxy.py so its own deny-list parser can be exercised."""
    spec = importlib.util.spec_from_file_location('installed_egress_proxy', path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast('EgressProxyModule', module)  # the template this repository ships, defines both functions


def template_outcome(ctx: es.Context) -> OutcomeDto:
    """The installed template files against the repository copies."""
    root = ctx.repo / 'local-llm'
    missing: list[str] = []
    differing: list[str] = []
    for rel in es.template_files(ctx.repo):
        dest = ctx.paths.local_llm / rel
        if not dest.exists():
            missing.append(rel.as_posix())
        elif es.files_differ(root / rel, dest):
            differing.append(rel.as_posix())
    if missing:
        return OutcomeDto('FAIL', 'local-llm files', 'missing: ' + ', '.join(missing))
    if differing:
        return OutcomeDto('WARN', 'local-llm files', 'differ from the repository: ' + ', '.join(differing))
    return OutcomeDto(
        'PASS', 'local-llm files', f'{len(es.template_files(ctx.repo))} template files present and identical'
    )


def binary_outcomes(ctx: es.Context) -> list[OutcomeDto]:
    """The private harness binaries against their pinned SHA-256."""
    results: list[OutcomeDto] = []
    for name, spec, dest in es.harness_binaries(ctx.config, ctx.paths):
        if not dest.exists():
            results.append(OutcomeDto('FAIL', f'{name} binary', f'{dest} missing'))
        elif es.sha256_file(dest) != spec['exe_sha256']:
            results.append(OutcomeDto('WARN', f'{name} binary', f'{dest} is not the pinned {spec["version"]}'))
        else:
            results.append(OutcomeDto('PASS', f'{name} binary', f'{dest} hashes to the pinned {spec["version"]}'))
    return results


def denylist_outcome(ctx: es.Context) -> OutcomeDto:
    """The installed deny list, parsed by the installed proxy: no duplicates, api.openai.com denied."""
    proxy = ctx.paths.local_llm / 'egress_proxy.py'
    denylist = ctx.paths.local_llm / 'egress-denylist.txt'
    if not (proxy.exists() and denylist.exists()):
        return OutcomeDto('FAIL', 'deny list', 'egress_proxy.py or egress-denylist.txt missing')
    module = load_proxy_module(proxy)
    entries = module.denied_domains()
    raw = [line.split('#', 1)[0].strip() for line in denylist.read_text(encoding='utf-8').splitlines()]
    raw = [entry for entry in raw if entry]
    duplicates = sorted({entry for entry in raw if raw.count(entry) > 1})
    if duplicates:
        return OutcomeDto('FAIL', 'deny list', f'duplicates: {duplicates}')
    if not module.is_denied('api.openai.com'):
        return OutcomeDto('FAIL', 'deny list', 'api.openai.com is not denied')
    return OutcomeDto('PASS', 'deny list', f'{len(entries)} entries, no duplicates')


def check_local_llm_files(ctx: es.Context) -> list[OutcomeDto]:
    """Templates, private binaries and the deny list under ~/local-llm."""
    return [template_outcome(ctx), *binary_outcomes(ctx), denylist_outcome(ctx)]


def check_firewall(ctx: es.Context) -> list[OutcomeDto]:
    """Every configured outbound block rule exists and matches."""
    results: list[OutcomeDto] = []
    for name, program in es.firewall_rules_for(ctx.config, ctx.paths).items():
        rule = es.firewall_rule(ctx.runner, name)
        if rule is None:
            results.append(OutcomeDto('FAIL', 'firewall', f"'{name}' missing"))
        elif not es.rule_matches(rule, program):
            results.append(OutcomeDto('WARN', 'firewall', f"'{name}' differs: {rule}"))
        else:
            results.append(OutcomeDto('PASS', 'firewall', f"'{name}' blocks {program}"))
    return results


def port_open(port: int) -> bool:
    """Whether something accepts connections on 127.0.0.1:port."""
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def through_proxy(url: str) -> ProxyProbeDto:
    """What a HEAD through the proxy met: allowed with the code, or denied or offline with the reason."""
    proxy = f'http://127.0.0.1:{es.PROXY_PORT}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    try:
        # The probe URLs are the https constants above; http_request() refuses any other scheme.
        with opener.open(es.http_request(url, 'HEAD'), timeout=20) as response:
            return ProxyProbeDto('allowed', str(response.status))
    except urllib.error.HTTPError as exc:
        exc.close()
        denied = exc.code == http.HTTPStatus.FORBIDDEN
        return ProxyProbeDto('denied' if denied else 'allowed', str(exc.code))
    except (urllib.error.URLError, OSError) as exc:
        text = str(exc)
        return ProxyProbeDto('denied' if '403' in text else 'offline', text)


def start_proxy(ctx: es.Context, proxy: Path, uv: str) -> subprocess.Popen[bytes]:
    """Start the proxy from its installed location, detached from this console's input and output."""
    args = [uv, 'run', '--no-project', '--python', ctx.config['tools']['python_for_proxy'], 'python', '-I', str(proxy)]
    # uv is the resolved full path and proxy the installed file; no shell, no user-supplied arguments.
    return subprocess.Popen(  # nosec B603  # noqa: S603
        args,
        cwd=str(proxy.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_process_tree(runner: CommandRunner, process: subprocess.Popen[bytes]) -> None:
    """Stop a process together with its children.

    `uv run` starts Python as a child, and terminating uv on Windows leaves that child running. The
    proxy would keep its port and, with its working directory in ~/local-llm, block deleting that tree.
    """
    runner.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], check=False)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=PROXY_STOP_SECONDS)


def proxy_outcomes(denied: ProxyProbeDto, allowed: ProxyProbeDto) -> list[OutcomeDto]:
    """Outcomes for the two probes: a denied host must be refused, an allowed host must answer."""
    denied_kind, denied_detail = denied
    allowed_kind, allowed_detail = allowed
    if denied_kind == 'offline' and allowed_kind == 'offline':
        return [OutcomeDto('SKIP', 'proxy', f'offline: {allowed_detail}')]
    results: list[OutcomeDto] = []
    if denied_kind == 'denied':
        results.append(OutcomeDto('PASS', 'proxy deny', f'{PROXY_DENIED_PROBE} refused ({denied_detail})'))
    else:
        results.append(
            OutcomeDto('FAIL', 'proxy deny', f'{PROXY_DENIED_PROBE} not refused ({denied_kind} {denied_detail})')
        )
    if allowed_kind == 'allowed':
        results.append(OutcomeDto('PASS', 'proxy allow', f'{PROXY_ALLOWED_PROBE} reachable ({allowed_detail})'))
    elif allowed_kind == 'offline':
        results.append(OutcomeDto('SKIP', 'proxy allow', f'offline: {allowed_detail}'))
    else:
        results.append(OutcomeDto('FAIL', 'proxy allow', f'{PROXY_ALLOWED_PROBE} refused'))
    return results


def check_proxy(ctx: es.Context) -> list[OutcomeDto]:
    """The egress proxy refuses a denied host and passes an allowed one; started here when not running."""
    proxy = ctx.paths.local_llm / 'egress_proxy.py'
    if not proxy.exists():
        return [OutcomeDto('FAIL', 'proxy', f'{proxy} missing')]
    uv = shutil.which('uv')
    if uv is None:
        return [OutcomeDto('FAIL', 'proxy', 'uv is not on PATH')]
    child = None
    if not port_open(es.PROXY_PORT):
        child = start_proxy(ctx, proxy, uv)
        deadline = time.monotonic() + PROXY_START_SECONDS
        while not port_open(es.PROXY_PORT) and time.monotonic() < deadline:
            time.sleep(0.5)
    try:
        if not port_open(es.PROXY_PORT):
            detail = f'did not listen on 127.0.0.1:{es.PROXY_PORT} within {PROXY_START_SECONDS} seconds'
            return [OutcomeDto('FAIL', 'proxy', detail)]
        return proxy_outcomes(through_proxy(PROXY_DENIED_PROBE), through_proxy(PROXY_ALLOWED_PROBE))
    finally:
        if child is not None:
            stop_process_tree(ctx.runner, child)


def check_binaries_run(ctx: es.Context) -> list[OutcomeDto]:
    """Each private binary starts and reports its pinned version."""
    results: list[OutcomeDto] = []
    for name, spec, exe in es.harness_binaries(ctx.config, ctx.paths):
        if not exe.exists():
            results.append(OutcomeDto('FAIL', f'{name} --version', f'{exe} missing'))
            continue
        try:
            completed = ctx.runner.run([str(exe), '--version'], check=False, timeout=VERSION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            results.append(
                OutcomeDto('FAIL', f'{name} --version', f'timed out after {VERSION_TIMEOUT_SECONDS} seconds')
            )
            continue
        output = ((completed.stdout or '') + (completed.stderr or '')).strip()
        if completed.returncode != 0:
            results.append(OutcomeDto('FAIL', f'{name} --version', f'exit {completed.returncode}: {output[:200]}'))
        elif spec['version'] in output:
            results.append(
                OutcomeDto('PASS', f'{name} --version', output.splitlines()[0] if output else spec['version'])
            )
        else:
            results.append(OutcomeDto('WARN', f'{name} --version', f'{output[:80]}; pinned {spec["version"]}'))
    return results


def check_path_and_wrappers(ctx: es.Context) -> list[OutcomeDto]:
    """The wrappers directory leads the user Path, the wrappers win on PATH, the alias block is present."""
    results: list[OutcomeDto] = []
    shell = ctx.config['shell']
    if shell['path_wrappers']:
        position = es.path_position(ctx.user_env.path_list(), es.WRAPPERS_PATH_ENTRY)
        if position is None:
            results.append(OutcomeDto('FAIL', 'user Path', f'{es.WRAPPERS_PATH_ENTRY} missing'))
        elif position != 0:
            results.append(
                OutcomeDto('WARN', 'user Path', f'{es.WRAPPERS_PATH_ENTRY} at position {position}, not first')
            )
        else:
            results.append(OutcomeDto('PASS', 'user Path', f'{es.WRAPPERS_PATH_ENTRY} first'))
        for command in ('opencode', 'claude'):
            found = shutil.which(command)
            inside = found is not None and os.path.normcase(str(Path(found).parent)) == os.path.normcase(
                str(ctx.paths.wrappers_bin)
            )
            if inside:
                results.append(OutcomeDto('PASS', f'{command} wrapper', found or ''))
            else:
                results.append(
                    OutcomeDto(
                        'WARN',
                        f'{command} wrapper',
                        f'resolves to {found or "nothing"}; a machine-wide PATH entry or a new console may be needed',
                    )
                )
    if shell['bashrc_aliases']:
        block = (ctx.repo / 'home' / 'bashrc.local-llm').read_text(encoding='utf-8')
        text = ctx.paths.bashrc.read_text(encoding='utf-8') if ctx.paths.bashrc.exists() else ''
        state = es.block_state(text, block)
        statuses: dict[BlockState, OutcomeStatus] = {'same': 'PASS', 'different': 'WARN', 'absent': 'FAIL'}
        status = statuses[state]
        results.append(OutcomeDto(status, '.bashrc aliases', f'block {state}'))
    return results


def check_uv_python(ctx: es.Context) -> list[OutcomeDto]:
    """The uv command is on PATH and finds the Python the proxy runs on."""
    version = ctx.config['tools']['python_for_proxy']
    if shutil.which('uv') is None:
        return [OutcomeDto('FAIL', 'uv', 'not on PATH')]
    completed = ctx.runner.run(['uv', 'python', 'find', version], check=False)
    if completed.returncode != 0:
        return [OutcomeDto('FAIL', 'python for the proxy', f'uv python find {version} failed')]
    return [OutcomeDto('PASS', 'python for the proxy', (completed.stdout or '').strip())]


def check_secrets(ctx: es.Context) -> list[OutcomeDto]:
    """secrets.env, when present, is readable by the user only."""
    path = ctx.secrets_path
    if not path.exists():
        return [OutcomeDto('SKIP', 'secrets', f'{path} absent (air-gapped profile only)')]
    broad = es.broad_acl_entries(es.acl_sids(ctx.runner, path))
    if broad:
        return [OutcomeDto('FAIL', 'secrets', f'{path} readable by {", ".join(broad)}')]
    return [OutcomeDto('PASS', 'secrets', f'{path} restricted to the user')]


def check_journal(ctx: es.Context) -> list[OutcomeDto]:
    """The install journal that uninstall.py reverts from is readable; its content is summarised."""
    path = ctx.paths.journal
    if not path.exists():
        return [OutcomeDto('SKIP', 'install journal', f'{path} absent: install.py recorded no change here')]
    try:
        journal = es.Journal(path)
    except es.ConfigError as exc:
        return [OutcomeDto('FAIL', 'install journal', str(exc))]
    counts = Counter(entry['kind'] for entry in journal.entries)
    summary = ', '.join(f'{count} {kind}' for kind, count in sorted(counts.items()))
    return [OutcomeDto('PASS', 'install journal', f'{len(journal.entries)} entries in {path} ({summary})')]


CHECKS: list[Callable[[es.Context], list[OutcomeDto]]] = [
    check_ollama,
    check_server_json,
    check_models,
    check_app_settings,
    check_user_env,
    check_local_llm_files,
    check_firewall,
    check_proxy,
    check_binaries_run,
    check_path_and_wrappers,
    check_uv_python,
    check_secrets,
    check_journal,
]


def run_checks(ctx: es.Context) -> int:
    """Run every check, print one line per outcome and a summary; 1 when any FAIL."""
    results: list[OutcomeDto] = []
    for check in CHECKS:
        try:
            results.extend(check(ctx))
        except Exception as exc:  # one broken check must not hide the others
            name = getattr(check, '__name__', repr(check))
            results.append(OutcomeDto('FAIL', name, f'{type(exc).__name__}: {exc}'))
    for outcome in results:
        ctx.log.info('%-4s %s: %s', outcome.status, outcome.name, outcome.detail)
    counts = {status: sum(1 for r in results if r.status == status) for status in ('PASS', 'WARN', 'SKIP', 'FAIL')}
    ctx.log.info('verify: %s', ', '.join(f'{count} {status}' for status, count in counts.items()))
    return 1 if counts['FAIL'] else 0


def build_parser() -> argparse.ArgumentParser:
    """The command line of verify.py."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, default=es.REPO / 'install.config.json')
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('USERPROFILE', Path.home())))
    parser.add_argument('--secrets-file', type=Path)
    parser.add_argument('--log-file', type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Verify the environment under --home; 0 when nothing FAILs."""
    args = build_parser().parse_args(argv, namespace=VerifyArgsDto())
    log = es.setup_logging(args.log_file)
    try:
        config = es.load_config(args.config)
    except es.ConfigError as exc:
        log.error('ERROR %s', exc)
        return 1
    ctx = es.Context(
        repo=es.REPO,
        paths=es.Paths(args.home),
        config=config,
        log=log,
        runner=es.Runner(log),
        user_env=es.UserEnv(),
        secrets_file=args.secrets_file,
    )
    ctx.user_env.refresh_process_path()
    return run_checks(ctx)


if __name__ == '__main__':
    sys.exit(main())
