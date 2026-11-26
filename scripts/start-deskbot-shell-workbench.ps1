[CmdletBinding()]
param([int]$Port = 8779, [switch]$Preview, [ValidateSet('characters', 'shells', 'capture', 'connection')][string]$View = 'characters', [string]$BodyNetworkProfile, [string]$InstalledPackage)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ($Preview -and -not $PSBoundParameters.ContainsKey('Port')) { $Port = 8780 }
$python = 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe'
$serialPython = 'D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe'
foreach ($runtime in @($python, $serialPython)) {
    if (-not (Test-Path -LiteralPath $runtime -PathType Leaf)) { throw "Missing configured Python: $runtime" }
}
$serialVersion = & $serialPython -c 'import serial; print(serial.__version__)' | Out-String
if ($LASTEXITCODE -ne 0 -or $serialVersion.Trim() -ne '3.5') { throw 'Check the configured serial Python.' }
$url = "http://127.0.0.1:$Port"
$existing = $null
try { $existing = Invoke-RestMethod "$url/api/shells/state" -TimeoutSec 2 } catch { }
if ($existing.schema -eq 'deskbot.shell_workbench/v1') {
    if ($BodyNetworkProfile) {
        $network = Invoke-RestMethod "$url/api/body/network" -TimeoutSec 3
        $expected = Get-Content -LiteralPath (Join-Path $BodyNetworkProfile 'receiver.json') -Raw | ConvertFrom-Json
        if (-not $network.available -or $network.binding.device_id -ne $expected.device_id -or $network.binding.firmware_sha256 -ne $expected.firmware_sha256) { throw 'Restart this workbench with the paired network profile.' }
    }
    $characters = $null
    try { $characters = Invoke-RestMethod "$url/api/characters/state" -TimeoutSec 2 } catch { }
    if ($characters.schema -ne 'deskbot.characters/v1') { throw 'The running service uses an earlier page. Restart that preview service to load character management.' }
    if ($InstalledPackage) {
        $expectedPackage = Get-Content -LiteralPath (Join-Path $InstalledPackage 'report.json') -Raw | ConvertFrom-Json
        if ($characters.expression_catalog.graph_sha256 -ne $expectedPackage.download_sha256.'deskbot-roles.json') { throw 'Restart this workbench with the installed role package.' }
    }
    $shellCharacters = $null
    try { $shellCharacters = Invoke-RestMethod "$url/api/shell-characters/state" -TimeoutSec 2 } catch { }
    if ($shellCharacters.schema -ne 'deskbot.shell_characters/v1') { throw 'The running service uses an earlier page. Restart that preview service to load shell associations.' }
    $installation = $null
    try { $installation = Invoke-RestMethod "$url/api/shell-installation/state" -TimeoutSec 2 } catch { }
    if ($installation.schema -ne 'deskbot.shell_installation/v1') { throw 'Restart this workbench service to load owner installation confirmation.' }
    Write-Output "$url/#$View"
    return
}
Push-Location $root
try {
    & $python scripts/build-deskbot-shell-workbench.py
    if ($LASTEXITCODE -ne 0) { throw 'Workbench generation did not complete.' }
    $logs = Join-Path $root 'tmp/shell-workbench-server'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $serverArgs = @('-u', 'scripts/serve-deskbot-shell-workbench.py', '--port', "$Port", '--serial-python', $serialPython)
    $imagePythonPackages = Join-Path $env:LOCALAPPDATA 'DeskBot/expression-runtime/site-packages'
    $serverArgs += @('--image-python-packages', ('"' + $imagePythonPackages + '"'))
    if ($BodyNetworkProfile) {
        $resolvedProfile = (Resolve-Path -LiteralPath $BodyNetworkProfile).Path
        $serverArgs += @('--body-network-profile', ('"' + $resolvedProfile + '"'))
    }
    if ($InstalledPackage) {
        $resolvedPackage = (Resolve-Path -LiteralPath $InstalledPackage).Path
        $serverArgs += @('--installed-package', ('"' + $resolvedPackage + '"'))
    }
    # Preview owns separate computer-side role state; the current USB service keeps its connection.
    if ($Preview) { $serverArgs += @('--state-path', "tmp/shell-workbench-server/roles-preview-$Port.json") }
    $process = Start-Process -FilePath $python -ArgumentList $serverArgs -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "stdout-$Port.log") -RedirectStandardError (Join-Path $logs "stderr-$Port.log") -PassThru
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 300
        if ($process.HasExited) { throw "Workbench startup stopped. Read $logs/stderr-$Port.log." }
        try {
            $state = Invoke-RestMethod "$url/api/shells/state" -TimeoutSec 1
            if ($state.schema -eq 'deskbot.shell_workbench/v1') {
                $characters = Invoke-RestMethod "$url/api/characters/state" -TimeoutSec 1
                $shellCharacters = Invoke-RestMethod "$url/api/shell-characters/state" -TimeoutSec 1
                if ($characters.schema -eq 'deskbot.characters/v1' -and $shellCharacters.schema -eq 'deskbot.shell_characters/v1') { Write-Output "$url/#$View"; return }
            }
        } catch { }
    }
    throw "Open the server logs in $logs to check startup."
} finally { Pop-Location }
