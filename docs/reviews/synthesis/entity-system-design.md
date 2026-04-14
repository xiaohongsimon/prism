# Synthesis: Prism v2 实体系统设计

## 参战阵容

| Model | Role | 核心主张 |
|-------|------|---------|
| Codex GPT-5.4 | System Architect | 固定 6 类实体 + alias 表 + candidate 缓冲 + theme cards |
| 百炼 GLM-5 | Constraint Auditor | 月1→2500实体，月6→8000，模糊匹配是灾难 |
| 百炼 Qwen-3.5-Plus | Contrarian | 不要图谱，用 Tags with Decay；实践关联必须用云端模型 |
| 本地 MiniMax | Product Designer | Mock briefing; 实体是幕后概念，呈现"与你相关的变化" |
| 本地 27B | Self-Assessor | 实体提取 mostly 可靠，关系分类 mostly，状态评估 sometimes |
| Claude Opus | Decision Architect | "注意力雷达"非知识图谱，砍 relations，pending 机制 |

---

## 一、最大分歧：要不要建"知识图谱"？

### 三个流派

| 流派 | 代表 | 核心抽象 | 复杂度 |
|------|------|---------|--------|
| **结构化实体图** | Codex | entity_profiles + aliases + relations + candidates | 高 |
| **注意力雷达** | Opus | topic profiles + events, 无 relations | 中 |
| **加权标签流** | Qwen | 纯 tags + decay，无持久化实体 | 低 |

### 裁决：取 Codex 的骨架，用 Opus 的心智

Codex 的方案最完整也最可实现（它读了代码库才设计的）。但 Qwen 和 Opus 的警告是对的——不能用"知识图谱"的心智去运营这个系统。

**最终选择：Codex 的架构 + Opus 的简化 + Qwen 的降级路径**

---

## 二、逐题裁决

### Q1: 实体粒度

| 方案 | 来源 | 优劣 |
|------|------|------|
| 固定 6 类 (person/org/project/model/technique/dataset) | Codex | ✅ 采纳。清晰、27B 能做分类 |
| 两层 (topics + mentions) | Opus | 部分采纳：macro themes 留在 trends 不进 entity |
| 纯 tags with decay | Qwen | 作为降级方案保留 |

**裁决：**
- 实体类别：`person`, `org`, `project`, `model`, `technique`, `dataset` — 共 6 类
- **宏观主题 (AI, agents, inference) 不进 entity_profiles** — 留在现有 trends 表（Codex 建议，精准）
- **微观细节 (文件名, commit hash, PR号) 不进 entity_profiles** — 进 entity_events.metadata_json
- 创建门槛：`confidence >= 0.8 AND specificity >= 4 AND 出现在 >= 2 条独立信号中`，或 `1 practice event + 1 external event`（Codex 公式）
- 每条 signal 最多提取 5 个实体，最多 2 个全新实体（Codex anti-sprawl 规则）

### Q2: 27B 提取可靠性

**采纳 Codex 的"确定性候选 + LLM 选择"方案：**

```
步骤 1 (代码): deterministic_candidates()
  - 从 signal 中提取: repo URL, owner/repo, @handles, 现有 tags, Title-case 专有名词

步骤 2 (27B): extract()
  - 输入: signal 摘要 + 候选列表 + 已知实体列表
  - 输出: 选择/确认候选 + 分类 + confidence + specificity
  - 关键: 27B 是"选择器"不是"生成器" (Codex fallback 策略，直接作为默认)

步骤 3 (代码): resolve()
  - alias 归一化: NFKC → casefold → strip → collapse
  - 匹配链: repo/URL 精确 > alias 精确 > norm 精确 > 同类模糊匹配
  - 命中 → upsert alias + 创建 event
  - 未命中但 promotable → 创建 profile
  - 否则 → stage 到 candidates 表 (30天过期)
```

**归一化用 alias 表（Codex 方案），不依赖 LLM（Opus 方案一致）。**

GLM-5 的 false positive 5-8% 警告有效。应对措施：
- 所有新建实体默认 `needs_review = 1`，每周 briefing 附人工确认提示
- 合并操作不可逆，建议用 `merged_into_id` 软关联

### Q3: 实体生命周期

**采纳 Codex 的指标驱动方案（与 Opus 的纯指标方案互印证）：**

