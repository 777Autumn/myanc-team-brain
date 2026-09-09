# v1 开发验证记录（2026-09-09）

知识库代码提交 `d8a19fc0746c2026753c19725679324441dab32b`，对应 [PR #1](https://github.com/77AutumN/myanc-team-brain/pull/1)。后续文档提交不代表更换已验证的运行代码。

- Python unittest 共 26 项：Linux 26 项通过；Windows 24 项通过，2 项 POSIX 宿主权限测试跳过。
- Python 3.11、3.13 × Linux、Windows 的四组合测试、compileall、wheel 构建均通过：[该提交 CI](https://github.com/77AutumN/myanc-team-brain/actions/runs/34338386140)。
- 覆盖事实引文、来源缺口、客户分离、账号/员工和 Excel/API 的混淆、私有页面和未发布内容排除、六身份复用、精确卡片、重复/过期确认、局部更新、并发、人工修改暂停、创建/写入超时、部分写入、进程重启恢复。
- 独立 Spec/Standards 静态审查已完成。发现的并发卡住、预览缺少目标、错误诊断和配置测试问题均已修正并补用例。

网关接入 [PR #5](https://github.com/77AutumN/cc-connect-anc/pull/5) 单独管理。Windows web 构建、完整 Go build、两类 adapter、全部 CUJ 与 Action/Hosted 定向测试通过。Windows 全仓测试有已有 POSIX 命令/符号链接/路径/权限和平台专属符号问题，未声称本地全仓测试通过。Linux 完整检查以 PR 最终提交的 CI 为准。

另有独立兼容副本：以原维护任务 [PR #4](https://github.com/77AutumN/cc-connect-anc/pull/4) 的 `7205aa7c196b8af01da897c815536aa6c309499a` 为基线，合成知识库接入至 `8333bb6`。保留原六域路由、共享监听器和 CRM open；构建、CRM/knowledge adapter 与全部 CUJ/Action/Hosted/ConversationOnly 定向检查通过，冲突解决经独立静态复审。未修改原维护分支或把它的改动归入知识库提交。

以上均使用虚构数据与 API 边界替身。没有对真实飞书租户进行集成测试，没有进行真实模型端到端评分，没有三人实际使用验收。真实文档结构、权限可见性、卡片展示、引用质量与实际会议复用须按 [发布清单](release-checklist.md) 回放；测试通过不替代这些步骤。
