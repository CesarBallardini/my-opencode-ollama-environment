"""Paths and names reach PowerShell inside single quotes, so an apostrophe in them must be doubled."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import envsetup as es

if TYPE_CHECKING:
    from conftest import FakeContext


def test_ps_quote_doubles_apostrophes() -> None:
    assert es.ps_quote('plain') == "'plain'"
    assert es.ps_quote("O'Neil") == "'O''Neil'"
    assert es.ps_quote("it's 'quoted'") == "'it''s ''quoted'''"


def test_firewall_and_acl_scripts_quote_names_and_paths_with_apostrophes(ctx: FakeContext) -> None:
    program = Path("C:\\Users\\O'Neil\\local-llm\\opencode\\bin\\opencode.exe")
    name = "it's a rule"
    es.firewall_add(ctx.runner, name, program)
    assert "-DisplayName 'it''s a rule'" in ctx.runner.scripts[-1]
    assert "-Program 'C:\\Users\\O''Neil\\local-llm\\opencode\\bin\\opencode.exe'" in ctx.runner.scripts[-1]
    es.firewall_rule(ctx.runner, name)
    assert "-DisplayName 'it''s a rule' -ErrorAction" in ctx.runner.scripts[-1]
    es.firewall_set_enabled(ctx.runner, name, enabled=False)
    assert ctx.runner.scripts[-1] == "Disable-NetFirewallRule -DisplayName 'it''s a rule'"
    es.firewall_remove(ctx.runner, name)
    assert ctx.runner.scripts[-1].startswith("Remove-NetFirewallRule -DisplayName 'it''s a rule'")
    es.acl_sids(ctx.runner, Path("C:\\Users\\O'Neil\\.secrets\\secrets.env"))
    assert "-LiteralPath 'C:\\Users\\O''Neil\\.secrets\\secrets.env'" in ctx.runner.scripts[-1]
