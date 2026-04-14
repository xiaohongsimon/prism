# Synthesis: Prism 信息源战略重构

## 辩论参与者
| Model | Role | 核心主张 |
|-------|------|---------|
| Codex GPT-5.4 | Proposer | 五环框架：官方发布>OSS动态>中国竞品>模型经济学>管理杠杆 |
| 百炼 GLM-5 | Constraint Auditor | 最小可行集，可行性第一，WeChat是技术债陷阱 |
| 百炼 Qwen-3.5-Plus | Counterexample Hunter | "你是算力军阀不是研究员"，问题不在信源而在缺Action Layer |
| Qwen3.5-27B | Objection Auditor | 自有集群遥测数据是被忽视的最重要信源 |
| Claude Opus | Arbiter | 综合裁决（本文） |

---

## 一、共识区 (所有模型一致)

### 1. arxiv 日推必须大幅降权
- **所有模型都同意**当前 cs.LG/CL/AI 全品类是噪音灾难
- 分歧在于"砍掉 vs 精滤"
- **裁决**: 不砍，但彻底重构过滤器。Qwen-3.5-Plus 说得对——有 1500 GPU 的 TL 需要知道哪些论文 6 个月后变产品。过滤条件从品类改为：
  - 关键词: `inference`, `quantization`, `MoE`, `sparse`, `KV-cache`, `long-context`, `agent`, `eval`
  - 作者/机构白名单: DeepSeek, Qwen, Meta FAIR, Google DeepMind, Anthropic
  - 每日上限: 3 篇
  - 频率: 从日推降为**周汇总** (GLM-5 建议，采纳)

### 2. GitHub trending 价值很低
- Codex: "只当异常检测器"
- GLM-5: "教程和样板项目居多"
- **裁决**: 替换为 **GitHub org release monitoring**，追踪 15-20 个关键组织的 release

### 3. follow_builders 该降权或移除
- Codex: "不是 executive-grade signal"
- **裁决**: 降为最低优先级，观察一个月数据，无法证明 alpha 就删

### 4. TL 和 IC 的信息策略根本不同
- Codex: "TL follows decision surfaces, IC follows implementation surfaces"
- Qwen-3.5-Plus: "技术权威就是管理杠杆"
- GLM-5: "TL 优化决策速度，IC 优化信息完整性"
- **裁决**: 这三个观点不矛盾。TL 需要的是**决策相关的技术深度**，而非泛泛的管理鸡汤，也非论文的实现细节

---

## 二、分歧区 + 裁决

### 分歧 1: 要不要加管理类内容？
- Codex: 要 (Pragmatic Engineer, Will Larson, SVPG)
- Qwen-3.5-Plus: 不要，"management advice is generic, technical authority IS management leverage"
- **裁决**: **有限度地加**。Qwen 的反直觉观点有道理但过于极端。一个管 40 人的 TL 确实需要组织设计、delegation 等方面的输入，但不需要天天看。采纳 Codex 的建议但降频为**周频**，且限于 Will Larson (技术管理实操) 和 Latent Space (AI + 产品交叉)。

### 分歧 2: 中国信源怎么加？
- Codex: InfoQ China, 智东西, BAAI, ModelScope
- GLM-5: WeChat 是技术债陷阱，只用付费 RSS 代理订 1-2 个号
- Qwen-3.5-Plus: 大部分中文媒体 echo Western Twitter +48h，只关注基础设施频道
- **裁决**: GLM-5 的可行性分析最务实。中国信源策略：
  1. **GitHub org 追踪** (零成本): deepseek-ai, QwenLM, THUDM, MiniMax, XiaoMi (MiMo)
  2. **InfoQ China AI 频道** (可爬): 质量尚可，有原创技术管理内容
  3. **付费微信 RSS 代理**: 只订 1 个号（机器之心，有原创价值）
  4. **放弃**: Zhihu, Juejin, 量子位 (echo chamber)

### 分歧 3: 信源数量多好还是少好？
- Codex: 较多 (~25 个信源)
- GLM-5: 最小可行集 (5 个)
- Qwen-3.5-Plus: 更少更好，关键是 action layer
- **裁决**: **中间路线**。15 分钟/天的消费预算决定了 briefing 条目不能超过 8-10 条。信源可以多，但 Prism 的 briefing 算法必须做好**聚合、去重、排序**。信源数量不是瓶颈，briefing 质量才是。

---

## 三、独特贡献 (单一模型提出，值得采纳)

### Qwen-3.5-Plus: Action Layer 概念 ★★★
> 当前: "注意力优化新论文"
> 应该: "新 kernel 声称 20% 加速，兼容 A800，建议派 2 人在 Dev 集群 B 验证"

**裁决**: 这是最有价值的洞察。Prism 不应只是信息聚合器，应该进化为**决策辅助系统**。每条 briefing 条目应附带 action tag: `Ship / Kill / Investigate / Watch`

### Qwen-3.5-Plus: GPU 现货价格监控 ★★
Lambda Labs, RunPod 等价格变动 → 预测基础设施趋势

**裁决**: 好想法，但工程投入 ROI 待验证。列为 Phase 2。

### Qwen-3.5-Plus: 竞品 JD 爬取 ★★
其他大厂招聘信息 → 预测 6 个月后技术栈方向

**裁决**: 高价值，但法律和合规风险需评估。列为 Phase 2，先手动观察验证。

### Codex: 模型经济学 API ★★★
Artificial Analysis, OpenRouter → 模型性价比实时跟踪

**裁决**: 采纳。对于管 1500 GPU 的 TL，"哪个模型够好够便宜可以上线"是核心决策点。

