# Decision Report: Following Tab Spec Debate

- **Preset**: debate
- **Stakes**: medium
- **Date**: 2026-04-11

## Model Dispatch

| # | Model | Lab | Role | Status | Adopted |
|---|-------|-----|------|--------|---------|
| 1 | Claude Opus 4.6 | Anthropic (Zenmux) | Proposer A | ✓ | full |
| 2 | Grok 4 | xAI | Red Team | ✓ | full |
| 3 | Gemini 3.1 Pro | Google | Proposer B | ✓ | partial |
| 4 | MiMo V2 Pro | Xiaomi | Feasibility Analyst | ✓ | full |
| 5 | Kimi K2.5 | Moonshot | User Advocate | ✓ | partial |
| 6 | MiniMax M2.7 | MiniMax | Scope Guardian | ✓ | full |
| 7 | GLM-5 | Zhipu | — | ✗ (百炼不通) | — |
| 8 | Qwen-3.5-Plus | Alibaba | — | ✗ (百炼不通) | — |

## Synthesis

- **Models contributed**: 6/8
- **Consensus points**: 5 (迁移风险、JSON 不可靠、分段过于模糊、三层导航摩擦、字幕不保障)
- **Divergence points**: 4 (批量 vs 按需、三层 vs 双栏、articles 表 vs signals 扩展、YAML vs DB creators)

### Per-Model Unique Contributions

- **Opus2**: 字幕语言处理遗漏、updated_at 支持重新生成、Markdown XSS 安全
- **Grok**: YAML 手动维护可持续性、X 与 YouTube 体验不一致、并发死锁风险
- **Gemini**: Master-detail 桌面双栏方案、DB creators 表解耦、按需生成架构
- **MiMo**: 迁移回滚困难、pipeline 监控告警、highlights 简化为纯文本
- **Kimi**: 阅读时长预估、"下一篇"导航、Reeder/微信公众号 UX 参考、未读状态
- **MiniMax**: 3 天最小 MVP 路径、YAGNI 砍自动重试、并发控制是伪需求

## Opus Decision: REVISED

Spec 已根据辩论结果修订，采纳 7 项改进，拒绝 4 项提议（均有明确理由）。核心架构（三层导航 + 批量 articlize + YAML 权威）保持不变。

主要修订：迁移安全门禁、MVP 简化（不分段、不自动重试）、创作者卡片内容预览、JSON fallback 解析。
