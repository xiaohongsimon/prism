# Tech Stack — Prism

> Constitution / Tech Stack 层。本文件只写**契约**（系统必须 / 不能 / 调用方式是什么），不写现状清单。
> Mission 见 `mission.md`；阶段化路线见 `roadmap.md`；当前实际在跑的东西（表、adapter、CLI、路由）见 `../SPEC.md`；运行时拓扑见 `../RUNTIME.md`。

**状态**：v7（锁定候选）。v7 升级点：§4.2 LLM 调用契约**闭包化**——`task` / `scope` 改为 `prism/pipeline/llm_tasks.py` 定义的 StrEnum 闭集（Task 11 项 + Scope 5 项），`prism/pipeline/llm.py` 的 `call_llm` / `call_llm_json` wrapper 必须拒绝 `isinstance(task, Task) is False` 的调用（运行时 TypeError），`tests/test_llm_tagging.py` AST 扫全仓强制每个调用点都带 `task=`；wrapper 同时向 `omlx-sdk` `tags` dict 合并 `{"task", "scope", "source_key"}`，token-tracker 据此聚合耗费。v6 内容保留。
>
> v6（2026-04-23）：应用 Codex CLI 评审修复：§2.1 新增 `prism/privacy/outbound.py::send()` 运行时出域闸（消除 §9 TW3 仅依赖 grep 静态扫的单点漏洞）；§3.2 扩展 `SourceCandidate` schema 至 `(id, source_key, source_type, evidence_json, similarity, status, gate_status, generator, created_at)` 并强制落 `source_candidates_draft` 表；§5.2 加 `quality-scan` 静态检查（web 层不得 import 非 Identity ranker、不得读 `ranking_shadow`）+ `promote` 命令必须验证 mission §5 五要素契约与 NN6 gate artifact 已就位；§6 将"候选源 / 推荐理由 / 相似度分数进入候选池或 UI"明确纳入"上线"并归 Mid gate，自动写 `sources.yaml` 归 High gate；§7.2 新增外部平台身份命名规范（`external_author_id` / `platform_account_id`，禁用 `user_id`）；**新增 §11 数据生命周期与备份契约**回应 Codex 盲点（SQLite 膨胀 / WAL / 备份恢复 / 模型产物存储宪章化）。

---

## 1. 技术栈锁定（契约）

以下技术选择**写进宪章**——改动需回到本节辩论：

- **语言 / 框架**：Python 3.12 + FastAPI + Jinja2 + HTMX + vanilla CSS
- **前端无构建工具**：不引入 node / webpack / npm；模板层直出 HTML + HTMX 即是整个前端
- **数据库**：SQLite 单文件（`data/prism.sqlite3`），schema 由 `prism/db.py::init_db()` **单入口**统一定义
- **本地 LLM**：通过 `omlx-sdk` 调用 omlx-manager，不走 raw HTTP
- **云 LLM**：Anthropic 走 `localhost:8100` token tracker proxy（独立计量通路）
- **调度**：macOS launchd（不引入 celery / airflow / cron）
- **部署**：Mac Studio 本机，单机 7×24，不做多机 / 不上云

**不接受的替代**：Postgres / Redis / Node 前端 / Docker / Kubernetes / 云托管 DB——这些偏离 NN2（本地算力上限）或 NN7（拒绝多用户 / 多租户）。

## 2. 数据 / 持久化契约

- **Schema 单入口**：所有表定义在 `prism/db.py::init_db()`；CLI 或任意代码不得 inline `CREATE TABLE`
- **SQLite 之外的状态都是脏点**：文件系统状态（如抓取中间产物）可用，但业务状态必须落 DB
- **降频不删除**（NN4）：源和历史内容永不物理删除，只能下调优先级 / 拉长抓取时间窗口（见 §3），或设 `enabled=false`；`cleanup` 操作仅针对明确过期的中间态（日志、队列完成态），不得触及源目录或内容表
- **`sources.yaml` 是源目录的唯一 source of truth**，DB 的 `sources` 表只存运行时状态（失败计数、last_sync_at 等），**不存配置**；两者冲突时以 YAML 为准
- **审计表必须存在且不可被业务路径跳过**：
  - `job_runs`：所有 pipeline 跑的流水
  - `decision_log`：所有自动决策（调源权重、优先级调整、算力上限告警处置等）必须落此表