```python
event_weight = impact * confidence * origin_weight  # practice: 1.25x
m7  = Σ(event_weight * exp(-age_days / 7))
m30 = Σ(event_weight * exp(-age_days / 30))
prev30 = days 31-60 的加权和

状态规则 (带迟滞防抖):
emerging:  age <= 14d AND events >= 2 AND m7 >= 3
growing:   events >= 4 AND m7 >= 1.5 * baseline
mature:    age >= 21d AND m30 >= 8 AND m7 ≈ baseline
declining: age >= 21d AND (silent > 14d OR m7 < 0.5 * baseline)

升级需连续 2 天确认，降级需连续 3 天。
60-90 天无活动 → 归档。
```

**practice 事件权重 1.25x** — 你自己做过的事比外部新闻更重要。

### Q4: 全局画像呈现

**这是最大分歧点。四种方案：**

| 方案 | 来源 | 优点 | 缺点 |
|------|------|------|------|
| Theme cards → 独立段落 | Codex | 结构清晰 | 可能被跳过 |
| 内嵌 entity context | Opus | 零额外阅读成本 | 缺全局视角 |
| "与你相关" 独立区块 | MiniMax | UX 最佳 mock | 需额外 LLM call |
| 隐式重排序 | Qwen | 最简单 | 用户无感知 |

**裁决：MiniMax UX + Opus 内嵌 + Codex 数据引擎**

1. **每条 signal 内嵌 entity context** (Opus): 零额外阅读成本
   ```
   🔬 vLLM 0.8 发布，speculative decoding 优化
     📍 vLLM [active, 本周第3条] | 🔗 你 3/25 在 omlx 测过 speculative decoding
   ```

2. **briefing 末尾附简短 Radar 摘要** (MiniMax 简化版): 只在有"啊哈时刻"时出现
   ```
   📡 Radar 变化
   ↑ 新进 active: DeepSeek-V3
   ↗ 实践交叉: vLLM (你测过 + 外部新版)
   ↓ 趋于沉寂: RLHF (14天无新信号)
   ```

3. **周五附完整周报** (Codex theme cards): 回顾本周全局图景

### Q5: 已实践数据集成

**采纳 Codex 的"practice as first-class source"方案** — 这是最优雅的设计：

```
practice 数据作为新的 source type 进入现有 pipeline:
git commits → 新 adapter: git_practice → raw_items → cluster → analyze → entity_link
Claude Code → 新 adapter: claude_sessions → raw_items → ...
Manual CLI → 新 adapter: practice_notes → raw_items → ...
```

**为什么好：** 不需要在 entity_link 里写特殊逻辑。practice 数据走完整 pipeline，自然和外部信号在 clustering 和 entity_link 阶段产生交叉。

**Git 消化策略 (Codex):**
- 不读 diff（太大）
- 读: repo名 + commit subject + top changed dirs/files + dependency changes
- 27B 生成: event_type=practice_commit, role=modified|used|evaluated

