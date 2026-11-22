#requires -Version 7.2
[CmdletBinding(DefaultParameterSetName='Run')]
param(
    [Parameter(Mandatory, ParameterSetName='Prepare')][switch]$Prepare,
    [Parameter(Mandatory, ParameterSetName='Check')][switch]$Check,
    [ValidateRange(1, 65535)][int]$ClientPort = 4314,
    [string]$ArchiveDatabase,
    [string]$ShareTokenFile,
    [ValidateRange(1, 65535)][int]$ShareClientPort = 4311,
    [ValidatePattern('^[A-Za-z0-9_-]{1,80}$')][string]$CharacterId = 'shaping-001'
)

$ErrorActionPreference = 'Stop'
$bridgeRoot = Split-Path -Parent $PSScriptRoot
$bridgeRuntime = Join-Path $bridgeRoot 'tmp/xiaozhi-life-tool-runtime-20261119'
$bridgeScript = Join-Path $bridgeRuntime 'mcp_pipe.py'
$bridgePackages = Join-Path $bridgeRuntime 'python-packages'
$bridgeManifest = $null
$bridgeSource = Join-Path $bridgeRoot 'integrations/client-conversation/xiaozhi-life-tool.mjs'
$bridgeRevision = 'c537f71d61fd73b47d6c8955b5df6d3721acf4e4'
$bridgeSha256 = '31af96a0bd83062251bafe31c22cc01dc02a6b687c5775c5f70e3cf552372df8'
$bridgePython = (Get-Command python.exe -ErrorAction Stop).Source
$bridgeNode = (Get-Command node.exe -ErrorAction Stop).Source
if ((& $bridgeNode --version) -notmatch '^v24\.') { throw '请使用 Node 24。' }
if ($PSBoundParameters.ContainsKey('ArchiveDatabase')) {
    if ($PSBoundParameters.ContainsKey('ClientPort')) { throw '请选择一个生活记录来源。' }
    if (!(Test-Path -LiteralPath $ArchiveDatabase -PathType Leaf)) { throw '找不到客户端正式 SQLite 存档。' }
    $bridgeSourceArgs = @('--archive-database', (Resolve-Path -LiteralPath $ArchiveDatabase).ProviderPath)
} else {
    $bridgeSourceArgs = @('--client-port', "$ClientPort")
}
if ($PSBoundParameters.ContainsKey('ShareTokenFile')) {
    if (!$ArchiveDatabase -or !(Test-Path -LiteralPath $ShareTokenFile -PathType Leaf)) {
        throw '语音分享需要正式存档和本机分享凭证。'
    }
    $bridgeSourceArgs += @('--share-client-port', "$ShareClientPort", '--share-token-file', (Resolve-Path -LiteralPath $ShareTokenFile).ProviderPath)
}

function Test-BridgeSource {
    (Test-Path -LiteralPath $bridgeScript -PathType Leaf) -and
        ((Get-FileHash -Algorithm SHA256 -LiteralPath $bridgeScript).Hash -eq $bridgeSha256)
}

if ($Prepare) {
    New-Item -ItemType Directory -Path $bridgeRuntime -Force | Out-Null
    if (!(Test-BridgeSource)) {
        $bridgeDownload = Join-Path $bridgeRuntime 'mcp_pipe.download'
        $download = @{
            Uri = "https://raw.githubusercontent.com/78/mcp-calculator/$bridgeRevision/mcp_pipe.py"
            OutFile = $bridgeDownload
            TimeoutSec = 15
        }
        Invoke-WebRequest @download
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $bridgeDownload).Hash -ne $bridgeSha256) {
            throw '小智桥接脚本摘要不符。'
        }
        Move-Item -LiteralPath $bridgeDownload -Destination $bridgeScript -Force
    }
    $install = @('-m', 'pip', '--disable-pip-version-check', 'install', '--quiet', '--no-deps',
        '--no-compile', '--only-binary', ':all:', '--index-url', 'https://pypi.org/simple',
        '--target', $bridgePackages, '--upgrade', 'websockets==15.0.1', 'python-dotenv==1.2.1')
    & $bridgePython @install
    if ($LASTEXITCODE -ne 0) { throw '小智桥接依赖安装失败。' }
}

