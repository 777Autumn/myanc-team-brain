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

`subject` 是客户范围，`query` 是搜索词，两者分别处理。用户明确给出客户标识时，原样保留，包括标点和大小写；标题或客户简称不能自动视为已确认标识。标识不明时向用户核对，通用方法仍可按 `team` 查询。不要把客户标识翻译成英文、缩写或换成 `team` 来重试客户记录。

搜索使用飞书原生服务，`query` 为 1–30 字符且不能全为空白。每次检查一页（最多五条）原生结果，保留原生顺序，过滤为已登记、同客户或通用、固定根下的页面。返回 `next_page_token` 非空时，用相同 `query`、`subject` 和该 `page_token` 继续；本页结果被全部过滤也可能仍有下一页。模型不能传入空间或其他筛选器。搜索只核验节点并返回标题/链接，不读取正文、不产生来源已读回执；引用和提案前必须调用 `knowledge_read`。没有自建关键词检索或本地索引。

客户记录检索未命中时，保持已确认的 `subject` 不变，仅逐次缩短 `query`：完整名称之后，最多再试两个有辨识度的短关键词或名称片段。含标点的完整名称可能空结果，而片段可以命中；先处理当前查询的分页，再换关键词。示例：虚构标识 `演示·青松B` 始终作为 `subject`，`query` 可从 `演示·青松B` 改为 `青松B`。结果属于通用方法页时，不能算作找到了客户记录；命中客户页后阅读正文再回答。

用户已给出页面链接且客户标识已确认时，可直接 `knowledge_read`，仍由宿主验证范围。完成上述检索仍未命中，说明试过的范围、关键词及缺口，索取准确标识或链接；不猜测其他客户标识继续搜索。搜索未命中不证明没有资料，索引延迟仅是可能原因。`unsupported_blocks` 表示图片、表格等未完整解析的内容，回答应注明缺口。首版正文解析不包含 OCR 或附件。

读取可用 `reference` 替代 `node_token`，支持配置租户下的 `/wiki/...` 或 `/docx/...` 链接；Docx 必须能解析为已经登记的团队 Wiki 节点。模型无法借此读取未选定的个人或空间外文档。

确认预览包含新建/追加/段落替换类型、标题、目的页或父节点链接、审核时旧正文及差异。预览超过 24 KB 时拒绝提交，改为选定段落的小提案；不截断后让成员盲目确认。

宿主 stdin 使用 `operation`（tool/bind/claim/execute）和可信 `principal`；这份入口仅存在于宿主隔离账户。模型工具只接受三种 knowledge 命令。`bind` 固定实际卡片 message_id；`claim` 验证五字段身份与该卡片，只有一次调用取得执行权。

发布状态：pending → claimed → executing → verified；取消/修改/过期结束原提案；conflict 需重读重提；unknown 保持锁，由管理员只读 reconcile。没有自动重试或自动释放不确定锁。另一个页面正在发布时，尚未开始任何写入的提案退回 pending，保留原卡片供再次确认；崩溃发生在持久化写入意图之前时，管理员可核对后恢复为待确认状态。