- **偏好数据不入 git**（NN3）：`preference_weights` / 画像 snapshot / 学到的排序向量 / **可反推个人偏好的模型产物**（embedding 字典、特征模板、微调权重、prompt 模板中的偏好片段）仅存 `data/` 或 `models/`；两目录 `.gitignore` 强制排除；禁止以 fixture / seed / 示例数据形式把任一类偏好产物提交进 repo
- **外部出域白名单**（NN3 + TW3 落地）：任何把 DB 或 `data/` 内容发往 prism 外部的通路（错误上报 / 云日志 / 公开 dashboard / 第三方 embedding API 等）必须登记在下方 §2.1 出域清单并显式 opt-in；默认**禁止**带 `prism/personalize/*` 或 `preference_*` 前缀的字段出域

### 2.1 外部出域清单

本节是所有"prism → 外部"数据通路的唯一登记处。新增通路 = 先登记再上线。

| 通路 | 方向 | 允许字段 | 禁止字段 | 开关 |
|---|---|---|---|---|
| Anthropic API（云 LLM） | 出 | 对话 messages、系统 prompt | 任何 `personalize/*` / `preference_*` 字段 | `.env` `ANTHROPIC_API_KEY` |
| （将来）错误上报 / 云日志 | 出 | 堆栈、异常类型 | 请求体、DB 行内容、偏好字段 | 默认关，启用需改本表 |
| （将来）公开 dashboard | 出 | 聚合计数（按源维度）| 个体内容、偏好字段 | 默认关 |

**运行时出域闸**：所有外部调用**必须**经过 `prism/privacy/outbound.py::send(channel, payload)` 统一入口；该函数：

1. 根据 `channel` 查本表登记项；未登记 channel → raise + `decision_log(action='outbound_denied')`
2. 按"禁止字段"列表对 `payload` 做结构化过滤，命中 `personalize/*` / `preference_*` 前缀即阻断
3. 成功出域写 `outbound_log(channel, bytes_out, ts, hash)` 审计表

直接调 `httpx` / `requests` / 云 SDK 绕过此函数 = 违约。`quality-scan` 静态扫仍是第二道防线（检 import / grep 字段名），运行时闸是第一道。未在表中登记的出域通路视为违约，TW3 必须同时命中静态扫与运行时闸。

## 3. 信号源契约

### 3.1 Adapter + 配置

- 所有 adapter 实现 `prism/sources/base.py::SourceAdapter` Protocol
- 新增源类型时必须提供 adapter；不允许在 pipeline 里 inline 处理单一源的特例逻辑
- `sources.yaml` CRUD 必须经过 `prism/sources/yaml_editor.py`；不允许其他地方直接写 YAML
- 源的**活跃度**由"优先级 + 抓取时间窗口"表达（高优先级 → 高频 / 短窗口），不存在显式"active / cold / 淘汰"状态；优先级或时间窗口的自动变更必须落 `decision_log`
- **降频不删除**（NN4）：reject / 失效 / 长期低胜率的源只下调优先级、拉长时间窗口、或 `enabled=false`，**不从 `sources.yaml` 物理删除**

### 3.2 系统化选入机制契约（mission §5 "就位" 五要素的技术落地）

mission §5 定义"就位" = 五要素闭环。tech-stack 层对每个要素的接口契约：

| mission §5 要素 | tech-stack 契约 |
|---|---|
| **候选源生成** | 至少一个 `prism/sources/candidates/` 下的 generator（LLM-from-profile / follow-graph-expand / external-ref-backtrack 任一），输出 `SourceCandidate` 结构写入 `source_candidates_draft` 表 |
| **质量理由** | `SourceCandidate.evidence_json` 必填，机器可读（JSON），包含内容采样 + 与已有源的偏好相似度或关注图谱路径 |
| **作者 approve/reject** | CLI `prism source candidate approve/reject <id>` 或 Web UI 按钮；**不得**以 git commit 或口头视为 approve；approve 才 promote 到 `sources.yaml` |
| **`decision_log` 记录** | 每次 approve/reject 写一条 `decision_log(layer='source_selection', action='approve'\|'reject', context_json=SourceCandidate)` |
| **降频不删除** | reject 的候选源 `status='rejected'` 保留在 `source_candidates_draft`，不物理删除；重新评估时可再次生成同 `source_key` 的新候选 |