if (!(Test-BridgeSource)) { throw '先运行启动脚本的 -Prepare 准备本机桥接程序。' }
$bridgeOldPythonPath = $env:PYTHONPATH
$bridgeOldMcpConfig = $env:MCP_CONFIG
$bridgeOldDotenv = $env:PYTHON_DOTENV_DISABLED
$bridgeOldEndpoint = $env:MCP_ENDPOINT
try {
    $env:PYTHONPATH = $bridgePackages
    $env:PYTHON_DOTENV_DISABLED = '1'
    & $bridgePython -c "import dotenv, websockets; from importlib.metadata import version; assert version('websockets') == '15.0.1' and version('python-dotenv') == '1.2.1'"
    if ($LASTEXITCODE -ne 0) { throw '运行 -Prepare 恢复固定版本的桥接依赖。' }
    & $bridgeNode --check $bridgeSource
    if ($LASTEXITCODE -ne 0) { throw 'DeskBot 近况工具无法运行。' }
    if ($Prepare -or $Check) {
        '小智桥接程序、Python 依赖与 Node 近况工具已就绪。'
        return
    }

    if (!$env:MCP_ENDPOINT) {
        $env:MCP_ENDPOINT = (Read-Host -Prompt '粘贴小智控制台 MCP 接入点（隐藏输入）' -MaskInput).Trim()
    }
    $bridgeEndpoint = $null
    if (![Uri]::TryCreate($env:MCP_ENDPOINT, [UriKind]::Absolute, [ref]$bridgeEndpoint) -or
        $bridgeEndpoint.Scheme -ne 'wss' -or $bridgeEndpoint.UserInfo) {
        throw '请使用小智控制台提供的 wss 接入点。'
    }
    # The relay reloads its configuration after reconnecting. Keep each binding separate.
    $bridgeSessionId = [Guid]::NewGuid().ToString('N')
    $bridgeManifest = Join-Path $bridgeRuntime ('mcp_config-' + $bridgeSessionId + '.json')
    $bridgeStatusLog = Join-Path $bridgeRuntime ('life-archive-status-' + $bridgeSessionId + '.jsonl')
    @{
        mcpServers = @{
            'deskbot-life' = @{
                type = 'stdio'
                command = $bridgeNode
                args = @($bridgeSource) + $bridgeSourceArgs + @('--character-id', $CharacterId, '--status-log', $bridgeStatusLog)
                env = @{ MCP_ENDPOINT = '' }
            }
        }
    } | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8NoBOM -LiteralPath $bridgeManifest
    $env:MCP_CONFIG = $bridgeManifest
    "状态记录 $bridgeStatusLog"
    # Forward status only; upstream exception messages may contain the endpoint.
    & $bridgePython $bridgeScript 2>&1 | ForEach-Object {
        switch -Regex ($_.ToString()) {
            'Successfully connected to WebSocket server$' { '已连接小智云。' }
            '^DeskBotLife tools/list$' { '小智已查询近况工具。' }
            '^DeskBotLife tools/call (available|empty|unavailable)$' { "小智已调用近况工具：$($Matches[1])。" }
            '^DeskBotLife tools/share (recorded|explicit_share_required|unavailable)$' { "小智语音分享：$($Matches[1])。" }
            'Connection closed \(attempt \d+\):' { '小智连接已断开，正在重试。' }
            'Program execution error:' { '桥接程序启动失败，请运行 -Check。' }
        }
    }
    if ($LASTEXITCODE -ne 0) { throw '小智桥接程序退出。' }
} finally {
    $env:PYTHONPATH = $bridgeOldPythonPath
    $env:MCP_CONFIG = $bridgeOldMcpConfig
    $env:PYTHON_DOTENV_DISABLED = $bridgeOldDotenv
    $env:MCP_ENDPOINT = $bridgeOldEndpoint
    if ($bridgeManifest -and (Test-Path -LiteralPath $bridgeManifest)) {
        Remove-Item -LiteralPath $bridgeManifest
    }
}
