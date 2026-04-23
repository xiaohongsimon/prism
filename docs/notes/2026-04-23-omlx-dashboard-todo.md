# omlx-manager Dashboard TODO — downstream of prism LLM tagging migration

Prism 侧 2026-04-23 完成 `task` / `scope` StrEnum 迁移（`prism/pipeline/llm_tasks.py`
+ `call_llm` wrapper + AST 回归测试）。新的 prism 调用会向 omlx-sdk 发送：

- `project=<Task.value>`（英文 slug：`translate` / `structurize` / `summarize` / …）
- `tags={"task", "scope", "source_key"}`

omlx-manager dashboard（`~/work/omlx-manager/src/omlx_manager/`）仍有两个
遗留问题，不在 prism 仓内修，记在这里供下次集中处理。

## 1. 仪表盘中文化（i18n）

**现状**：`src/omlx_manager/static/dashboard.html` 直接渲染原始 `project` 值。
新调用进来是英文 slug，老行是中文 + `unassigned`，混排难读。

**动作**（约 30 行改动）：
1. 在 `dashboard.html` 加一个 JS 常量表 `TASK_DISPLAY_ZH`，抄自
   `prism/pipeline/llm_tasks.py::DISPLAY_NAMES_ZH`。
2. 渲染 `/v1/stats/by-project` 结果时做 `TASK_DISPLAY_ZH[row.project] ?? row.project`。
3. `unassigned` 改显示为"未标注（老数据）"。

**不做的反方向**：让 prism wrapper 直接传中文 `project`——会把每个 caller 的
字典拷贝风险转嫁到下游，还会让 SQL group-by 对 CJK 敏感。保持 wrapper 出英文
slug，展示层翻译，这是契约。

## 2. 260 条 `unassigned` 历史数据回填

**来源**：迁移前的 prism 调用（没有 `project=`）+ 可能混入的 Claude Code 流量
（走同一 port，没有 caller 区分）。

**评估**：**不建议回填**。
- omlx-manager `token_usage.project` 行没有 prism `job_runs.id` 链接字段
- 时间戳匹配能对上部分行，但 Claude Code 流量会被误归成 prism task
- 回填后的统计精度低于"老数据"桶的隔离展示

**替代**：dashboard i18n 改动里直接把 `unassigned` 标成"未标注（老数据）"，
新数据从此点开始干净。预计 ~30 天后老桶占比 < 5%，自然淡出。

## 3. 跨 caller 契约（后续）

SPEC 辩论或 tech-stack v8 应讨论：omlx-manager 是否应该把 `project` 升级成
`(caller, task)` 二元组，这样 prism/claude-code/wechat-insight 之间的
`translate` 不会互相污染维度。当前没有紧迫性，先记下。
