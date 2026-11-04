param(
    [int]$Port = 8766,
    [string]$SerialPython = 'D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe',
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
$DeskBotRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $DeskBotRoot
$DeskBotEnv = & "$PSScriptRoot\preflight-deskbot-toolchain.ps1" -Mode Offline | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $SerialPython -PathType Leaf)) { throw '请指定包含 pyserial 的 Python 3.11 路径。' }
$DeskBotSerialVersion = & $SerialPython -c "import sys,serial; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor) + ' serial=' + serial.__version__)"
if ($LASTEXITCODE -ne 0 -or $DeskBotSerialVersion -ne '3.11 serial=3.5') { throw '串口环境应为 Python 3.11、pyserial 3.5。' }
if ($Port -lt 1024 -or $Port -gt 65535) { throw '请选择 1024–65535 之间的本机端口。' }
$DeskBotPython = $DeskBotEnv.offline_python.path
if ($Check) {
    [ordered]@{ edition='E3a'; offline_python=$DeskBotPython; serial_python=$SerialPython;
        serial=$DeskBotSerialVersion; serial_opened=$false; device_written=$false } | ConvertTo-Json
    exit 0
}
& $DeskBotPython "$PSScriptRoot\build-deskbot-studio-e3a.py" --local --check
if ($LASTEXITCODE -ne 0) { throw '请先生成 E3a 本机页面。' }
& $DeskBotPython "$PSScriptRoot\serve-deskbot-studio-e3a.py" --port $Port --serial-python $SerialPython
exit $LASTEXITCODE
