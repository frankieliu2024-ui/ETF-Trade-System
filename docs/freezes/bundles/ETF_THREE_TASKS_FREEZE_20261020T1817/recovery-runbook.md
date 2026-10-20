# ETF三类任务冻结包恢复与漂移核对合同

本文件只用于封存审计、差异检测和人工恢复，不是业务规则源，不得替代ETF_SYSTEM_INDEX.md或所属正式协议。

## 固定核对顺序

1. 读取当前main的ETF_SYSTEM_INDEX.md。
2. 读取CURRENT指针和本冻结包MANIFEST.json。
3. 校验冻结包对应的main提交、文件完整性和指纹算法。
4. 按task_id核对automation_id、conversation_id、Prompt原文指纹和Schedule指纹。
5. 按code-map核对协议、脚本、workflow、测试与当前main版本。
6. 按运行身份和content_hash绑定FINAL_CONTENT、completion、event、REPORT、notification和provider result。
7. 任一层不一致时标记DRIFT_DETECTED_PENDING_CONFIRMATION，停止自动恢复。

## 恢复边界

恢复只能提供人工核对依据；不得自动改写平台任务、不得复用历史运行身份、不得重跑历史请求、不得创建第二正文、第二completion、第二event或旁路通知。

## 当前限制

平台界面未提供稳定的Prompt/Schedule原生哈希。冻结包中的指纹若存在，只代表云浏览器实际回读内容的仓库计算指纹。
