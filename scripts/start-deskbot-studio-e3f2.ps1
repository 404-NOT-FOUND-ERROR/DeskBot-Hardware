param([int]$Port=8774,[string]$SerialPython='D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe',[switch]$Check)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$DeskBotOverviewEnv=& "$PSScriptRoot\preflight-deskbot-toolchain.ps1" -Mode Offline | ConvertFrom-Json
if ($Port -lt 1024 -or $Port -gt 65535) { throw '请选择 1024–65535 之间的本机端口。' }
if ($Check) { [ordered]@{edition='E3f-2';offline_python=$DeskBotOverviewEnv.offline_python.path;serial_opened=$false;device_written=$false}|ConvertTo-Json;exit 0 }
& $DeskBotOverviewEnv.offline_python.path "$PSScriptRoot\build-deskbot-studio-e3f2.py" --local --check
if ($LASTEXITCODE -ne 0) { throw '请先生成 E3f-2 本机页面。' }
& $DeskBotOverviewEnv.offline_python.path "$PSScriptRoot\serve-deskbot-studio-e3f2.py" --port $Port --serial-python $SerialPython
exit $LASTEXITCODE
