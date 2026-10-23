# 测试范围 / Test Scope

从仓库根目录运行，Python 3.11+：

```powershell
python -m pip install -r tests/requirements-body-facts-validation.txt
python scripts/check-publication.py
python scripts/run-offline-checks.py
```

可移植测试明确列在 `scripts/run-offline-checks.py`：UART 帧重组、语义与去重、磁场识别/遥测、身体事实状态机/schema、EAF 编解码/布局、角色 profile 和原有表达预览工具。它们不连接串口或设备，不证明实机 UI/DOA/触摸行为。外部录制不在仓库时，相应测试明确 skip，不伪称完整覆盖。

## 历史集成套件

`test_role_binding_consumer.py`、`test_shell_binding_workflow_e1_6.py`、`test_client_transport_contract_e1_7.py`、`test_client_record_receiver_e1_8.py` 保留原有研究测试。它们会校验原 E1.4c 发布锁，需要固定提交的上游 Git 对象、候选 EAF 和锁中所有原始输入；这些未全部在公开仓库提供，因此**不在干净 clone 的默认测试范围内**。不为使测试变绿而删除哈希验证或把缺失输入改成通过。

旧 R9 C++ 事件测试已移至 `legacy-r9/`，仅为历史参考；当前主机 C++ 磁场测试为 `o2d_magnetic_core_test.cc`，需支持 C++17 的编译器。浏览器 `.cjs` 脚本是既有 QA 入口，需要另行准备 Playwright 与浏览器，不会由默认测试安装或访问用户浏览会话。

The public CI runs the portable scope and current magnetic C++ core. It does not run ESP-IDF, flash hardware, certify a new firmware binary or complete pending E1.13 acceptance.