**`SourceCandidate` schema（契约）**：

```python
@dataclass
class SourceCandidate:
    id: str                  # UUID，候选池主键
    source_key: str          # 未来 sources.yaml 的 key，如 "x:some_author"
    source_type: str         # adapter 类型，如 "x" / "hn" / "arxiv"
    evidence_json: dict      # 质量理由（采样 + 相似度 / 图谱路径），机器可读
    similarity: float        # 与既有偏好画像的量化相似度 [0, 1]
    status: str              # "pending" / "approved" / "rejected"
    gate_status: str         # "draft" / "shadow" / "live"（对齐 §6 gate matrix）
    generator: str           # 生成器名（"llm_from_profile" / "follow_graph" / ...）
    created_at: datetime
```

字段缺任一 = schema 违约；`id` 必须存在才能走 approve/reject CLI（否则无法定位）。候选源**必须**先落 `source_candidates_draft` 表，approve 后才写入 `sources.yaml`（draft state 模型，见 §6.2）。

五要素全部以契约落地 = `mission §5 "就位"`；任一缺失 = 未就位，mission §8 推荐引擎 shadow-only 约束持续生效。

## 4. Pipeline 契约

### 4.1 阶段分离

采集（sync）→ 聚合（cluster）→ 分析（analyze）→ 交付（briefing/publish）必须是独立 CLI 阶段；不允许合并成单一"全跑"命令。每阶段幂等（重跑不产生重复数据）。

### 4.2 LLM 调用契约

**所有本地推理必须走 `omlx-sdk`**，不得 raw HTTP 打 `:8002/:8003`。**所有 prism 内部调用必须经 `prism/pipeline/llm.py` 的 `call_llm` / `call_llm_json` wrapper**，不得在业务代码里直接 `import omlx_sdk`——wrapper 是唯一打标入口。

调用契约：

```python
from prism.pipeline.llm import call_llm_json
from prism.pipeline.llm_tasks import Task, Scope

result = call_llm_json(
    prompt,
    system=SYSTEM_PROMPT,
    intent="reasoning",            # 可选，默认按任务挑
    task=Task.STRUCTURIZE,         # 必填，StrEnum 闭集
    scope=Scope.ITEM,              # 必填，StrEnum 闭集
    source_key="x:karpathy",       # 可选，出处定位
    max_tokens=4096,
)
```

**Wrapper 职责**（`prism/pipeline/llm.py`）：
- `task` / `scope` 参数**强类型 StrEnum**；非 Enum 传入 → `TypeError`
- 向 `omlx-sdk` `chat(...)` 透传 `tags={"task": task.value, "scope": scope.value, "source_key": ...}`；`project` 字段保留传 `task.value` 以保持 token-tracker 仪表盘兼容
- 必传 `caller="prism"`、`session_id`（字符串化的 `job_runs.id`）

**闭集枚举**（source of truth：`prism/pipeline/llm_tasks.py`）：

| 维度 | 成员 | 新增流程 |
|---|---|---|
| `Task` | `translate` / `asr` / `ocr` / `video_transcribe` / `summarize` / `polish` / `structurize` / `extract` / `classify` / `judge` / `source_probe` | 需改 `llm_tasks.py` + 更新此表 + 前端 i18n 映射 |
| `Scope` | `item` / `cluster` / `daily` / `source_profile` / `corpus` | 同上 |

显示名走 `llm_tasks.DISPLAY_NAMES_ZH`（翻译 / 语音转文字 / 图像转文字 / 视频转文字 / 摘要 / 文章加工 / 结构化 / 字段抽取 / 打标/分诊 / 质量门禁 / 源探测 / 单篇 / 簇级 / 日级 / 作者画像 / 全量）。**token-tracker 仪表盘不得直接展示英文 slug**，必须经 DISPLAY_NAMES_ZH 查表。

**Intent 选择准则**（调用方按场景自选，具体任务清单见 SPEC.md）：

