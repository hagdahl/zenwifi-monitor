<#
.SYNOPSIS
Creates a history-free `_public` staging copy for public GitHub publication.

.DESCRIPTION
The script copies only files tracked by the current, clean source repository.
It never copies the source `.git` directory, ignored local configuration,
runtime data, logs, credentials, or untracked files. The destination must not
already exist, preventing accidental overwrite of a staged public repository.
#>
[CmdletBinding()]
param([switch]$WriteStaging)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$stagingPath = Join-Path $projectRoot '_public'
$safeDirectory = $projectRoot -replace '\\', '/'

& git -c "safe.directory=$safeDirectory" -C $projectRoot diff --quiet
if ($LASTEXITCODE -ne 0) { throw 'The source repository has uncommitted changes. Commit or discard them before preparing public staging.' }

$trackedFiles = & git -c "safe.directory=$safeDirectory" -C $projectRoot ls-files
if ($LASTEXITCODE -ne 0 -or -not $trackedFiles) { throw 'No tracked source files were found.' }

Write-Host "Tracked files selected for public staging: $($trackedFiles.Count)"
Write-Host 'The source Git history and all ignored files are excluded.'
if (-not $WriteStaging) {
  Write-Host 'Dry-run only. Re-run with -WriteStaging to create the empty-safe _public staging directory.'
  exit 0
}

if (Test-Path -LiteralPath $stagingPath) { throw '_public already exists. Review or remove that exact staging directory manually before creating a new one.' }
New-Item -ItemType Directory -Path $stagingPath -ErrorAction Stop | Out-Null
foreach ($relativePath in $trackedFiles) {
  $sourcePath = Join-Path $projectRoot $relativePath
  $destinationPath = Join-Path $stagingPath $relativePath
  New-Item -ItemType Directory -Path (Split-Path -Parent $destinationPath) -Force | Out-Null
  Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -ErrorAction Stop
}
Write-Host 'Public staging created. Review _public before initializing a new Git repository or adding a remote.'
