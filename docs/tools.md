# 工具协议与使用方式

模型环境安装无凭据的 `team-brain-tool`。输入使用 stdin JSON，不把材料放在命令行。网关固定回环地址 `127.0.0.1:18743/tool`，注入临时 `TEAM_BRAIN_SESSION_TOKEN`；模型不提供 principal、空间或发布动作。

```sh
team-brain-tool knowledge_search <<'JSON'
{"query":"客户背景", "subject":"synthetic-customer-a"}
JSON
team-brain-tool knowledge_read <<'JSON'
{"node_token":"node0", "subject":"synthetic-customer-a"}
JSON
team-brain-tool knowledge_propose <<'JSON'
{"title":"虚构客户访谈", "subject":"synthetic-customer-a", "statements":[{"text":"系统现阶段只能导出 Excel。", "evidence":"partner_report", "quote":"系统现阶段只能导出 Excel。", "source":{"submitted_text":"系统现阶段只能导出 Excel。", "label":"虚构访谈逐字稿"}}]}
JSON
```

来源二选一：`source: {node_token}` 引用本会话刚读过、内容未变的正式页面；`source: {submitted_text, label}` 提供用户选择提交的文字或现成逐字稿。发表的是引文及来源说明；整份输入不自动归档。事实必须与引文一致，假设可以解释引文。来源类别由提交者核对，代码只能验证引文匹配，不能证明发言者身份或事实真伪。

`evidence`：`partner_report`、`hypothesis`、`customer_confirmed`、`verified_outcome`。最后两类必须有直接确认或验证材料，不可从伙伴转述升级。

更新使用 `target` 指定正式节点，标题保持一致；默认追加。`block_id` 只替换一个纯文本段落；较长更新拆成独立提案。`related` 可给最多五个同客户或通用节点。Wiki 的父子目录自动形成索引，关联链接随已确认正文写入；首版无独立索引数据库或额外自动摘要。

每个页面在账本登记唯一 subject；客户页面只可在对应 subject 下读写，`team` 通用页可供其他主题引用但不得借客户更新改写。客户标识由三人约定并核对，系统不自动合并疑似重复客户。

搜索只遍历账本中已登记、属于当前客户或 `team` 的页面，通过飞书正文读取接口取得当前文字，在标题和正文中匹配关键词（忽略大小写，多个词用空格分隔且需全部命中）。每次最多检查五页；`next_after` 非空时，以相同 query/subject 和 `after: <next_after>` 继续，直到为空。`skipped_pages` 表示写入中或已移出范围而跳过的页面，需说明覆盖缺口。检索发现不构成已阅读来源，引用前仍须 `knowledge_read`。

这是小规模、无索引的字面检索，不是语义检索；无匹配不证明没有资料，可换同义的短关键词再查。`unsupported_blocks` 表示图片、表格等未完整解析的内容，回答应注明缺口。首版正文解析不包含 OCR 或附件。真实环境仍须核对分页检索耗时是否适合现有工具超时。

读取可用 `reference` 替代 `node_token`，支持配置租户下的 `/wiki/...` 或 `/docx/...` 链接；Docx 必须能解析为已经登记的团队 Wiki 节点。模型无法借此读取未选定的个人或空间外文档。

确认预览包含新建/追加/段落替换类型、标题、目的页或父节点链接、审核时旧正文及差异。预览超过 24 KB 时拒绝提交，改为选定段落的小提案；不截断后让成员盲目确认。

宿主 stdin 使用 `operation`（tool/bind/claim/execute）和可信 `principal`；这份入口仅存在于宿主隔离账户。模型工具只接受三种 knowledge 命令。`bind` 固定实际卡片 message_id；`claim` 验证五字段身份与该卡片，只有一次调用取得执行权。

发布状态：pending → claimed → executing → verified；取消/修改/过期结束原提案；conflict 需重读重提；unknown 保持锁，由管理员只读 reconcile。没有自动重试或自动释放不确定锁。另一个页面正在发布时，尚未开始任何写入的提案退回 pending，保留原卡片供再次确认；崩溃发生在持久化写入意图之前时，管理员可核对后恢复为待确认状态。
