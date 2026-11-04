param([int]$Port=8766,[string]$SerialPython='D:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe',[switch]$Check)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$DeskBotEnv=& "$PSScriptRoot\preflight-deskbot-toolchain.ps1" -Mode Offline | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $SerialPython -PathType Leaf)) { throw '请指定包含 pyserial 的 Python 3.11 路径。' }
$DeskBotSerialVersion=& $SerialPython -c "import sys,serial; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor)+' serial='+serial.__version__)"
if ($LASTEXITCODE -ne 0 -or $DeskBotSerialVersion -ne '3.11 serial=3.5') { throw '串口环境应为 Python 3.11、pyserial 3.5。' }
if ($Port -lt 1024 -or $Port -gt 65535) { throw '请选择 1024–65535 之间的本机端口。' }
if ($Check) { [ordered]@{edition='E3b';offline_python=$DeskBotEnv.offline_python.path;serial_python=$SerialPython;serial_opened=$false;device_written=$false}|ConvertTo-Json;exit 0 }
& $DeskBotEnv.offline_python.path "$PSScriptRoot\build-deskbot-studio-e3b.py" --local --check
if ($LASTEXITCODE -ne 0) { throw '请先生成 E3b 本机页面。' }
& $DeskBotEnv.offline_python.path "$PSScriptRoot\serve-deskbot-studio-e3b.py" --port $Port --serial-python $SerialPython
exit $LASTEXITCODE