| intent | 选用条件 |
|---|---|
| `fast` | 短输入 / 高吞吐 / 廉价过滤 |
| `reasoning` | 需 CoT、深读、跨 cluster 综合、结构化抽取 |
| `coding` | 代码生成场景 |
| `default` | 不挑、一次性脚本 |
| `vision` | 多模态 |

**回归保险**：`tests/test_llm_tagging.py` AST 扫 `prism/` 全仓，任何 `call_llm(...)` / `call_llm_json(...)` 缺 `task=` kwarg 即 CI 红。新增调用点若不走 wrapper（例如 slides horse race 直接拿 `OmlxClient`）必须在此处或 §4.3 列为显式例外。

**显式 `model=` 的合法例外**：多模型对比场景（如 slides horse race），本就是拿具体模型做横向比较——仍须经 wrapper，只是 `intent` 让位给 `model`。

**不走 omlx-sdk 的合法例外**：
- Anthropic 云调用 → `localhost:8100` token tracker proxy
- ASR（目前 omlx-sdk 未包） → 保持直连，待 SDK 扩展后迁移

### 4.3 消费节奏（Freshness Warden drain 模型）

翻译 / 结构化 / analyze-expand 等积压型任务**由 idle-aware drain worker 驱动**，不依赖 cron 定时切片。
约束：omlx busy（被 Claude Code 或其他 caller 占用）时让出；idle 时连续消费积压。

## 5. 偏好 / 排序隔离契约（NN3 + mission §8 落地）

### 5.1 目录与 Protocol 隔离

- 所有偏好相关代码**必须**在 `prism/personalize/` 目录下，与 core pipeline 解耦
- Core pipeline（sync / cluster / analyze / articlize / briefing）不得直接 import `personalize/`
- Personalize 层通过**显式的 ReRanker Protocol**接入 web 层；未来替换偏好模型（新推荐引擎 / embedding 重排 / 无偏好 pass-through）= 只改 `personalize/`，零 core diff
- **偏好数据与衍生模型产物不入 git**：范围与禁令见 §2（含 embedding / 特征字典 / 微调权重 / prompt 偏好片段）
- Web 层涉及"偏好结果呈现"的路由（`/feed` / `/feed/following` / `/creator/*` 等）默认**不公开**：部署公开站点时必须显式 opt-in 白名单，否则走 auth gate

### 5.2 Shadow-only 排序契约（mission §8 推荐引擎启动前置）

Mission §8 强制：`§5 "就位"` 未满足前，偏好推荐引擎只能 offline + shadow，不得影响可见排序。tech-stack 落地：

- **ReRanker Protocol 必须支持两种模式**：
  - `mode='live'`：输出进入 `/feed` 可见排序——仅在 mission §5 五要素闭环**且**推荐引擎通过 NN6 门禁后才允许激活
  - `mode='shadow'`：输出仅落 `ranking_shadow` 表（产物不出现在任何 UI 路径），与 `mode='live'` 的当前 ranker 并行运行，供离线对比评估
- **默认 mode**：`IdentityReRanker`（按时间倒排）是唯一默认 live；任何非 identity 的 ReRanker 初始注册**必须**以 `mode='shadow'` 上线
- **切 live 的唯一通道**：通过 `prism personalize promote <ranker_name>` CLI 显式切换；该命令**必须**在执行前验证以下全部前置，任一不满足即 exit non-zero：
  - mission §5 五要素全部以 `source_candidates_draft` + `decision_log(layer='source_selection')` 存在证据（近 30 天内有 approve/reject 流水）
  - 目标 ranker 已实现 §6.3 一键失败标记入口 + §6.4 `gate_config.py` 阈值声明（NN6 gate artifact）
  - `ranking_shadow` 中该 ranker 与当前 live ranker 的离线对比报告已产出且作者签字（`decision_log(action='personalize_promote_approved')`）
- **误切防护（静态检查）**：`quality-scan` 必须扫描 `prism/web/` 下源码，命中以下任一模式即阻断发布：
  - `from prism.personalize` 或 `import prism.personalize.*`（非 `PersonalizeRegistry` 的 import）
  - 直接查询 `ranking_shadow` 表
  - 硬编码 ranker 类名绕过 `PersonalizeRegistry.get_live()`
