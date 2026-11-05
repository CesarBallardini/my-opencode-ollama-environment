"""Install the local-LLM environment on a Windows 11 laptop.

User phase (default): everything that needs no elevation. Admin phase (``--phase admin``):
the firewall rules; the user phase starts it through UAC when rules are missing, or runs it
inline when the process is already elevated. Every step is idempotent: it checks its target
state first and acts only on what is missing (plan, section 2.8).

Every change a step makes is recorded in the install journal (envsetup.Journal) before or right
after it happens, so that uninstall.py can revert exactly those changes and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeIs

sys.path.insert(0, str(Path(__file__).resolve().parent))
import envsetup as es
import verify

if TYPE_CHECKING:
    import logging
    import subprocess  # nosec B404
    from collections.abc import Callable

    from envtypes import JsonObject, JsonValue, Phase, StepName

REACHABILITY_URLS = ('https://github.com', 'https://ollama.com', 'https://downloads.claude.ai')
STEP_NAMES: tuple[StepName, ...] = (
    'ollama',
    'python',
    'ollama-config',
    'models',
    'app-settings',
    'local-llm',
    'binaries',
    'shell',
    'cloud',
    'firewall',
)
# The first Windows 11 build; earlier builds are Windows 10.
WINDOWS_11_FIRST_BUILD = 22000
SERVER_START_SECONDS = 120


class PreflightError(Exception):
    """A precondition of the install is not met."""


@dataclass
class Step:
    """One idempotent unit of work: check lists what is pending, do brings the target state about."""

    name: StepName
    check: Callable[[es.Context], list[str]]
    do: Callable[[es.Context], None]


class InstallArgsDto(argparse.Namespace):
    """The parsed command line of install.py."""

    config: Path
    home: Path
    secrets_file: Path | None
    dry_run: bool
    only: str
    skip: str
    models_from: Path | None
    replace_binaries: bool
    update_templates: bool
    phase: Phase
    log_file: Path | None


# ----------------------------------------------------------------------------- helpers


def read_json(path: Path) -> JsonObject | None:
    """The JSON object in path, or None when the file is absent, invalid or holds something else."""
    try:
        value: JsonValue = json.loads(path.read_text(encoding='utf-8'))
    except OSError, ValueError:
        return None
    return value if isinstance(value, dict) else None


def anthropic_key(auth: JsonObject) -> str | None:
    """The key in the anthropic entry of an OpenCode credentials file, or None."""
    entry = auth.get('anthropic')
    key = entry.get('key') if isinstance(entry, dict) else None
    return key if isinstance(key, str) else None


def ollama_exe(ctx: es.Context) -> str | None:
    """The Ollama binary under the profile root, else the one on PATH, else None."""
    if ctx.paths.ollama_exe.exists():
        return str(ctx.paths.ollama_exe)
    return shutil.which('ollama')


def version_of(ctx: es.Context, exe: str) -> str | None:
    """The x.y.z version that exe --version prints, or None."""
    completed = ctx.runner.run([exe, '--version'], check=False)
    match = re.search(r'(\d+\.\d+\.\d+)', (completed.stdout or '') + (completed.stderr or ''))
    return match.group(1) if match else None


def ollama_run(ctx: es.Context, *args: str, stream: bool = False) -> subprocess.CompletedProcess[str]:
    """Run the ollama command with args; raise on a non-zero exit."""
    exe = ollama_exe(ctx)
    if exe is None:
        raise RuntimeError('ollama.exe not found')
    return ctx.runner.run([exe, *args], stream=stream)


def ensure_server(ctx: es.Context) -> bool:
    """Whether the Ollama API answers, starting the app when it is installed but not running."""
    if es.api_version():
        return True
    if not ctx.paths.ollama_app_exe.exists():
        return False
    ctx.log.info('  starting the Ollama app')
    es.start_ollama_app(ctx.paths.ollama_app_exe, dict(os.environ))
    return es.wait_for_api(SERVER_START_SECONDS) is not None


def restart_ollama(ctx: es.Context) -> None:
    """Stop every Ollama process and start the app again with the current environment."""
    es.stop_ollama(ctx.runner)
    if not ctx.paths.ollama_app_exe.exists():
        ctx.log.warning('  WARN %s not found; start Ollama by hand', ctx.paths.ollama_app_exe)
        return
    es.start_ollama_app(ctx.paths.ollama_app_exe, dict(os.environ))
    if es.wait_for_api(SERVER_START_SECONDS) is None:
        raise RuntimeError(f'Ollama did not answer on 127.0.0.1:11434 within {SERVER_START_SECONDS} seconds')


def load_secrets(ctx: es.Context) -> dict[str, str]:
    """The secrets file as a dict, after restricting its ACL to the user; empty when absent."""
    path = ctx.secrets_path
    if not path.exists():
        return {}
    broad = es.broad_acl_entries(es.acl_sids(ctx.runner, path))
    if broad:
        if ctx.dry_run:
            ctx.log.warning('  WARN %s is readable by %s; the installer would restrict it', path, ', '.join(broad))
        else:
            ctx.log.info('  restricting the ACL of %s (granted to %s)', path, ', '.join(broad))
            es.restrict_acl(ctx.runner, path, os.environ['USERNAME'])
            broad = es.broad_acl_entries(es.acl_sids(ctx.runner, path))
            if broad:
                raise es.ConfigError(f'{path} is still readable by {", ".join(broad)}')
    return es.read_secrets(path)


# ----------------------------------------------------------------------------- journal helpers


def ensure_dir(ctx: es.Context, path: Path) -> None:
    """Create path and its parents, recording the topmost directory that did not exist.

    Under ~/local-llm the record marks a tree: the uninstaller deletes it whole, so the files
    written inside it later are not recorded one by one. Elsewhere the uninstaller removes the
    directory only once the files it recorded there are gone and nothing else is left.
    """
    if path.is_dir():
        return
    top = path
    while top.parent != ctx.paths.home and not top.parent.exists():
        top = top.parent
    path.mkdir(parents=True, exist_ok=True)
    if top != ctx.paths.home and not ctx.journal.covers_path(top):
        tree = top == ctx.paths.local_llm or top.is_relative_to(ctx.paths.local_llm)
        ctx.journal.add({'kind': 'dir-created', 'path': str(top), 'tree': tree})


def record_file(ctx: es.Context, path: Path) -> Path | None:
    """Record path before it is created or replaced; an existing file is moved to the backups.

    Nothing is recorded, and nothing moved, when the file or a directory above it is already in
    the journal: the uninstaller restores or deletes it from that earlier entry. The backup
    location is returned when a file was moved.
    """
    if ctx.journal.covers_path(path):
        return None
    backup = None
    if path.exists():
        ctx.paths.journal_backups.mkdir(parents=True, exist_ok=True)
        number = len(ctx.journal.entries)
        backup = ctx.paths.journal_backups / f'{number:04d}-{path.name}'
        while backup.exists():
            number += 1
            backup = ctx.paths.journal_backups / f'{number:04d}-{path.name}'
        shutil.move(str(path), str(backup))
    ctx.journal.add({'kind': 'file', 'path': str(path), 'backup': None if backup is None else str(backup)})
    return backup


# ----------------------------------------------------------------------------- steps


def check_ollama(ctx: es.Context) -> list[str]:
    """Pending: the winget install when no Ollama binary exists; another version is kept and reported."""
    spec = ctx.config['ollama']
    found = ollama_exe(ctx)
    if found is None:
        return [f'winget install {spec["winget_id"]} --version {spec["version"]}']
    version = version_of(ctx, found)
    if version and version != spec['version']:
        ctx.log.warning(
            '  WARN Ollama %s is installed; the pinned version is %s (left unchanged)', version, spec['version']
        )
    else:
        ctx.log.info('  Ollama %s found at %s', version or '(version unknown)', found)
    return []


def do_ollama(ctx: es.Context) -> None:
    """Install the pinned Ollama through winget, record it, and pick up the PATH it adds."""
    spec = ctx.config['ollama']
    es.winget_install(ctx.runner, spec['winget_id'], spec['version'])
    ctx.journal.add_once({'kind': 'winget-install', 'id': spec['winget_id']})
    ctx.user_env.refresh_process_path()


def check_python(ctx: es.Context) -> list[str]:
    """Pending: the uv-managed Python the proxy runs on, when uv cannot find it."""
    version = ctx.config['tools']['python_for_proxy']
    if shutil.which('uv') is None:
        return [f'uv python install {version} (uv is not on PATH)']
    completed = ctx.runner.run(['uv', 'python', 'find', version], check=False)
    return [] if completed.returncode == 0 else [f'uv python install {version}']


def do_python(ctx: es.Context) -> None:
    """Install the proxy's Python through uv and record it."""
    version = ctx.config['tools']['python_for_proxy']
    ctx.runner.run(['uv', 'python', 'install', version], stream=True)
    ctx.journal.add_once({'kind': 'uv-python-install', 'version': version})


