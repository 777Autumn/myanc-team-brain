# 发布清单：待单独确认，当前未部署

本清单用于审批一个明确的发布版本。代码、模板和离线测试完成不代表已取得飞书权限，也不代表三人真实使用验收已完成。CI 只做检查，不安装生产文件、不更新权限、不重启服务。

## 发布前基线

- 记录知识库 PR 的最终 commit、CI、审查结果和构建包 SHA-256；在发布记录中填写 gateway 对应 commit。
- 原维护任务已在 [PR #4](https://github.com/77AutumN/cc-connect-anc/pull/4) 合入线上六环境与 CRM open，主分支为 `7895bac`。知识库补丁已移到该基线，保留原严格路由和现有 CRM 功能。
- 实际安装前仍需核对部署清单、二进制及策略文件哈希与所选 GitHub commit 的对应关系。知识库补丁只叠加知识功能，不提交或覆盖原任务保留的本地改动。
- 重新核对六个真实身份元组、运行账户、沙箱与网络边界。原来的账户、凭据、CRM 账本均不由本任务改写。
- 原有未提交改动保持原位；独立集成目录只用于审查补丁。真实配置和材料不进入 GitHub。

## 待批准的飞书能力

使用应用身份的 tenant token，资源限制为一个明确团队 Wiki 空间与根节点之下的 Docx 原生节点。代码拒绝快捷方式和跨空间目标。

| 原生调用 | 用途 | 需审核的权限与资源条件 |
| --- | --- | --- |
| 已登记页面逐页读取并匹配关键词 | 团队文档检索 | 复用下列 Wiki/Docx 读取权限，不新增搜索 scope 或个人 OAuth |
| `GET /wiki/v2/spaces/get_node`、`GET /wiki/v2/spaces/:space_id/nodes` | 解析节点与创建后核对 | `wiki:node:read`（或既有 `wiki:wiki:readonly`）；目标空间可读 |
| `GET /docx/v1/documents/:id`、`/raw_content`、`/blocks` | 正文及版本读取 | `docx:document:readonly`；目标文档可读 |
| `POST /wiki/v2/spaces/:space_id/nodes` | 创建正式页面 | `wiki:node:create`；固定父节点可编辑 |
| `POST /docx/v1/documents/:id/blocks/:parent/children`、`PATCH /blocks/:block` | 追加或局部修改正文 | `docx:document:write_only`（或既有 `docx:document`）；目标文档可编辑 |

飞书后台展示的具体可选 scope 以届时应用的接口权限页为准，先列出现有与缺失项给用户核对。本轮未查询或改变真实应用授权。不要使用会附带授予 full_access 的 CLI 创建封装。没有删除、权限管理、跨空间移动或全员聊天读取能力需求。

Wiki 节点读取/创建 scope 与 Docx token 的 `obj_type=docx` 参数已用本机官方 CLI 的只读 schema 核对。Docx 写入的最小 scope 已通过官方 Markdown 文档核对。当前配置仍未绑定真实空间，后台权限勾选不等于目标资源可读写；具体应用的既有/缺失权限由独立接入记录保存，不将真实标识提交到仓库。

接口依据：[Wiki 搜索要求用户身份](https://open.feishu.cn/document/server-docs/docs/wiki-v2/search_wiki.md)、[获取知识空间节点](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node)、[创建节点](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/create)、[创建块](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/create.md)、[更新块](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/patch.md)。之前的统一搜索契约未获核实，不能据替身测试判定它支持应用身份。Docx revision 不是严格比较并交换锁；client_token 不被当作无限期幂等保证。

## 待安装文件与隔离

| 文件或目录 | 预期作用与模式 |
| --- | --- |
| `/opt/team-brain/releases/<commit>/` | 固定版本代码和独立 Python 3.11+ venv；代码由宿主管理，Agent 不可写 |
| `/opt/team-brain/current` | 发布版本选择；包装器路径的祖先必须可信，配置和状态不可通过不可信链接访问 |
| `/var/lib/team-brain/config.json` | 由网关宿主账户持有、0600；固定应用、空间、根、六元组；不用 CRM 配置或账本 |
| `/var/lib/team-brain/state/` | 宿主账户持有、0700；独立 SQLite、WAL、发布锁；不挂载进 Agent |
| 宿主专用 `team-brain-host` 包装器 | 绝对路径启动固定解释器与配置；不可由 Agent 修改 |
| 六环境中的 `team-brain-tool` 与 skill | 仅模型侧工具及说明，无私钥、真实 host 配置或批准入口 |

`ops/host.example.json` 和包装器均为占位示例，不可直接用于生产。宿主配置文件及祖先目录不可被其他用户写入；模型进程必须使用与宿主不同的、无提权能力的账户。验证每个 Agent 不能读状态/配置、不能运行宿主包装器取得权限、不能访问租户凭据。POSIX 模式检查不能替代 ACL、容器挂载及 sudo 策略核验。

网关新增两个宿主环境变量：`CC_TEAM_BRAIN_COMMAND`（受保护的绝对包装器路径），`CC_TEAM_BRAIN_PROJECTS`（恰好六个现有项目名）。启动初期读取并移除，不传给 Agent。共享现有回环工具监听器；无第二公网服务。现有 CRM host 按原逻辑保留，知识 host 通过独立命令和卡片 kind 路由。

## 上线顺序与验证

1. 用户审核八篇草稿，逐篇标记保留、修改或暂缓；个人内容逐篇选择并确认团队版本。未经确认的草稿不登记到正式语料。
2. 用户确认上述权限差额、安装文件、目标 Wiki 范围、维护窗口和回退步骤后，才进行相应操作。
3. 先在独立测试空间/节点及隔离配置中验证真实 API 返回形状、搜索可见性、正文读写、卡片签名回调与六身份映射。需要的测试资源也包含在授权范围内。
4. 对齐线上基线，备份待替换代码和配置哈希；停机前检查两类服务的执行中操作、待审批卡片和新消息。确认维护窗口后由原运维流程切换网关。
5. 逐身份执行知识读取、来源引用、提交草稿、本人确认、跨成员拒绝、重复确认；CRM 原有查询、跟进审批和 open 链接回归；检查仍是六个隔离会话。
6. 三个人分别实际完成一次“会前准备 → 会后沉淀 → 确认发布 → 再次查询”，以已审阅材料回放，记录页面链接、回执、未确认事项和纠错结果。
7. 记录实际版本、安装文件哈希、Wiki 范围、验证结果和已知限制。全部通过后才宣布 v1 上线。

## 运维与中断恢复

管理员在受保护宿主环境运行 `python -m team_brain.host --config <protected-config> --admin inspect --id <proposal>`，查看 proposal、目标、步骤和回执。输出包含业务资料，只保存在受保护位置。

已批准的外部页面通过 `--admin register --id <wiki-node> --subject <customer-or-team> --reviewed` 加入正式检索。注册前核对团队版本、来源与隐私；不做目录扫描或自动导入。Docx 链接只有对应已登记的 Wiki 节点才可读，空间外输入应由用户提供选定的文字片段。

人工编辑前 `--admin pause --id <node>`；人工审阅后 `--admin resume --id <node> --reviewed`。检测到正文变化时也会自动暂停写入。评论变更可能导致保守的版本冲突，应重读后重新提案。

unknown/executing 状态不自动重试、不清除不确定写入的锁。`--admin reconcile --id <proposal>` 只读远端，精确核对已写内容及未触及段落，成功后补回执；与活跃发布互斥。若账本证明尚未进入任何远端写入步骤，且已经取得发布互斥锁，可恢复原提案为待确认（已过期则结束），再次写入仍需卡片确认。创建请求已发出但节点 ID 未落账、部分写入或不能证明完整结果时，需要管理员调查；首版没有“强制成功”“强制清锁”入口。可据记录检查固定根节点的创建结果，暂留原提案和锁，不重复建页。人工恢复如需远端编辑或账本修复，应单独制定并确认具体修复动作。

数据库备份使用 SQLite backup API 或服务静默后的完整一致性副本，不仅复制主文件而遗漏 WAL。未知操作的记录和锁必须一起保留。

## 回退

停止知识发布入口，保留完整账本与未决操作证据；在已批准维护流程中恢复此前网关代码和配置。关闭知识 host 配置即可停止新知识工具，CRM 使用原有版本。**不回滚 CRM 业务数据，不恢复旧知识账本覆盖新回执，不删除已经验证的 Wiki 页面。** 已产生的页面由管理员按回执核对，必要的正文修正另行确认。
