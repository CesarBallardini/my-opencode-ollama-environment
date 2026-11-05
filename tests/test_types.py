"""The types in envtypes.py and the values the code checks at run time must say the same thing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAliasType, get_args, get_origin, get_type_hints, is_typeddict

import pytest

import envsetup as es
import envtypes
import install

if TYPE_CHECKING:
    from pathlib import Path
    from types import UnionType

    from conftest import FakeContext


def schema_of(hint: type | UnionType | TypeAliasType) -> es.Schema:
    """The schema that the type hint hint of a DTO field stands for."""
    if isinstance(hint, TypeAliasType):
        return schema_of(hint.__value__)
    if is_typeddict(hint):
        return {key: schema_of(sub) for key, sub in get_type_hints(hint).items()}
    origin = get_origin(hint)
    if origin is Literal:
        return str
    if origin is list:
        return es.ListOf(get_args(hint)[0])
    if origin is dict:
        value = get_args(hint)[1]
        return es.MapOf(value.__value__ if isinstance(value, TypeAliasType) else value)
    return hint


def literal_values(alias: TypeAliasType) -> set[str]:
    return set(get_args(alias.__value__))


def test_the_configuration_schema_matches_its_dto() -> None:
    assert schema_of(envtypes.InstallConfigDto) == es.CONFIG_SCHEMA


def test_every_journal_kind_has_a_dto_a_schema_and_an_identity() -> None:
    kinds = literal_values(envtypes.JournalKind)
    assert set(es.JOURNAL_SCHEMAS) == kinds
    assert set(es.JOURNAL_IDENTITY) == kinds
    for dto in get_args(envtypes.JournalEntryDto.__value__):
        kind = get_args(get_type_hints(dto)['kind'])[0]
        assert schema_of(dto) == es.JOURNAL_SCHEMAS[kind], kind
        assert es.JOURNAL_IDENTITY[kind] in es.JOURNAL_SCHEMAS[kind], kind


def test_the_step_names_match_their_type_and_the_steps() -> None:
    assert set(install.STEP_NAMES) == literal_values(envtypes.StepName)
    assert {step.name for step in [*install.USER_STEPS, install.FIREWALL_STEP]} == set(install.STEP_NAMES)


def test_read_json_returns_objects_only(tmp_path: Path) -> None:
    path = tmp_path / 'a.json'
    assert install.read_json(path) is None
    path.write_text('[1]', encoding='utf-8')
    assert install.read_json(path) is None
    path.write_text('{"a": 1}', encoding='utf-8')
    assert install.read_json(path) == {'a': 1}


@pytest.mark.parametrize(
    ('auth', 'expected'),
    [
        ({}, None),
        ({'anthropic': 'not an object'}, None),
        ({'anthropic': {'type': 'api'}}, None),
        ({'anthropic': {'key': 7}}, None),
        ({'anthropic': {'key': 'sk'}}, 'sk'),
    ],
)
def test_anthropic_key_copes_with_what_the_file_holds(auth: envtypes.JsonObject, expected: str | None) -> None:
    assert install.anthropic_key(auth) == expected


def test_an_auth_file_with_an_odd_anthropic_entry_is_reported_not_a_crash(
    ctx: FakeContext, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ctx.config['cloud']['install_harnesses'] = True
    monkeypatch.setattr(es, 'which_outside', lambda _name, _exclude: 'C:/x.exe')
    ctx.secrets_file = ctx.paths.home / 's.env'
    ctx.secrets_file.parent.mkdir(parents=True)
    ctx.secrets_file.write_text('ANTHROPIC_API_KEY=sk\n', encoding='utf-8')
    ctx.paths.opencode_cloud_auth.parent.mkdir(parents=True)
    ctx.paths.opencode_cloud_auth.write_text('{"anthropic": "odd"}', encoding='utf-8')
    install.check_cloud(ctx)
    assert 'another key' in caplog.text
