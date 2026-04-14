# Claude Opus — Decision Architect

## 我的核心论点：不要建"知识图谱"，建"注意力雷达"

"知识图谱"这个词暗示了一种企业级复杂性：本体论、关系类型、一致性约束。但这是一个单人系统，处理 50-100 条/天的信号。我认为正确的抽象不是 graph，而是 **tech radar** — 一个持续演进的"我在关注什么、世界在发生什么、我做过什么"的注意力地图。

名字变了，但 DB schema 可以几乎不变。区别在于设计心智：

- Knowledge Graph 心智 → 追求完整性、一致性、关系正确性 → 维护成本高
- Attention Radar 心智 → 追求"对这个人有用"、允许模糊、宁缺毋滥 → 维护成本低

---

## Q1: 实体粒度 — 两层就够

不要让 LLM 自由发挥，也不要手动定义。用**两个固定层级 + 宽松映射**：

**Layer 1: Topics (技术方向)**
- 粒度：能用 3-5 个词描述的技术领域
- 数量：稳态 20-40 个
- 例：`inference-optimization`, `ai-agents`, `open-source-models`, `local-llm`
- 怎么控制数量：新 topic 创建时必须尝试合并到现有 topic（编辑距离 + embedding 相似度）。合并阈值宽松，宁可合并错也不要爆炸。

**Layer 2: Mentions (具体提及)**
- 粒度：具体项目、论文、产品、人物
- 不做独立 entity_profile，只作为 topic 的 event 附属
- 例：`vLLM 0.8 release` 是 `inference-optimization` 下的一个 event
- 这层不需要去重，event 描述里自然包含

**为什么不要第三层：**
"AI" 太粗不需要建模（所有东西都是 AI）。"PagedAttention CUDA kernel" 太细不需要建模（它就是某个 event 的细节）。两层覆盖 95% 的需求。

---

## Q2: 27B 提取策略 — 不要让它做困难的事

27B 的强项：文本分类、摘要、关键词提取
27B 的弱项：实体归一化、关系推理、状态评估

**策略：给 27B 简单任务，用代码做困难的事**

```
27B 的任务（每条 signal）:
输入: signal.summary + signal.action + signal.tags_json
输出 JSON:
{
  "topics": ["inference optimization", "vLLM"],  // 自由文本，允许不标准
  "event_type": "release",                        // 从固定枚举选
  "impact": "medium",                             // high/medium/low
  "one_line": "vLLM 0.8 发布，PagedAttention v2 提速 30%"
}
```

**代码做归一化（不依赖 LLM）：**
```python
def normalize_topic(raw_topic: str, existing_topics: list[str]) -> str:
    # 1. 小写 + strip
    cleaned = raw_topic.lower().strip()
    # 2. 别名表（硬编码高频同义词）
    ALIASES = {"vllm": "inference-optimization", "sglang": "inference-optimization", ...}
    if cleaned in ALIASES:
        return ALIASES[cleaned]
    # 3. 编辑距离匹配现有 topics (threshold=0.8)
    best_match = fuzzy_match(cleaned, existing_topics, threshold=0.8)
    if best_match:
        return best_match
    # 4. 新 topic 候选 → 累计 3 次独立出现才正式创建
    pending_topics[cleaned] += 1
    if pending_topics[cleaned] >= 3:
        return create_new_topic(cleaned)
    return None  # 暂不关联
```

**关键设计：pending 机制防止爆炸。** 一个概念必须在 3 天内被 3 条独立信号提及才会变成正式 topic。这自动过滤掉一次性噪音。

---

## Q3: 生命周期 — 不用 LLM，纯指标驱动

```python
def assess_status(topic: EntityProfile) -> str:
    recent_7d = count_events(topic, days=7)
    recent_30d = count_events(topic, days=30)
    total = topic.event_count
    age_days = (now - topic.first_seen_at).days

    if age_days < 14 and total < 5:
        return "emerging"
    elif recent_7d >= 3:  # 本周热度高
        return "active"   # 注意：我把 "growing" 改成了 "active"，更直觉
    elif recent_30d >= 5:
        return "tracking"  # 有稳定关注度
    elif recent_30d < 2 and age_days > 60:
        return "dormant"
    else:
        return "tracking"
```

**为什么不用 emerging/growing/mature/declining？**
这套生命周期暗示线性演进，但技术话题不是这样的。vLLM 可能连续 3 个月 dormant 然后突然 active。用 `emerging / active / tracking / dormant` 四态更符合实际。

