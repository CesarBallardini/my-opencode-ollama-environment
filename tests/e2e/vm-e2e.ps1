<#
.SYNOPSIS
Full installer run inside a disposable Windows 11 VM, compared at machine level before and after.

.DESCRIPTION
Run this inside the guest, from a clone of the repository, as the normal user of the VM. It changes
the machine for real: never run it on a laptop in daily use. It stops at the first deviation.

The steps: capture the machine; install with a small model instead of the 18 GB one; verify must
report no FAIL; a second install must find nothing to do; list and apply the uninstall; capture the
machine again and compare file hashes, the stored user environment, the firewall rules and
winget list.

Scenarios:
  fresh        nothing is installed beforehand. The uninstall removes Ollama as well.
  preexisting  Ollama, OpenCode and Claude Code are installed through winget first, and the cloud
               harnesses are requested. The installer must leave all three alone, and the
               uninstall must still leave all three installed.

Each scenario expects a clean guest. On the host, between runs:

  VBoxManage snapshot "win11-e2e" restore "clean"
  VBoxManage startvm "win11-e2e"

Make that snapshot once, after README steps 1 to 3 (winget, Git, uv, the clone) and before any
install. Run the fresh scenario twice, once from a plain console and once from an elevated one. The
plain console shows one UAC prompt for the install and one for the uninstall, and someone has to
accept them; the elevated console shows none. The second install must show no prompt at all.

Ollama writes some files of its own (its identity key, its logs); those are the only leftovers
tolerated after the fresh scenario, and they are listed below in $OllamaOwnFiles.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('fresh', 'preexisting')][string]$Scenario,
    [string]$Repo = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$Model = 'all-minilm:l6-v2',
    [string]$WorkDir = (Join-Path ([IO.Path]::GetTempPath()) 'local-llm-vm-e2e')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'e2e-common.ps1')

$UserHome = $env:USERPROFILE
# Paths relative to the profile that the installer or the programs it installs may touch.
$StateRoots = @(
    '.bashrc',
    '.ollama',
    'local-llm',
    '.local\share\opencode\auth.json',
    'AppData\Local\my-opencode-ollama-environment',
    'AppData\Local\Programs\Ollama'
)
$OllamaOwnFiles = @('.ollama\id_ed25519', '.ollama\id_ed25519.pub', '.ollama\history', '.ollama\logs\*')
$Packages = @('Ollama.Ollama', 'SST.opencode', 'Anthropic.ClaudeCode')
$PackageLine = 'Ollama|OpenCode|ClaudeCode|Claude Code'

function Get-FileHashes {
    $hashes = @{}
    foreach ($root in $StateRoots) {
        $path = Join-Path $UserHome $root
        if (-not (Test-Path -LiteralPath $path)) { continue }
        foreach ($file in @(Get-ChildItem -LiteralPath $path -Recurse -File -Force)) {
            $relative = $file.FullName.Substring($UserHome.Length + 1)
            try {
                $hashes[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
            } catch {
                $hashes[$relative] = 'in use'  # a running Ollama keeps its log open
            }
        }
    }
    return $hashes
}

function Get-WingetLines {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $lines = @(& winget list --accept-source-agreements --disable-interactivity 2>&1 | ForEach-Object { "$_".Trim() })
    $ErrorActionPreference = $previous
    # Only table rows: the spinner and the column ruler carry no package.
    return @($lines | Where-Object { $_ -match '\s{2,}' -and $_ -notmatch '^-+$' -and $_ -notmatch '^Name\s' } | Sort-Object -Unique)
}

function Get-PackageInstalled([string]$Id) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & winget list --id $Id --exact --accept-source-agreements --disable-interactivity 2>&1 | Out-Null
    $found = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $previous
    return $found
}

function Get-MachineState {
    return [pscustomobject]@{
        Files = Get-FileHashes
        Environment = Get-UserEnvironment
        Rules = @(Get-LocalLlmRules)
        Winget = @(Get-WingetLines)
    }
}

function Get-StateDifferences($Before, $After, [string[]]$Tolerated) {
    $differences = @()
    foreach ($line in @(Compare-Mapping $Before.Files $After.Files 'file')) {
        $relative = ($line -replace '^file (.+?): .*$', '$1')
        $ownFile = @($Tolerated | Where-Object { $relative -like $_ }).Count -gt 0
        if ($ownFile) { Write-Host "  tolerated: $line" } else { $differences += $line }
    }
    $differences += @(Compare-Mapping $Before.Environment $After.Environment 'user variable')
    $rulesBefore = @($Before.Rules) -join ', '
    $rulesAfter = @($After.Rules) -join ', '
    if ($rulesBefore -ne $rulesAfter) { $differences += "firewall rules: '$rulesBefore' -> '$rulesAfter'" }
    foreach ($row in @(Compare-Object @($Before.Winget) @($After.Winget))) {
        $line = "winget list $($row.SideIndicator) $($row.InputObject)"
        # A store update during the run is noise; a change to the three packages is a finding.
        if ($row.InputObject -match $PackageLine) { $differences += $line } else { Write-Host "  unrelated: $line" }
    }
    return $differences
}

