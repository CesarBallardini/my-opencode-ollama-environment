"""The types the programs share: data transfer objects, closed sets of values, and system seams.

A name with the Dto suffix only carries data: a JSON document (TypedDict) or a small record
(NamedTuple). The Literal aliases close the sets of values that would otherwise be plain strings.
The protocols describe what a step needs from the machine, so that the real implementations and the
test fakes are interchangeable.

Types only, no program logic. envsetup.py checks the TypedDicts of this module at the two places
JSON comes in, install.config.json and the install journal, against schemas that a test keeps
identical to them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NamedTuple, NotRequired, Protocol, TypedDict

if TYPE_CHECKING:
    import subprocess  # nosec B404
    from pathlib import Path

# ----------------------------------------------------------------------------- JSON

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


# ----------------------------------------------------------------------------- closed sets of values

type StepName = Literal[
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
]
type Phase = Literal['user', 'admin']
type HarnessName = Literal['opencode', 'claude']
type BlockState = Literal['absent', 'same', 'different']
type AppSettingsState = Literal['no-db', 'no-table', 'no-columns', 'no-row', 'same', 'different']
type OutcomeStatus = Literal['PASS', 'WARN', 'SKIP', 'FAIL']
type ProbeKind = Literal['allowed', 'denied', 'offline']
type JournalKind = Literal[
    'winget-install',
    'uv-python-install',
    'dir-created',
    'file',
    'user-env',
    'user-path',
    'bashrc-block',
    'app-settings',
    'model',
    'auth-key',
    'firewall-rule',
]


# ----------------------------------------------------------------------------- install.config.json


class ToolsConfigDto(TypedDict):
    """The tools section of install.config.json."""

    python_for_proxy: str


class OllamaConfigDto(TypedDict):
    """The ollama section of install.config.json."""

    winget_id: str
    version: str
    installer_sha256: str
    server_json: dict[str, JsonScalar]
    user_env: dict[str, str]
    app_settings: dict[str, int]


class ModelsConfigDto(TypedDict):
    """The models section of install.config.json: tags to pull, and tag to Modelfile to create."""

    pull: list[str]
    create: dict[str, str]
    optional_pull: list[str]
    optional_create: dict[str, str]
    install_optional: bool


class HarnessPinDto(TypedDict):
    """A pinned harness binary."""

    version: str
    url: str
    exe_sha256: str


class OpencodePinDto(HarnessPinDto):
    """The pinned OpenCode binary, which is published inside a zip archive."""

    zip_sha256: str


class HarnessConfigDto(TypedDict):
    """The harness section of install.config.json."""

    opencode: OpencodePinDto
    claude: HarnessPinDto


class FirewallConfigDto(TypedDict):
    """The firewall section of install.config.json."""

    block_harness_and_runner: bool
    block_ollama_server_and_app: bool


class ShellConfigDto(TypedDict):
    """The shell section of install.config.json."""

    bashrc_aliases: bool
    path_wrappers: bool
    confidential_roots: list[str]


class CloudConfigDto(TypedDict):
    """The cloud section of install.config.json; winget maps a command name to its package id."""

    install_harnesses: bool
    winget: dict[str, str]


class InstallConfigDto(TypedDict):
    """The whole of install.config.json."""

    minimum_free_gb: int
    minimum_free_gb_with_optional: int
    tools: ToolsConfigDto
    ollama: OllamaConfigDto
    models: ModelsConfigDto
    harness: HarnessConfigDto
    firewall: FirewallConfigDto
    shell: ShellConfigDto
    cloud: CloudConfigDto


# ----------------------------------------------------------------------------- install journal


class JournalEntryBaseDto(TypedDict):
    """What every journal entry carries; Journal.add stamps the time, so a new entry leaves it out."""

    recorded_datetime_local_as_str: NotRequired[str]


class WingetInstallEntryDto(JournalEntryBaseDto):
    """The installer installed a winget package."""

    kind: Literal['winget-install']
    id: str


class UvPythonInstallEntryDto(JournalEntryBaseDto):
    """The installer installed a uv-managed Python."""

    kind: Literal['uv-python-install']
    version: str


class DirCreatedEntryDto(JournalEntryBaseDto):
    """The installer created a directory; tree means it owns everything inside it."""

    kind: Literal['dir-created']
    path: str
    tree: bool


class FileEntryDto(JournalEntryBaseDto):
    """The installer wrote a file; backup is where the file it replaced went, if any."""

    kind: Literal['file']
    path: str
    backup: str | None


class UserEnvEntryDto(JournalEntryBaseDto):
    """The installer set a user variable; previous is the value it had, if any."""

    kind: Literal['user-env']
    name: str
    previous: str | None


class UserPathEntryDto(JournalEntryBaseDto):
    """The installer prepended an entry to the user Path."""

    kind: Literal['user-path']
    entry: str


class BashrcBlockEntryDto(JournalEntryBaseDto):
    """The installer wrote the alias block; previous_block is the block it replaced, if any."""

    kind: Literal['bashrc-block']
    path: str
    created: bool
    previous_block: str | None


class AppSettingsEntryDto(JournalEntryBaseDto):
    """The installer changed the Ollama app settings; previous holds the values it found."""

    kind: Literal['app-settings']
    db: str
    previous: dict[str, int]


class ModelEntryDto(JournalEntryBaseDto):
    """The installer pulled, created or copied a model tag."""

    kind: Literal['model']
    tag: str


class AuthKeyEntryDto(JournalEntryBaseDto):
    """The installer added the anthropic entry to a credentials file that already existed."""

    kind: Literal['auth-key']
    path: str


class FirewallRuleEntryDto(JournalEntryBaseDto):
    """The installer added a firewall rule."""

    kind: Literal['firewall-rule']
    name: str


type JournalEntryDto = (
    WingetInstallEntryDto
    | UvPythonInstallEntryDto
    | DirCreatedEntryDto
    | FileEntryDto
    | UserEnvEntryDto
    | UserPathEntryDto
    | BashrcBlockEntryDto
    | AppSettingsEntryDto
    | ModelEntryDto
    | AuthKeyEntryDto
    | FirewallRuleEntryDto
)


class JournalDocumentDto(TypedDict):
    """The install journal file."""

    version: int
    entries: list[JournalEntryDto]


# ----------------------------------------------------------------------------- other records


class FirewallRuleDto(TypedDict):
    """A firewall rule as the query in envsetup.firewall_rule reports it; every value is PowerShell text."""

    enabled: str
    direction: str
    action: str
    program: str


class HarnessBinaryDto(NamedTuple):
    """One private harness binary: its pin and where it goes."""

    name: HarnessName
    pin: HarnessPinDto
    dest: Path


class RevertCountsDto(NamedTuple):
    """How many journal entries a revert undid, and how many it kept for a later run."""

    reverted: int
    kept: int


class ProxyProbeDto(NamedTuple):
    """What a request through the egress proxy met; detail is the status code or the reason."""

    kind: ProbeKind
    detail: str


# ----------------------------------------------------------------------------- system seams


class CommandRunner(Protocol):
    """Runs external commands."""

    def run(  # noqa: PLR0913  (mirrors the subprocess.run options the callers need)
        self,
        args: list[str],
        *,
        check: bool = True,
        stream: bool = False,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run args without a shell; capture its output unless stream is true."""
        ...

    def powershell(self, script: str, *, check: bool = True) -> str:
        """Run a PowerShell snippet and return its standard output."""
        ...


class UserEnvironment(Protocol):
    """The environment variables of the user."""

    def get(self, name: str) -> str | None:
        """The value of the variable name, or None when unset."""
        ...

    def set(self, name: str, value: str, expandable: bool = False) -> None:
        """Set the variable name; expandable keeps %VAR% references unexpanded."""
        ...

    def delete(self, name: str) -> None:
        """Delete the variable name; an unset variable is not an error."""
        ...

    def path_list(self) -> list[str]:
        """The user Path split into entries, empty ones kept."""
        ...

    def set_path_list(self, entries: list[str]) -> None:
        """Write the user Path from entries, or delete it when none remain."""
        ...

    def broadcast(self) -> None:
        """Tell running programs that the environment changed."""
        ...

    def refresh_process_path(self) -> None:
        """Rebuild the PATH of this process from the stored machine and user values."""
        ...


class EgressProxyModule(Protocol):
    """The part of the installed egress_proxy.py that verify.py exercises."""

    def denied_domains(self) -> frozenset[str]:
        """The deny list."""
        ...

    def is_denied(self, host: str) -> bool:
        """Whether host is a listed domain or one of its subdomains."""
        ...
