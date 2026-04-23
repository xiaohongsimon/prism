# Tech-stack v5 Codex CLI 独立评审 — 2026-04-23

Orchestrator: Claude Opus 4.6（本会话）
评审者: Codex CLI（非交互模式 `codex exec --sandbox read-only --skip-git-repo-check`）
评审对象: `docs/constitution/tech-stack.md` v5（对齐 mission v7 锁定候选后的版本）

原始输出：`/tmp/codex-techstack-output.txt`
原始 prompt：`/tmp/codex-techstack-review.md`

---

## 背景

mission.md v7 锁定后 tech-stack 升至 v5：§3.2 新增系统化选入机制契约（对应 mission §5 五要素）、§5.2 加 shadow-only 排序契约（对应 mission §8 推荐引擎启动前置）、§7.2 加 NN7 物理护城河（不实现用户系统 / 不引入租户模型）、§2 出域清单消除悬空引用。

调用 Codex CLI 做 tech-stack v5 独立评审，对照 mission v7 契约是否真的闭环、单机 7×24 实操是否有可被绕过的缝隙。

## Codex 评审结论

**一句话判断**：方向对齐到位，但执行层还有三处跨节漏缝会在真实压力下被绕过。

**总评置信度**：high。所有问题都是可直接定位的跨节冲突与物理可达性不足，不是口味判断。

## 三条致命缺陷（全部被接受并修复）

### 1. Shadow-only 物理不可达仅靠 API 约定

- §5.2 写"shadow ranker 物理不可达 web 层"，但实际只靠 `PersonalizeRegistry.get_live()` 这一接口约定；任意新加一行 `from prism.personalize.foo import FooReRanker` 就能绕过，无静态检查兜底
- 风险：疲劳夜里改代码，绕过 registry 直接 import，mission §8 "shadow-only 前置"被静默违约
- 定位：§5.2 末两行

### 2. §2.1 外部出域清单与 §9 TW3 口径漂移

- §2.1 是"登记表"，§9 TW3 只检查 `git diff` + grep 字段名
- 风险：运行时真实出域（httpx 直连云日志、Sentry SDK、推送 webhook）完全不经过清单，登记表沦为文档装饰；NN3 在真实压力下靠静态扫一道兜底
- 定位：§2.1 / §9 TW3

### 3. §3.2 `SourceCandidate` 接口存在 ≠ 就位

- §3.2 列了五要素接口但 `SourceCandidate` 只有 `(source_key, source_type, evidence, similarity)`，缺 `id`，approve/reject CLI 无法定位；没说候选源落哪张表、draft state 如何体现
- 风险：作者自己觉得"接口写了就算就位"，promote 时 §5 五要素其实未闭环但 CLI 无法准确验证
- 定位：§3.2

## 修复措施（均已落地）

| # | Codex 建议 | 落地位置 |
|---|---|---|
| 1 | §5.2 加 `quality-scan` 静态检查（web 不得 import 非 Identity ranker / 不得读 `ranking_shadow`）+ `promote` 必须检查 mission §5 五要素 + NN6 gate artifact | tech-stack §5.2 |
| 2 | §2.1 加运行时出域闸 `prism/privacy/outbound.py::send(channel, payload)`，统一 registry + 审计表 | tech-stack §2.1 |
| 3 | §3.2 扩展 `SourceCandidate` schema 到 `(id, source_key, source_type, evidence_json, similarity, status, gate_status, generator, created_at)`，强制落 `source_candidates_draft` 表 | tech-stack §3.2 |
| 4 | §6 "上线" 清单加"候选源 / 推荐理由 / 相似度分数进入候选池或 UI"为 Mid gate，自动写 `sources.yaml` 为 High gate | tech-stack §6 |
| 5 | §7.2 加外部平台身份命名规范（`external_author_id` / `platform_account_id`，禁 `user_id`）防 NN7 违约扫误判 | tech-stack §7.2 |
| + | 新增 §11 数据生命周期与备份契约（SQLite 容量 / WAL / 备份 / 恢复演练 / 模型产物预算） | tech-stack §11 |

## 特殊关注点的回答

| 问题 | Codex 结论 |
|---|---|
| a. mission §5 五要素契约技术落地是否真的可验证 | 部分落地。有 `SourceCandidate` 结构但缺 id 与持久化表，promote 命令无法真验证就位——已修 |
| b. shadow-only 契约是否有物理可达性漏洞 | 有。仅靠 `PersonalizeRegistry.get_live()` 是 API 约定，无静态扫兜底——已修 |
| c. NN7 物理护城河措辞过严否 | 不过严。单人项目把 `user_id` 拉红线是合理的，配合外部平台字段命名规范即可无误伤 |
| d. §6 交付门禁契约是否覆盖候选源自动写入 | 未覆盖。候选源写入候选池 / UI 是自动化可见路径但未进 §6 列表——已修 |
| e. §2.1 出域清单是否足以兜住 NN3 | 不足。清单是登记表，实际 runtime 出域可绕过；必须加运行时闸——已修 |

## Codex 的少数派立场

**我可能是少数派：tech-stack 应加 §11 数据生命周期与备份契约**。mission 不适合写这层（不是产品定位问题），但 tech-stack 不写就没地方写了；本地单机 7×24 长运行最大的失效模式其实不是功能 bug，而是 DB 膨胀 / 损坏导致无法恢复。单人项目档案库不可恢复 = 信任归零，比任何 NN 违约都更 terminal。

**作者接受该少数派立场**，新增 §11。

## 最担心的盲点（已进宪章）

**本地单机 7×24 的数据增长、SQLite/WAL、备份恢复、模型产物占用没有宪章级契约**：6 个月后最可能不是功能失效，而是 512GB 本机数据膨胀或一次 DB 损坏让档案库不可恢复。NN4 "降频不删除" 在无容量 + 备份契约时会反向变成自毁前提。

**进入宪章的对抗措施**（§11）：
- SQLite 10 GiB 告警 / 30 GiB 阻断
- WAL 强制启用 + 膨胀追溯月报
- 每日备份 + 异盘 + 14 天滚动 + 月末全量
- 季度恢复演练，连续两次缺失触发 mission §9 重评
- 模型产物 20 GiB 预算 + META.yaml + 孤儿扫
- 备份受 NN3 出域约束（不能备份到云盘）

## 锁定状态

- **tech-stack.md v5 → v6（锁定候选）**，三条跨节漏缝全部修复，新增数据生命周期契约
- §5.2 shadow-only 双重保险（运行时 registry + 静态扫）
- §2.1 出域清单 + 运行时闸双层
- §3.2 `SourceCandidate` 有完整 schema + 持久化表，promote 可真验证
- §11 补齐单机长期运行档案库健康契约

**锁定生效条件**：下次 tech-stack 改动需 mission §9 trip-wire 触发或 tech-stack 发现新违约模式。

## 参考

- mission v7 codex review：`2026-04-23-mission-v7-codex-review.md`
- 本次 codex 原始输出：`/tmp/codex-techstack-output.txt`
- tech-stack.md v6：`../../constitution/tech-stack.md`
- mission.md v7：`../../constitution/mission.md`
