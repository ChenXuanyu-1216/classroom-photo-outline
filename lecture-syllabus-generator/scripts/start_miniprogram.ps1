# Start a LAN-reachable local backend; check-only never starts or edits anything.
param(
    [switch]$CheckOnly,
    [ValidateRange(1, 65535)][int]$Port = 8000
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$backendDir = Join-Path $projectRoot 'lecture-syllabus-generator/backend'
$pythonPath = Join-Path $backendDir '.venv/Scripts/python.exe'

function Read-BackendMeta([string]$BaseUrl) {
    $response = $null
    $reader = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create($BaseUrl + '/api/meta')
        $request.Proxy = $null
        $request.Timeout = 1200
        $request.ReadWriteTimeout = 1200
        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        $value = $reader.ReadToEnd() | ConvertFrom-Json
        if ($value.model_provider -and $value.model_name) { return $value }
        return $null
    } catch { return $null }
    finally { if ($reader) { $reader.Dispose() }; if ($response) { $response.Dispose() } }
}

function Get-PortListeners {
    return @([System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() | Where-Object { $_.Port -eq $Port })
}

function Get-LanAddresses {
    $addresses = foreach ($adapter in [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($adapter.OperationalStatus -ne 'Up' -or $adapter.NetworkInterfaceType -eq 'Loopback') { continue }
        foreach ($item in $adapter.GetIPProperties().UnicastAddresses) {
            $address = $item.Address
            if ($address.AddressFamily -eq 'InterNetwork' -and -not [System.Net.IPAddress]::IsLoopback($address) -and -not $address.ToString().StartsWith('169.254.')) { $address.ToString() }
        }
    }
    return @($addresses | Sort-Object -Unique)
}

$localUrl = 'http://127.0.0.1:' + $Port
$metadataUrl = $localUrl
$meta = Read-BackendMeta $localUrl
$listeners = @(Get-PortListeners)
if ($meta -and @($listeners | Where-Object { -not [System.Net.IPAddress]::IsLoopback($_.Address) }).Count -eq 0) {
    throw 'LOOPBACK_ONLY: Backend is healthy only on 127.0.0.1. Stop that backend, then restart with APP_SERVER_HOST=0.0.0.0. It is not ready for a phone. No process was stopped.'
}
# An explicit LAN bind can be healthy without accepting loopback requests.
if (-not $meta -and @($listeners | Where-Object { -not [System.Net.IPAddress]::IsLoopback($_.Address) }).Count -gt 0) {
    foreach ($address in @(Get-LanAddresses)) {
        $candidateUrl = 'http://' + $address + ':' + $Port
        $candidateMeta = Read-BackendMeta $candidateUrl
        if ($candidateMeta) { $meta = $candidateMeta; $metadataUrl = $candidateUrl; break }
    }
}
if (-not $meta) {
    if ($CheckOnly) { throw 'BACKEND_NOT_RUNNING: No reachable local backend. Check-only did not start or modify anything.' }
    if ($listeners.Count -gt 0) { throw 'PORT_IN_USE: Port is occupied but backend metadata is unavailable. Existing processes were left unchanged.' }
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Python environment missing. Install the backend dependencies first.' }
    $logDir = Join-Path $backendDir 'data'
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $arguments = '-m uvicorn app.main:app --host 0.0.0.0 --port ' + $Port
    $startedProcess = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'miniprogram-server.log') -RedirectStandardError (Join-Path $logDir 'miniprogram-server-error.log')
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        Start-Sleep -Milliseconds 500
        $meta = Read-BackendMeta $localUrl
        if ($meta -or $startedProcess.HasExited) { break }
    }
}
if (-not $meta) { throw 'BACKEND_START_FAILED: Check backend/data/miniprogram-server-error.log and the port.' }
$listeners = @(Get-PortListeners)
if (@($listeners | Where-Object { -not [System.Net.IPAddress]::IsLoopback($_.Address) }).Count -eq 0) { throw 'LOOPBACK_ONLY: Backend is not listening on a LAN interface.' }
$lanUrls = @(foreach ($address in @(Get-LanAddresses)) {
    $url = 'http://' + $address + ':' + $Port
    if (Read-BackendMeta $url) { $url }
})
if ($lanUrls.Count -eq 0) { throw 'LAN_NOT_REACHABLE: Localhost works but no current LAN address passed metadata checks. Check network, binding and firewall.' }
Write-Output ('LAN_READY: ' + $metadataUrl)
foreach ($url in $lanUrls) { Write-Output ('Phone URL (verified from this computer): ' + $url) }
Write-Output ('Provider: ' + $meta.model_provider + '; model: ' + $meta.model_name + '; configured: ' + $meta.model_ready)
if ($CheckOnly) { return }

$privatePath = Join-Path $projectRoot 'miniprogram/project.private.config.json'
$privateConfig = if (Test-Path -LiteralPath $privatePath) { Get-Content -LiteralPath $privatePath -Raw | ConvertFrom-Json } else { [PSCustomObject]@{} }
if (-not $privateConfig.PSObject.Properties['setting']) { $privateConfig | Add-Member -NotePropertyName setting -NotePropertyValue ([PSCustomObject]@{}) }
if ($privateConfig.setting.PSObject.Properties['urlCheck']) { $privateConfig.setting.urlCheck = $false }
else { $privateConfig.setting | Add-Member -NotePropertyName urlCheck -NotePropertyValue $false }
[System.IO.File]::WriteAllText($privatePath, ($privateConfig | ConvertTo-Json -Depth 30), (New-Object System.Text.UTF8Encoding $false))
Write-Output 'Local domain-check override saved. Recompile in WeChat Devtools. Check the phone connection separately; this is not a public HTTPS deployment. No model generation was requested.'
