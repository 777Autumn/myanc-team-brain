# 人工批准的首次安装

`ops/install.py` 是 root 运维入口，不接入模型、CI 或定时任务。它只用于首次安装；已存在知识状态或已开始的安装不能重跑。Python 3.11+，仅标准库。线上动作仍须取得用户对具体版本、文件和维护窗口的批准。

## 冻结材料

由维护者在私有发布目录准备 `plan.json`、该 commit 的 `install.py` 和 `payload/`。目录必须 root 所有、0700，祖先不可被其他用户写入或含符号链接/额外 POSIX ACL。另记录整个归档 SHA-256；审批必须同时绑定归档和计划。操作参数中的哈希是防误用校验，不是用户审批的替代品。

计划含 `release_directory`（`/opt/team-brain/releases/<40位commit>`）、`old_pid`、`old_gateway_sha256`、`preserve`（现有文件绝对路径到哈希）、两个 `crm_ledgers` 路径、`host_config`、八个 `seeds`、`files`。每个文件项包含绝对 `target`、包内相对 `source`、`sha256`、八进制 `mode`，宿主专用包装器另标 `host_group=true`。配置不能包含 `app_secret`；停服安装时从原受保护网关配置在服务器内存中提取，写入新的宿主 0600 配置，不输出或复制到开发机。

保留基线须覆盖：网关 TOML、现有 unit/drop-in、全部 CRM Python 模块与三份配置、六份 CLAUDE.md，以及实际 guard/sandbox 文件。新文件只含知识源码和 bootstrap、两个固定路径包装器、一个新增 systemd drop-in、六组 skill 和工具协议；现有网关二进制是唯一替换文件。宿主源码和状态路径不接受 Agent UID；每个 Agent UID 只在自己的 home 祖先范围内允许。

`seeds` 每项含 `node_token`、`title`、`raw_sha256`、`subject=team`。只使用已审阅发布回执，测试页不登记。配置的六个身份元组必须逐项匹配当前网关，并继续使用不同于宿主的六个 Agent 账户。包装器以 `/usr/bin/python3 -I -B` 固定导入版本源码，排除工作目录、PYTHONPATH 和用户 site-packages。

## 执行顺序

以下命令中的路径和哈希必须替换为已审阅包的实际值，不能直接复制占位符执行。

1. `python3 -I -B /root/<bundle>/install.py check /root/<bundle>`：只读核对基线、进程、目录、CRM 账本、消息完成及至少两分钟静默。
2. 获得具体部署批准后，运行 `prepare /root/<bundle> --approved-plan-sha256 <sha>`，保存旧二进制和原模式、UID、GID，落盘回执并再次检查；这一步不停止服务。
3. 在已批准维护窗口运行 `systemctl stop cc-connect.service`。随后运行 `install /root/<bundle> --approved-plan-sha256 <sha>`；安装器再次核对消息是否在备份与停止之间到达、有无残留 receiver/监听，才创建知识状态、安装文件、读回八篇并事务登记。
4. 安装通过后运行 `start /root/<bundle> --approved-plan-sha256 <sha>`，检查六引擎、单连接、单接收进程和回环监听。此时状态仅为 `started_pending_acceptance`。
5. 逐环境验证实际沙箱中的工具调用和不能读取宿主秘密，再做真实消息/确认卡片、CRM 回归与三人验收。安装器的 runuser 读权限检查不等于原生沙箱或真人验收。

步骤 2–4 与回退都使用同一个 `python3 -I -B /root/<bundle>/install.py` 入口。所有变更命令持有 root 私有部署锁；未知异常只输出脱敏错误，不打印配置或子进程原始输出。

## 停止条件与回退

旧网关的同步命令可能没有 `turn complete`。安装器默认阻止所有未配对的消息。仅当维护者逐条核实确为已完成同步命令时，才在重新审阅的冻结计划中添加 `completed_without_turn`：消息 ID、精确 `received_at` 微秒时间、人工核对依据 `evidence`。已出现处理/排队绑定的消息不能例外。静默时长、CRM 账本检查及停服后的消息竞态检查仍执行；绝不按“足够旧”批量忽略未完成事件。日志缺失或无法判断则暂停维护并补证据。

`prepare` 后有新消息时不安装。若服务已停，先核对消息和账本，再单独决定恢复原服务；不要绕过检查继续发布。安装从第一笔变更前就记录 `installing`；任何中断只允许调查或回退，不能自动重跑、重新建页或覆盖状态。

启动健康检查失败时保留当前服务状态并记录 `startup_failed_needs_inspection`，避免直接杀掉刚接到的业务。操作员先检查所有启动尝试后的 receiver 日志、真实在途消息和两类账本，再决定安全停止；不能仅凭健康超时强停。回退会检查整个启动时间窗内所有 PID 的日志；日志缺失、同步消息无完成依据或业务仍未完结时，需要人工调查，不能直接恢复文件。

确认业务可停止并停服后，运行 `rollback /root/<bundle> --approved-plan-sha256 <sha>`。安装器复核旧备份和新增 drop-in 未被别人改动，恢复旧网关及原属主/模式，仅移除本次新增 drop-in，保留服务停止。再核对原服务配置和业务状态，按维护批准手动启动旧服务。

回退始终保留知识源码、配置、整个 SQLite/WAL/锁/回执及 Wiki 页面，包括 unknown 或部分初始化的账本；不调用远端写入、不恢复旧数据库、不清理锁。旧网关没有知识入口。后续重新启用需要基于保留状态制定新的发布方案，不能重跑首次安装器。
