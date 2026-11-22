[CmdletBinding()]
param([int]$Port = 8779, [switch]$Preview)
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
try {
    $state = Invoke-RestMethod "$url/api/shells/state" -TimeoutSec 2
    if ($state.schema -eq 'deskbot.shell_workbench/v1') { Write-Output "$url/#shells"; return }
} catch { }
Push-Location $root
try {
    & $python scripts/build-deskbot-shell-workbench.py
    if ($LASTEXITCODE -ne 0) { throw 'Workbench generation did not complete.' }
    $logs = Join-Path $root 'tmp/shell-workbench-server'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $serverArgs = @('-u', 'scripts/serve-deskbot-shell-workbench.py', '--port', "$Port", '--serial-python', $serialPython)
    # Preview owns separate computer-side role state; the current USB service keeps its connection.
    if ($Preview) { $serverArgs += @('--state-path', "tmp/shell-workbench-server/roles-preview-$Port.json") }
    $process = Start-Process -FilePath $python -ArgumentList $serverArgs -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "stdout-$Port.log") -RedirectStandardError (Join-Path $logs "stderr-$Port.log") -PassThru
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 300
        if ($process.HasExited) { throw "Workbench startup stopped. Read $logs/stderr-$Port.log." }
        try {
            $state = Invoke-RestMethod "$url/api/shells/state" -TimeoutSec 1
            if ($state.schema -eq 'deskbot.shell_workbench/v1') { Write-Output "$url/#shells"; return }
        } catch { }
    }
    throw "Open the server logs in $logs to check startup."
} finally { Pop-Location }
