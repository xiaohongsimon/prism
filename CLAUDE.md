# Project: Prism
> AI 驱动的个人推荐系统 — 通过 pairwise comparison 学习偏好，动态优化召回与排序

## Why
- 解决 AI 从业者的信息过载：不是给你一堆新闻自己读，而是 AI 读完后每次只呈现两条，你选更感兴趣的那个
- 目标：自演化推荐引擎 — 从用户的选择和文字反馈中持续学习，动态调整"去哪找信息"和"怎么排序"
- Non-goals：不做多用户/SaaS，单人桌面使用；不做内容生产，只做信号筛选和推荐

## Core Interaction
用户在 Web UI 上每次看到 **两条信号**（pairwise），可以：
1. 选一个更喜欢的（1-bit 偏好信号）
2. 附带文字说明选择逻辑（高价值偏好信号）
3. 两个都行 / 两个都不行（也是有效信号）
4. 跳过（pair 质量差的元信号 — 犹豫超 10 秒也视为此类）
可选批量模式：展示 4-6 条，用户拖拽排序，一次产生 C(n,2) 个偏好信号。

**外部投喂信号**（最强正反馈）：
用户随时可以投喂外部链接或话题（从其他渠道看到的感兴趣内容），系统将：
- 提取 topic/source/author 等维度标签
- 作为强正反馈更新偏好模型（权重 > pairwise 选择）
- 驱动召回层拓展相关源（如：投喂了某 X 用户的帖子 → 自动关注该用户）

## Architecture: 两层闭环 + Decision Log

> 经 6 模型辩论（2026-04-01），三层简化为两层，Meta 层并入排序层后台任务。

### 1. 召回层 (Recall) — "去哪找"
- 信号源: X/HN/arXiv/YouTube/GitHub Trending/GitHub Releases 等
- **动态召回（三阶段递进）**:
  - Phase 1（当前）: 现有源 + 源权重动态调整。每个源根据其产出信号在 pairwise 中的胜率调整采集频率，纯规则，零 LLM 开销
  - Phase 2: LLM 分析偏好 profile 生成候选源列表，用户确认后加入
  - Phase 3: 全自动试运行新源，胜率高于阈值则永久纳入
- 外部投喂的链接 → 自动提取源信息，建议用户添加对应源
- 当前实现: config/sources.yaml 静态配置 + prism/sources/ 适配器

### 2. 排序层 (Ranking) — "怎么排 + 怎么变好"
- **偏好模型: Bradley-Terry + 多维权重向量**
  - 每条信号维护 Elo score，pairwise 结果用标准公式更新（<50 行 Python）
  - 辅助维度: topic/source/author 权重向量，从 pairwise 结果同步更新
  - 冷启动: LLM embedding 相似度初始化新信号 score
- **反馈信号权重**: 外部投喂(3.0) > save(2.0) > pairwise 选择(1.0) > 都行(0.3) > 都不行(-0.5)
- **文字反馈**: LLM 离线提取偏好标签 → 更新权重向量（不在渲染路径调 LLM）
- **Pair 选择策略**: 70% 高分+不确定（active learning）/ 20% 双新（纯探索）/ 10% 随机
  - 连续 3 次"都不感兴趣" → 切换全随机打破局部最优
  - 探索内容标注推荐理由
- **后台策略优化**: 每日定时任务汇总反馈，更新源权重，标记低效源

### Decision Log（从第一天就建）
- `decision_log(id, timestamp, layer, action, reason, context_json)`
- 所有自动决策（调源权重、修改探索比例、纳入新源）必须经过此表
- 支持回溯和调试

## Runtime State (当前在跑什么)
**→ 看 `docs/RUNTIME.md`** — 活/死功能清单、launchd 拓扑、已知坑、排查手册。排查问题从这里开始。改架构必须同步更新。

## Codebase Navigation
入口 CLI: prism/cli.py
数据管道: prism/pipeline/ (sync → cluster → analyze → trends)
信号源适配器: prism/sources/ (base.py 定义 SourceAdapter 协议)
Web 交互: prism/web/ (routes.py + ranking.py + slides.py + auth.py + templates/)
输出/发布: prism/output/ (briefing.py, notion.py)
数据库: prism/db.py (SQLite, 单 init_db() 含全部 schema)
配置: prism/config.py + .env + config/sources.yaml
调度: prism/scheduling/ (launchd plists: hourly/daily/web)
Entity 系统: prism/pipeline/entity_*.py (已暂停，待重新规划)

## Constraints & Gotchas
- **YAML 权威**: sources.yaml 是信号源配置的 source of truth，DB 只跟踪运行时状态
- **LLM via omlx**: 调用本地 omlx 后端 (port 8002)，gateway (8003) 不稳定时直连后端，模型可切换，见 .env
- **LLM 并发限制**: prism 和 Claude Code 共享 omlx，高并发会 503，analyze 应错峰运行
- **Reasoning 模型**: LLM 输出可能含 `<think>` 标签，llm.py 已处理
- **source_key 含冒号**: 如 `x:karpathy`，在 HTML id/CSS selector 中需转义或避免使用
- **Web 前端无构建工具**: 纯 Jinja2 + HTMX + vanilla CSS，不要引入 node/webpack
- **反馈用 HTML form**: HTMX 反馈按钮用 `<form>` + hidden input，不要用 hx-vals JSON
- **Slides 系统**: prism/web/slides.py 用多模型 horse race + judge 选择生成信号卡片
- **测试**: pytest，DB 测试用 `:memory:` SQLite，路由测试用 FastAPI TestClient

## Commands
- Dev server: `.venv/bin/prism serve --port 8080`
- Test: `.venv/bin/pytest tests/ -v`
- Sync sources: `.venv/bin/prism sync`
- Cluster: `.venv/bin/prism cluster`
- Analyze: `.venv/bin/prism analyze --incremental`
- Publish Notion: `.venv/bin/prism publish --notion`

## Current Focus
- 阶段: Alpha → 重新定位
- v1 MVP 核心验证: 用户是否愿意持续做 pairwise 选择
- v1 scope: Pairwise UI + Bradley-Terry 评分 + 源权重动态调整 + Decision Log
- 不做: LLM 源发现、Meta 层独立优化
- Last updated: 2026-04-01

## Context Links
- Original Design: docs/specs/2026-03-24-prism-design.md
- Web Feed Spec: docs/superpowers/specs/2026-03-29-prism-web-feed-design.md
- Entity System Spec: docs/specs/2026-03-29-prism-v2-entity-system.md (已暂停)
- HN Hotness Spec: docs/superpowers/specs/2026-04-01-hn-hotness-weighting.md
- 架构辩论记录: docs/reviews/synthesis/2026-04-01-prism-v2-debate.md
