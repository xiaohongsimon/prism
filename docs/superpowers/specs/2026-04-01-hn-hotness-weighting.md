# HN 热度加权设计

> 让 HackerNews 高分帖在 Web Feed 中获得与其社区热度匹配的排名

## 问题

当前 HN adapter 通过 hnrss.org/best RSS 采集，只拿到 title/link/pubDate，没有 points 和 comments 数据。一条 500+ points 的爆款帖和一条 50 points 的普通帖在排序时权重相同，导致重大事件（如 Claude Code 源码泄露）可能被淹没。

## 方案：A+B 组合

### Part A: HN Adapter Enrichment

**改动文件**: `prism/sources/hackernews.py`

RSS 解析完成后，批量调用 HN Algolia API 补充热度数据：

1. 从 hnrss.org RSS 的 `<comments>` 标签或 `<link>` 中提取 HN item ID
   - hnrss.org 的 RSS item 包含 `<comments>https://news.ycombinator.com/item?id=XXXXX</comments>`
   - 用正则 `item\?id=(\d+)` 提取 ID
2. 批量请求 `http://hn.algolia.com/api/v1/items/{id}` 获取 points 和 num_comments
   - 并发请求，单个超时 5s
   - 全部失败不阻塞：points/comments 设为 null
3. 数据存入 raw_json：
   ```json
   {
     "title": "...",
     "link": "...",
     "pubDate": "...",
     "hn_id": 12345,
     "hn_points": 523,
     "hn_comments": 187
   }
   ```

**容错**:
- Algolia API 不可达 → 跳过 enrichment，raw_json 不含 hn_points 字段
- 单个 item API 失败 → 该 item hn_points 设为 null
- 不影响现有同步流程的成功/失败判定

### Part B: Ranking Boost

**改动文件**: `prism/web/ranking.py`

在 `compute_feed()` 计算完基础 score 后，对含 HN 源的 cluster 追加热度 boost。

**数据获取**:
- 在已有的 `source_rows` 查询中扩展，额外 JOIN raw_items.raw_json
- 对 source_key 以 `hn:` 开头的 raw_items，解析 raw_json 提取 hn_points
- 每个 cluster 取其关联 HN items 的 max(hn_points)

**Boost 公式**:
```
hn_points_max = max hn_points across cluster's HN raw_items (default 0)
hn_boost = min(hn_points_max / 500, 1.0) * HN_BOOST_CAP
```

- `HN_BOOST_CAP = 0.15` — 可调常量
- 500 points 封顶归一化
- 最终 score: `w_heat * heat_norm + w_pref * pref + w_decay * decay + hn_boost`

**Tab 限制**:
- `hot` tab: 应用 boost（热度优先）
- `recommend` tab: 应用 boost（帮助发现重要事件）
- `follow` tab: 不应用（个人订阅为主，不需要外部热度干预）

### 效果示例

| HN Points | hn_boost | 效果 |
|-----------|----------|------|
| 50        | 0.015    | 微弱提升 |
| 200       | 0.06     | 明显提升 |
| 500+      | 0.15     | 最大提升，配合 signal_strength=4 大概率进前 5 |

## 不做的事

- **不改 LLM 分析逻辑** — signal_strength 仍由 LLM 判断，不注入 points 上下文
- **不做历史 backfill** — 只对新同步的 HN items 生效
- **不做通用 "外部热度" 抽象** — 仅针对 HN，未来如需其他平台可扩展

## 测试策略

- `test_parse_hn_rss`: 验证 RSS 解析含 hn_id 提取
- `test_hn_enrichment`: mock Algolia API，验证 points/comments 写入 raw_json
- `test_hn_enrichment_failure`: Algolia 不可达时正常降级
- `test_ranking_hn_boost`: 构造含不同 hn_points 的 cluster，验证排序变化
- `test_ranking_follow_no_boost`: follow tab 不应用 hn_boost

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `prism/sources/hackernews.py` | 添加 Algolia enrichment 逻辑 |
| `prism/web/ranking.py` | 添加 HN boost 计算 |
| `tests/sources/test_hackernews.py` | enrichment 测试 |
| `tests/web/test_ranking.py` | boost 排序测试 |
