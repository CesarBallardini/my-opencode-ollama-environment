"""The install journal: persistence, what the install helpers record, and the envsetup additions."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import pytest

import envsetup as es
import install

if TYPE_CHECKING:
    from pathlib import Path

    from conftest import FakeContext


def test_journal_persists_entries_and_vanishes_when_empty(tmp_path: Path) -> None:
    path = tmp_path / 'state' / 'install-journal.json'
    journal = es.Journal(path)
    assert journal.entries == []
    journal.add({'kind': 'user-env', 'name': 'X', 'previous': None})
    journal.add({'kind': 'dir-created', 'path': str(tmp_path / 'made'), 'tree': True})
    journal.add({'kind': 'dir-created', 'path': str(tmp_path / 'plain'), 'tree': False})
    document = json.loads(path.read_text(encoding='utf-8'))
    assert document['version'] == es.JOURNAL_VERSION
    assert [entry['kind'] for entry in document['entries']] == ['user-env', 'dir-created', 'dir-created']
    assert all('recorded_datetime_local_as_str' in entry for entry in document['entries'])
    reloaded = es.Journal(path)
    assert reloaded.entries == journal.entries
    assert reloaded.has('user-env', name='X')
    assert not reloaded.has('user-env', name='Y')
    assert not reloaded.has('model')
    assert reloaded.of_kind('dir-created') == journal.entries[1:]
    assert reloaded.covers_path(tmp_path / 'made' / 'inner' / 'file.txt')
    assert not reloaded.covers_path(tmp_path / 'plain' / 'file.txt')
    assert not reloaded.covers_path(tmp_path / 'other.txt')
    for entry in list(reloaded.entries):
        reloaded.remove(entry)
    assert not path.exists()
    assert not path.with_name(path.name + '.tmp').exists()


def test_journal_rejects_a_file_that_is_not_a_journal(tmp_path: Path) -> None:
    path = tmp_path / 'install-journal.json'
    path.write_text('[]', encoding='utf-8')
    with pytest.raises(es.ConfigError, match='not an install journal'):
        es.Journal(path)
    path.write_text('{"entries": [{"no": "kind"}]}', encoding='utf-8')
    with pytest.raises(es.ConfigError, match='not an install journal'):
        es.Journal(path)
    path.write_text('{', encoding='utf-8')
    with pytest.raises(es.ConfigError, match='cannot read'):
        es.Journal(path)


def test_context_loads_the_journal_lazily_and_reloads(ctx: FakeContext) -> None:
    assert ctx.journal.entries == []
    es.Journal(ctx.paths.journal).add({'kind': 'user-path', 'entry': 'X'})  # another process writes it
    assert ctx.journal.entries == []
    ctx.reload_journal()
    assert ctx.journal.has('user-path', entry='X')


def test_ensure_dir_records_the_topmost_missing_directory_only(ctx: FakeContext) -> None:
    home = ctx.paths.home
    install.ensure_dir(ctx, home)  # the profile root is never recorded
    assert home.is_dir()
    assert ctx.journal.entries == []
    install.ensure_dir(ctx, home / 'a' / 'b' / 'c')
    assert (home / 'a' / 'b' / 'c').is_dir()
    assert ctx.journal.has('dir-created', path=str(home / 'a'), tree=False)
    install.ensure_dir(ctx, home / 'a')  # exists: nothing new
    install.ensure_dir(ctx, home / 'a' / 'b' / 'd')  # under a recorded directory that is no tree: recorded
    assert [e['path'] for e in ctx.journal.entries if e['kind'] == 'dir-created'] == [
        str(home / 'a'),
        str(home / 'a' / 'b' / 'd'),
    ]
    install.ensure_dir(ctx, ctx.paths.local_llm / 'x' / 'y')
    assert ctx.journal.has('dir-created', path=str(ctx.paths.local_llm), tree=True)
    install.ensure_dir(ctx, ctx.paths.local_llm / 'z')  # under a tree: nothing new
    assert len(ctx.journal.entries) == 3


def test_record_file_moves_the_previous_file_to_the_backups(ctx: FakeContext) -> None:
    home = ctx.paths.home
    home.mkdir(parents=True)
    target = home / 'settings.json'
    assert install.record_file(ctx, target) is None  # absent: recorded as created
    assert ctx.journal.has('file', path=str(target), backup=None)
    assert install.record_file(ctx, target) is None  # already recorded: nothing new
    assert len(ctx.journal.entries) == 1
    other = home / 'other.json'
    other.write_text('old', encoding='utf-8')
    backup = install.record_file(ctx, other)
    assert backup is not None
    assert backup.parent == ctx.paths.journal_backups
    assert backup.read_text(encoding='utf-8') == 'old'
    assert not other.exists()
    assert ctx.journal.has('file', path=str(other), backup=str(backup))
    # a later backup of a file with the same name does not overwrite the first one
    other.write_text('new', encoding='utf-8')
    ctx.journal.remove(ctx.journal.entries[-1])
    second = install.record_file(ctx, other)
    assert second is not None
    assert second != backup
    assert second.read_text(encoding='utf-8') == 'new'
    assert backup.read_text(encoding='utf-8') == 'old'
    # inside a tree the installer created, nothing is recorded or moved
    install.ensure_dir(ctx, ctx.paths.local_llm)
    inside = ctx.paths.local_llm / 'f.txt'
    inside.write_text('x', encoding='utf-8')
    assert install.record_file(ctx, inside) is None
    assert inside.exists()
    assert not ctx.journal.has('file', path=str(inside))


def test_add_once_keeps_the_first_record(ctx: FakeContext) -> None:
    ctx.journal.add_once({'kind': 'user-env', 'name': 'A', 'previous': None})
    ctx.journal.add_once({'kind': 'user-env', 'name': 'A', 'previous': 'changed'})
    ctx.journal.add_once({'kind': 'user-env', 'name': 'B', 'previous': '1'})
    entries = ctx.journal.entries
    assert [(e['name'], e['previous']) for e in entries if e['kind'] == 'user-env'] == [('A', None), ('B', '1')]


@pytest.mark.parametrize(
    ('entry', 'message'),
    [
        ('{"kind": "from-the-future", "path": "x"}', 'unknown entry kind'),
        ('{"kind": "model", "recorded_datetime_local_as_str": "t"}', 'missing key'),
        ('{"kind": "model", "tag": 1, "recorded_datetime_local_as_str": "t"}', 'expected str'),
        ('{"kind": "model", "tag": "x", "extra": 1, "recorded_datetime_local_as_str": "t"}', 'unknown key'),
        ('{"kind": "user-env", "name": "A", "previous": 1, "recorded_datetime_local_as_str": "t"}', 'expected'),
    ],
)
def test_journal_checks_each_entry_against_the_schema_of_its_kind(tmp_path: Path, entry: str, message: str) -> None:
    path = tmp_path / 'install-journal.json'
    path.write_text(f'{{"version": 1, "entries": [{entry}]}}', encoding='utf-8')
    with pytest.raises(es.ConfigError, match=message):
        es.Journal(path)


def test_block_text() -> None:
    block = f'{es.BASHRC_BEGIN}\nalias x=y\n{es.BASHRC_END}\n'
    assert es.block_text('a\n' + block + 'b\n') == block
    assert es.block_text('nothing\n') is None


def test_app_settings_read(tmp_path: Path) -> None:
    db = tmp_path / 'db.sqlite'
    connection = sqlite3.connect(db)
    connection.execute('CREATE TABLE settings (auto_update_enabled INTEGER, airplane_mode INTEGER)')
    connection.commit()
    connection.close()
    with pytest.raises(es.ConfigError, match='no row'):
        es.app_settings_read(db, ['auto_update_enabled'])
    with pytest.raises(es.ConfigError, match='does not have the columns'):
        es.app_settings_read(db, ['nope'])
    connection = sqlite3.connect(db)
    connection.execute('INSERT INTO settings VALUES (1, 0)')
    connection.commit()
    connection.close()
    assert es.app_settings_read(db, ['airplane_mode', 'auto_update_enabled']) == {
        'airplane_mode': 0,
        'auto_update_enabled': 1,
    }


def test_winget_uninstall_command(ctx: FakeContext) -> None:
    es.winget_uninstall(ctx.runner, 'Some.Package')
    assert ctx.runner.calls[-1][:5] == ['winget', 'uninstall', '--id', 'Some.Package', '-e']
    assert '--silent' in ctx.runner.calls[-1]
