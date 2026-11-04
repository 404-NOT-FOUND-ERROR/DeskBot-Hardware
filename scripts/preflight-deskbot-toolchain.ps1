[CmdletBinding()]
param(
    [ValidateSet('Offline', 'Build', 'Flash')]
    [string]$Mode = 'Build',
    [string]$Port
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$idfPython = 'D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe'
$idfPath = 'D:\Espressif\frameworks\esp-idf-v5.5.5'
$offlinePython = 'D:\Program Files (x86)\Python\Python 3.13.0\python.exe'
$cmake = 'D:\Espressif\tools\cmake\3.30.2\bin\cmake.exe'
$ninja = 'D:\Espressif\tools\ninja\1.12.1\ninja.exe'
$gcc = 'D:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20260121\xtensa-esp-elf\bin\xtensa-esp32s3-elf-gcc.exe'
$nm = 'D:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20260121\xtensa-esp-elf\bin\xtensa-esp32s3-elf-nm.exe'
$donor = Join-Path $repoRoot 'tmp\e139-octal-20261030\p'

function Require-File([string]$Path, [string]$Purpose) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing ${Purpose}: $Path"
    }
}

function Run-Text([string]$FilePath, [string[]]$Arguments) {
    $output = & $FilePath @Arguments 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')`n$output"
    }
    return $output.Trim()
}

$result = [ordered]@{
    schema = 'deskbot.toolchain_preflight/v1'
    mode = $Mode
    repository = $repoRoot
    idf_python = [ordered]@{ path = $null; version = $null; esptool = $null; serial = $null; yaml = $null }
    idf = [ordered]@{ path = $null; version = $null }
    build_tools = [ordered]@{ cmake = $null; ninja = $null; gcc = $null; nm = $null }
    offline_python = $null
    donor = [ordered]@{ path = $null; lock_present = $false; managed_components_present = $false }
    serial_opened = $false
    device_written = $false
}

if ($Mode -eq 'Offline') {
    Require-File $offlinePython 'system Python 3.13 interpreter'
    $offlineVersion = Run-Text $offlinePython @('--version')
    if ($offlineVersion -notmatch '^Python 3\.13\.') { throw "Unexpected offline Python: $offlineVersion" }
    $schema = Run-Text $offlinePython @('-c', 'import importlib.metadata; print(importlib.metadata.version(''jsonschema''))')
    if ($schema -notmatch '^4\.23(?:\.|$)') { throw "Unexpected jsonschema version: $schema" }
    $result.offline_python = [ordered]@{ path = $offlinePython; version = $offlineVersion; jsonschema = $schema }
} else {
    Require-File $idfPython 'ESP-IDF Python interpreter'
    $idfPythonVersion = Run-Text $idfPython @('--version')
    if ($idfPythonVersion -notmatch '^Python 3\.11\.') { throw "Unexpected IDF Python: $idfPythonVersion" }
    $result.idf_python.path = $idfPython
    $result.idf_python.version = $idfPythonVersion
    $esptoolVersion = Run-Text $idfPython @('-m', 'esptool', 'version')
    if ($esptoolVersion -notmatch '4\.12\.0') { throw "Unexpected esptool version: $esptoolVersion" }
    $result.idf_python.esptool = $esptoolVersion
    $serialVersion = Run-Text $idfPython @('-c', 'import serial; print(serial.__version__)')
    $result.idf_python.serial = "serial=$serialVersion"
}

if ($Mode -eq 'Build') {
    if (-not (Test-Path -LiteralPath $idfPath -PathType Container)) {
        throw "Missing ESP-IDF root: $idfPath"
    }
    Require-File (Join-Path $idfPath 'tools\idf.py') 'ESP-IDF idf.py'
    Require-File $cmake 'pinned CMake'
    Require-File $ninja 'pinned Ninja'
    Require-File $gcc 'ESP32-S3 compiler'
    Require-File $nm 'ESP32-S3 symbol audit tool'
    $env:IDF_PATH = $idfPath
    $env:IDF_TOOLS_PATH = 'D:\Espressif'
    $env:IDF_PYTHON_ENV_PATH = Split-Path -Parent (Split-Path -Parent $idfPython)
    $env:IDF_COMPONENT_CHECK_NEW_VERSION = '0'
    $idfVersion = Run-Text $idfPython @((Join-Path $idfPath 'tools\idf.py'), '--version')
    if ($idfVersion -notmatch 'ESP-IDF v5\.5\.5') { throw "Unexpected IDF version: $idfVersion" }
    $result.idf.path = $idfPath
    $result.idf.version = ($idfVersion -split "`r?`n" | Select-Object -Last 1)
    $yamlVersion = Run-Text $idfPython @('-c', 'import yaml; print(yaml.__version__)')
    $result.idf_python.yaml = "yaml=$yamlVersion"
    $cmakeOutput = Run-Text $cmake @('--version')
    $cmakeVersion = ($cmakeOutput -split "`r?`n")[0]
    if ($cmakeVersion -notmatch '3\.30\.2') { throw "Unexpected CMake version: $cmakeVersion" }
    $result.build_tools.cmake = $cmakeVersion
    $ninjaVersion = Run-Text $ninja @('--version')
    if ($ninjaVersion -ne '1.12.1') { throw "Unexpected Ninja version: $ninjaVersion" }
    $result.build_tools.ninja = $ninjaVersion
    $gccOutput = Run-Text $gcc @('--version')
    $gccVersion = ($gccOutput -split "`r?`n")[0]
    if ($gccVersion -notmatch '14\.2\.0') { throw "Unexpected ESP32-S3 compiler: $gccVersion" }
    $result.build_tools.gcc = $gccVersion
    $nmOutput = Run-Text $nm @('--version')
    $nmVersion = ($nmOutput -split "`r?`n")[0]
    if ($nmVersion -notmatch '^GNU nm \(crosstool-NG esp-14\.2\.0_20260121\) 2\.43\.1$') { throw "Unexpected ESP32-S3 nm: $nmVersion" }
    $result.build_tools.nm = [ordered]@{ path = $nm; version = $nmVersion }
    Require-File (Join-Path $repoRoot 'firmware\body\dependencies.lock') 'locked ESP-IDF dependencies'
    Require-File (Join-Path $donor 'dependencies.lock') 'locked component donor'
    if (-not (Test-Path -LiteralPath $donor -PathType Container)) { throw "Missing locked component donor: $donor" }
    $managedComponents = Join-Path $donor 'managed_components'
    if (-not (Test-Path -LiteralPath $managedComponents -PathType Container)) {
        throw "Missing locked managed components in donor: $managedComponents"
    }
    $result.donor.path = $donor
    $result.donor.lock_present = $true
    $result.donor.managed_components_present = $true
}

if ($Mode -eq 'Flash') {
    if ($Port -notmatch '^COM[0-9]+$') { throw 'Flash mode requires an explicit port such as COM4.' }
    $ports = [System.IO.Ports.SerialPort]::GetPortNames()
    if ($ports -notcontains $Port.ToUpperInvariant()) { throw "Explicit port is not currently enumerated: $Port" }
}

$result | ConvertTo-Json -Depth 8
