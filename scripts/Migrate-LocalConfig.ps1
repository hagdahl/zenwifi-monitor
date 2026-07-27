# ZenWiFi Monitor version: 0.1.0
<#
.SYNOPSIS
Safely migrates an earlier local ZenWiFi Monitor configuration to the current schema.

.DESCRIPTION
A thin wrapper. All migration logic lives in scripts/configure.py, which is the
single implementation for Windows and Debian alike; two implementations would
drift into disagreeing about what a current configuration looks like, which is
the same failure the shared defaults module exists to prevent.

The behaviour is unchanged: a dry run unless -WriteConfig is supplied, nothing
contacts the router or Notion, no credential is read, execution_mode is never
changed when it is already present, and an ignored timestamped backup is
written next to the configuration before anything is updated.
#>
[CmdletBinding()]
param(
  [switch]$EnableNotion,
  [switch]$WriteConfig
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$tool = Join-Path $projectRoot 'scripts\configure.py'
if (-not (Test-Path -LiteralPath $tool)) { throw "The configuration tool is missing at $tool." }

# Prefer the runtime environment, fall back to the project one, then to the
# launcher. configure.py is standard-library only, so any Python 3.11 will do:
# configuration work has to be possible before the environment exists.
$candidates = @(
  (Join-Path $env:LOCALAPPDATA 'ZenWiFiMonitor\.venv\Scripts\python.exe'),
  (Join-Path $projectRoot '.venv\Scripts\python.exe')
)
$python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
  if (Get-Command py -ErrorAction SilentlyContinue) { $python = 'py'; $prefix = @('-3.11') }
  else { throw 'No Python 3.11 interpreter was found. Install one, or run Install.ps1 -InstallDependencies.' }
}
if (-not $prefix) { $prefix = @() }

$arguments = @('--migrate')
if ($EnableNotion) { $arguments += '--enable-notion' }
if ($WriteConfig) { $arguments += '--apply' }

& $python @prefix $tool @arguments
exit $LASTEXITCODE
