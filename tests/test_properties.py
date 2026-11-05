"""Property-based tests (hypothesis) for the pure helpers: text transforms, Path edits, tags, configuration."""

from __future__ import annotations

import string
from typing import TYPE_CHECKING

import pytest
from conftest import nested, raw_config
from hypothesis import given, settings
from hypothesis import strategies as st

import envsetup as es

if TYPE_CHECKING:
    from pathlib import Path

BLOCK = f'{es.BASHRC_BEGIN}\nalias a=b\n{es.BASHRC_END}\n'
LINE = st.text(alphabet=st.sampled_from(string.ascii_letters + ' '), max_size=20)
TEXT = st.lists(LINE, max_size=6).map(lambda lines: '\n'.join(lines) + ('\n' if lines else ''))
PATH_ENTRY = st.text(alphabet=st.sampled_from(string.ascii_letters + ':\\'), min_size=1, max_size=12).filter(
    lambda entry: entry.strip('\\').strip() != ''
)
TAG = st.from_regex(r'[a-z][a-z0-9-]{0,8}(:[a-z0-9.]{1,6})?', fullmatch=True)
SECRET_KEY = st.from_regex(r'[A-Z][A-Z0-9_]{0,10}', fullmatch=True)
SECRET_VALUE = st.text(alphabet=st.sampled_from(string.ascii_letters + string.digits + '-_=/+'), max_size=20)


def key_paths(schema: es.Schema, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Every key path of the configuration schema, nested ones included."""
    paths: list[tuple[str, ...]] = []
    if isinstance(schema, dict):
        for key, sub in schema.items():
            paths.append((*prefix, key))
            paths.extend(key_paths(sub, (*prefix, key)))
    return paths


KEY_PATHS = key_paths(es.CONFIG_SCHEMA)


@settings(deadline=None)
@given(text=TEXT)
def test_bashrc_block_apply_is_idempotent_and_remove_undoes_it(text: str) -> None:
    applied = es.block_apply(text, BLOCK)
    assert es.block_state(applied, BLOCK) == 'same'
    assert es.block_text(applied) == BLOCK
    assert es.block_apply(applied, BLOCK) == applied
    assert es.block_remove(applied).rstrip('\n') == text.rstrip('\n')
    assert es.block_state(text, BLOCK) == 'absent'


@settings(deadline=None)
@given(entries=st.lists(PATH_ENTRY, max_size=6), entry=PATH_ENTRY)
def test_path_prepend_puts_the_entry_first_and_remove_takes_every_spelling_out(entries: list[str], entry: str) -> None:
    prepended = es.path_prepend(entries, entry)
    assert prepended[0] == entry
    assert es.path_position(prepended, entry) == 0
    removed = es.path_remove(entries, entry)
    assert es.path_position(removed, entry) is None
    assert removed == [candidate for candidate in entries if es.path_position([candidate], entry) is None]
    assert es.path_prepend(prepended, entry) == prepended


@settings(deadline=None)
@given(tags=st.lists(TAG, max_size=5))
def test_ollama_list_parsing_round_trips_and_tag_normalisation_is_idempotent(tags: list[str]) -> None:
    listing = 'NAME ID SIZE MODIFIED\n' + ''.join(f'{es.normalize_tag(tag)} abc 1 GB now\n' for tag in tags)
    present = es.parse_ollama_list(listing)
    assert present == {es.normalize_tag(tag) for tag in tags}
    assert es.missing_tags(tags, present) == []
    assert all(es.normalize_tag(es.normalize_tag(tag)) == es.normalize_tag(tag) for tag in tags)


@settings(deadline=None)
@given(path=st.sampled_from(KEY_PATHS), mutation=st.sampled_from(['delete', 'retype']))
def test_validate_config_names_the_offending_key(path: tuple[str, ...], mutation: str) -> None:
    config = raw_config()
    parent = nested(config, *path[:-1])
    if mutation == 'delete':
        del parent[path[-1]]
    else:
        parent[path[-1]] = None  # null is the wrong type for every entry of the schema
    with pytest.raises(es.ConfigError) as info:
        es.validate_config(config, es.CONFIG_SCHEMA)
    assert path[-1] in str(info.value)


@settings(deadline=None)
@given(secrets=st.dictionaries(SECRET_KEY, SECRET_VALUE, max_size=5))
def test_read_secrets_round_trips(secrets: dict[str, str], tmp_path_factory: pytest.TempPathFactory) -> None:
    path: Path = tmp_path_factory.mktemp('secrets') / 'secrets.env'
    lines = ['# a comment', '', *(f'{key}={value}' for key, value in secrets.items())]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    assert es.read_secrets(path) == secrets
