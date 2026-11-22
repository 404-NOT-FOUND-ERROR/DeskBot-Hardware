[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$idfPython = 'D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $idfPython -PathType Leaf)) {
    throw "Missing ESP-IDF Python interpreter: $idfPython"
}

$version = & $idfPython --version 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $version.Trim() -notmatch '^Python 3\.11\.') {
    throw "Unexpected capture Python: $version"
}
$serial = & $idfPython -c 'import serial; print(serial.__version__)' 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $serial.Trim() -ne '3.5') {
    throw "Unexpected pyserial version: $serial"
}

[ordered]@{
    schema = 'deskbot.magnetic.capture_preflight/v1'
    repository = $repoRoot
    idf_python = [ordered]@{
        path = $idfPython
        version = $version.Trim()
        serial = $serial.Trim()
    }
    serial_opened = $false
    device_written = $false
} | ConvertTo-Json -Depth 4
