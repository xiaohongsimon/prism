# Brief: Prism v2 实体系统设计辩论

## 背景

Prism 是一个个人 AI 情报系统，当前架构: sync → cluster → analyze → trends → briefing。
v2 要在 analyze 之后插入 `entity_link` 步骤，建立持久化的知识图谱。

## 已确认的设计约束

1. **架构**: 增量演进，不重写，在现有 pipeline 插入 entity_link 步骤
2. **实体管理**: 全自动 LLM 提取，无手动种子
3. **已实践技术点**: 混合采集 (git + Claude Code 自动 + CLI 手动)
4. **LLM**: 本地 27B 为主 (凌晨跑)，云端备用
5. **受众**: 纯个人
6. **时效**: 天级

## 现有相关基础设施

- `entities.yaml`: 手动维护的平铺列表 (project: [vLLM, SGLang...], org: [OpenAI...], person: [])
- `pipeline/entities.py`: 基于子串匹配的 entity tagging，用于 clustering，不持久化
- `signals` 表: 含 tags_json、summary、signal_layer、action 等 LLM 分析结果
- `clusters` 表: 含 topic_label、merged_context
- SQLite + FTS5

## 提议的 DB Schema

```sql
entity_profiles (
    id, name UNIQUE, category, status, summary,
    first_seen_at, last_event_at, event_count, metadata_json
)
entity_events (
    id, entity_id FK, date, signal_id FK, event_type, description, impact
)
entity_relations (
    entity_a_id, entity_b_id, relation_type, strength, last_seen_at
)
```

## 需要辩论的核心问题

### Q1: 实体粒度
"AI" 太粗，"PagedAttention v3 的 CUDA kernel" 太细。合适的粒度在哪里？
- 是固定几个层级 (领域 → 方向 → 技术 → 项目)？
- 还是让 LLM 自由提取，系统自动聚合？
- 怎么防止实体爆炸 (entity sprawl)?

### Q2: 27B 模型能可靠提取实体吗？
- 自动提取的实体名不一致怎么办？("vllm" vs "vLLM" vs "vLLM-project")
- 27B 能理解实体之间的关系吗？还是只能做简单标签？
- 需要什么后处理来保证质量？

### Q3: 实体生命周期
- 怎么检测 emerging → growing → mature → declining？
- 什么信号触发状态变化？(event频率？信号强度？时间衰减？)
- 过时实体怎么处理？自动归档？

### Q4: 全局画像怎么生成？
- 10-15 个活跃实体时好处理，100+ 个时怎么办？
- "全局画像变化"这段 briefing 内容具体应该包含什么？
- 周度复盘 vs 日度快照的信息差异应该是什么？

### Q5: 已实践技术点怎么关联？
- git commit → 实体的映射怎么做？commit message 太短，diff 太长
- Claude Code 会话历史 → 实体的映射怎么做？
- 实践记录和外部信号怎么交叉分析？
  - 例："你上周在 omlx 上测了 speculative decoding，今天 vLLM 发布了相关优化"
