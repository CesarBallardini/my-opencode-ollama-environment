"""The documentation and the code must not drift apart: options, configuration keys, the plan's draft, the guide."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from conftest import PINNED, REPO, nested, raw_config

import envsetup as es
import envtypes
import install
import uninstall
import verify

if TYPE_CHECKING:
    import argparse

    from envtypes import JsonObject, JsonValue

README = (REPO / 'README.md').read_text(encoding='utf-8')
PLAN = (REPO / '2026-11-04-plan-repo-to-install.md').read_text(encoding='utf-8')
DEVELOPMENT = (REPO / 'development.md').read_text(encoding='utf-8')
# What a backticked token must end in to count as a file: `typing.Any` and `--home` are not files.
FILE_SUFFIXES = {'.py', '.cmd', '.ps1', '.yml', '.yaml', '.toml', '.json'}
# The gates `make qa` is documented to run.
QA_TOOLS = ('ruff', 'pyright', 'pyrefly', 'pytest', 'bandit', 'pip-audit', 'osv-scanner', 'gitleaks')
PROGRAMS = {'install.py', 'verify.py', 'uninstall.py', 'ollama_pull.py', 'envsetup.py', 'envtypes.py'}
# Options the README does not need to teach: defaults, the elevated child and the log location.
INTERNAL_OPTIONS = {'--help', '--config', '--secrets-file', '--phase', '--log-file'}


def key_structure(value: JsonValue) -> JsonValue:
    """The nested key structure of a JSON object, values dropped."""
    if isinstance(value, dict):
        return {key: key_structure(sub) for key, sub in value.items()}
    return None


def long_options(parser: argparse.ArgumentParser) -> set[str]:
    return {option for action in parser._actions for option in action.option_strings if option.startswith('--')}


def test_readme_documents_every_user_facing_option() -> None:
    for parser in (install.build_parser(), uninstall.build_parser(), verify.build_parser()):
        for option in sorted(long_options(parser) - INTERNAL_OPTIONS):
            assert f'`{option}`' in README or f' {option}' in README, option


def test_readme_names_the_configuration_keys_it_tells_the_reader_to_review() -> None:
    schema_keys = {key for path in _paths(es.CONFIG_SCHEMA) for key in path}
    for key in ('models', 'install_optional', 'firewall', 'shell', 'confidential_roots', 'cloud', 'install_harnesses'):
        assert key in schema_keys
        assert f'`{key}`' in README or f'"{key}"' in README, key


def test_the_plan_draft_configuration_is_valid_and_has_the_same_keys() -> None:
    block = PLAN.split('## Appendix B: install.config.json draft', 1)[1].split('```json\n', 1)[1].split('```', 1)[0]
    draft: JsonObject = json.loads(block)
    es.validate_config(draft, es.CONFIG_SCHEMA)
    assert key_structure(draft) == key_structure(raw_config())
    assert nested(draft, 'tools')['python_for_proxy'] == PINNED['tools']['python_for_proxy']


def test_the_plan_lists_every_journal_kind_and_every_verify_check() -> None:
    journal_section = PLAN.split('### 2.9 Reverting: the install journal', 1)[1].split('## 3.', 1)[0]
    for kind in es.JOURNAL_SCHEMAS:
        assert f'`{kind}`' in journal_section, kind
    appendix = PLAN.split('## Appendix D: what verify.py checks', 1)[1]
    rows = [line for line in appendix.splitlines() if line.startswith('| ') and line[2].isdigit()]
    assert len(rows) == len(verify.CHECKS)


def test_the_python_version_is_the_same_everywhere() -> None:
    version = PINNED['tools']['python_for_proxy']
    assert (REPO / '.python-version').read_text(encoding='utf-8').startswith(version)
    for name in ('install.cmd', 'verify.cmd', 'uninstall.cmd', 'ollama-pull.cmd', 'local-llm/ensure-proxy.cmd'):
        assert f'--python {version}' in (REPO / name).read_text(encoding='utf-8'), name
    assert f'uv python install {version}' in README


def documented_paths(text: str) -> set[str]:
    """Every repository path a guide names: backticked tokens that look like files, and scripts it runs."""
    tokens = [code.split()[0] for code in re.findall(r'`([^`\n]+)`', text)]
    named = {
        token
        for token in tokens
        if '\\' not in token and ('/' in token or token.startswith('.') or Path(token).suffix in FILE_SUFFIXES)
    }
    scripts = {script.replace('\\', '/') for script in re.findall(r'-File (\S+)', text)}
    # .git/ is a property of the clone, not of the repository's content.
    return {path for path in named | scripts if not path.startswith('.git/')}


def test_the_readme_points_to_the_development_guide() -> None:
    assert '`development.md`' in README


def test_the_development_guide_names_only_existing_make_targets() -> None:
    documented = set(re.findall(r'(?:`|^ +)make ([a-z][a-z-]*)', DEVELOPMENT, flags=re.MULTILINE))
    targets = set(
        re.findall(r'^([a-z][a-z0-9-]*):', (REPO / 'Makefile').read_text(encoding='utf-8'), flags=re.MULTILINE)
    )
    assert documented
    assert documented <= targets, documented - targets


def test_the_development_guide_names_only_existing_paths() -> None:
    paths = documented_paths(DEVELOPMENT)
    assert paths
    for path in sorted(paths):
        assert any(REPO.glob(path)) or any((REPO / 'tests').rglob(path)), path


def test_the_development_guide_names_only_existing_vm_scenarios() -> None:
    script = (REPO / 'tests' / 'e2e' / 'vm-e2e.ps1').read_text(encoding='utf-8')
    validate_set = re.search(r'ValidateSet\(([^)]*)\)', script)
    assert validate_set is not None
    allowed = set(re.findall(r"'(\w+)'", validate_set.group(1)))
    documented = set(re.findall(r'-Scenario (\w+)', DEVELOPMENT))
    assert documented
    assert documented == allowed


def test_make_qa_runs_the_gates_the_development_guide_lists() -> None:
    makefile = (REPO / 'Makefile').read_text(encoding='utf-8')
    for tool in QA_TOOLS:
        assert tool in DEVELOPMENT, tool
        assert tool in makefile, tool


def test_the_type_rules_in_the_development_guide_hold() -> None:
    for name in ('JsonValue', 'JournalKind', 'JournalEntryDto'):
        assert f'`{name}`' in DEVELOPMENT, name
        assert hasattr(envtypes, name), name
    assert '`Journal`' in DEVELOPMENT
    assert hasattr(es, 'Journal')
    assert 'two `match` statements' in DEVELOPMENT.replace('\n', ' ')
    assert (REPO / 'uninstall.py').read_text(encoding='utf-8').count("match entry['kind']:") == 2
    assert '`typing.Any` is banned' in DEVELOPMENT
    assert '"typing.Any"' in (REPO / 'ruff.toml').read_text(encoding='utf-8')
    assert 'strict mode on the programs' in DEVELOPMENT.replace('\n', ' ')
    strict = set(json.loads((REPO / 'pyrightconfig.json').read_text(encoding='utf-8'))['strict'])
    assert strict >= PROGRAMS, PROGRAMS - strict


def _paths(schema: es.Schema, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    if isinstance(schema, dict):
        for key, sub in schema.items():
            paths.append((*prefix, key))
            paths.extend(_paths(sub, (*prefix, key)))
    return paths
