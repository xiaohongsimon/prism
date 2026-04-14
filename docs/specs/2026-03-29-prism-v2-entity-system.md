# Prism v2: Entity-Driven Intelligence System

> 从信号聚合器进化为注意力雷达——以实体为中心的个人 AI 情报系统

## 1. Problem Statement

Prism v1 是一个信号聚合器：采集 → 聚类 → LLM 分析 → 趋势 → 日报。核心缺陷：

- **无记忆**: 今天的 briefing 不知道昨天发生了什么。同一个项目连续 3 天有重大进展，但每天都当新事件处理。
- **无关联到自身实践**: 你在 omlx 上测了 speculative decoding，第二天 vLLM 发布相关优化，但 briefing 无法建立这个关联。
- **信源单一**: 缺少 YouTube 高质量访谈、HN 社区脉搏、GitHub org release 追踪、模型经济学数据。
- **无全局画像**: 只有增量快照，没有"这个月 inference optimization 方向整体怎么样了"的持续追踪。

## 2. Design Goals

1. **实体持久化**: 自动提取、归一化、追踪技术实体 (project, model, org, person, technique, dataset) 的生命周期
2. **实践关联**: 用户的 git commits、Claude Code 会话、手动笔记作为一等数据源，与外部信号自动交叉
3. **信源扩展**: YouTube transcript, HN /best, GitHub org releases, 模型经济学 API
4. **本地 LLM 驱动**: 凌晨本地 27B 完成采集+提取，云端模型只做 daily synthesis
5. **增量演进**: 不重写现有 pipeline，在 analyze 之后插入 entity_link 步骤

## 3. Architecture

### 3.1 Pipeline Extension

```
现有 (不变):
sync → cluster → analyze → trends → briefing

v2 扩展:
sync → cluster → analyze → [entity_link] → trends → briefing
  ↑                              ↓
新 sources                  entity_profiles (持久化)
(youtube, hn,               entity_aliases (归一化)
 org_releases,              entity_candidates (缓冲)
 model_economics,           entity_events (时间线)
 git_practice,
 claude_sessions,
 practice_notes)
```

### 3.2 LLM 分层

| 时段 | 模型 | 任务 | 约束 |
|------|------|------|------|
| 凌晨 2-4am | 本地 27B (omlx) | 信号采集处理、实体提取、YouTube 字幕摘要 | 与 Claude Code 零冲突 |
| 早晨 6am | 云端 (DashScope qwen-plus) | Daily synthesis、briefing narrative | 质量保底 |
| 按需 fallback | 云端 | 实践关联推理 (如 27B 质量不够) | 成本可控 |

### 3.3 调度

```
02:00  sync (所有 sources，含 practice adapters)
02:15  cluster
02:30  analyze (incremental, 本地 27B)
02:45  entity_link (本地 27B)
03:00  trends
06:00  analyze (daily, 云端)
06:10  briefing generate + publish
```

## 4. New Source Adapters

### 4.1 YouTube Transcript (`youtube`)

```yaml
# sources.yaml
- type: youtube
  key: "youtube:ai-interviews"
  channels:
    - UCbfYPyITQ-7l4upoX8nvctg  # Lex Fridman
    - UC6107grRI4m0o2-emgoDnAA  # Dwarkesh Podcast
    - UCb1VhJpMEaMQ0yIflg0sDQQ  # Latent Space
    - UCUyeluBRhGPCW4acMCN7V0Q  # AI Explained
    - UCZHmQk67mSJgfCCTn7xBfew  # Yannic Kilcher
    - UCsBjURrPoezykLs9EqgamOA  # Fireship
```

**流程:**
1. YouTube RSS feed (`/feeds/videos.xml?channel_id=XXX`) → 检测新视频
2. `yt-dlp --write-auto-sub --sub-lang en --skip-download` → 只拿字幕文件
3. 本地 27B: transcript → 800字摘要 + 关键论点提取 + entity 标签
4. 摘要作为 `RawItem.body` 进入标准 pipeline
5. 原始 transcript 存 `raw_json` 备查

**输出**: `RawItem(url=video_url, title=video_title, body=summary, author=channel, raw_json={transcript, key_points})`

### 4.2 Hacker News (`hackernews`)

```yaml
- type: hackernews
  key: "hn:best"
  feed_url: "https://hnrss.org/best"
  max_items: 15
```

标准 RSS 获取，无特殊处理。

### 4.3 GitHub Org Releases (`github_releases`)

