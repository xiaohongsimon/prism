# Mission v8 辩论议题清单 — 2026-04-24

> **这是辩论前的 agenda，不是 synthesis**。辩论跑完后在本目录追加 `2026-04-24-mission-v8-ecosystem-debate.md` 作为综合。
>
> 上游：mission v7（锁定候选，见 `docs/constitution/mission.md`）。
> 触发：作者 2026-04-24 提出把 Prism 的结构化产物喂给 `~/work/wechat-insight` 做价值投资二次加工；经 chat 辩论识别出 v7 对"作者的 AI 工具生态中 Prism 的位置"未定义，结构性议题不得走 spec 通道绕过，升级到 mission 重评。

## 一句话背景

作者的论断（reframe）：**"所有项目的最终目标都是服务我，提升我的认知，包括财富。"**

这句话把 Prism 从「单点工作台」重新定位为「作者 AI 工具生态中的一块」。v7 mission 的所有条款都建立在单点框架上，因此需要评审：哪些条款还站得住，哪些要升级，哪些要重写。

核心命题：**Prism 可不可以、在什么前提下、做"作者本人的其他自用 AI 系统"的 upstream？**

---

## 议题清单（按优先级）

### 议题 1：§2 主用户定义是否升级

**v7 现状**：
> 只有一个：作者本人。任何设计冲突下，作者的使用体验优先。不存在次级用户、目标受众、"顺带受益者"在 mission 层的地位。

**选项**：
- **A（守）**：维持 v7 字面。任何把 Prism 产物导出给另一个项目消费的设计都被挡。作者若要 wechat-insight 访问 Prism，必须是 wechat-insight 单方面 ingest（Prism 不声明接口、不做契约）
- **B（扩）**：§2 升级为"**作者本人的认知系统**"。作者直接在 Prism UI 前消费仍是主路径，但允许"作者本人的其他自用工具"作为合法 downstream。需要定义"自用工具"的验收口径（单机？仅作者消费？）
- **C（放）**：§2 改为"作者本人及作者的 AI 工具生态"。承认生态框架是主体，Prism 只是一块

**红线问题**：B 和 C 都会稀释 v5→v6→v7 三轮辩论守住的"单用户"护城河。NN7（拒绝多用户）如何与之共存？

### 议题 2：NN3 在跨项目场景的边界

**v7 现状**：
> 偏好数据是隐私：画像 / 排序结果 / 学到的权重 / 可反推个人偏好的模型产物不入 git、不对外呈现；实现代码可公开。

**未定义的盲区**：
- Prism 的 **内容选择**（哪些源、哪些 article 被 articlize）本身是偏好信号。当这些内容导出给 wechat-insight，wechat-insight 的 tier-2 调 Anthropic Haiku（cloud API），payload 就走了云端 log
- **Anthropic log 算不算"对外日志"**？v7 的 TW3 列举的是 git/Sentry/公开 dashboard，未覆盖 cloud LLM API
- Prism 的 `article.body` 是公开内容（来自 YouTube 字幕等），但 **"我选了这条"这个动作** 是偏好

**选项**：
- **A（严）**：NN3 补一句"派生产物流经的任何外部系统（含 cloud LLM API）视同对外"。后果：downstream 项目不得用 cloud LLM 消费 Prism 内容。wechat-insight 的 tier-2 必须改本地
- **B（宽）**：NN3 补一句"作者自用工具链内部（含其调用的 cloud LLM API）不视作对外"。信任"作者知道风险自担"
- **C（分级）**：per-source / per-article 打 privacy tag，high-privacy 内容不进导出通道；其余允许。需要定义打标机制

**边角**：Prism 自己都调 omlx（本地），不调 cloud LLM，这是 v7 事实上的默认隐私形态。升级到生态框架后是否破坏这个默认？

### 议题 3：NN7 "拒绝多用户" 在生态里的语义

**v7 现状**：
> 拒绝多用户 / 多租户：代码开源但架构、issue 响应、文档不以多用户为前提。开源是副作用，不是承诺。

**触发点**：wechat-insight 的 CLAUDE.md 明确写"为**自己和朋友**的投资决策提供情报系统"。一旦 Prism → wechat-insight → 朋友决策，NN7 的精神被绕过（字面没违反，因为 Prism 本身仍单用户）。

**选项**：
- **A（收口径）**：要求作者在 mission v8 里同步修改 wechat-insight 的定位（"只为自己，不涉朋友"），否则 Prism 不能作为其 upstream
- **B（设防火墙）**：允许 wechat-insight 多人用，但**只有其 personal-only 数据路径**可消费 Prism；服务朋友的路径独立自建
- **C（放弃 NN7 精神）**：既然"所有项目最终服务作者的认知"，则朋友是作者的"认知反馈环"的一部分（朋友给作者投资建议反哺作者判断），允许间接服务朋友。NN7 退化为"Prism 本体不对多用户做工程承诺"，不限制产物流向

