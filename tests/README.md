# 离线测试

从仓库根目录执行。这些测试不连接串口、不访问硬件，也不进入 ESP32 固件镜像。
Python 3.10 以上可运行协议、能力导出、预览和格式检查；C++ 测试需要支持 C++17 的主机编译器，例如 Clang 或 GCC。

```powershell
python .\tests\base_event_protocol_test.py
python .\tests\test-expression-capabilities.py -v
python .\tests\test-expression-preview.py -v
python .\tests\test_printf_portability_guard.py -v
```

协议测试覆盖标准帧、分片输入、连续粘包、错误校验和、非法长度、磁吸滑动、配件、配对、校准、动作完成和未知值。能力导出与预览测试使用 `firmware/body` 中的资源、表情映射和动作能力，不依赖作者电脑上的私有路径。格式检查测试避免在精简 libc 配置下新增不支持的 64 位整数格式。

## C++ 事件测试

`base_event_mapper_test.cc`、`base_event_journal_test.cc`、`base_event_state_test.cc` 和 `base_event_policy_test.cc` 分别覆盖语义映射、24 条事件环、状态去重和事件策略。仓库包含必要的最小 FreeRTOS/协议桩。下面的 PowerShell 示例使用 `clang++`；若编译器不在 PATH，可将 `$compiler` 改为自己的主机编译器绝对路径。

```powershell
$compiler = "clang++"
$root = Get-Location
$board = Join-Path $root "firmware\body\main\boards\esp_vocat"
$stubs = Join-Path $root "tests\host_stubs"
$testDirectory = Join-Path $env:TEMP "deskbot-hardware-host-tests"
New-Item -ItemType Directory -Force -Path $testDirectory | Out-Null

foreach ($module in @("base_event_mapper", "base_event_journal", "base_event_state", "base_event_policy")) {
    $sources = @((Join-Path $board "$module.cc"))
    if ($module -ne "base_event_mapper") { $sources += Join-Path $board "base_event_mapper.cc" }
    $sources += Join-Path $root "tests\${module}_test.cc"
    $executable = Join-Path $testDirectory "$module.exe"
    & $compiler -std=c++17 -Wall -Wextra -Werror -I $stubs -I $board @sources -o $executable
    if ($LASTEXITCODE -ne 0) { throw "$module compile failed" }
    & $executable
    if ($LASTEXITCODE -ne 0) { throw "$module test failed" }
}
```

构建成功与离线测试通过不等于实机验收。每次固件变更仍需检查 App 大小、分区和资源边界，随后完成菜单、对话、摸头、声源跟随、底座动作和外壳识别回归，详见 [`../docs/reproducible-workflow.md`](../docs/reproducible-workflow.md)。