- **误切防护（运行时）**：`web/routes.py` 渲染路径调用 ReRanker 时，只认 `PersonalizeRegistry.get_live()`；shadow ranker 物理不可达 web 层
- **Shadow 对比评估**：`prism personalize eval` CLI 读 `ranking_shadow` 与 live 产物的差异，产出报告，**不**自动切换

## 6. 交付门禁契约（NN6 落地）

"上线" = **任何自动化写入用户可见路径的行为**，包括但不限于：
- LLM 产物进入 UI（翻译 `body_zh`、`tl;dr`、结构化文章、briefing 段落、推荐理由）
- Source 自动上下线 / 优先级 / 时间窗口变更影响 feed 组成
- Pipeline 自动决策导致 cluster 归并 / 主题标签 / 交叉链接等出现在 UI
- **候选源 / 推荐理由 / 相似度分数进入候选池或 UI 展示**：自动 generator 将 `SourceCandidate` 写入 `source_candidates_draft` 并暴露给作者的候选审查界面——属于自动化写入用户可见路径，至少受 Mid gate（见 §6.6）
- **自动写入 `sources.yaml`**：无论是否经候选池中转，自动修改 `sources.yaml` 的行为属于 High gate，必须留人工复核窗口 + 可回滚

### 6.1 五条硬约束

每一类"上线"动作必须满足：

1. **显式质量 gate**：产物落**主表**前经过质量检查（置信度阈值 / LLM-as-judge / 异常模式检测 / schema 校验）——形式按类型定，但必须存在
2. **不过关必须隔离**：gate 失败的产物进入待复核状态或回滚，**不得进入可见路径**；严禁 "gate 失败 fallback 到原文直出" 这种静默降级
3. **失败必落日志**：gate 失败必须写 `quality_anomalies`（异常事实）+ `decision_log`（若导致自动决策）
4. **热路径不得绕过门禁**：为响应速度 / UI 流畅度跳过 gate 属于违约；慢就慢，不能静默污染
5. **Source 级决策**（自动上下线 / 优先级变更）必须留人工复核窗口：自动决策不立即生效，而是先写 `decision_log` pending 态，留短窗口允许作者回滚

### 6.2 架构级隔离（shadow / draft 模型）

所有新 pipeline 上线前，必须实现以下两条之一：

- **Draft state**：产物先写 `*_draft` 表 / `status='draft'` 字段，gate 通过才 promote 到主路径可读；主路径查询默认过滤 draft
- **Shadow DB**：pipeline 先产出到 shadow 表，与主表并行存在，gate 或人工 approve 后再切换指针

**禁止**：产物直接 UPSERT 到主表然后"如果质量低再删"——这是静默污染模式。

### 6.3 一键失败标记入口（NN6 (a)）

每条 pipeline 的产物在 UI 上必须提供一键"标记为污染"入口（按钮 / 快捷键 / Agent CLI 命令任一）。标记后：

- 立即回撤该产物出主路径（改 `status='rejected'` 或删除）
- 写 `quality_anomalies` 含 `marked_by='author'` + `pipeline` + `product_id`
- 计入 TW4 滚动窗口统计（2 周内累计 ≥3 次或同类 ≥2 次触发 pipeline 暂停；单次污染只回撤产物，不暂停 pipeline）

**没有该入口的 pipeline 不得上线**——这是 NN6 在 tech-stack 层的硬前置。

### 6.4 连续失败降级阈值（NN6 (b)）

每条 pipeline 必须在 `prism/pipeline/<name>/gate_config.py` 或等价位置声明：

- `max_consecutive_failures`：连续 N 次 gate 失败 → 该 pipeline 自动降级为 staging 模式（产物只进 draft，不 promote）
- `recovery_condition`：恢复自动 promote 的条件（人工 approve M 次 / 修复 commit 后重置）

阈值可选激进或保守，但**必须存在且被 gate 逻辑读取**。无配置视为未上线。

### 6.5 绕过审计（运维紧急通道）

紧急情况下（如 pipeline 阻塞日常使用）允许临时绕过门禁直出，但：