**元问题**：NN7 是 v6 辩论里唯一从 Non-goals 升到 NN 的条款（2026-04-23），刚升上去就稀释，论证强度够不够？

### 议题 4：§8 "镜子而非投其所好" 在多 lens 场景下的解释

**v7 现状**：
> 偏好推荐引擎（§3 痛点 4 的解决方案）——镜子而非投其所好；比任一单渠道平台更懂作者，靠的是跨渠道偏好视图（痛点 1 的副产品）而非更强的排序模型。

**触发点**：如果 wechat-insight 用价值投资 lens 读 Prism，未来可能出现心理学 lens、技术 lens、职业发展 lens ... 每个 lens 是一面镜子还是多面镜？

**选项**：
- **A**：§8 保持单一"镜子"，多 lens 是 downstream 各自的事，Prism 只负责一面综合镜
- **B**：§8 扩展为"镜子集"，Prism 可以同时维护若干主题 lens，每个 lens 独立学习偏好
- **C**：承认 lens 是 downstream 责任，§8 改名"Prism 的镜子"（区别于"作者认知系统的镜子"）

### 议题 5：§9 Trip-wires 增补

生态框架一旦启用，需要新的熔断条件：

**候选 TW5**：任一 downstream 项目接入 Prism 后，Prism 的 mission §2 主路径（`/feed/following`）访问频次 30 天内下降 > 50% → 触发 mission 重评。防止作者实际消费重心漂移到 downstream 而 mission 依旧说 Prism 是工作台。

**候选 TW6**：downstream 项目出现 Prism 未授权的使用场景（如朋友通过它间接看到 Prism 内容、cloud LLM log 泄漏偏好）→ 立即切断该 downstream 的 export 通道。

**议题**：要不要加？加几条？阈值怎么定？

### 议题 6（派生）：v8 若通过，配套文档变更清单

（辩论后写，不在 agenda 范围；此处占位确保 v8 不遗漏）

- `tech-stack.md` §2 / §10 引用更新
- `roadmap.md` Wave 序列里加入"ecosystem seam"
- `SPEC.md` 同步差距分析
- 新增 `docs/constitution/ecosystem.md`？（定义 Prism 作为 upstream 的合法契约形态，否则每次 downstream 接入都要回 mission）

---

## 参战模型建议

沿用 v5/v7 同班底，覆盖对抗性视角：

| Role | 候选模型 | 对抗向量 |
|---|---|---|
| Proposer | Claude Opus 4.7 | 作者的 reframe 在 mission 层成立吗？ |
| Red Team | Grok 4.2 fast | 生态框架是不是 mission scope creep 的借口？ |
| Alt Proposer | Gemini 3.1 Pro preview | 有没有更干净的 mission 结构设计？ |
| Feasibility Analyst | Xiaomi MiMo v2 Pro | NN3/NN7 升级能在 tech-stack 落地吗？ |
| User Advocate | Moonshot Kimi K2.6 | 作者 6 个月后会后悔哪条升级？ |
| Scope Guardian / YAGNI | MiniMax M2.7 | 生态框架有没有直接被 YAGNI 掉的条款？ |
| 中文视角评审 | Z-AI GLM-5.1 | 中文语境下 "认知系统" vs "工作台" 语义漂移 |
| 独立二审 | Codex CLI | v7 升 v8 是否跨文档契约一致 |

## Prompt 骨架

每个模型都收到：
1. mission v7 全文
2. 本 agenda 全文
3. chat 辩论摘要（作者 reframe + Claude 反驳 + 收敛到升级 mission 这一路径）
4. wechat-insight CLAUDE.md（作为第一个 downstream 的事实样本）
5. 要求：对议题 1-5 逐条给出 (立场 / 理由 / 一条具体措辞建议)；在议题 6 给出变更清单。最后一句话判断："能锁定 v8" / "能但要改" / "需返工"

## 输出要求

- 每个模型 1500-2500 字中文回应
- 原始输出存 `/tmp/mm-debate-v8/out_*.txt`
- Orchestrator（当前会话的 Claude Opus 4.7）跑完后写综合到 `docs/reviews/synthesis/2026-04-24-mission-v8-ecosystem-debate.md`
- 综合 → 作者拍板 → 改 mission.md 到 v8

## 时间与成本

7 模型 × ~2k tokens out × 2k in ≈ Zenmux 单次辩论常规成本。可在当前会话一次性跑完（历次辩论有先例）。

---

## 不讨论范围（明确）

- **ai_digest 的 schema / 存储 / 导出实现**：v8 mission 落地前不进入 spec 流程
- **wechat-insight 侧改造**：不是 Prism 仓库范围
- **朋友通过什么 UI 访问 wechat-insight**：wechat-insight 自己的 mission 决定
- **多模型辩论该不该继续**：方法论本身不在此次议题内