### Qwen3.5-27B: 自有集群遥测 ★★
GPU 利用率、故障模式、成本趋势

**裁决**: 非常好的观点。但这不是 Prism 的外部信源，而是内部运维数据。建议在 Prism 之外的内部系统处理，Prism 专注外部信息。

### Codex: 跨语言去重 ★★
同一事件不应从英文媒体、中文媒体、GitHub 出现三次

**裁决**: 非常实际的工程需求，纳入 Prism 改进清单。

### MiniMax: 内部信号维度 ★★★
其他模型都在讨论外部信源，MiniMax 指出 TL 最高价值的信号可能来自**公司内部** — 战略会纪要、OKR 进度、预算分配、高管路线图。

**裁决**: 非常好的视角。Prism 作为外部情报系统无法直接获取，但应该在 briefing 模板中预留"内部关联"提示位 — 比如"DeepSeek 发布新模型 → 你的团队在用 DeepSeek 吗？需要评估升级吗？"

### MiniMax: 个人品牌建设视角 ★★
每条信息应该问："这能变成我下次技术评审/晋升答辩的素材吗？"

**裁决**: 实际但不应成为过滤主逻辑。作为 briefing 的一个 optional tag `📢 Shareable` 纳入。

### MiniMax: 融资/并购信号 ★
Crunchbase, IT桔子 — 预测行业整合方向

**裁决**: 对 TL 日常决策的直接价值有限，列为 Phase 3 可选。

---

## 四、最终方案: Prism 信息源 v2

### Tier 1: 每日必看 (Daily, 自动聚合)

| 信源 | 类型 | 实现方式 | 权重 |
|------|------|---------|------|
| **官方发布** (OpenAI, Anthropic, Google, DeepSeek, Qwen, ByteDance Seed) | Release notes | RSS/Scraping | 35% |
| **关键 GitHub org releases** (vllm, sglang, deepseek-ai, QwenLM, THUDM, huggingface, pytorch) | Release | GitHub API | 25% |
| **HN /best** | 社区 | RSS | 15% |
| **模型经济学** (Artificial Analysis, OpenRouter) | Data API | REST API | 10% |
| **X VIP** (karpathy, simonw — 仅 2 人) | Social | API/RSS | 10% |
| **InfoQ China AI** | 中文媒体 | Scraping | 5% |

### Tier 2: 周频 (Weekly digest)

| 信源 | 类型 | 实现方式 |
|------|------|---------|
| **arxiv 精选** (inference/quant/MoE/agent 关键词，限 3 篇) | 论文 | arXiv API |
| **HuggingFace Daily Papers 周汇总** | 论文 | API |
| **管理/生产力** (Will Larson, Latent Space) | Newsletter | RSS |
| **机器之心** (付费 RSS 代理) | 中文媒体 | Paid proxy |

### Tier 3: 月度/按需

| 信源 | 类型 | 实现方式 |
|------|------|---------|
| Product Hunt AI | 产品 | RSS |
| OSS Insight (star velocity) | 数据 | API |
| GitHub trending | 异常检测 | Existing |
| follow_builders | 社区 | Existing (观察期) |

### 移除
- swyx X 账号 → 替换为 Latent Space RSS (周频)
- arxiv 全品类日推 → 重构为关键词过滤周汇总
- GitHub trending 作为主力源 → 降为 Tier 3 异常检测

### Briefing 改造
1. 每日 ≤ 8 条，每条附 action tag: `🚀 Ship / ❌ Kill / 🔬 Investigate / 👀 Watch`
2. 按决策相关性排序，不按时间
3. 跨语言去重
4. 每周一次"本周最该做的 3 件事"自动生成

---

## 五、实施优先级

| Phase | 内容 | 工程量 | 预期价值 |
|-------|------|--------|---------|
| **Phase 1** (本周) | 添加 HN RSS, GitHub org releases, 模型经济学 API; 重构 arxiv 过滤器; 降权 trending/follow_builders | 低 | 高 |
| **Phase 2** (下周) | 添加官方 release note 爬取 (7 家); briefing action tag 系统; 跨语言去重 | 中 | 高 |
| **Phase 3** (月度) | 付费微信 RSS; GPU 价格监控; 竞品 JD 信号 (手动验证) | 中-高 | 中 |

---

## Decision Report

- **Preset**: debate
- **Stakes**: medium
- **Tier**: Standard + Enhanced (added local models)

### Model Dispatch
| Model | Role | Channel | Status | Adopted |
|-------|------|---------|--------|---------|
| Codex GPT-5.4 | proposer | codex-exec | ✅ | full |
| 百炼 GLM-5 | constraint-auditor | bailian | ✅ | full |
| 百炼 Qwen-3.5-Plus | counterexample | bailian | ✅ | full |
| omlx MiniMax-M2.5 | product-strategist | omlx | ✅ (slow, ~8min) | partial |
| omlx Qwen3.5-27B | objection-audit | omlx | ✅ (truncated) | partial |
| omlx MiMo-V2-Flash | cn-ecosystem | omlx | ❌ load failed | discarded |
| Claude Opus | arbiter | native | ✅ | synthesis |

### Synthesis
- **Consensus points**: 4 (arxiv降权, trending降级, follow_builders观察, TL≠IC)
- **Divergence points**: 3 (管理内容, 中国信源策略, 信源数量)
- **Per-model unique contributions**: 4 adopted (action layer, 模型经济学, 集群遥测, 跨语言去重)

### Quality
- **Quorum**: Met (4/5 valid outputs within 60s)
- **Opus decision**: APPROVED — ready for user review
