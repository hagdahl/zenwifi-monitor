# ZenWiFi Monitor version: 0.1.0
[CmdletBinding()]
param(
  [switch]$InstallDependencies,
  [switch]$RegisterTask,
  [switch]$RegisterHealthTask,
  [switch]$EnableExecution
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
# The environment lives beside the runtime state rather than in the project
# tree. A virtual environment inside a cloud-synced directory is not durable:
# a sync or hygiene routine can move it away, and the scheduled jobs then have
# no interpreter. This is the same reasoning ADR-001 applies to the database
# and the logs. The project-local location remains a fallback for development.
$runtimePath = Join-Path $env:LOCALAPPDATA 'ZenWiFiMonitor\.venv'
$pythonPath = Join-Path $runtimePath 'Scripts\python.exe'
$projectPythonPath = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath) -and (Test-Path -LiteralPath $projectPythonPath)) {
  $pythonPath = $projectPythonPath
}

if (-not $InstallDependencies -and -not $RegisterTask -and -not $RegisterHealthTask) {
  throw 'Choose -InstallDependencies, -RegisterTask, -RegisterHealthTask, or a combination. Run Setup-LocalConfig.ps1 and Setup-RouterConfig.ps1 separately.'
}
if ($EnableExecution -and -not $RegisterTask) { throw '-EnableExecution requires -RegisterTask.' }

if ($InstallDependencies) {
  if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Python Launcher was not found. Install Python 3.11 (64-bit) from python.org and verify with: py -3.11 --version'
  }
  & py -3.11 --version
  if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.11 is required. Install it and verify with: py -3.11 --version'
  }
  & py -3.11 -m venv $runtimePath
  $pythonPath = Join-Path $runtimePath 'Scripts\python.exe'
  if (-not (Test-Path -LiteralPath $pythonPath)) { throw "The environment was not created at $runtimePath." }
  # NOT `pip install --upgrade pip` first. That fetched an unpinned, unhashed
  # distribution from the network and then used it to enforce the hash checking
  # below, putting the tool that verifies the supply chain outside the supply
  # chain it verifies. The interpreter's bundled pip has supported
  # --require-hashes for years; if it ever needs upgrading, pin it in
  # requirements.in so it arrives hashed like everything else.
  # --require-hashes makes pip refuse the whole file unless every distribution
  # matches a recorded hash, so a compromised or substituted artefact fails the
  # install instead of reaching an unattended job that can restart a router.
  & $pythonPath -m pip install --require-hashes -r (Join-Path $root 'requirements.lock.txt')
  if ($LASTEXITCODE -ne 0) { throw 'Hash-locked dependency installation failed.' }
  & $pythonPath -m pip check
  if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed.' }
  Write-Host "Python environment and hash-locked dependencies are ready at $runtimePath."

}

if ($RegisterTask) {
  if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'The Python environment is missing. Run Install.ps1 -InstallDependencies first.'
  }
  $wrapper = Join-Path $root 'scripts\RouterWatchdog.vbs'
  $runAs = "$env:USERDOMAIN\$env:USERNAME"
  $executionArgument = if ($EnableExecution) { ' --execute' } else { '' }
  schtasks.exe /Create /TN 'ZenWiFiMonitor' /TR ('wscript.exe "' + $wrapper + '"' + $executionArgument) /SC MINUTE /MO 5 /RU $runAs /IT /F | Out-Host
  if ($LASTEXITCODE -ne 0) { throw 'Windows Task Scheduler registration failed.' }
  [xml]$taskXml = schtasks.exe /Query /TN 'ZenWiFiMonitor' /XML
  if ($taskXml.Task.Principals.Principal.LogonType -ne 'InteractiveToken') { throw 'The registered task is not configured for the interactive Windows user.' }
  if ($taskXml.Task.Actions.Exec.Command -ne 'wscript.exe') { throw 'The registered task does not run through the silent VBS wrapper.' }
  if ($EnableExecution) { Write-Warning 'The task now passes --execute. Router restart remains gated by config.local.json execution_mode.' }
  else {
    Write-Host 'The silent five-minute task is registered. It remains dry-run until separately activated.'
    Write-Host 'This is also how gate 1 is closed again: re-running -RegisterTask without'
    Write-Host '-EnableExecution replaces the task with one that carries no --execute.'
  }
}

if ($RegisterHealthTask) {
  if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'The Python environment is missing. Run Install.ps1 -InstallDependencies first.'
  }
  $wrapper = Join-Path $root 'scripts\RouterWatchdog.vbs'
  $runAs = "$env:USERDOMAIN\$env:USERNAME"
  schtasks.exe /Create /TN 'ZenWiFiMonitorHealth' /TR ('wscript.exe "' + $wrapper + '" --script=src\health.py') /SC MINUTE /MO 15 /RU $runAs /IT /F | Out-Host
  if ($LASTEXITCODE -ne 0) { throw 'Windows Task Scheduler registration of the health task failed.' }
  [xml]$healthXml = schtasks.exe /Query /TN 'ZenWiFiMonitorHealth' /XML
  if ($healthXml.Task.Principals.Principal.LogonType -ne 'InteractiveToken') { throw 'The registered health task is not configured for the interactive Windows user.' }
  if ($healthXml.Task.Actions.Exec.Command -ne 'wscript.exe') { throw 'The registered health task does not run through the silent VBS wrapper.' }
  if ($healthXml.Task.Actions.Exec.Arguments -match '--execute') { throw 'The health task must never carry --execute.' }
  Write-Host 'The silent fifteen-minute health task is registered. It can never restart the router.'
}
