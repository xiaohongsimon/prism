# Mission v7 Codex CLI 独立评审 — 2026-04-23

Orchestrator: Claude Opus 4.6（本会话）
评审者: Codex CLI（非交互模式 `codex exec --sandbox read-only`）
评审对象: `docs/constitution/mission.md` v7（锁定前候选版）+ `docs/constitution/tech-stack.md` v3

原始输出：`/tmp/codex-review-output.txt`
原始 prompt：`/tmp/codex-review-prompt.md`

---

## 背景

v5 经 7 模型辩论（2026-04-22）→ v6（加 NN6 牙齿 / NN3 模型产物 / §5 阻塞锚点 / §10 多用户回归 / §4 优先级声明 / TW3+TW4）→ v7 候选（§3 加痛点 4 信息推荐官 / §8 加源自动发现无解命题 + 删 Entity graph / §10 Non-goals 整节删除 + 多用户升 NN7 / TW4 阈值放宽 / §4 优先级语义清理）。

调用 Codex CLI 做 v7 锁定前的最后独立评审，避开作者自评盲点。

## Codex 评审结论

**一句话判断**：不能直接锁定；最大阻碍是跨文档契约分叉。

**总评置信度**：high。阻碍是可直接定位的跨文档冲突，不是审美判断。

## 三条致命缺陷（全部被接受并修复）

### 1. TW4 mission vs tech-stack 阈值分叉

- Mission v7 改"2 周累计 ≥3 次或同类 ≥2 次"
- tech-stack v3 还写"连续 2 周 ≥1 次触发暂停"
- 风险：实际执行以 tech-stack 为准，mission 改了个寂寞；或作者发现后手动绕过，NN6 可信度被磨掉
- 定位：mission.md §9 TW4 / tech-stack.md §6.3 §9

### 2. §5 "系统化选入机制就位"无验收口径

- §5 说系统化选入缺失阻塞 §8，§8 又把它列为前置，但没定义"就位"最低标准
- 风险：作者可同时合理主张"还没就位继续延"和"够用了先启动"，宪章无法裁决
- 定位：mission.md §5 / §8

### 3. 锁定声明早于跨文档同步

- mission header 已写 "v7（锁定）"，tech-stack 状态仍是"对齐 mission v6"，§1 §10 引用指向已删章节
- 风险："宪章是单一来源"变口号，后续 PR 抓不同文档各取所需
- 定位：mission.md 状态行 / tech-stack.md 状态行 / tech-stack §1 末

## 修复措施（均已落地）

| # | Codex 建议 | 落地位置 |
|---|---|---|
| 1 | mission TW4 + tech-stack TW4 统一口径 | mission §9 / tech-stack §6.3 + §9 |
| 2 | mission TW4 加"tech-stack 不得另设更严全局阈值"双向约束 | mission §9 TW4 末句 |
| 3 | §5 定义"就位"五要素（候选生成 + 质量理由 + approve/reject + decision_log + 降频不删除） | mission §5 |
| 4 | §8 推荐引擎启动前置改为"满足 §5 五要素闭环；未满足前只允许 offline + shadow 排序，不影响可见排序" | mission §8 |
| 5 | mission header 改"v7（锁定候选）"，锁定生效需 tech-stack 升至对齐 v7 | mission 状态行 |
| + | tech-stack header 改 v4，`§10` stale 引用改为 `NN7` | tech-stack 状态行 + §1 末 |

## 特殊关注点的回答

| 问题 | Codex 结论 |
|---|---|
| a. NN7 是否冗余 NN1 | 不冗余。NN1 是产品身份，NN7 是协作边界（开源后的 PR / issue 压力防线） |
| b. TW4 阈值是否合理 | 合理，不太松；关键是 single-pollution 必须立即回撤，High 风险可 tech-stack 局部更严 |
| c. §4 优先级声明位置 | 放 NN 表下方合适，保持"冲突裁决"语气比数学不等式好 |
| d. §5 ↔ §8 循环引用 | 不是绕圈，是同一前置条件两处互锚；问题在缺"就位"定义（已修） |
| e. §3 痛点 4 vs §1 一句话张力 | 不是矛盾，是身份与长期能力分层；§1 保"工作台"，§3/§8 说推荐官野心，当前聚焦 1-3 已压住 |

## Codex 的少数派立场

**我可能是少数派：NN7 不过度**。NN1 只说明产品偏作者，NN7 处理的是开源后的外部压力和 PR 边界，两者不是同一层问题。可以把 NN7 文案瘦身，但不应降回 non-goal 或删除。

（与作者在本次讨论中的直觉一致——作者在把 §10 Non-goals 整节删除时，唯一保留并升级的就是多用户约束 → NN7。）

## 最担心的盲点（未进宪法，流转到 roadmap 讨论）

**作者把 Prism 输出当默认事实**。NN6 假设作者能发现污染并标记，但 6 个月后真正危险的是作者对低质产出的认知免疫——一个翻译错误看了 20 遍会被大脑自动修正，幻觉摘要读多了被内化为事实。这是所有 trip-wire 都抓不到的温水煮青蛙。

**可行对抗**：周期性抽样回看原文核对翻译 / 摘要是否失真，结果落月度报告。

**决策**：不进 mission（认知同化不是 mission 层能约束的）、不进 tech-stack（不是契约层）、待 roadmap 阶段讨论放 Wave 3 还是 Wave 4。与 2026-04-22 辩论综述里 opus47 / kimi26 独立指出的"作者即法官" / "习惯化污染"盲点一致。

## 锁定状态

- **mission.md v7 候选** + **tech-stack.md v4** 契约闭环，三条跨文档分叉全部修复
- mission §9 TW4 阈值 = tech-stack §6.3 §9 口径
- mission §5 五要素 = tech-stack 未来实现的直接对照清单
- NN7 拒绝多用户 = tech-stack §1 不接受的替代理由

**锁定生效条件已满足**。下次 mission 改动需 §9 trip-wire 触发或 mission 重评。

## 参考

- v5 7 模型辩论：`2026-04-22-mission-v5-multi-model-debate.md`
- 本次 codex 原始输出：`/tmp/codex-review-output.txt`
- mission.md v7：`../../constitution/mission.md`
- tech-stack.md v4：`../../constitution/tech-stack.md`
