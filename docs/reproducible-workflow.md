# 可复现增量开发 / Verification Workflow

## 一轮只跨一个风险边界

1. 固定上游 commit、源码/配置哈希、目标芯片、基线与预期行为；记录禁止修改的文件/分区。
2. 先做离线协议、错误输入、事件去重、过期/乱序、资源边界与内存上限测试。
3. 独立目录构建，检查 App 和资源容量、分区表、模型及所有未修改成员；保持原始备份。
4. 用户独立授权后才识别明确端口的芯片/设备。COM 号不是永久身份，S3 与 C61 分开。
5. 仅写获准范围，不全片擦除，不覆盖 NVS；App 与 assets 不能混为同一授权。
6. 全范围回读比较摘要和字节，核对未改 App/分区表；记录启动日志。
7. 用户实机验收显示、对话中/后切菜单、摸头、睡眠/唤醒、DOA/动作及本轮触发。
8. 失败停止叠加；保留证据，恢复同范围已知良好备份。只有用户回归通过才标 accepted。

Build success, deployment integrity and physical acceptance are separate claims. A 25-second clean boot does not prove UI stability. Git publication does not authorize a device write.

## 公开发布

使用独立 clone、正常提交与 push，保留远程历史。只发布源码、必要构建输入、协议、脱敏样例、工具和可视化。排除完整 Flash、NVS、Wi-Fi/API 凭据、MAC 运行日志、工具链缓存与本机绝对路径。依赖锁中的本地组件改成仓库相对路径，并公开记录这一包装差异。

```powershell
python -m pip install -r tests/requirements-body-facts-validation.txt
python scripts/check-publication.py
python scripts/run-offline-checks.py
git diff --check
```

CI 的主机测试范围由 `scripts/run-offline-checks.py` 明确列出，不以“全量测试通过”概括未执行的 ESP-IDF、浏览器或设备测试。依赖本机私密证据的历史集成脚本保留研究价值，但不在干净 clone 上宣称可直接复现。