- 必须写 `docs/reviews/adhoc/YYYY-MM-DD-nn6-override-<pipeline>.md`，说明产物范围、原因、回滚计划、预计恢复时间
- 同时写 `decision_log` 含 `action='nn6_override'` + 指向该文件
- 连续两次未记录的绕过（通过 diff / log 审计发现）即触发 mission §9 重评

### 6.6 风险分级门禁矩阵

| 风险等级 | 典型产物 | 最低门禁要求 |
|---|---|---|
| Low | 翻译 `body_zh` | schema 校验 + 长度 / 语言检测 + draft state |
| Mid | 结构化摘要 / `tl;dr` / 观点抽取 | LLM-as-judge 评分阈值 + 关键字段非空 + draft state + 一键标记 |
| High | 推荐排序 / source 自动上下线 / 交叉链接断言 | 多信号一致性 + 人工复核窗口 + 可回滚 + shadow 表或 pending 态 |

## 7. Web / Auth 契约

### 7.1 公开 / 鉴权边界

- 公开路由白名单在 `prism/web/routes.py::_PUBLIC_PATHS`，其他路由必须经 `_get_user()` 401 gate
- 任何改状态的操作前端后端**都要 gate**（模板 `is_anonymous` + 路由 `_get_user`），单侧 gate 不合规
- 新路由 URL 使用语义 slug（`source_key` / 自然 key），不用数字 ID
- 反馈表单用 HTML `<form>` + hidden input，不用 `hx-vals` JSON

### 7.2 NN7 多用户拒绝的物理护城河

NN7 不是道德宣言，是架构红线。tech-stack 层禁止以下结构出现，即使只是"为未来留接口"：

- **不实现用户注册 / 登录 / session 以外的身份体系**：当前单人使用通过简单 auth cookie，不引入 `users` 表、OAuth、SSO、JWT claims
- **不引入租户模型**：DB schema 不得出现 `tenant_id` / `org_id` / `workspace_id` 等多租户字段；所有表默认"单用户全局"
- **不做资源配额 / 速率隔离**：omlx 配额、算力预算、抓取频率按项目级全局管理，不按用户切片
- **不做权限系统**：没有 role / permission / ACL；"作者"是唯一身份，其他访问路径只能是"匿名只读公开白名单"
- **issue / PR 默认拒绝触发条件**：任何 PR 引入上述任一结构（grep `tenant_id`、`user_id` 非 session 用途、`@permission_required` 装饰器等）即自动违约，不需个案讨论

**外部平台身份命名规范**：外部内容平台的作者 / 账号标识**必须**使用 `external_author_id` / `platform_account_id` / `external_handle` 等明确外部语义的字段名；**禁用** `user_id` / `account_id` 等无限定前缀命名——后者会被 NN7 违约扫（`quality-scan` grep `\buser_id\b`）误判为本地用户体系。外部平台身份是"内容属性"而非"本地账号"，命名必须体现这一区分。

这些约束使得"多用户化"不再是产品决策，而是需要先推翻 tech-stack 契约——NN7 的真实防线在于此。

## 8. CLI / Agent 只读接口契约（NN5 落地）

- 存在一组**只读 CLI 命令**供 Agent（Claude Code 等）读取 feed / 订阅 / 源列表；这些命令保证不产生副作用
- 只读命令的稳定性等同于宪章——命名和输出 schema 改动需 mission 级理由
- 写操作（`source add/remove`、`sources prune` 等）不属于 Agent 接口范畴

## 9. 可观测性与熔断契约

- **使用熔断**（mission TW1）：作者连续 14 天未访问 `/feed/following` → 暂停 pipeline 调度；恢复需手动
- **算力上限熔断**（mission TW2）：推理队列积压 > 72h 必须触发告警（CLI exit non-zero 或 quality-scan 异常），不得静默降质
- **隐私外流熔断**（mission TW3）：`quality-scan` 必须检查 git diff / 外部出域日志 / Sentry payload 是否包含 `prism/personalize/*` 或 `preference_*` 字段；命中立即阻断发布并 exit non-zero
- **污染事后扳机**（mission TW4）：`quality_anomalies` 中 `marked_by='author'` 的事件按 pipeline 在 2 周滚动窗口内聚合：
  - **单次污染**：立即回撤该产物（`status='rejected'`），不暂停 pipeline
  - **累计 ≥3 次 或 同类 ≥2 次**：自动将该 pipeline 切到 staging 模式（只写 draft），恢复需人工 approve
  - **High 风险管线**（推荐排序 / source 自动上下线 / 交叉链接断言）可在 `gate_config.py` 声明更严阈值（如 ≥1 次即暂停），但不得全局收紧到低于 mission TW4 的基线