Write-Host "scenario: $Scenario; elevated: $(Test-Elevated); model: $Model"
if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }
New-Item -ItemType Directory -Force $WorkDir | Out-Null
$install = Join-Path $Repo 'install.cmd'
$verify = Join-Path $Repo 'verify.cmd'
$uninstall = Join-Path $Repo 'uninstall.cmd'

Step 'the configuration: a small model, and the cloud harnesses for the preexisting scenario'
$config = Get-Content -Raw (Join-Path $Repo 'install.config.json') | ConvertFrom-Json
$config.models.pull = @($Model)
$config.models.create = [pscustomobject]@{}
$config.minimum_free_gb = 5
$config.cloud.install_harnesses = ($Scenario -eq 'preexisting')
$configPath = Join-Path $WorkDir 'install.config.json'
[IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 10))
$configArgs = @('--config', $configPath)

if ($Scenario -eq 'preexisting') {
    Step 'the software that was there before the installer'
    foreach ($id in $Packages) {
        if (-not (Get-PackageInstalled $id)) {
            & winget install --id $id --exact --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -ne 0) { Fail "could not install the preexisting $id" }
        }
    }
    # A new process would see these on PATH; the installer looks them up in this one.
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$userPath;$machinePath"
}

Step 'the machine before the install'
$before = Get-MachineState
if (@($before.Rules).Count -gt 0) { Fail "local-llm rules already exist: $(@($before.Rules) -join ', ')" }

Step 'first install'
$first = Invoke-Wrapper $install ($configArgs + @('--log-file', "$WorkDir\install-1.log")) "$WorkDir\install-1.out"
if ($first.Code -ne 0) { Fail "the first install exited with $($first.Code)" }
Assert-NoErrorLines $first 'the first install'
$journal = Join-Path $UserHome 'AppData\Local\my-opencode-ollama-environment\install-journal.json'
$kinds = @((Get-Content -Raw $journal | ConvertFrom-Json).entries | ForEach-Object { $_.kind })
if ($Scenario -eq 'preexisting' -and $kinds -contains 'winget-install') {
    Fail 'the installer recorded a winget install although everything was already there'
}
if ($Scenario -eq 'fresh' -and $kinds -notcontains 'winget-install') { Fail 'Ollama was not recorded as installed' }

Step 'verify: no FAIL'
$check = Invoke-Wrapper $verify $configArgs "$WorkDir\verify.out"
if ($check.Code -ne 0) { Fail "verify exited with $($check.Code)" }
$fails = Get-MatchingLines $check '^FAIL'
if ($fails.Count -gt 0) { Fail "verify reported: $($fails -join '; ')" }

Step 'second install: nothing to do, and no UAC prompt'
$journalHash = (Get-FileHash -Algorithm SHA256 $journal).Hash
$second = Invoke-Wrapper $install ($configArgs + @('--log-file', "$WorkDir\install-2.log")) "$WorkDir\install-2.out"
if ($second.Code -ne 0) { Fail "the second install exited with $($second.Code)" }
Assert-NothingPending $second 'the second install'
if ((Get-MatchingLines $second 'already done').Count -eq 0) { Fail 'the second install reported no step as already done' }
if ((Get-FileHash -Algorithm SHA256 $journal).Hash -ne $journalHash) { Fail 'the second install changed the journal' }

Step 'uninstall listing'
$listing = Invoke-Wrapper $uninstall ($configArgs + @('--log-file', "$WorkDir\uninstall-list.log")) "$WorkDir\uninstall-list.out"
if ($listing.Code -ne 2) { Fail "the listing exited with $($listing.Code), expected 2" }

Step 'uninstall --yes'
$revert = Invoke-Wrapper $uninstall ($configArgs + @('--yes', '--log-file', "$WorkDir\uninstall.log")) "$WorkDir\uninstall.out"
if ($revert.Code -ne 0) { Fail "uninstall --yes exited with $($revert.Code)" }
Assert-NoErrorLines $revert 'the uninstall'

Step 'the machine after the uninstall'
$after = Get-MachineState
$tolerated = if ($Scenario -eq 'fresh') { $OllamaOwnFiles } else { @() }
$differences = @(Get-StateDifferences $before $after $tolerated)
if ($differences.Count -gt 0) { Fail "the machine differs:`n  $($differences -join "`n  ")" }

if ($Scenario -eq 'preexisting') {
    foreach ($id in $Packages) {
        if (-not (Get-PackageInstalled $id)) { Fail "$id was installed before the run and is gone" }
    }
}

Write-Host "`nvm e2e passed ($Scenario); logs in $WorkDir"
exit 0