def check_ollama_config(ctx: es.Context) -> list[str]:
    """Pending: server.json keys and user environment variables that differ from the configuration."""
    pending: list[str] = []
    current = read_json(ctx.paths.server_json) or {}
    for key, value in ctx.config['ollama']['server_json'].items():
        if current.get(key) != value:
            pending.append(f'set {key}={json.dumps(value)} in {ctx.paths.server_json}')
    for name, value in ctx.config['ollama']['user_env'].items():
        have = ctx.user_env.get(name)
        if have != value:
            suffix = f' (currently {have})' if have is not None else ''
            pending.append(f'set user environment variable {name}={value}{suffix}')
    return pending


def do_ollama_config(ctx: es.Context) -> None:
    """Merge the configured keys into server.json, set the user variables and restart Ollama."""
    current = read_json(ctx.paths.server_json) or {}
    merged: JsonObject = {**current, **ctx.config['ollama']['server_json']}
    if merged != current:
        ensure_dir(ctx, ctx.paths.server_json.parent)
        record_file(ctx, ctx.paths.server_json)
        ctx.paths.server_json.write_text(json.dumps(merged, indent=2) + '\n', encoding='utf-8')
    for name, value in ctx.config['ollama']['user_env'].items():
        have = ctx.user_env.get(name)
        if have != value:
            ctx.log.info('  %s: %r -> %r', name, have, value)
            ctx.journal.add_once({'kind': 'user-env', 'name': name, 'previous': have})
            ctx.user_env.set(name, value)
        os.environ[name] = value
    ctx.user_env.broadcast()
    if ollama_exe(ctx):
        restart_ollama(ctx)


