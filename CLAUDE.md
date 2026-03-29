# Project: Prism
> AI 信号情报系统 — 从多源信息流中提取、聚类、分析、推荐 AI/ML 领域动态

## Why
- 解决 AI 从业者的信息过载：自动聚合 X/HN/arXiv/YouTube/GitHub 等信号源
- 目标：私人 AI 新闻平台，带个性化排序和反馈学习
- Non-goals：不做多用户/SaaS，单人桌面使用

## Codebase Navigation
入口 CLI: prism/cli.py
数据管道: prism/pipeline/ (sync → cluster → analyze → trends → entity_link)
信号源适配器: prism/sources/ (base.py 定义 SourceAdapter 协议)
Web Feed: prism/web/ (routes.py + ranking.py + templates/)
输出/发布: prism/output/ (briefing.py, notion.py)
数据库: prism/db.py (SQLite, 单 init_db() 含全部 schema)
配置: prism/config.py + .env + config/sources.yaml + config/entities.yaml
调度: prism/scheduling/ (launchd plists: hourly/daily/web)
API: prism/api/ (JSON API at /api/*, Web UI at /)

## Constraints & Gotchas
- **YAML 权威**: sources.yaml 是信号源配置的 source of truth，DB 只跟踪运行时状态
- **LLM via omlx-manager**: 调用本地 omlx gateway (port 8003)，模型可切换，见 .env
- **LLM 并发限制**: prism 和 Claude Code 共享 omlx，高并发会 503，analyze 应错峰运行
- **Reasoning 模型**: LLM 输出可能含 `<think>` 标签，llm.py 已处理
- **source_key 含冒号**: 如 `x:karpathy`，在 HTML id/CSS selector 中需转义或避免使用
- **Web 前端无构建工具**: 纯 Jinja2 + HTMX + vanilla CSS，不要引入 node/webpack
- **反馈用 HTML form**: HTMX 反馈按钮用 `<form>` + hidden input，不要用 hx-vals JSON
- **测试**: pytest，DB 测试用 `:memory:` SQLite，路由测试用 FastAPI TestClient

## Commands
- Dev server: `.venv/bin/prism serve --port 8080`
- Test: `.venv/bin/pytest tests/ -v`
- Sync sources: `.venv/bin/prism sync`
- Cluster: `.venv/bin/prism cluster`
- Analyze: `.venv/bin/prism analyze --incremental`
- Publish Notion: `.venv/bin/prism publish --notion`

## Current Focus
- 阶段: Alpha
- 当前: Web Feed UI 优化 + 反馈驱动的个性化排序
- Last updated: 2026-03-30

## Context Links
- Spec: docs/superpowers/specs/2026-03-29-prism-web-feed-design.md
- Plan: docs/superpowers/plans/2026-03-29-prism-web-feed.md
- Entity System Spec: docs/specs/2026-03-29-prism-v2-entity-system.md
