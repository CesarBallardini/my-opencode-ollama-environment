<#
.SYNOPSIS
End-to-end run of install.cmd, verify.cmd and uninstall.cmd on a disposable Windows machine.

.DESCRIPTION
Meant for the windows-latest GitHub runner (workflow e2e.yml) or any throwaway VM. Never run it on a
laptop in daily use: it changes the real user environment and Path and the real firewall rules.

Installs into a temporary profile root with the Ollama and models steps skipped, so the run needs no
1.7 GB installer and no 18 GB model, but still downloads the two pinned binaries, writes the real
user environment and Path, and creates the real firewall rules. A second install must change
nothing. The uninstall is listed, then applied, and the machine must be back to the state captured
before the first run. Exits non-zero on the first deviation.

The firewall phase needs an elevated process: the runner is one, a plain console is not.
#>
[CmdletBinding()]
param(
    [string]$Repo = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$HomeDir = (Join-Path ([IO.Path]::GetTempPath()) 'local-llm-e2e-home'),
    [string]$LogDir = (Join-Path ([IO.Path]::GetTempPath()) 'local-llm-e2e-logs')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'e2e-common.ps1')

if (-not (Test-Elevated)) {
    Fail 'run from an elevated process: the firewall phase would otherwise wait on a UAC prompt'
}

New-Item -ItemType Directory -Force $LogDir | Out-Null
if (Test-Path $HomeDir) { Remove-Item -Recurse -Force $HomeDir }
$install = Join-Path $Repo 'install.cmd'
$verify = Join-Path $Repo 'verify.cmd'
$uninstall = Join-Path $Repo 'uninstall.cmd'
$journal = Join-Path $HomeDir 'AppData\Local\my-opencode-ollama-environment\install-journal.json'
$homeArgs = @('--home', $HomeDir)
$skipArgs = @('--skip', 'ollama,models')

Step 'the machine before the first run'
if (@(Get-LocalLlmRules).Count -gt 0) { Fail "local-llm rules already exist: $(@(Get-LocalLlmRules) -join ', ')" }
$envBefore = Get-UserEnvironment
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& uv python find 3.14 2>&1 | Out-Null
$pythonWasThere = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $previous
Write-Host "  uv python 3.14 present before the run: $pythonWasThere"

Step 'first install (Ollama and models skipped)'
$first = Invoke-Wrapper $install ($homeArgs + $skipArgs + @('--log-file', "$LogDir\install-1.log")) "$LogDir\install-1.out"
Assert-NoErrorLines $first 'the first install'
$required = @(
    'PASS server.json', 'PASS user environment', 'PASS local-llm files', 'PASS opencode --version', 'PASS claude --version',
    "PASS firewall: 'local-llm: opencode direct outbound'", "PASS firewall: 'local-llm: claude direct outbound'",
    "PASS firewall: 'local-llm: ollama runner direct outbound'", "PASS firewall: 'local-llm: ollama server direct outbound'",
    "PASS firewall: 'local-llm: ollama app direct outbound'", 'PASS proxy deny', 'PASS user Path', 'PASS .bashrc aliases',
    'PASS python for the proxy', 'PASS install journal'
)
foreach ($line in $required) { Assert-Line $first $line }
# Ollama was skipped on purpose, so verify reports it missing; that is the only failure allowed here.
$unexpected = @(Get-MatchingLines $first '^FAIL' | Where-Object { $_ -notmatch '^FAIL ollama' })
if ($unexpected.Count -gt 0) { Fail "unexpected FAIL lines: $($unexpected -join '; ')" }
if (-not (Test-Path $journal)) { Fail 'the install journal was not written' }
$journalAfterFirst = (Get-FileHash -Algorithm SHA256 $journal).Hash

Step 'second install: nothing pending'
$second = Invoke-Wrapper $install ($homeArgs + $skipArgs + @('--log-file', "$LogDir\install-2.log")) "$LogDir\install-2.out"
Assert-NoErrorLines $second 'the second install'
Assert-NothingPending $second 'the second install'
if ((Get-FileHash -Algorithm SHA256 $journal).Hash -ne $journalAfterFirst) { Fail 'the second run changed the journal' }

Step 'verify on its own'
$check = Invoke-Wrapper $verify $homeArgs "$LogDir\verify.out"
if ((Get-MatchingLines $check '^verify: ').Count -eq 0) { Fail 'verify printed no summary' }

Step 'uninstall listing'
$listing = Invoke-Wrapper $uninstall ($homeArgs + @('--log-file', "$LogDir\uninstall-list.log")) "$LogDir\uninstall-list.out"
if ($listing.Code -ne 2) { Fail "the listing exited with $($listing.Code), expected 2" }
if ((Get-MatchingLines $listing "remove the firewall rule 'local-llm: opencode direct outbound'").Count -eq 0) {
    Fail 'the listing did not mention the rules'
}
if (-not (Test-Path (Join-Path $HomeDir 'local-llm\egress_proxy.py'))) { Fail 'the listing removed files' }

Step 'uninstall --yes'
$revert = Invoke-Wrapper $uninstall ($homeArgs + @('--yes', '--log-file', "$LogDir\uninstall.log")) "$LogDir\uninstall.out"
if ($revert.Code -ne 0) { Fail "uninstall --yes exited with $($revert.Code)" }
Assert-NoErrorLines $revert 'the uninstall'
if (@(Get-LocalLlmRules).Count -gt 0) { Fail "rules left behind: $(@(Get-LocalLlmRules) -join ', ')" }
if (Test-Path (Join-Path $HomeDir 'local-llm')) { Fail 'local-llm was not removed' }
$changed = @(Compare-Mapping $envBefore (Get-UserEnvironment) 'user variable')
if ($changed.Count -gt 0) { Fail ($changed -join '; ') }
if ($pythonWasThere) {
    if (Test-Path $journal) { Fail 'the journal should be gone: nothing was left to revert' }
} else {
    $left = @((Get-Content -Raw $journal | ConvertFrom-Json).entries)
    if ($left.Count -ne 1 -or $left[0].kind -ne 'uv-python-install') {
        Fail "unexpected journal entries left: $($left | ConvertTo-Json -Compress)"
    }
}

Step 'install again after the uninstall, then clean up'
$third = Invoke-Wrapper $install ($homeArgs + $skipArgs + @('--log-file', "$LogDir\install-3.log")) "$LogDir\install-3.out"
Assert-NoErrorLines $third 'the third install'
$cleanup = Invoke-Wrapper $uninstall ($homeArgs + @('--yes', '--log-file', "$LogDir\uninstall-2.log")) "$LogDir\uninstall-2.out"
if ($cleanup.Code -ne 0) { Fail "the final uninstall exited with $($cleanup.Code)" }
if (@(Get-LocalLlmRules).Count -gt 0) { Fail 'rules left behind after the final uninstall' }
$changed = @(Compare-Mapping $envBefore (Get-UserEnvironment) 'user variable')
if ($changed.Count -gt 0) { Fail ($changed -join '; ') }

Write-Host "`ne2e passed; logs in $LogDir"
exit 0
