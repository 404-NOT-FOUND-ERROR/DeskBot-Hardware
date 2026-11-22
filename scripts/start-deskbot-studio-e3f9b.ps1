param([int]$Port=8779,[string]$SerialPython='D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe',[switch]$Check)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$DeskBotRolePackageEnv=& "$PSScriptRoot\preflight-deskbot-toolchain.ps1" -Mode Offline | ConvertFrom-Json
if ($Port -lt 1024 -or $Port -gt 65535) { throw '请选择 1024–65535 之间的本机端口。' }
if (-not (Test-Path -LiteralPath $SerialPython -PathType Leaf)) { throw '找不到 IDF Python 串口运行时。' }
if ($Check) { [ordered]@{edition='E3f-9b';offline_python=$DeskBotRolePackageEnv.offline_python.path;serial_python=$SerialPython;serial_opened=$false;device_written=$false}|ConvertTo-Json;exit 0 }
& $DeskBotRolePackageEnv.offline_python.path "$PSScriptRoot\build-deskbot-studio-e3f9b.py" --local --check
if ($LASTEXITCODE -ne 0) { throw '请先生成 E3f-9b 本机页面。' }
& $DeskBotRolePackageEnv.offline_python.path "$PSScriptRoot\serve-deskbot-studio-e3f9b.py" --port $Port --serial-python $SerialPython
exit $LASTEXITCODE