def check_models(ctx: es.Context) -> list[str]:
    """Pending: the pulls and creates for tags that `ollama list` does not show."""
    models = ctx.config['models']
    tags = es.required_tags(ctx.config)
    pulls = set(models['pull']) | (set(models['optional_pull']) if models['install_optional'] else set[str]())
    if ollama_exe(ctx) is None:
        return [f'ollama {"pull" if tag in pulls else "create"} {tag}' for tag in tags]
    if not ensure_server(ctx):
        raise RuntimeError('the Ollama server is not running and could not be started')
    present = es.parse_ollama_list(ollama_run(ctx, 'list').stdout)
    return [f'ollama {"pull" if tag in pulls else "create"} {tag}' for tag in es.missing_tags(tags, present)]


def present_tags(ctx: es.Context) -> set[str]:
    """The normalised tags that `ollama list` shows."""
    return es.parse_ollama_list(ollama_run(ctx, 'list').stdout)


def do_models(ctx: es.Context) -> None:
    """Copy or pull the missing base models, then create the derived tags from the Modelfiles.

    Every tag that appears is recorded, so that the uninstaller removes those and no other.
    """
    models = ctx.config['models']
    present = present_tags(ctx)
    if ctx.models_from is not None:
        ctx.log.info('  copying models from %s', ctx.models_from)
        ensure_dir(ctx, ctx.paths.ollama_models)
        es.robocopy(ctx.runner, ctx.models_from, ctx.paths.ollama_models)
        restart_ollama(ctx)
        copied = present_tags(ctx) - present
        for tag in sorted(copied):
            ctx.journal.add_once({'kind': 'model', 'tag': tag})
        present |= copied
    pulls = list(models['pull'])
    if models['install_optional']:
        pulls += models['optional_pull']
    creates = dict(models['create'])
    if models['install_optional']:
        creates.update(models['optional_create'])
    missing_pulls = es.missing_tags(pulls, present)
    if missing_pulls:
        rule = es.firewall_rule(ctx.runner, es.RULE_OLLAMA_SERVER)
        if rule is not None and rule['enabled'] == 'True':
            raise RuntimeError(
                f"the rule '{es.RULE_OLLAMA_SERVER}' blocks ollama.exe; run ollama-pull.cmd for "
                + ', '.join(missing_pulls)
                + ' and then re-run the installer'
            )
    for tag in missing_pulls:
        ctx.log.info('  ollama pull %s', tag)
        ollama_run(ctx, 'pull', tag, stream=True)
        ctx.journal.add_once({'kind': 'model', 'tag': es.normalize_tag(tag)})
    for name, modelfile in creates.items():
        if es.normalize_tag(name) not in present:
            ctx.log.info('  ollama create %s -f %s', name, modelfile)
            ollama_run(ctx, 'create', name, '-f', str(ctx.repo / modelfile), stream=True)
            ctx.journal.add_once({'kind': 'model', 'tag': es.normalize_tag(name)})