---

## Q4: 全局画像 — 不做独立段落，做 context enrichment

我反对在 briefing 里加一个独立的"全局画像"段落。原因：

1. **15 分钟里用户不会读它。** 增量消息是紧急的、全局画像是"嗯知道了"级别的。
2. **脱离增量信号的全局画像是空洞的。**

**替代方案：Entity-enriched signals**

不加新段落，改造现有 signal 的呈现：

```
原来:
🔬 vLLM 0.8 发布，PagedAttention v2 提速 30%
  → 建议评估是否升级内部推理服务

v2:
🔬 vLLM 0.8 发布，PagedAttention v2 提速 30%
  → 建议评估是否升级内部推理服务
  📍 inference-optimization [active, 本周第3条]
  🔗 你 3/25 在 omlx 测过 speculative decoding，这个版本有相关优化
```

每条 signal 下面附 1-2 行 entity context。用户自然就看到了全局画像——不是抽象的"这个方向在增长"，而是嵌入在具体事件里的"你应该关心这个因为..."。

**唯一的独立全局内容：周五 briefing 末尾附一个 "本周 Radar 变化"**
- 新进入 active 的 topics
- 从 active 回到 tracking 的 topics
- 本周新发现的 topics

这个一周看一次就够了。

---

## Q5: 已实践数据 — 分层处理

**自动层 (git)：**
```
每日扫描本机 git repos → 提取最近 24h 的 commit messages + changed file paths
→ 27B: "这些 commits 涉及哪些技术 topics？" → 输出 topic 列表
→ 创建 event_type="practice" 的 entity_events
```

注意：不读 diff（太大），只读 commit message + file paths。27B 从文件路径就能推断很多（`prism/sources/youtube.py` → 显然涉及 youtube adapter 开发）。

**自动层 (Claude Code)：**
Claude Code 的 CLAUDE.md memory 系统已经有结构化记忆。不需要分析会话历史（太庞大）。
→ 每日读取 `.claude/projects/*/memory/MEMORY.md`
→ 27B 提取涉及的 topics
→ 创建 practice events

**手动层 (CLI)：**
```bash
prism practice "在 omlx 上测试了 MiMo-V2-Flash 的推理速度，122GB 加载约 90 秒，3.3 tok/s"
```
→ 27B 提取 topics: `local-llm`, `open-source-models`
→ 创建 practice event with full description

**交叉分析（最有价值的部分）：**
当新 signal 关联到某个 topic，且该 topic 近期有 practice event 时，briefing 自动附上关联。这个不需要 LLM，纯 SQL join：

```sql
SELECT e.description FROM entity_events e
WHERE e.entity_id = :topic_id
  AND e.event_type = 'practice'
  AND e.date >= date('now', '-14 days')
ORDER BY e.date DESC LIMIT 1
```

---

## Schema 微调

```sql
entity_profiles (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,              -- normalized topic name
    display_name TEXT,             -- 人类可读名
    status TEXT DEFAULT 'emerging', -- emerging/active/tracking/dormant
    summary TEXT,                   -- 最近一次 LLM 生成的摘要
    first_seen_at TIMESTAMP,
    last_event_at TIMESTAMP,
    event_count_7d INTEGER DEFAULT 0,  -- 缓存，每日更新
    event_count_30d INTEGER DEFAULT 0,
    event_count_total INTEGER DEFAULT 0,
    aliases_json TEXT DEFAULT '[]'     -- ["vllm", "vLLM", "vllm-project"]
)

-- pending_topics: 还没正式创建的候选
pending_topics (
    name TEXT PRIMARY KEY,
    mention_count INTEGER DEFAULT 1,
    first_mentioned_at TIMESTAMP,
    sample_signals_json TEXT  -- 最多存 3 条 signal_id 参考
)
```

去掉 `entity_relations` 表。原因：
1. 27B 不能可靠提取关系类型
2. 单用户 20-40 个 topics 不需要关系图
3. 用现有的 `cross_links` 表间接反映 topic 关联已经够了

---

## 降级方案 (如果 27B 质量不够)

- entity 提取退化为：正则 + 别名表 + 现有 entities.yaml 匹配
- 每周用云端模型跑一次"本周信号 → topic 修正"
- practice data 只保留手动 CLI 输入

## Confidence: high
## Key Risk: "全自动无种子"在冷启动阶段可能产生低质量 topics，前两周需要人工观察和修正 aliases。