```yaml
- type: github_releases
  key: "github:releases"
  orgs:
    - vllm-project
    - sgl-project
    - deepseek-ai
    - QwenLM
    - THUDM
    - huggingface
    - pytorch
    - anthropics
    - openai
    - meta-llama
    - mistralai
    - google-deepmind
    - apple  # MLX
    - XiaoMi  # MiMo
    - MiniMaxAI
```

**流程:**
1. GitHub API: `GET /orgs/{org}/repos?sort=updated` → 找最近更新的 repos
2. `GET /repos/{owner}/{repo}/releases?per_page=3` → 获取最新 releases
3. 按 `published_at` 过滤最近 24h
4. Release notes 作为 `RawItem.body`

### 4.4 Model Economics (`model_economics`)

```yaml
- type: model_economics
  key: "economics:models"
  sources:
    - artificial_analysis  # API
    - openrouter           # API
```

**输出**: 每日模型价格/速度/质量变化快照。价格变化 >10% 或新模型上线时生成 signal。

### 4.5 Practice Sources

#### Git Practice (`git_practice`)

```yaml
- type: git_practice
  key: "practice:git"
  repos:
    - /Users/leehom/work/prism
    # 可扩展更多本地 repos
  lookback_hours: 24
```

**流程:**
1. `git log --since="24 hours ago" --format="%H|%s|%an|%ai"` + `git diff --stat`
2. 每个 repo 生成一条日摘要 RawItem:
   - title: `"[Practice] {repo_name} daily activity"`
   - body: commit subjects + top changed dirs/files + dependency changes
3. 不读 diff 内容，只读 stat

#### Claude Code Sessions (`claude_sessions`)

```yaml
- type: claude_sessions
  key: "practice:claude"
  memory_dirs:
    - /Users/leehom/.claude/projects
```

**流程:**
1. 扫描 `.claude/projects/*/memory/MEMORY.md`
2. 对比上次扫描的快照，提取增量变化
3. 生成 RawItem:
   - title: `"[Practice] Claude Code session summary"`
   - body: 新增 memory 条目内容

#### Manual Practice Notes (`practice_notes`)

```bash
prism practice "在 omlx 上测试了 MiMo-V2-Flash，122GB 加载约 90 秒，3.3 tok/s"
```

直接创建 RawItem 进入 pipeline。

## 5. Entity System

### 5.1 Schema

```sql
-- 实体画像
CREATE TABLE entity_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('person','org','project','model','technique','dataset')),
    status TEXT DEFAULT 'emerging' CHECK(status IN ('emerging','growing','mature','declining')),
    summary TEXT DEFAULT '',
    needs_review INTEGER DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_event_at TEXT,
    event_count_7d INTEGER DEFAULT 0,
    event_count_30d INTEGER DEFAULT 0,
    event_count_total INTEGER DEFAULT 0,
    m7_score REAL DEFAULT 0.0,
    m30_score REAL DEFAULT 0.0,
    metadata_json TEXT DEFAULT '{}'
);

-- 别名表 (归一化核心)
CREATE TABLE entity_aliases (
    alias_norm TEXT NOT NULL,       -- NFKC + casefold + strip
    entity_id INTEGER NOT NULL REFERENCES entity_profiles(id),
    surface_form TEXT NOT NULL,      -- 原始形式 "vLLM"
    source TEXT DEFAULT 'llm',      -- llm | repo_url | manual
    created_at TEXT NOT NULL,
    PRIMARY KEY (alias_norm, entity_id)
);
CREATE INDEX idx_alias_norm ON entity_aliases(alias_norm);

-- 候选缓冲 (防爆炸)
CREATE TABLE entity_candidates (
    name_norm TEXT PRIMARY KEY,
    display_name TEXT,
    category TEXT,
    mention_count INTEGER DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    sample_signals_json TEXT DEFAULT '[]',  -- max 3 signal IDs
    expires_at TEXT NOT NULL               -- first_seen + 30 days
);

-- 实体事件
CREATE TABLE entity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entity_profiles(id),
    signal_id INTEGER REFERENCES signals(id),
    date TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- release|paper|discussion|price_change|practice_commit|practice_session|practice_note
    role TEXT DEFAULT 'subject',  -- subject|comparison|dependency|practice_target
    impact TEXT DEFAULT 'medium' CHECK(impact IN ('high','medium','low')),
    confidence REAL DEFAULT 0.8,
    description TEXT DEFAULT '',
    metadata_json TEXT DEFAULT '{}'
);
CREATE INDEX idx_entity_events_entity ON entity_events(entity_id, date);
CREATE INDEX idx_entity_events_date ON entity_events(date);

-- 实体全文搜索
CREATE VIRTUAL TABLE entity_search USING fts5(
    canonical_name, display_name, summary,
    content=entity_profiles, content_rowid=id
);
```

