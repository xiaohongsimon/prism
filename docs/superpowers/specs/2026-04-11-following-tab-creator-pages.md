# Spec: 关注 Tab 重构 — 创作者列表 + 创作者主页 + 视频转文章

> 日期: 2026-04-11
> 状态: Draft
> 范围: YouTube + X 博主

## 背景

当前"关注"tab 是一个混合 feed，把所有关注源的信号按排序混在一起展示。用户期望的体验是：
1. 关注 tab → 创作者头像/名字列表
2. 点击创作者 → 该博主的视频/推文卡片列表
3. 点击 YouTube 视频卡片 → 站内结构化文章（字幕转文章 + 高亮）
4. X 推文卡片直接展示推文内容，链接跳转源

## 现状问题

1. **YouTube 频道未拆分**: 8 个频道挤在 `youtube:ai-interviews` 一个 source_key 下，无法做每频道独立展示
2. **字幕获取不完整**: 仅在 body < 200 字符时才补充字幕，导致 144 条视频中 62 条 body 为空
3. **无文章格式内容**: signals 表存的是分析型信号（summary/why_it_matters），不是可读文章格式
4. **无创作者维度页面**: 缺少按创作者聚合的展示和导航

## 设计

### 1. 数据层

#### 1.1 YouTube Source 拆分

将 sources.yaml 从单一多频道 source 改为每频道一个独立 source：

```yaml
# 旧
- type: youtube
  key: "youtube:ai-interviews"
  channels: [UCGWYKICLOE8Wxy7q3eYXmPA, ...]

# 新
- type: youtube
  key: "youtube:bestpartners"
  channel_id: UCGWYKICLOE8Wxy7q3eYXmPA
  display_name: "最佳拍档"

- type: youtube
  key: "youtube:sunriches"
  channel_id: UCkHrq03gWLLx6vjS2DOJ8aA
  display_name: "孙行者"
  # ... 每个频道一条
```

YouTube adapter 相应改为单频道模式。

**迁移策略**: 根据 `raw_items.author` + `raw_items.raw_json` 中的 `channel_id` 将历史数据重新关联到新 source。旧 `youtube:ai-interviews` 标记为 `yaml_removed`。

#### 1.2 新增 articles 表

```sql
CREATE TABLE articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_item_id INTEGER UNIQUE REFERENCES raw_items(id),
    title TEXT NOT NULL,
    subtitle TEXT,              -- 一句话摘要
    structured_body TEXT,       -- Markdown 格式的结构化正文
    highlights_json TEXT,       -- JSON array of key quotes/insights
    word_count INTEGER,
    model_id TEXT,              -- 生成用的模型
    created_at TEXT DEFAULT (datetime('now'))
);
```

`structured_body` 是 LLM 生成的 Markdown，包含章节标题、核心要点（粗体标注）、关键引用（blockquote 格式）。

#### 1.3 字幕获取增强

修改 youtube.py：所有 YouTube 视频都尝试获取完整字幕，不再限制 body < 200 才补充。使用 youtube-transcript-api（快），yt-dlp 仅 fallback。

### 2. 视频转文章 Pipeline

#### 2.1 触发时机

在现有 pipeline 中新增步骤，位于 sync 之后：

```
sync → articlize → cluster → analyze
```

新增 CLI 命令 `prism articlize`。

处理条件：
- source type = youtube
- raw_items.body 有内容（字幕已获取）
- 对应 articles 记录不存在

#### 2.2 LLM Prompt

输入：视频标题 + 完整字幕文本

```
你是一个专业的内容编辑。将以下视频字幕转化为结构化文章。

要求：
1. 提取 3-5 个核心章节，每个章节有标题和正文
2. 用 **粗体** 标注关键洞察和数据点
3. 提取 3-5 条最有价值的原始引用（用 > 引用格式）
4. 写一句话摘要（subtitle）
5. 去除口语化填充词、重复内容、无关闲聊
6. 保留原始观点和论证逻辑，不要添加评论

输出 JSON:
{
  "subtitle": "一句话摘要",
  "body": "Markdown 正文",
  "highlights": ["关键引用1", "关键引用2", ...]
}
```

#### 2.3 处理策略

- **长视频（字幕 > 8000 字）**: 分段送 LLM，最后合并
- **无字幕视频**: 跳过，不生成 article
- **并发控制**: 串行处理，避免和 Claude Code 抢 omlx 资源
- **错误重试**: 失败记录日志，下次 articlize 时重试

### 3. Web UI — 三层页面

#### 3.1 关注 Tab 入口页 (`/?tab=follow`)

改造现有关注 tab，从混合 feed 变为创作者卡片网格。

**按源类型分组显示：**
- ▶ YouTube 频道 — 频道头像 + 名称 + 文章数 + 最新更新
- 𝕏 博主 — 头像 + handle + 推文数 + 最新更新

每个卡片可点击，进入创作者主页。

#### 3.2 创作者主页 (`/creator/{source_key}`)

**YouTube 创作者：**
- 顶部：头像 + 频道名 + [打开 YouTube 频道] 链接
- 列表：视频卡片，每张包含：
  - 视频标题
  - 一句话摘要（来自 articles.subtitle，未生成则显示 signals.summary 截断）
  - 发布时间 + 字数 + 状态（已转文章 / 字幕获取中）
  - 点击 → 进入文章详情页

**X 博主：**
- 顶部：头像 + 显示名 + handle + [打开 X 主页] 链接
- 列表：推文卡片，每张包含：
  - 推文正文
  - engagement 指标（如有）
  - 发布时间
  - 点击 → 跳转原始推文

#### 3.3 文章详情页 (`/article/{article_id}`)

仅 YouTube 视频。展示 articles 表的结构化内容：
- 标题 + subtitle
- 发布时间 + [观看原视频] 链接
- Markdown 正文渲染为 HTML（Jinja2 + markdown 库）
- 高亮引用用 `<mark>` 或 blockquote 样式
- 返回按钮回到创作者主页

### 4. 路由

```python
# 改造
GET /?tab=follow              → 创作者列表页（替换现有混合 feed）

# 新增
GET /creator/{source_key}     → 创作者主页
GET /article/{article_id}     → 文章详情页
```

### 5. 技术约束

- **前端**: Jinja2 + HTMX + vanilla CSS，不引入构建工具
- **Markdown 渲染**: 服务端用 `markdown` 或 `markdown-it` Python 库
- **导航**: HTMX partial + 浏览器 history API
- **头像**: YouTube 频道头像在 sources.yaml 中手动配置 `avatar` URL（从频道页复制）；X 继续用 `unavatar.io/x/{handle}`

### 6. X 博主数据补充

现有 X source 缺 `display_name`。在 sources.yaml 中补充 display_name 字段，头像继续用 `unavatar.io/x/{handle}`。

## 实现优先级

**Phase 1（核心路径）：**
1. YouTube source 拆分（yaml + adapter 改造 + 迁移脚本）
2. 字幕获取增强（所有视频获取完整字幕）
3. articles 表 + articlize pipeline
4. 关注 tab 改造为创作者列表
5. 创作者主页（视频/推文列表）
6. 文章详情页

**Phase 2（打磨）：**
7. X 博主 display_name 补充
8. 文章内搜索/高亮
9. 未读标记（新视频提示）

## 不做

- 不做自动发现新频道/博主（Phase 2+ 的动态召回）
- 不做视频内嵌播放
- 不做多用户
- 不做 X 推文的"转文章"（推文本身就短）
