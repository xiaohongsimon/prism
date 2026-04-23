# prism-lens 出圈路径辩论综合

> 2026-04-21 · preset=debate · stakes=medium · 5/6 参与（Gemini 网关失败）

## 参与模型与角色

| # | 模型 | 角色 | 立场 | Confidence |
|---|---|---|---|---|
| 1 | Claude Opus 4.6 (Zenmux) | Proposer A | **改良版 C**：compare 模式 + 内部 subprocess last30days，用户只装一个 | medium |
| 2 | Grok 4 | Red Team | 全部攻击，指出 **B 最差**（放弃野心= 自我欺骗） | high |
| 3 | MiMo V2 Pro | Feasibility Analyst | **否决 A 原版**，推 B（Chrome cookie 跨平台地雷太多） | medium |
| 4 | Kimi K2.5 | User Advocate | **改良版 A**：单源 MVP（只做 X For You），天然漏斗引向 Prism | high |
| 5 | MiniMax M2.7 | Scope Guardian | **不值得为出圈单独投入**，一周回归 CTR 训练，prism-lens 作为 Prism 子功能 | high |

---

## 共识点

1. **方案 A 原版（全量 standalone fallback）不可行** — Opus/MiMo/MiniMax/Kimi 四家都反对
   - MiMo 的硬理由：`yt-dlp --cookies-from-browser` 在 macOS 需 Keychain 授权、Windows 需 DPAPI、用户浏览器可能非 Chrome；bird CLI 又依赖本地 launchd
   - 维护双路径 = 代码腐烂（Grok + Opus 都指出）
2. **方案 C 原版（要求用户先装 last30days）也不行** — Opus/Grok/MiMo 三家认为这等于提高门槛
3. **"我们的护城河"需要怀疑态度** — 多家指出：last30days 复制个性化只是时间问题；没有用户基数就谈不上护城河

## 分歧的关键轴：**出圈这件事本身是否值得做？**

| 立场 | 代表 | 核心论点 |
|---|---|---|
| 值得做，但要换姿势 | Opus, Grok, Kimi | 不出圈 = 放弃 AI era super-individual 叙事；服务"100 个同事"天花板太低 |
| 不值得单独做 | MiniMax, MiMo | 1600 人时机会成本太贵；护城河需要流量基础；现有用户=0 时做分发是徒劳 |

**两派同时 high confidence** —— 这本身就是强信号：这不是事实问题，是**价值观问题**，取决于用户把这周时间定价成什么。

## 各模型独特贡献

- **Opus 的工程解法**：compare 命令内部 subprocess last30days（它本身就是开源 skill），破解了 C 方案的"双依赖死锁"
- **Kimi 的漏斗设计**：免费 X For You 体验 30 秒到 wow → 用户好奇 "YT/GH 怎么解锁" → 自然引流到 Prism 完整版
- **MiMo 的技术否决**：具体到 Keychain/DPAPI 级别的跨平台坑，是方案 A 的客观约束
- **MiniMax 的量化**：把"一周"折成 1600 人时，把"600 star"对 TL 叙事的贡献折算为"一次内部 tech talk"
- **Grok 的扎心**：方案 B 的本质是"承认开源梦是幻觉"——这是 MiniMax 没直面的
- **Kimi 的 5 秒阈值**：用户看 README 5 秒内没看到"装完即用"证据就关标签页

---

## Opus 综合决策

**采纳 Opus + Kimi 的合并方案，时间盒限死到 3 天，带 7 天 gate。**

### Why this shape

- Red Team vs Scope Guardian 正好相反立场、都是 high confidence → 这是价值观裂口，不是事实裂口
- 对 "40 人团队 + 上市公司 + 1500 GPU" 的用户来说，**职业天花板不是"更多 GPU"而是"个人叙事"**——MiniMax 低估了这一点
- 但 MiniMax 的 1600 人时警告也得内化成**限制条件**：不是不做，是**严格限制投入**
- MiMo 的技术否决是硬约束：**不做全平台 fallback，只做 X For You 单源**

### 三段式执行

**Day 1（1 天硬性封顶）**
- `lens.py compare` 命令：subprocess 调 `last30days-skill` + 本地 lens，side-by-side JSON
- SKILL.md 加 compare 模板
- **人工挑 3-5 个高对比度 query**（"Claude 4.6 launch"、"MCP 生态"、"scaling laws"）生成对比截图——这些是发射核心素材

**Day 2（1 天）**
- Standalone fallback 只加 **X For You 一个源**（bird CLI 一行调用）——跨平台风险最小，用户装一次 bird 即可
- 其他源（YT、GH）显式标 "Unlock with Prism"——Kimi 的漏斗生效
- 独立 repo 拆出，Prism 主仓 submodule 引用（避免重复维护）

**Day 3（0.5 天）**
- README 重写：顶部放 compare 截图 + "Your feed is not their feed" 叙事
- 发射：X thread + Show HN + CC Discord

**Day 7 Gate**
- star ≥ 30 且 real install ≥ 5 → 继续投入，加 YT/GH standalone
- star < 20 → **停手**，compare 截图已赚到叙事素材，这周剩余时间回归深化 Prism（MiniMax 的预案）

### 明确拒绝的路径

- ❌ 全量 standalone fallback（MiMo 否决生效）
- ❌ 让用户先装 last30days（Opus + Grok + MiMo 一致）
- ❌ 方案 B 原版（Grok 对：放弃野心对上市公司 TL 的个人叙事不一致）
- ❌ 超过 3 天投入（MiniMax 的机会成本警告生效）

### Opus 最终 Key Risk

**compare 截图的对比度决定一切。** 如果挑的 query 让"全网 vs 你的图谱"差异不够戏剧性，整个叙事塌方。发射前**必须人工挑选至少 3 个高对比度 case**，Day 1 封板前就要把截图拍出来；拍不出来就不发射，直接降级为 Day 7 预案。

---

## Decision Report

- **Preset**: debate
- **Stakes**: medium
- **Models contributed**: 5/6（Gemini 3.1 Pro 网关失败）
- **Consensus points**: 3（A 原版否决 / C 原版否决 / 护城河需怀疑）
- **Divergence**: 价值观裂口（出圈是否值得做）
- **Opus Decision**: **APPROVED with modifications** — 3 天时间盒 + Opus-Kimi 合并方案 + 7 天 gate
