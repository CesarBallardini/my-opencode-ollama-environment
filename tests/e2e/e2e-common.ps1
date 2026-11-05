# Helpers shared by ci-e2e.ps1 and vm-e2e.ps1. Dot-source this file; it runs nothing on its own.
# Written for both Windows PowerShell 5.1 and PowerShell 7, and kept ASCII because 5.1 reads a file
# without a byte order mark in the ANSI code page.

function Step([string]$Name) { Write-Host "`n=== $Name" }

function Fail([string]$Message) {
    Write-Host "E2E FAILED: $Message"
    exit 1
}

function Test-Elevated {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-Wrapper([string]$Wrapper, [string[]]$Arguments, [string]$Log) {
    # PowerShell runs a .cmd through cmd.exe and quotes each argument itself, so %~dp0 and %* behave
    # as for the user. Stderr lines arrive as error records, which must not stop this script.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = & $Wrapper @Arguments 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    $lines = @($output | ForEach-Object { "$_" })
    $lines | Set-Content -Encoding utf8 $Log
    $lines | ForEach-Object { Write-Host "  $_" }
    return [pscustomobject]@{ Code = $code; Lines = $lines }
}

function Get-MatchingLines($Result, [string]$Pattern) {
    # The comma keeps an empty or one-item result an array: unrolled, .Count would throw in strict mode.
    return , @($Result.Lines | Where-Object { $_ -match $Pattern })
}

function Assert-Line($Result, [string]$Prefix) {
    $found = @($Result.Lines | Where-Object { $_.StartsWith($Prefix) })
    if ($found.Count -eq 0) { Fail "no line starts with '$Prefix'" }
}

function Assert-NoErrorLines($Result, [string]$What) {
    $errors = Get-MatchingLines $Result '^ERROR'
    if ($errors.Count -gt 0) { Fail "$What logged errors: $($errors -join '; ')" }
}

function Assert-NothingPending($Result, [string]$What) {
    $pending = Get-MatchingLines $Result 'pending:'
    if ($pending.Count -gt 0) { Fail "$What still had work: $($pending -join '; ')" }
}

function Get-UserEnvironment {
    # The stored form: an expandable value stays unexpanded, so %USERPROFILE% entries compare as written.
    $key = Get-Item -Path 'HKCU:\Environment'
    $values = @{}
    foreach ($name in $key.GetValueNames()) {
        $values[$name] = "$($key.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames))"
    }
    return $values
}

function Get-LocalLlmRules {
    return @(Get-NetFirewallRule -DisplayName 'local-llm:*' -ErrorAction SilentlyContinue |
            ForEach-Object { $_.DisplayName } | Sort-Object)
}

function Compare-Mapping($Before, $After, [string]$Label) {
    # Every key whose value differs between two hashtables, as printable lines.
    $differences = @()
    foreach ($name in (@($Before.Keys) + @($After.Keys) | Sort-Object -Unique)) {
        $was = if ($Before.ContainsKey($name)) { $Before[$name] } else { '<absent>' }
        $is = if ($After.ContainsKey($name)) { $After[$name] } else { '<absent>' }
        if ($was -ne $is) { $differences += "$Label ${name}: '$was' -> '$is'" }
    }
    return $differences
}
