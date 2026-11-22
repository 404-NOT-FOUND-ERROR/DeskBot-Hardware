[CmdletBinding()]
param(
    [string]$TrustedWifiAddress,
    [Parameter(Mandatory)][string]$LlmConfig,
    [string]$DevicePort = 'COM4',
    [string]$StateDirectory,
    [switch]$Check,
    [switch]$Resume
)

$ErrorActionPreference = 'Stop'
$trialRoot = Split-Path -Parent $PSScriptRoot
$nodeExe = (Get-Command node.exe -ErrorAction Stop).Source
if ((& $nodeExe --version) -notmatch '^v24\.') { throw '语音试用需要 Node 24。' }
$configPath = (Resolve-Path -LiteralPath $LlmConfig).Path
$network = $null
if ($TrustedWifiAddress) {
    $network = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $TrustedWifiAddress -ErrorAction SilentlyContinue
    if (-not $network -or $TrustedWifiAddress -notmatch '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)') {
        throw '请填写电脑在桌宠所用的私人 Wi-Fi 网络里的 IPv4 地址。'
    }
} elseif (-not $Check) {
    throw '请先让电脑连到桌宠所用的私人 Wi-Fi，并填写电脑在该网络里的 IPv4 地址。'
}

$accepted = Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $trialRoot 'artifacts/display-clock-e3f9d3/live-result.json') | ConvertFrom-Json
$observed = Invoke-RestMethod -Uri 'http://127.0.0.1:8780/api/body/state' -NoProxy -TimeoutSec 4
$hello = $observed.identity.hello
if (-not $observed.live -or $observed.freshness -ne 'fresh' -or $observed.connection.port -ne $DevicePort -or
    $hello.device_id -ne $accepted.boot.hello.device_id -or $hello.firmware.app_elf_sha256 -ne $accepted.app.app_elf_sha256) {
    throw '工作台尚未确认试用所需的桌宠头部和固件。'
}
$portDevice = @(Get-PnpDevice -PresentOnly -Class Ports | Where-Object {$_.FriendlyName -like "*($DevicePort)"})
if ($portDevice.Count -ne 1) { throw '没有找到唯一的桌宠头部 USB 连接。' }
$parent = (Get-PnpDeviceProperty -InstanceId $portDevice[0].InstanceId -KeyName 'DEVPKEY_Device_Parent').Data
if ($parent -notmatch '^USB\\VID_303A&PID_1001\\((?:[0-9a-f]{2}:){5}[0-9a-f]{2})$') { throw '未取得头部的 USB 硬件身份。' }
$mac = $Matches[1].ToLowerInvariant()
$seed = [byte[]]([Text.Encoding]::ASCII.GetBytes("deskbot-link-v1`0") + [Convert]::FromHexString($mac.Replace(':','')))
$publicId = ([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($seed))).ToLowerInvariant().Substring(0,32)
if ($hello.device_id -ne "device-$publicId") { throw 'USB 与桌宠运行身份不匹配。' }

$bootLog = Join-Path $trialRoot 'tmp/e3c-live-20261113-e3f9d3-live1/captures/051-eat-boot.log'
$lines = Get-Content -Encoding utf8 -LiteralPath $bootLog
$uuidLines = @($lines | Select-String 'Board: UUID=([a-f0-9-]{36}) SKU=esp_vocat')
if ($uuidLines.Count -ne 1 -or -not ($lines | Select-String -SimpleMatch $hello.device_id -Quiet) -or
    -not ($lines | Select-String -SimpleMatch $hello.firmware.app_elf_sha256 -Quiet)) {
    throw '原启动记录没有绑定本体身份，请先补一份同设备的启动记录。'
}
$uuid = $uuidLines[0].Matches[0].Groups[1].Value
$record = Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $trialRoot 'tmp/client-conversation-20261118/backup-validation.json') | ConvertFrom-Json
$backup = Join-Path $trialRoot $record.baseline
if ($Check) {
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $backup).Hash -ne $record.baseline_sha256 -or
        ((Test-Path -LiteralPath "$backup-wal") -and (Get-Item -LiteralPath "$backup-wal").Length -gt 0)) {
        throw '试用备份的摘要或 SQLite 日志与记录不符。'
    }
    Push-Location $trialRoot
    try {
        & $nodeExe --input-type=module -e 'await import("./scripts/serve-client-voice-pilot.mjs")'
        if ($LASTEXITCODE -ne 0) { throw '语音运行依赖未就绪。' }
    } finally { Pop-Location }
    @{status='preflight_passed'; port=$DevicePort; firmware=$accepted.edition; private_wifi_selected=[bool]$network} |
        ConvertTo-Json -Compress
    return
}
$statePath = if ($StateDirectory) { [IO.Path]::GetFullPath($StateDirectory, $trialRoot) }
    else { Join-Path $trialRoot 'tmp/device-voice-trial-20261119' }
New-Item -ItemType Directory -Path $statePath -Force | Out-Null
$identityPath = Join-Path $statePath 'device-identity.json'
@{host=$network.IPAddress; device_mac=$mac; client_uuid=$uuid; firmware_elf_sha256=$hello.firmware.app_elf_sha256} |
    ConvertTo-Json -Compress | Set-Content -Encoding utf8NoBOM -LiteralPath $identityPath
$pilotArgs = @(
    (Join-Path $trialRoot 'scripts/serve-client-voice-pilot.mjs'),
    '--client-root', (Join-Path $trialRoot 'tmp/client-conversation-20261118/client'),
    '--backup', $backup,
    '--backup-sha256', $record.baseline_sha256,
    '--config-path', $configPath,
    '--source-id', $record.installation.source_id,
    '--device-id', $hello.device_id,
    '--state-dir', $statePath,
    '--device-trial-config', $identityPath
)
if ($Resume) { $pilotArgs += '--resume' }
& $nodeExe @pilotArgs
if ($LASTEXITCODE -ne 0) { throw "语音服务启动或运行失败（$LASTEXITCODE）。" }
