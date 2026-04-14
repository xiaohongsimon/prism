# Prism v2 架构辩论记录

> 2026-04-01 | 6 模型辩论 (debate preset, high stakes)
> 议题: Pairwise 偏好学习推荐系统的架构设计

## 参与模型

| 模型 | 角色 | 核心立场 |
|------|------|----------|
| Claude Opus (Zenmux) | Proposer A | 全力支持 pairwise + Bradley-Terry，三阶段递进召回 |
| Grok 4 | Red Team | 最悲观：可能沦为"学术实验"，用户疲劳致命 |
| Gemini 3.1 Pro | Proposer B | Tinder 单卡滑动替代，LLM 仅离线标签 |
| MiMo V2 Pro | Feasibility | 谨慎乐观，多维权重向量 + 护栏 |
| Kimi K2.5 | User Advocate | 用户时间预算硬约束，建议混合交互 |
| MiniMax M2.7 | Scope Guardian | 最 YAGNI：v1 只做排序层 |

## 共识点（6/6）

1. **Pairwise 有价值但不能是唯一交互** — 需要逃逸出口
2. **动态召回 v1 用规则，不用 LLM 发现** — 源权重基于胜率调整
3. **偏好模型从简** — 加权评分 / Bradley-Terry，不上复杂 ML
4. **Meta 层 v1 过度设计** — 合并到排序层后台任务
5. **冷启动是最大风险** — 需要 100-200 次比较才收敛

## 关键分歧

| 问题 | Opus | Gemini | Kimi |
|------|------|--------|------|
| 交互模型 | 纯 pairwise + 逃逸口 | Tinder 单卡滑动 | 列表 + 可选 pairwise |
| 探索比例 | 70/20/10 三档 | 硬性 80/20 | 硬上限 20% 用户可调 |
| 架构层数 | 三层递进 | 双重闭环 | 两层 |

## 各模型独特贡献

- **Opus**: Decision Log 从第一天建，pair 质量决定产品存亡
- **Grok**: 连续 3 次"都不感兴趣" → 全随机打破局部最优
- **Gemini**: LLM 移出关键渲染路径，排序纯 SQL
- **MiMo**: Meta 层需护栏，LLM 只生成"建议"需规则校验
- **Kimi**: 批量 pairwise（拖拽排 4-6 条），探索内容标注推荐理由
- **MiniMax**: v1 核心验证 = "用户愿意持续做 pairwise 选择"

## Opus 最终决策

1. **交互**: Pairwise 为主 + 快速跳过 + 可选批量模式
2. **偏好模型**: Bradley-Terry (<50行) + 多维权重向量
3. **动态召回**: 三阶段递进（规则→LLM建议+人工确认→全自动）
4. **架构**: 两层 + Decision Log（Meta 并入排序层后台任务）
5. **v1 MVP**: Pairwise UI + BT评分 + 源权重调整 + Decision Log

## 用户补充决策（辩论后）

- 新增"外部投喂"信号类型：用户主动投喂链接/话题作为最强正反馈
- 权重: 外部投喂(3.0) > save(2.0) > pairwise选择(1.0)
- 投喂内容驱动召回层拓展相关源
