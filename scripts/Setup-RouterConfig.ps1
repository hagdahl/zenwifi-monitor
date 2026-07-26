<#
.SYNOPSIS
Discovers and safely records local router settings for ZenWiFi Monitor.

.DESCRIPTION
1. Run without -WriteConfig to discover the Windows default gateway and verify TLS.
2. Confirm that the detected gateway and model are correct.
3. Resolve any TLS warning in the router administration interface before continuing.
4. Re-run with -WriteConfig only after TLS is verified. This updates config.local.json only.
5. Use -AllowInsecureHttp only as an explicit, last-resort exception. It requires typed confirmation.

The script never reads credentials, authenticates to the router, changes router settings,
or falls back to HTTP unless the user explicitly authorizes the insecure exception.
#>
[CmdletBinding()]
param(
  [string]$RouterModel = 'ASUSWRT-compatible router',
  [int]$HttpsPort = 8443,
  [int]$HttpPort = 80,
  [switch]$AllowInsecureHttp,
  [switch]$WriteConfig
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$configPath = Join-Path $projectRoot 'config.local.json'

function Test-TlsEndpoint {
  param([string]$HostName, [int]$Port)
  $client = [System.Net.Sockets.TcpClient]::new()
  $stream = $null
  try {
    $connect = $client.BeginConnect($HostName, $Port, $null, $null)
    if (-not $connect.AsyncWaitHandle.WaitOne(10000)) { throw 'TLS connection timed out.' }
    $client.EndConnect($connect)
    $stream = [System.Net.Security.SslStream]::new($client.GetStream(), $false, { $true })
    $stream.AuthenticateAsClient($HostName)
    $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]$stream.RemoteCertificate
    return [pscustomobject]@{ Protocol = $stream.SslProtocol.ToString(); Subject = $certificate.Subject; Thumbprint = $certificate.Thumbprint }
  } catch {
    return $null
  } finally {
    if ($stream) { $stream.Dispose() }
    $client.Dispose()
  }
}
$defaultRoute = Get-NetRoute -DestinationPrefix '0.0.0.0/0' |
  Where-Object { $_.NextHop -ne '0.0.0.0' } |
  Sort-Object RouteMetric |
  Select-Object -First 1

if (-not $defaultRoute) { throw 'No IPv4 default gateway was found.' }
$routerHost = $defaultRoute.NextHop.ToString()
$portReachable = Test-NetConnection -ComputerName $routerHost -Port $HttpsPort -InformationLevel Quiet
$tlsResult = if ($portReachable) { Test-TlsEndpoint -HostName $routerHost -Port $HttpsPort } else { $null }

Write-Host "Detected default gateway: $routerHost"
Write-Host "Configured router model: $RouterModel"
Write-Host "HTTPS port $HttpsPort reachable: $portReachable"
Write-Host "TLS available (certificate not validated): $([bool]$tlsResult)"
if ($tlsResult) {
  Write-Host "Negotiated TLS protocol: $($tlsResult.Protocol)"
  Write-Host "Certificate subject: $($tlsResult.Subject)"
  Write-Host "Certificate thumbprint: $($tlsResult.Thumbprint)"
}

if (-not $tlsResult) {
  Write-Warning "HTTPS/TLS is not available at https://$routerHost`:$HttpsPort. Do not use HTTP as a fallback. Enable or repair HTTPS/TLS in the router administration interface, then run this script again."
  if ($WriteConfig -and -not $AllowInsecureHttp) { throw 'Refusing to write router configuration without verified HTTPS/TLS. Use -AllowInsecureHttp only after explicit risk acceptance.' }
}

if (-not $WriteConfig) {
  Write-Host 'Dry-run only. Re-run with -WriteConfig to update config.local.json.'
  exit 0
}

$useTls = $true
$managementPort = $HttpsPort
if (-not $tlsResult) {
  $httpReachable = Test-NetConnection -ComputerName $routerHost -Port $HttpPort -InformationLevel Quiet
  Write-Host "HTTP port $HttpPort reachable: $httpReachable"
  if (-not $httpReachable) { throw 'Insecure HTTP was authorized but the configured HTTP port is not reachable.' }
  $confirmation = Read-Host 'Type I ACCEPT INSECURE HTTP to allow unencrypted router communication'
  if ($confirmation -cne 'I ACCEPT INSECURE HTTP') { throw 'Insecure HTTP authorization was not confirmed exactly. No configuration was changed.' }
  Write-Warning 'Insecure HTTP is enabled by explicit user authorization. Credentials and router traffic will not have TLS transport protection.'
  $useTls = $false
  $managementPort = $HttpPort
}

if (-not (Test-Path -LiteralPath $configPath)) { throw 'config.local.json does not exist. Create it from config.example.json first.' }
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$config.router | Add-Member -NotePropertyName host -NotePropertyValue $routerHost -Force
$config.router | Add-Member -NotePropertyName management_port -NotePropertyValue $managementPort -Force
$config.router | Add-Member -NotePropertyName use_tls -NotePropertyValue $useTls -Force
$config.router | Add-Member -NotePropertyName model -NotePropertyValue $RouterModel -Force
$json = $config | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($configPath, $json, (New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false))
Write-Host 'config.local.json updated. No credentials were read or written.'
