<#
.SYNOPSIS
Safely migrates an earlier local ZenWiFi Monitor configuration to the current schema.

.DESCRIPTION
The script is a dry-run unless -WriteConfig is supplied. It never contacts the
router or Notion, reads no credentials, and never enables restart execution.

The migration maps the legacy router https_port setting to management_port,
records use_tls as true, and adds the explicit Notion enabled flag. Notion
remains disabled unless it was already enabled or -EnableNotion is supplied.
Before writing, the script creates an ignored timestamped backup next to
config.local.json.
#>
[CmdletBinding()]
param(
  [switch]$EnableNotion,
  [switch]$WriteConfig
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$configPath = Join-Path $projectRoot 'config.local.json'

if (-not (Test-Path -LiteralPath $configPath)) {
  throw 'config.local.json does not exist. Run Setup-LocalConfig.ps1 first.'
}

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if (-not $config.PSObject.Properties['router']) { throw 'The local configuration has no router section.' }
if (-not $config.PSObject.Properties['notion']) { $config | Add-Member -NotePropertyName notion -NotePropertyValue ([pscustomobject]@{}) }

$changes = [System.Collections.Generic.List[string]]::new()
if (-not $config.router.PSObject.Properties['management_port']) {
  if (-not $config.router.PSObject.Properties['https_port']) { throw 'The router section has neither management_port nor https_port.' }
  $config.router | Add-Member -NotePropertyName management_port -NotePropertyValue $config.router.https_port
  $config.router.PSObject.Properties.Remove('https_port')
  $changes.Add('Mapped router.https_port to router.management_port.')
}
if (-not $config.router.PSObject.Properties['use_tls']) {
  $config.router | Add-Member -NotePropertyName use_tls -NotePropertyValue $true
  $changes.Add('Set router.use_tls to true.')
}

$notionWasEnabled = $false
if ($config.notion.PSObject.Properties['enabled']) { $notionWasEnabled = [bool]$config.notion.enabled }
$notionEnabled = $notionWasEnabled -or $EnableNotion
$config.notion | Add-Member -NotePropertyName enabled -NotePropertyValue $notionEnabled -Force
if (-not $notionWasEnabled) {
  if ($EnableNotion) { $changes.Add('Enabled optional Notion logging by explicit request.') }
  else { $changes.Add('Set optional Notion logging to disabled.') }
}
if (-not $config.notion.PSObject.Properties['api_version']) {
  $config.notion | Add-Member -NotePropertyName api_version -NotePropertyValue '2026-03-11'
  $changes.Add('Added the Notion API version.')
}
if (-not $config.PSObject.Properties['execution_mode']) {
  $config | Add-Member -NotePropertyName execution_mode -NotePropertyValue 'dry-run'
  $changes.Add('Set execution_mode to dry-run.')
}

Write-Host 'Configuration migration plan:'
if ($changes.Count -eq 0) { Write-Host 'No schema changes are required.' }
else { $changes | ForEach-Object { Write-Host "- $_" } }
Write-Host "Notion logging after migration: $notionEnabled"
Write-Host 'Router communication after migration: TLS enabled.'
Write-Host 'Restart execution after migration: unchanged.'

if (-not $WriteConfig) {
  Write-Host 'Dry-run only. Re-run with -WriteConfig to create a backup and update config.local.json.'
  exit 0
}

$backupPath = "$configPath.$(Get-Date -Format 'yyyyMMdd-HHmmss').bak"
Copy-Item -LiteralPath $configPath -Destination $backupPath -ErrorAction Stop
$json = $config | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($configPath, $json, (New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false))
Write-Host 'Local configuration migrated. No router, Notion, or credential action was performed.'
Write-Host "Backup created: $backupPath"