**Claude Code 消化策略 (Codex):**
- 不读完整会话（太长）
- 读: .claude/projects/*/memory/MEMORY.md (Opus 补充)
- 27B 生成: event_type=practice_session

**交叉分析 (Codex + Opus 共识):**
```sql
-- practice_overlap: 7 天内同时有 practice 和 external event 的实体
SELECT ep.* FROM entity_profiles ep
WHERE EXISTS (SELECT 1 FROM entity_events e1
              WHERE e1.entity_id = ep.id AND e1.event_type LIKE 'practice_%'
              AND e1.date >= date('now','-7 days'))
  AND EXISTS (SELECT 1 FROM entity_events e2
              WHERE e2.entity_id = ep.id AND e2.event_type NOT LIKE 'practice_%'
              AND e2.date >= date('now','-7 days'))
```

---

## 三、entity_relations 要不要？

| 立场 | 模型 |
|------|------|
| 要，但小类型集 | Codex (implements, integrates_with, competes_with, about) |
| 不要 | Opus, Qwen |
| 27B 能做 "mostly" | 27B 自评 |
| 会爆炸到 200K/年 | GLM-5 |

**裁决：v1 不做 entity_relations。**

理由：
1. 27B 自评 "mostly" 做关系分类，但 GLM-5 指出幻觉关系是最高频失败模式
2. 现有 `cross_links` 表已能间接反映 topic 关联
3. 单用户 50-100 实体不需要图遍历
4. **v2 可选加入**：先跑 3 个月看需求是否真实

---

## 四、最终 Schema

```sql
-- 实体画像
entity_profiles (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT UNIQUE,
    display_name TEXT,
    category TEXT NOT NULL,  -- person|org|project|model|technique|dataset
    status TEXT DEFAULT 'emerging',  -- emerging|growing|mature|declining
    summary TEXT,
    needs_review INTEGER DEFAULT 1,
    first_seen_at TIMESTAMP,
    last_event_at TIMESTAMP,
    event_count_7d INTEGER DEFAULT 0,
    event_count_30d INTEGER DEFAULT 0,
    event_count_total INTEGER DEFAULT 0,
    m7_score REAL DEFAULT 0,
    m30_score REAL DEFAULT 0,
    metadata_json TEXT DEFAULT '{}'
)

-- 别名表 (归一化核心)
entity_aliases (
    alias_norm TEXT NOT NULL,  -- NFKC+casefold+stripped
    entity_id INTEGER REFERENCES entity_profiles,
    surface_form TEXT,  -- 原始形式 "vLLM"
    source TEXT,  -- 'llm'|'repo_url'|'manual'
    PRIMARY KEY (alias_norm, entity_id)
)

-- 候选缓冲 (防爆炸)
entity_candidates (
    name_norm TEXT PRIMARY KEY,
    mention_count INTEGER DEFAULT 1,
    category TEXT,
    first_seen_at TIMESTAMP,
    last_seen_at TIMESTAMP,
    sample_signals_json TEXT,  -- max 3 signal IDs
    expires_at TIMESTAMP  -- first_seen + 30 days
)

-- 实体事件
entity_events (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER REFERENCES entity_profiles,
    signal_id INTEGER REFERENCES signals,
    date DATE,
    event_type TEXT,  -- release|paper|discussion|practice_commit|practice_session|practice_note
    role TEXT,  -- subject|comparison|dependency|practice_target
    impact TEXT,  -- high|medium|low
    confidence REAL,
    description TEXT,
    metadata_json TEXT DEFAULT '{}'
)

-- FTS
CREATE VIRTUAL TABLE entity_search USING fts5(
    canonical_name, display_name, summary,
    content=entity_profiles, content_rowid=id
);
```

**注意：没有 entity_relations。** v1 用 cross_links + co-occurrence 间接反映。

---

## 五、omlx 需求 (基于本设计)

### 必需 (否则自己写队列)
1. **Batch Queue**: 提交 50-100 个 completion 请求，顺序处理，返回结果集
2. **Priority**: background (Prism batch) vs urgent (Claude Code 对话)

### 强烈需要
3. **Scheduled Model Swap**: 2-4am 保证 27B 可用
4. **Concurrent Decode ≥ 4** (对 ≤30GB 模型): 批处理吞吐 4x

### 降级方案 (omlx 不升级时)
- Prism 自建简单 FIFO 队列
- 凌晨 2am 直接发 sequential requests to omlx
- 用 semaphore 控制并发 (max 2)
- 预计 50 signals × 5s/each = ~4 min 总处理时间，可接受

---

## Decision Report

| Model | Role | Status | Adopted |
|-------|------|--------|---------|
| Codex GPT-5.4 | System Architect | ✅ | **heavy** — schema, prompt, lifecycle, practice integration |
| 百炼 GLM-5 | Constraint Auditor | ✅ | full — 数字验证了所有 anti-sprawl 措施的必要性 |
| 百炼 Qwen-3.5-Plus | Contrarian | ✅ | partial — "不要图谱"说服了砍 relations，降级路径采纳 |
| 本地 MiniMax | Product Designer | ✅ | partial — mock briefing 指导了 UX，冷启动策略采纳 |
| 本地 27B | Self-Assessor | ✅ (truncated) | advisory — 验证了"27B 做选择器不做生成器"的策略 |
| Claude Opus | Decision Architect | ✅ | medium — pending 机制、内嵌 context、砍 relations |

### Synthesis
- **Consensus**: 5/6 模型同意需要 pending/candidate 机制防爆炸
- **Divergence**: 3 票砍 relations vs 1 票保留 → 砍
- **Key arbitration**: 全局画像呈现 — 折中方案 (内嵌 + 简短 radar + 周报)
- **Opus decision**: APPROVED — ready for spec writing