def check_app_settings(ctx: es.Context) -> list[str]:
    """Pending: the Ollama app settings that differ; an unknown database layout is reported, not changed."""
    wanted = ctx.config['ollama']['app_settings']
    state = es.app_settings_state(ctx.paths.app_db, wanted)
    if state == 'same':
        return []
    if state == 'different':
        return [f'set {key}={value} in the Ollama app settings' for key, value in wanted.items()]
    ctx.log.warning(
        "  WARN Ollama app settings not updated (%s): in the app's Settings turn auto-update off and airplane mode on",
        state,
    )
    return []


def do_app_settings(ctx: es.Context) -> None:
    """Record the current app settings, stop Ollama, write the configured ones and start it again."""
    wanted = ctx.config['ollama']['app_settings']
    previous = es.app_settings_read(ctx.paths.app_db, list(wanted))
    es.stop_ollama(ctx.runner)
    es.app_settings_apply(ctx.paths.app_db, wanted)
    ctx.journal.add_once({'kind': 'app-settings', 'db': str(ctx.paths.app_db), 'previous': previous})
    restart_ollama(ctx)


def check_local_llm(ctx: es.Context) -> list[str]:
    """Pending: template files to copy, profile directories to create and the generated-once files."""
    pending: list[str] = []
    root = ctx.repo / 'local-llm'
    for rel in es.template_files(ctx.repo):
        dest = ctx.paths.local_llm / rel
        if not dest.exists():
            pending.append(f'copy {rel.as_posix()}')
        elif es.files_differ(root / rel, dest):
            if ctx.update_templates:
                pending.append(f'overwrite {rel.as_posix()} (--update-templates)')
            else:
                ctx.log.warning('  WARN %s differs from the repository template (left unchanged)', dest)
    for directory in ctx.paths.profile_dirs:
        if not directory.is_dir():
            pending.append(f'create {directory}')
    if not ctx.paths.claude_json.exists():
        pending.append('write claude/config/.claude.json from the template')
    if not ctx.paths.confidential_roots.exists():
        pending.append('write confidential-roots.txt from shell.confidential_roots')
    return pending


def confidential_roots_text(roots: list[str]) -> str:
    """The content of confidential-roots.txt for roots, with CRLF line ends for cmd."""
    lines = ['# One confidential directory per line; %VAR% references are expanded; # starts a comment.']
    return '\r\n'.join(lines + list(roots)) + '\r\n'


def do_local_llm(ctx: es.Context) -> None:
    """Copy the missing (or, with --update-templates, differing) templates and write the generated-once files."""
    root = ctx.repo / 'local-llm'
    ensure_dir(ctx, ctx.paths.local_llm)
    for rel in es.template_files(ctx.repo):
        dest = ctx.paths.local_llm / rel
        if not dest.exists() or (ctx.update_templates and es.files_differ(root / rel, dest)):
            ctx.log.info('  writing %s', dest)
            ensure_dir(ctx, dest.parent)
            record_file(ctx, dest)
            shutil.copy2(root / rel, dest)
    for directory in ctx.paths.profile_dirs:
        ensure_dir(ctx, directory)
    if not ctx.paths.claude_json.exists():
        template = root / 'claude' / 'config' / ('.claude.json' + es.TEMPLATE_SOURCE_SUFFIX)
        ensure_dir(ctx, ctx.paths.claude_json.parent)
        record_file(ctx, ctx.paths.claude_json)
        shutil.copy2(template, ctx.paths.claude_json)
    if not ctx.paths.confidential_roots.exists():
        record_file(ctx, ctx.paths.confidential_roots)
        ctx.paths.confidential_roots.write_text(
            confidential_roots_text(ctx.config['shell']['confidential_roots']), encoding='utf-8', newline=''
        )


def check_binaries(ctx: es.Context) -> list[str]:
    """Pending: the downloads of absent binaries; a different binary is kept unless --replace-binaries."""
    pending: list[str] = []
    for name, spec, dest in es.harness_binaries(ctx.config, ctx.paths):
        if not dest.exists():
            pending.append(f'download {name} {spec["version"]} to {dest}')
        elif es.sha256_file(dest) != spec['exe_sha256']:
            if ctx.replace_binaries:
                pending.append(f'replace {dest} with the pinned {name} {spec["version"]} (--replace-binaries)')
            else:
                ctx.log.warning('  WARN %s is not the pinned %s %s (left unchanged)', dest, name, spec['version'])
    return pending