**注意: v1 不建 entity_relations 表。** 实体间关联通过现有 cross_links 表间接反映。

### 5.2 Entity Link Pipeline (`prism/pipeline/entity_link.py`)

```python
def run_entity_link(conn, date, llm_client):
    """entity_link: analyze 之后, trends 之前"""

    # 1. 加载当天 signals
    signals = load_current_signals(conn, date)

    # 2. 加载已知实体 (含 aliases)
    known_entities = load_known_entities(conn)

    # 3. 逐 signal 提取
    for signal in signals:
        # Step A: 确定性候选提取 (代码, 不依赖 LLM)
        candidates = deterministic_candidates(signal)
        # → repo URLs, owner/repo, @handles, tags_json, Title-case spans

        # Step B: 27B 选择+分类 (LLM)
        llm_out = extract_entities(
            signal=signal,
            candidates=candidates,
            known_entities=subset_by_candidates(known_entities, candidates),
            llm_client=llm_client
        )

        # Step C: 归一化 + 解析
        for ent in llm_out.get("entities", []):
            alias_norm = normalize(ent["mention"])
            match = resolve(alias_norm, ent["category"], known_entities)

            if match:
                upsert_alias(conn, match.id, ent["mention"], alias_norm)
                insert_event(conn, match.id, signal, ent)
            elif is_promotable(ent):
                new_id = create_profile(conn, ent)
                upsert_alias(conn, new_id, ent["mention"], alias_norm)
                insert_event(conn, new_id, signal, ent)
                known_entities = reload_known_entities(conn)  # refresh cache
            else:
                stage_candidate(conn, ent, signal)

    # 4. 过期候选清理
    expire_candidates(conn)

    # 5. 候选晋升检查
    promote_ready_candidates(conn)

    # 6. 更新 lifecycle 指标
    update_lifecycle_scores(conn, date)

    # 7. 更新状态 (带迟滞)
    update_entity_statuses(conn)
```

### 5.3 LLM Prompt (27B Entity Extraction)

```
System:
You are Prism entity linker.
Extract only persistent, trackable entities from this signal.
Categories: person, org, project, model, technique, dataset.
REJECT: broad themes (AI, machine learning), file names, commit hashes, PR numbers, generic nouns.
Return 0-5 entities. Prefer matching KNOWN entities. Max 2 brand-new entities.

User:
DATE: {date}
SIGNAL:
- topic: {topic_label}
- summary: {summary}
- why_it_matters: {why_it_matters}
- tags: {tags_json}
- sources: {source_types}

CANDIDATES (from text analysis):
{deterministic_candidates_list}

KNOWN ENTITIES (may match):
{known_entities_subset_json}

Return JSON:
{
  "entities": [
    {
      "mention": "exact text",
      "canonical_name": "normalized name",
      "matched_entity_id": 123 or null,
      "category": "project",
      "role": "subject|comparison|dependency|practice_target",
      "specificity": 1-5,
      "confidence": 0.0-1.0
    }
  ]
}
```

### 5.4 Normalization & Anti-Sprawl

**归一化链:**
```python
def normalize(text: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKC", text)
    s = s.casefold().strip()
    s = re.sub(r'[^\w\s-]', '', s)  # strip punctuation
    s = re.sub(r'\s+', ' ', s)      # collapse whitespace
    return s
```

**解析优先级:**
1. repo URL / GitHub link → 精确匹配
2. alias_norm 精确匹配
3. 同 category 模糊匹配 (Jaro-Winkler > 0.9)
4. 无匹配 → 检查 promotable 条件

**Promotable 条件 (创建新实体):**
- `confidence >= 0.8`
- `specificity >= 4`
- 以下任一:
  - 有 repo URL / GitHub link 佐证
  - 在 entity_candidates 中 `mention_count >= 3`
  - 有 1 个 practice event + 1 个 external signal

**Anti-sprawl 硬规则:**
- 每条 signal 最多 5 entities
- 每条 signal 最多 2 个全新实体
- specificity < 3 直接拒绝
- stoplist: common nouns, programming keywords, generic terms
- entity_candidates 30 天过期自动清理
- entity_profiles 90 天无事件 → 归档 (status=archived, 保留数据)

### 5.5 Lifecycle Algorithm

