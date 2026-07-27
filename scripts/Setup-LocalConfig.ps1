# ZenWiFi Monitor version: 0.1.0
<#
.SYNOPSIS
Creates or updates the local ZenWiFi Monitor configuration without storing secrets.

.DESCRIPTION
Run this script under the Windows account that will run the scheduled task.
It asks for an absolute local data directory, stores the SQLite database and
log directory below it, and lets the user enable optional Notion event logging.
Router credentials and Notion tokens are never handled here; use Setup-Secrets.py.

The script changes config.local.json only. That file is ignored by Git.
#>
[CmdletBinding()]
param(
  [string]$DataDirectory,
  [switch]$EnableNotion,
  [string]$NotionDataSourceId
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$configPath = Join-Path $projectRoot 'config.local.json'
$examplePath = Join-Path $projectRoot 'config.example.json'

if (-not $DataDirectory) {
  $defaultDirectory = Join-Path $env:LOCALAPPDATA 'ZenWiFiMonitor'
  $enteredDirectory = Read-Host "Local data directory (press Enter for $defaultDirectory)"
  $DataDirectory = if ($enteredDirectory) { $enteredDirectory } else { $defaultDirectory }
}

if ([System.IO.Path]::GetPathRoot($DataDirectory) -notmatch '^[A-Za-z]:\\$') {
  throw 'The local data directory must be an absolute path.'
}

$dataPath = [System.IO.Path]::GetFullPath($DataDirectory)
$logPath = Join-Path $dataPath 'logs'
$databasePath = Join-Path $dataPath 'zenwifi-monitor.sqlite3'
New-Item -ItemType Directory -Path $logPath -Force | Out-Null

if (Test-Path -LiteralPath $configPath) {
  $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
  Write-Host 'Updating existing config.local.json. Router settings are preserved.'
} else {
  $config = Get-Content -LiteralPath $examplePath -Raw | ConvertFrom-Json
  Write-Host 'Creating config.local.json from config.example.json.'
}

$config.paths.log_directory = $logPath
$config.paths.state_database = $databasePath
if (-not $config.PSObject.Properties['notion']) {
  $config | Add-Member -NotePropertyName notion -NotePropertyValue ([pscustomobject]@{})
}
$config.notion | Add-Member -NotePropertyName enabled -NotePropertyValue ([bool]$EnableNotion) -Force

if ($EnableNotion) {
  if (-not $NotionDataSourceId) {
    $NotionDataSourceId = Read-Host 'Notion data source ID'
  }
  if (-not $NotionDataSourceId) { throw 'A Notion data source ID is required when Notion logging is enabled.' }
  $config.notion | Add-Member -NotePropertyName data_source_id -NotePropertyValue $NotionDataSourceId -Force
  if (-not $config.notion.PSObject.Properties['api_version']) {
    $config.notion | Add-Member -NotePropertyName api_version -NotePropertyValue '2026-03-11'
  }
  Write-Host 'Notion logging is enabled. Run Setup-Secrets.py and choose the Notion-token option.'
} else {
  Write-Host 'Notion logging is disabled. Local SQLite logging remains enabled.'
}

$json = $config | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($configPath, $json, (New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false))
Write-Host "Local data directory: $dataPath"
Write-Host "SQLite database: $databasePath"
Write-Host "Log directory: $logPath"
Write-Host 'Setup completed. No secrets were read or written.'