def do_binaries(ctx: es.Context) -> None:
    """Download the pinned binaries into a staging directory, verify them, then move them into place.

    A binary that is replaced goes to the journal backups (the uninstaller puts it back), unless
    it lies in a directory the installer created, in which case it is simply overwritten.
    """
    staging = Path(tempfile.mkdtemp(prefix='my-opencode-ollama-'))
    try:
        for name, spec, dest in es.harness_binaries(ctx.config, ctx.paths):
            if dest.exists() and not (ctx.replace_binaries and es.sha256_file(dest) != spec['exe_sha256']):
                continue
            if name == 'opencode':
                archive = staging / 'opencode-windows-x64.zip'
                zip_sha256 = ctx.config['harness']['opencode']['zip_sha256']
                es.download_verified(spec['url'], archive, zip_sha256, ctx.log)
                staged = staging / 'opencode.exe'
                with zipfile.ZipFile(archive) as bundle:
                    staged.write_bytes(bundle.read('opencode.exe'))
            else:
                staged = staging / 'claude.exe'
                es.download_verified(spec['url'], staged, spec['exe_sha256'], ctx.log)
            if es.sha256_file(staged) != spec['exe_sha256']:
                raise es.IntegrityError(f'{staged.name}: executable SHA-256 differs from the pinned value')
            ensure_dir(ctx, dest.parent)
            backup = record_file(ctx, dest)
            if backup is not None:
                ctx.log.info('  previous binary kept as %s', backup)
            shutil.move(str(staged), str(dest))
            ctx.log.info('  installed %s', dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def bashrc_block(ctx: es.Context) -> str:
    """The alias block the repository ships for ~/.bashrc."""
    return (ctx.repo / 'home' / 'bashrc.local-llm').read_text(encoding='utf-8')


def check_shell(ctx: es.Context) -> list[str]:
    """Pending: the .bashrc alias block and the wrappers entry at the front of the user Path."""
    pending: list[str] = []
    shell = ctx.config['shell']
    if shell['bashrc_aliases']:
        text = ctx.paths.bashrc.read_text(encoding='utf-8') if ctx.paths.bashrc.exists() else ''
        state = es.block_state(text, bashrc_block(ctx))
        if state == 'absent':
            pending.append(f'append the local-llm alias block to {ctx.paths.bashrc}')
        elif state == 'different':
            if ctx.update_templates:
                pending.append(f'replace the local-llm alias block in {ctx.paths.bashrc} (--update-templates)')
            else:
                ctx.log.warning(
                    '  WARN the local-llm alias block in %s differs from the repository (left unchanged)',
                    ctx.paths.bashrc,
                )
    if shell['path_wrappers']:
        position = es.path_position(ctx.user_env.path_list(), es.WRAPPERS_PATH_ENTRY)
        if position is None:
            pending.append(f'prepend {es.WRAPPERS_PATH_ENTRY} to the user Path')
        elif position != 0:
            ctx.log.warning('  WARN %s is in the user Path but not first (left unchanged)', es.WRAPPERS_PATH_ENTRY)
    return pending


def do_shell(ctx: es.Context) -> None:
    """Write the alias block and prepend the wrappers directory to the user Path."""
    shell = ctx.config['shell']
    if shell['bashrc_aliases']:
        existed = ctx.paths.bashrc.exists()
        text = ctx.paths.bashrc.read_text(encoding='utf-8') if existed else ''
        state = es.block_state(text, bashrc_block(ctx))
        if state == 'absent' or (state == 'different' and ctx.update_templates):
            ctx.journal.add_once(
                {
                    'kind': 'bashrc-block',
                    'path': str(ctx.paths.bashrc),
                    'created': not existed,
                    'previous_block': es.block_text(text),
                }
            )
            ctx.paths.bashrc.parent.mkdir(parents=True, exist_ok=True)
            ctx.paths.bashrc.write_text(es.block_apply(text, bashrc_block(ctx)), encoding='utf-8', newline='\n')
    if shell['path_wrappers']:
        entries = ctx.user_env.path_list()
        if es.path_position(entries, es.WRAPPERS_PATH_ENTRY) is None:
            ctx.user_env.set_path_list(es.path_prepend(entries, es.WRAPPERS_PATH_ENTRY))
            ctx.journal.add_once({'kind': 'user-path', 'entry': es.WRAPPERS_PATH_ENTRY})
            ctx.user_env.broadcast()
            ctx.user_env.refresh_process_path()


def resolved_cloud(ctx: es.Context) -> dict[str, str | None]:
    """Harness name -> the cloud binary found on PATH outside the wrappers directory, or None."""
    return {name: es.which_outside(name, ctx.paths.wrappers_bin) for name in ctx.config['cloud']['winget']}


def cloud_binaries_text(resolved: dict[str, str | None]) -> str:
    """The content of cloud-binaries.cmd for resolved, with CRLF line ends for cmd."""
    lines = ['@rem Generated by install.py: the cloud (unrestricted) harness binaries, or empty when absent.']
    for name, path in resolved.items():
        lines.append(f'@set "{name.upper()}_CLOUD_EXE={path or ""}"')
    return '\r\n'.join(lines) + '\r\n'


def check_cloud(ctx: es.Context) -> list[str]:
    """Pending: cloud harness installs, the Anthropic key for OpenCode, and cloud-binaries.cmd."""
    pending: list[str] = []
    cloud = ctx.config['cloud']
    resolved = resolved_cloud(ctx)
    if cloud['install_harnesses']:
        for name, package_id in cloud['winget'].items():
            if not resolved[name]:
                pending.append(f'winget install {package_id}')
        key = load_secrets(ctx).get('ANTHROPIC_API_KEY')
        if key:
            auth = read_json(ctx.paths.opencode_cloud_auth) or {}
            if 'anthropic' not in auth:
                pending.append(f'write the Anthropic key into {ctx.paths.opencode_cloud_auth}')
            elif anthropic_key(auth) != key:
                ctx.log.warning(
                    '  WARN %s already has an anthropic entry with another key; the key in secrets.env '
                    'was not applied',
                    ctx.paths.opencode_cloud_auth,
                )
    current = ctx.paths.cloud_binaries_cmd.read_bytes() if ctx.paths.cloud_binaries_cmd.exists() else None
    if current != cloud_binaries_text(resolved).encode('utf-8'):
        pending.append(f'write {ctx.paths.cloud_binaries_cmd}')
    return pending


def write_anthropic_key(ctx: es.Context, key: str) -> None:
    """Add the anthropic entry to the cloud OpenCode credentials, recording the file or the entry."""
    path = ctx.paths.opencode_cloud_auth
    existing = read_json(path)
    if existing is None:
        ensure_dir(ctx, path.parent)
        record_file(ctx, path)
        auth: JsonObject = {}
    else:
        auth = existing
        ctx.journal.add_once({'kind': 'auth-key', 'path': str(path)})
    auth['anthropic'] = {'type': 'api', 'key': key}
    path.write_text(json.dumps(auth, indent=2) + '\n', encoding='utf-8')
    ctx.log.info('  wrote the Anthropic key into %s', path)


def do_cloud(ctx: es.Context) -> None:
    """Install the missing cloud harnesses, write the key once, and regenerate cloud-binaries.cmd."""
    cloud = ctx.config['cloud']
    if cloud['install_harnesses']:
        for name, package_id in cloud['winget'].items():
            if not es.which_outside(name, ctx.paths.wrappers_bin):
                es.winget_install(ctx.runner, package_id)
                ctx.journal.add_once({'kind': 'winget-install', 'id': package_id})
                ctx.user_env.refresh_process_path()
        key = load_secrets(ctx).get('ANTHROPIC_API_KEY')
        if key and 'anthropic' not in (read_json(ctx.paths.opencode_cloud_auth) or {}):
            write_anthropic_key(ctx, key)
    wanted = cloud_binaries_text(resolved_cloud(ctx)).encode('utf-8')
    current = ctx.paths.cloud_binaries_cmd.read_bytes() if ctx.paths.cloud_binaries_cmd.exists() else None
    if current != wanted:
        ensure_dir(ctx, ctx.paths.cloud_binaries_cmd.parent)
        record_file(ctx, ctx.paths.cloud_binaries_cmd)
        ctx.paths.cloud_binaries_cmd.write_bytes(wanted)


def check_firewall(ctx: es.Context) -> list[str]:
    """Pending: the configured rules that do not exist; a rule that differs is reported, not changed."""
    pending: list[str] = []
    for name, program in es.firewall_rules_for(ctx.config, ctx.paths).items():
        rule = es.firewall_rule(ctx.runner, name)
        if rule is None:
            pending.append(f"add the outbound block rule '{name}' for {program}")
        elif not es.rule_matches(rule, program):
            ctx.log.warning(
                "  WARN the rule '%s' exists but differs from the expected one (%s); left unchanged", name, rule
            )
    return pending


def do_firewall(ctx: es.Context) -> None:
    """Add the configured rules that do not exist (needs elevation), recording each one."""
    for name, program in es.firewall_rules_for(ctx.config, ctx.paths).items():
        if es.firewall_rule(ctx.runner, name) is None:
            ctx.log.info("  adding '%s'", name)
            es.firewall_add(ctx.runner, name, program)
            ctx.journal.add_once({'kind': 'firewall-rule', 'name': name})


USER_STEPS = [
    Step('ollama', check_ollama, do_ollama),
    Step('python', check_python, do_python),
    Step('ollama-config', check_ollama_config, do_ollama_config),
    Step('models', check_models, do_models),
    Step('app-settings', check_app_settings, do_app_settings),
    Step('local-llm', check_local_llm, do_local_llm),
    Step('binaries', check_binaries, do_binaries),
    Step('shell', check_shell, do_shell),
    Step('cloud', check_cloud, do_cloud),
]
FIREWALL_STEP = Step('firewall', check_firewall, do_firewall)


# ----------------------------------------------------------------------------- orchestration


def selected(ctx: es.Context, name: StepName) -> bool:
    """Whether --only/--skip select the step called name."""
    if ctx.only and name not in ctx.only:
        return False
    return name not in ctx.skip


def run_step(ctx: es.Context, step: Step) -> None:
    """Check, act unless dry-run, then check again: the step must report nothing pending."""
    ctx.log.info('[%s]', step.name)
    pending = step.check(ctx)
    if not pending:
        ctx.log.info('  already done')
        return
    for action in pending:
        ctx.log.info('  pending: %s', action)
    if ctx.dry_run:
        ctx.log.info('  dry run: no change')
        return
    step.do(ctx)
    remaining = step.check(ctx)
    if remaining:
        raise RuntimeError(f'step {step.name}: still pending after execution: {remaining}')
    ctx.log.info('  done')


def platform_problems() -> list[str]:
    """Why this machine or session cannot run the installer: Windows 11, 64-bit, console user."""
    problems: list[str] = []
    if not es.is_windows() or es.windows_build() < WINDOWS_11_FIRST_BUILD:
        problems.append(f'Windows 11 (build {WINDOWS_11_FIRST_BUILD} or later) is required')
    if platform.machine().upper() not in ('AMD64', 'ARM64'):
        problems.append('64-bit Windows is required')
    user = os.environ.get('USERNAME', '')
    console = es.console_user()
    if console and user.lower() != console.lower():
        problems.append(f'run as the console user: this process runs as {user} but the session belongs to {console}')
    return problems


def download_problems(ctx: es.Context, ollama_missing: bool, cloud_missing: bool) -> list[str]:
    """Why the downloads this run needs cannot happen: no winget, or the sources are unreachable."""
    problems: list[str] = []
    binaries_missing = selected(ctx, 'binaries') and any(
        not binary.dest.exists() for binary in es.harness_binaries(ctx.config, ctx.paths)
    )
    if shutil.which('winget') is None:
        if ollama_missing or cloud_missing:
            problems.append('winget is not available; update App Installer from Microsoft Store')
        else:
            ctx.log.warning('  WARN winget is not available (not needed for this run)')
    if (ollama_missing or binaries_missing or cloud_missing) and not ctx.dry_run:
        problems.extend(f'{url} is unreachable' for url in REACHABILITY_URLS if not es.reachable(url))
    return problems


def disk_problems(ctx: es.Context, ollama_missing: bool) -> list[str]:
    """Why the system drive is too small for a first install; a smaller later run only warns."""
    system_drive = Path(os.environ.get('SYSTEMDRIVE', 'C:') + '\\')
    needed = ctx.config[
        'minimum_free_gb_with_optional' if ctx.config['models']['install_optional'] else 'minimum_free_gb'
    ]
    free = es.free_gb(system_drive)
    if ollama_missing and free < needed:
        return [f'{free:.0f} GB free on {system_drive}; {needed} GB are required']
    if free < needed:
        ctx.log.warning(
            '  WARN %.0f GB free on %s; the configuration expects %d GB for a full install', free, system_drive, needed
        )
    return []


def preflight(ctx: es.Context) -> None:
    """Raise PreflightError with every unmet precondition; warn about the optional ones.

    A step that --skip or --only leaves out needs no download, so its sources and winget are not
    required for the run.
    """
    ollama_missing = selected(ctx, 'ollama') and ollama_exe(ctx) is None
    cloud_missing = (
        selected(ctx, 'cloud')
        and ctx.config['cloud']['install_harnesses']
        and any(not path for path in resolved_cloud(ctx).values())
    )
    problems = platform_problems()
    problems += download_problems(ctx, ollama_missing, cloud_missing)
    problems += disk_problems(ctx, ollama_missing)
    system32 = Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'System32'
    if shutil.which('nvidia-smi') is None and not (system32 / 'nvidia-smi.exe').exists():
        ctx.log.warning('  WARN no NVIDIA driver detected (nvidia-smi); Ollama will run on the CPU')
    if problems:
        raise PreflightError('\n'.join(problems))
    ctx.log.info('  ok')


def firewall_phase(ctx: es.Context, config_path: Path) -> None:
    """Run the firewall step inline when elevated, otherwise through an elevated child process."""
    if not selected(ctx, FIREWALL_STEP.name):
        ctx.log.info('[firewall] skipped')
        return
    ctx.log.info('[firewall]')
    pending = check_firewall(ctx)
    if not pending:
        ctx.log.info('  already done')
        return
    for action in pending:
        ctx.log.info('  pending: %s', action)
    if ctx.dry_run:
        ctx.log.info('  dry run: no elevation, no change')
        return
    if es.is_admin():
        do_firewall(ctx)
    else:
        ctx.log.info('  starting the admin phase through UAC')
        args = [
            *es.python_args(ctx.repo / 'install.py'),
            '--phase',
            'admin',
            '--home',
            str(ctx.paths.home),
            '--config',
            str(config_path),
        ]
        if ctx.log_file is not None:
            args += ['--log-file', str(ctx.log_file)]
        code = es.run_elevated(args, ctx.repo)
        ctx.reload_journal()
        if code != 0:
            raise RuntimeError(f'the admin phase exited with code {code}; see {ctx.log_file}')
    remaining = check_firewall(ctx)
    if remaining:
        raise RuntimeError(f'firewall rules still missing after the admin phase: {remaining}')
    ctx.log.info('  done')


def build_parser() -> argparse.ArgumentParser:
    """The command line of install.py (plan, section 2.5)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, default=es.REPO / 'install.config.json')
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('USERPROFILE', Path.home())))
    parser.add_argument('--secrets-file', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--only', default='', help='comma-separated step names: ' + ', '.join(STEP_NAMES))
    parser.add_argument('--skip', default='', help='comma-separated step names')
    parser.add_argument('--models-from', type=Path, help='copy an Ollama models directory instead of pulling')
    parser.add_argument('--replace-binaries', action='store_true')
    parser.add_argument('--update-templates', action='store_true')
    parser.add_argument('--phase', choices=('user', 'admin'), default='user')
    parser.add_argument('--log-file', type=Path)
    return parser


def parse_args(argv: list[str] | None) -> InstallArgsDto:
    """The command line of install.py, parsed."""
    return build_parser().parse_args(argv, namespace=InstallArgsDto())


def is_step_name(name: str) -> TypeIs[StepName]:
    """Whether name is one of the steps."""
    return name in STEP_NAMES


def parse_names(value: str) -> set[StepName]:
    """The step names in a comma-separated list; unknown names raise ConfigError."""
    names = {name.strip() for name in value.split(',') if name.strip()}
    known: set[StepName] = {name for name in names if is_step_name(name)}
    unknown = sorted(name for name in names if not is_step_name(name))
    if unknown:
        raise es.ConfigError(f'unknown step name(s): {unknown}')
    return known


def build_context(args: InstallArgsDto, log: logging.Logger, log_file: Path) -> es.Context:
    """The Context for the parsed command line."""
    return es.Context(
        repo=es.REPO,
        paths=es.Paths(args.home),
        config=es.load_config(args.config),
        log=log,
        runner=es.Runner(log),
        user_env=es.UserEnv(),
        dry_run=args.dry_run,
        replace_binaries=args.replace_binaries,
        update_templates=args.update_templates,
        models_from=args.models_from,
        secrets_file=args.secrets_file,
        log_file=log_file,
        only=parse_names(args.only),
        skip=parse_names(args.skip),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the selected phase; 0 on success, 1 on any error (details in the log file)."""
    args = parse_args(argv)
    log_file = args.log_file or es.REPO / 'logs' / f'install-{es.started_datetime_local_as_str()}.log'
    log = es.setup_logging(log_file)
    try:
        ctx = build_context(args, log, log_file)
        log.info('install.py phase=%s dry_run=%s home=%s log=%s', args.phase, args.dry_run, args.home, log_file)
        if args.phase == 'admin':
            if not es.is_admin():
                log.error('the admin phase requires elevation')
                return 1
            run_step(ctx, FIREWALL_STEP)
            return 0
        log.info('[preflight]')
        preflight(ctx)
        for step in USER_STEPS:
            if selected(ctx, step.name):
                run_step(ctx, step)
            else:
                log.info('[%s] skipped', step.name)
        firewall_phase(ctx, args.config)
        if ctx.dry_run:
            log.info('dry run finished; verify.cmd not run')
            return 0
        log.info('[verify]')
        return verify.run_checks(ctx)
    except (
        es.ConfigError,
        PreflightError,
        es.CommandError,
        es.IntegrityError,
        es.ElevationError,
        RuntimeError,
    ) as exc:
        log.error('ERROR %s', exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