```python
def update_lifecycle_scores(conn, today):
    """每日更新 m7, m30, event counts"""
    for entity in all_active_entities(conn):
        events = get_events(conn, entity.id, days=60)
        m7, m30, prev30 = 0.0, 0.0, 0.0
        count_7d, count_30d = 0, 0

        for e in events:
            age = (today - e.date).days
            weight = IMPACT_WEIGHT[e.impact] * e.confidence
            if e.event_type.startswith("practice_"):
                weight *= 1.25  # practice boost

            m7 += weight * exp(-age / 7)
            m30 += weight * exp(-age / 30)
            if age <= 7: count_7d += 1
            if age <= 30: count_30d += 1
            if 31 <= age <= 60:
                prev30 += weight * exp(-(age - 30) / 30)

        update_scores(conn, entity.id, m7, m30, count_7d, count_30d)

IMPACT_WEIGHT = {"high": 3.0, "medium": 1.5, "low": 0.5}

def update_entity_statuses(conn):
    """带迟滞的状态转换"""
    for entity in all_active_entities(conn):
        age = days_since(entity.first_seen_at)
        baseline = max(1.0, entity.m30 / 4.3)  # 月均周化
        days_silent = days_since(entity.last_event_at)

        if age <= 14 and entity.event_count_total >= 2 and entity.m7_score >= 3:
            target = "emerging"
        elif entity.event_count_total >= 4 and entity.m7_score >= 1.5 * baseline:
            target = "growing"
        elif age >= 21 and entity.m30_score >= 8 and 0.67 <= entity.m7_score / baseline <= 1.5:
            target = "mature"
        elif age >= 21 and (days_silent > 14 or entity.m7_score < 0.5 * baseline):
            target = "declining"
        else:
            target = entity.status  # no change

        # 迟滞: 升级需 2 天确认, 降级需 3 天
        if should_transition(entity, target):
            set_status(conn, entity.id, target)
```

## 6. Briefing v2

### 6.1 增量 + 内嵌 Entity Context (每日)

每条 signal 下附 entity context:

```
🔬 vLLM 0.8 发布，speculative decoding 优化 30%
  → 建议评估是否升级推理服务
  📍 vLLM [growing, 本周第3条] | 🔗 你 3/25 在 omlx 测过 speculative decoding
```

**数据来源:**
```python
def enrich_signal_with_entity(signal, conn):
    events = get_entity_events_for_signal(conn, signal.id)
    for entity_id in events:
        profile = get_entity_profile(conn, entity_id)
        practice = get_recent_practice(conn, entity_id, days=14)
        signal.entity_context.append({
            "name": profile.display_name,
            "status": profile.status,
            "week_count": profile.event_count_7d,
            "practice_note": practice.description if practice else None
        })
```

### 6.2 Radar 摘要 (每日 briefing 末尾)

只在有变化时出现:

```
📡 Radar 变化
↑ 新进 growing: DeepSeek-V3
↗ 实践交叉: vLLM (你测过 + 外部新版)
↓ 趋于沉寂: RLHF (14天无新信号)
🆕 新发现: Claude Code (从 practice 自动识别)
```

**数据来源:**
```python
def generate_radar_changes(conn, today):
    changes = []
    # 状态变化
    for e in entities_with_status_change(conn, today):
        changes.append(f"↑ 新进 {e.status}: {e.display_name}")
    # 实践交叉
    for e in practice_overlap_entities(conn, days=7):
        changes.append(f"↗ 实践交叉: {e.display_name}")
    # 沉寂
    for e in newly_declining(conn, today):
        changes.append(f"↓ 趋于沉寂: {e.display_name}")
    # 新发现
    for e in newly_created(conn, today):
        changes.append(f"🆕 新发现: {e.display_name}")
    return changes if changes else None  # None = 不显示
```

### 6.3 周报 (周五 briefing 追加)

```
📊 本周 Radar 全景
- 活跃实体 Top 5: vLLM, DeepSeek-V3, Claude Code, MoE, SGLang
- 本周新进: 3 | 趋于沉寂: 2
- 实践交叉: 2 个实体同时有你的实践+外部动态
- 状态总览: emerging(4) growing(8) mature(12) declining(3)
```

## 7. Cold Start

1. **第 1 天**: 现有 entities.yaml 中的 project/org 自动导入为初始 entity_profiles
2. **第 1 周**: briefing 显示 "已追踪 N 个实体 (其中与您相关 X 个)" 提示系统在学习
3. **第 2 周**: entity_candidates 开始晋升，Radar 摘要开始有意义
4. **第 1 月**: lifecycle 指标稳定，全局画像成型

