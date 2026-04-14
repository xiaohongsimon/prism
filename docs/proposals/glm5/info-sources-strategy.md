# 百炼 GLM-5 — Constraint Auditor

## Key Points

### Feasibility Tiers
- **Tier 1 (Easy, High Signal):** HN /best, GitHub org releases (20-30 orgs), Product Hunt AI
- **Tier 2 (Feasible but Degraded):** X/Twitter (fragile API), WeChat公众号 (paid proxy only)
- **Tier 3 (Technical Debt Traps):** arXiv daily firehose, Zhihu, Juejin

### Feasibility Matrix
| Source | Difficulty | Weekly Signal | TL Relevance | Verdict |
|--------|-----------|---------------|--------------|---------|
| arXiv daily | Low | Low | Low | DROP |
| GitHub trending | Low | Low | Medium | Drop |
| HN /best | Low | High | High | ADD |
| GitHub org releases | Low | Medium | High | ADD |
| X (official) | Medium | Medium | Medium | Keep (paid) |
| WeChat (paid proxy) | Low | Medium | High | Consider |
| Product Hunt | Low | Low-Med | Medium | Optional |

### Minimum Viable Set (< 8 items/day)
1. HN /best (RSS)
2. GitHub org releases (API, 5-7 orgs)
3. X (3-5 curated accounts)
4. One Chinese tech proxy (机器之心 or 量子位)
5. Product Hunt AI (weekly)
+ Weekly: arXiv top 3 papers

### Priority Framework
(1) Informs shipping decision this week?
(2) Increases visibility?
(3) Explainable to team in 5 min?
If no to all three → deprioritize.

### TL vs IC
TL optimizes for decision velocity, IC optimizes for information completeness.

### Confidence: High
### Key Risk: X API fragility
