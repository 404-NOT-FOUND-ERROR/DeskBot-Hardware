[CmdletBinding()]
param(
    [string]$ProjectRoot,
    [string]$BuildDirectory,
    [string]$IdfPath = $env:IDF_PATH
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot '..\firmware\body'
}
if ([string]::IsNullOrWhiteSpace($BuildDirectory)) {
    $BuildDirectory = Join-Path $PSScriptRoot '..\firmware\body\build\body'
}
$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$build = [System.IO.Path]::GetFullPath($BuildDirectory)
$toolRoot = if ($env:IDF_TOOLS_PATH) { Join-Path $env:IDF_TOOLS_PATH 'tools' } else { $null }
if ($toolRoot) {
    $toolBins = @()
    foreach ($toolName in @('cmake', 'ninja', 'xtensa-esp-elf', 'esp32ulp-elf')) {
        $installed = Join-Path $toolRoot $toolName
        if (Test-Path -LiteralPath $installed) {
            $toolBins += Get-ChildItem -LiteralPath $installed -Directory -Recurse |
                Where-Object { $_.Name -eq 'bin' -or (Test-Path -LiteralPath (Join-Path $_.FullName 'ninja.exe')) } |
                Select-Object -ExpandProperty FullName
        }
    }
    if ($toolBins) {
        $env:PATH = (($toolBins + @($env:PATH)) -join [IO.Path]::PathSeparator)
    }
}
$defaults = @(
    (Join-Path $project 'sdkconfig.defaults'),
    (Join-Path $project 'sdkconfig.defaults.esp32s3'),
    (Join-Path $project 'sdkconfig.defaults.esp-vocat-baseline'),
    (Join-Path $project 'sdkconfig.defaults.r9')
) | Where-Object { Test-Path -LiteralPath $_ }

if (-not $defaults) {
    throw "No sdkconfig defaults found under $project"
}

$idfCommand = Get-Command idf.py -ErrorAction SilentlyContinue
if ($idfCommand) {
    $idfExecutable = $idfCommand
    $idfPrefix = @()
} else {
    if ([string]::IsNullOrWhiteSpace($IdfPath)) {
        throw 'idf.py was not found. Initialize ESP-IDF first or pass -IdfPath.'
    }
    $idfScript = Join-Path $IdfPath 'tools\idf.py'
    if (-not (Test-Path -LiteralPath $idfScript)) {
        throw "ESP-IDF tools/idf.py not found under $IdfPath"
    }
    $pythonCandidate = if ($env:IDF_PYTHON_ENV_PATH) {
        Join-Path $env:IDF_PYTHON_ENV_PATH 'Scripts\python.exe'
    } else {
        (Get-Command python -ErrorAction SilentlyContinue).Source
    }
    $idfExecutable = $pythonCandidate
    if (-not $idfExecutable) {
        throw 'python was not found for the ESP-IDF fallback invocation.'
    }
    if (-not (Test-Path -LiteralPath $idfExecutable)) {
        throw "ESP-IDF Python was not found: $idfExecutable. Initialize an ESP-IDF shell or set IDF_PYTHON_ENV_PATH."
    }
    $idfPrefix = @($idfScript)
}

$env:IDF_TARGET = 'esp32s3'
$defaultsValue = $defaults -join ';'
New-Item -ItemType Directory -Force -Path $build | Out-Null
$sdkconfig = Join-Path $build 'sdkconfig'
$idfArgs = @(
    '-B', $build,
    '-D', 'IDF_TARGET=esp32s3',
    '-D', 'BOARD_NAME=esp_vocat',
    '-D', 'BOARD_TYPE=esp_vocat',
    '-D', "SDKCONFIG=$sdkconfig",
    '-D', "SDKCONFIG_DEFAULTS=$defaultsValue"
)

Push-Location $project
try {
    & $idfExecutable @idfPrefix @idfArgs reconfigure
    if ($LASTEXITCODE -ne 0) { throw "idf.py reconfigure failed with exit code $LASTEXITCODE" }
    & $idfExecutable @idfPrefix @idfArgs build
    if ($LASTEXITCODE -ne 0) { throw "idf.py build failed with exit code $LASTEXITCODE" }
    Write-Host "Build complete: $build"
    Write-Host 'No flash or erase command is performed by this script.'
} finally {
    Pop-Location
}