- `quality-scan` 是健康度唯一入口；其他管线不得内嵌"静默修复"逻辑绕过异常表

## 10. 调度契约

- 所有定时任务通过 macOS launchd plist 注册，plist 放 `prism/scheduling/`
- 每个定时任务入口必须写 `job_runs` 起止和 stats；不写 `job_runs` = 不合规调度
- launchd 以外的后台循环（drain worker 等）视为"长期运行服务"，由独立 plist 管理 + 健康探针

## 11. 数据生命周期与备份契约

本节回应"本地 7×24 长运行下最大失效模式不是功能 bug，而是 DB 膨胀 / 损坏导致档案库不可恢复"的宪章级风险。NN4 "降频不删除" 在没有容量和备份契约时会变成自毁前提。

### 11.1 SQLite 容量与健康

- **单文件上限软约束**：`data/prism.sqlite3` 超过 **10 GiB** 时，每日 `quality-scan` 必须 exit 警告（非阻断），触发作者决策（归档 / 切片 / 升级）；超过 **30 GiB** 时 exit non-zero 阻断 pipeline 新写入（只读模式），待人工处置
- **WAL 必须启用**：`PRAGMA journal_mode=WAL`，由 `init_db()` 统一设置；`wal` 文件大小 > **1 GiB** 视为异常（通常意味着有长事务或 checkpoint 失败），`quality-scan` 告警
- **膨胀源可追溯**：每月自动运行 `prism db size-report`（由 launchd 触发），按表输出行数 + 字节数 + 增长率，写入 `decision_log(layer='data_lifecycle')`；作者据此决定哪张表需要归档

### 11.2 备份与恢复

- **每日备份必须存在**：`data/prism.sqlite3` 每日至少一份完整备份（SQLite `.backup` 或 `VACUUM INTO`），落 `backup/YYYY-MM-DD/` 目录；launchd plist 注册，失败两日连续即 `quality-scan` 阻断 pipeline
- **备份必须异盘 / 异机**：备份目录不得与 `data/` 在同一物理卷；单盘故障即全毁 = 违约
- **至少保留 14 天滚动 + 4 周月末全量**：超出窗口可清理，但月末全量强制保留；清理必须写 `decision_log`
- **恢复演练契约**：每季度至少一次 `prism db restore-dry-run`，挂载上周备份到临时路径验证 schema + 核心表可读，结果写 `decision_log(action='restore_rehearsal')`；连续两季度未演练 = 触发 mission §9 重评（备份不演练等于无备份）
- **备份同受 NN3 约束**：备份文件含偏好数据时**禁止**出域（不上云盘 / 不入 git / 不同步到外部服务）；`§2.1 运行时出域闸`必须拦截备份目录路径

### 11.3 模型产物存储预算

- 偏好模型、embedding 字典、微调权重等落 `models/`，总大小软上限 **20 GiB**，超出 `quality-scan` 告警
- 每个模型产物必须有 `models/<name>/META.yaml`，记录：生成时间 / 数据切片范围 / 上游 job_run_id / 预期保留周期
- 无 META.yaml 的产物视为"孤儿"，`quality-scan` 告警；超过保留周期未使用自动归档至 `models/_archive/`（仍受 NN3 不出域约束）

### 11.4 失效判定

以下任一条件命中即触发 mission §9 TW（按严重度归 TW2 或独立新 trip-wire，由下次 mission 评审决定归属）：

- 备份连续 3 天失败
- 季度恢复演练连续两次缺失
- SQLite 文件损坏（`PRAGMA integrity_check` 非 `ok`）

---

*具体表清单、adapter 清单、CLI 清单、路由清单见 `../SPEC.md`——那里会标记每条的活/僵尸/冻结状态。*