## 8. omlx Manager Requirements

### P0: 必须

1. **Batch Queue API**
   - `POST /v1/batch/submit` — 提交 N 个请求到队列
   - `GET /v1/batch/{id}/status` — 查询状态
   - `GET /v1/batch/{id}/results` — 获取结果
   - 用例: 凌晨提交 50-100 条信号处理任务

2. **Auto-load on Request**
   - 请求未加载模型时自动加载，不返回 500
   - 可配置: `manual | auto_load | auto_load_and_evict`

### P1: 强烈需要

3. **Priority Levels**
   - Header: `X-Priority: background | normal | urgent`
   - background (Prism) 在 urgent (Claude Code) 到来时让路

4. **Concurrent Decode ≥ 4** (≤30GB 模型)
   - 批处理吞吐 4x

### P2: 可选

5. **Scheduled Model Swap** — 指定时段加载特定模型
6. **Health Endpoint** — `/v1/health` 返回 loaded models, queue depth, memory
7. **Webhook** — batch 完成时回调

### 降级方案 (omlx 不升级)

Prism 自建 FIFO 队列:
- 凌晨 2am sequential requests to omlx chat completions API
- asyncio.Semaphore(2) 控制并发
- 50 signals × ~5s/each = ~4 min，可接受

## 9. File Structure (New/Modified)

```
prism/
├── sources/
│   ├── youtube.py          # NEW: YouTube transcript adapter
│   ├── hackernews.py       # NEW: HN /best RSS adapter
│   ├── github_releases.py  # NEW: GitHub org release adapter
│   ├── model_economics.py  # NEW: Artificial Analysis / OpenRouter
│   ├── git_practice.py     # NEW: Local git commit digests
│   ├── claude_sessions.py  # NEW: Claude Code memory scanner
│   └── practice_notes.py   # NEW: Manual CLI practice notes
├── pipeline/
│   ├── entity_link.py      # NEW: Entity extraction + linking
│   ├── entity_lifecycle.py # NEW: Lifecycle scoring + status
│   └── entities.py         # MODIFIED: Migrate from YAML to DB
├── output/
│   ├── briefing.py         # MODIFIED: Add entity context + radar
│   └── templates/
│       └── briefing.html.j2 # MODIFIED: Entity sections
├── db.py                   # MODIFIED: New tables
├── cli.py                  # MODIFIED: `prism practice` command
└── models.py               # MODIFIED: Entity dataclasses
config/
├── sources.yaml            # MODIFIED: New source entries
└── entities.yaml           # DEPRECATED: Migrated to DB on first run
```

## 10. Migration: entities.yaml → DB

首次运行 entity_link 时自动迁移:

```python
def migrate_entities_yaml(conn, yaml_path):
    """一次性: 将 entities.yaml 导入 entity_profiles"""
    data = yaml.safe_load(yaml_path.read_text()) or {}
    category_map = {"project": "project", "org": "org", "person": "person"}
    for cat, entries in data.items():
        db_cat = category_map.get(cat, cat)
        for entry in entries:
            name = entry["name"] if isinstance(entry, dict) else entry
            # 跳过已存在的
            if entity_exists(conn, normalize(name)):
                continue
            create_profile(conn, name=name, category=db_cat, status="mature",
                          needs_review=0, source="yaml_migration")
    # 迁移完成后 entities.yaml 保留但不再读取
```

迁移后 `pipeline/entities.py` 的 `tag_entities()` 改为从 DB 读取 entity_aliases，保持对 clustering 的兼容。

## 11. Acceptance Criteria

1. [ ] `prism sync` 能成功采集 YouTube transcript, HN, GitHub releases
2. [ ] `prism entity-link` 能从 signals 中自动提取实体，归一化存入 entity_profiles
3. [ ] entity_candidates 表正确缓冲低置信度实体，30 天过期
4. [ ] alias 归一化能正确合并 "vllm" / "vLLM" / "vLLM-project"
5. [ ] practice 数据 (git + Claude Code + manual) 能通过标准 pipeline 产生 entity_events
6. [ ] 每条 briefing signal 附带 entity context (实体名 + 状态 + 实践关联)
7. [ ] briefing 末尾有 Radar 摘要 (状态变化 + 新发现 + 实践交叉)
8. [ ] lifecycle 指标每日自动更新，状态转换带迟滞防抖
9. [ ] 本地 27B 能在 <10 分钟内处理一天的 signals (50-100 条)
10. [ ] entity_profiles 数量在 3 个月后 < 500 (anti-sprawl 有效)
