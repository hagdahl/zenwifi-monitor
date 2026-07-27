# ZenWiFi Monitor version: 0.1.0
[CmdletBinding()]
param(
  [switch]$InstallDependencies,
  [switch]$RegisterTask,
  [switch]$EnableExecution
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$pythonPath = Join-Path $root '.venv\Scripts\python.exe'

if (-not $InstallDependencies -and -not $RegisterTask) {
  throw 'Choose -InstallDependencies, -RegisterTask, or both. Run Setup-LocalConfig.ps1 and Setup-RouterConfig.ps1 separately.'
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
  & py -3.11 -m venv (Join-Path $root '.venv')
  & $pythonPath -m pip install --upgrade pip
  & $pythonPath -m pip install -r (Join-Path $root 'requirements.txt')
  & $pythonPath -m pip check
  if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed.' }
  Write-Host 'Python environment and pinned dependencies are ready.'
}

if ($RegisterTask) {
  if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'The project Python environment is missing. Run Install.ps1 -InstallDependencies first.'
  }
  $wrapper = Join-Path $root 'scripts\RouterWatchdog.vbs'
  $runAs = "$env:USERDOMAIN\$env:USERNAME"
  $executionArgument = if ($EnableExecution) { ' --execute' } else { '' }
  schtasks.exe /Create /TN 'ZenWiFiMonitor' /TR ('wscript.exe "' + $wrapper + '"' + $executionArgument) /SC MINUTE /MO 5 /RU $runAs /IT /F | Out-Host
  if ($LASTEXITCODE -ne 0) { throw 'Windows Task Scheduler registration failed.' }
  [xml]$taskXml = schtasks.exe /Query /TN 'ZenWiFiMonitor' /XML
  if ($taskXml.Task.Principals.Principal.LogonType -ne 'InteractiveToken') { throw 'The registered task is not configured for the interactive Windows user.' }
  if ($EnableExecution) { Write-Warning 'The task now passes --execute. Router restart remains gated by config.local.json execution_mode.' }
  else { Write-Host 'The silent five-minute task is registered. It remains dry-run until separately activated.' }
}
