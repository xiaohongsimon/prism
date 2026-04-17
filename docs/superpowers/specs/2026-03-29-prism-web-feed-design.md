# Prism Web Feed — 私人 AI 新闻平台

**Date:** 2026-03-29
**Status:** Approved

## 目标

将 Prism 从"每日 Notion 报告"升级为一个本地常驻 Web 服务，提供 X 风格的信息流体验。核心价值：

1. **快速消费**：统一排序的 Feed，言简意赅，吸引注意力
2. **个性化排序**：通过显式反馈（like/dislike/save）学习偏好，逐步优化推荐
3. **频道管理**：支持跳转到来源频道、关注/取消关注

## 技术选型

**FastAPI + Jinja2 + HTMX + vanilla CSS**

- 零构建工具，复用现有技术栈
- HTMX 实现无刷新加载、无限滚动、反馈提交
- 暗色主题，X/Twitter 布局语言

## UI 设计

### 主界面

- **顶部 Tab 栏**：推荐 / 关注 / 热门
- **Feed 卡片**（X 风格）：
  - 左侧：信号类型图标（🔥热门 / 📦发布 / 📄论文）
  - 右侧内容区：
    - 标题（粗体）
    - 元信息行：热度 · 时间 · 来源数 · 信号层级标签（actionable=蓝 / strategic=绿 / paper=橙）
    - 摘要正文（2-3 行）
    - 来源频道标签（蓝色 pill，可点击跳转到频道页）
    - 底部操作栏：👍 👎 ⭐ 🔗
- **无限滚动**：HTMX `hx-trigger="revealed"` 加载下一页

### 频道页

- 点击来源标签 → 筛选该来源的所有 signal
- 顶部显示频道信息 + "取消关注" 按钮

### 暗色主题

- 背景 #000，卡片分隔线 #2f3336，正文 #e7e9ea，次要文字 #71767b
- 参考 X 的配色和间距

## 排序算法

### 综合分数

```
score = w_heat × norm(heat_score)
      + w_pref × preference_score
      + time_decay(published_at)
```

### 各因子

**heat_score（已有）**
- `signal_strength × item_count`，来自 trends 表
- 归一化到 0-1（除以当日最大值）

**preference_score（新增）**
- 四个维度：source、tag、entity、signal_layer
- 每个维度维护 `(key → weight)` 字典，存 preference_weights 表
- 反馈更新规则：
  - like → 关联 key 各 +1.0
  - dislike → 关联 key 各 -1.0
  - save → 关联 key 各 +2.0
- 一条 signal 的 preference_score = 命中的所有 weight 求和，sigmoid 归一化到 0-1
- 冷启动：初始 weight 全 0，preference_score = 0.5（中性），不影响排序

**time_decay**
- `e^(-age_hours / half_life)`，half_life = 24h

### Tab 排序策略

| Tab | 公式 | 数据范围 |
|-----|------|---------|
| 推荐 | heat(0.4) + pref(0.4) + decay(0.2) | 全部 signals |
| 关注 | pref(0.5) + decay(0.3) + heat(0.2) | 仅 enabled 来源 |
| 热门 | heat(0.6) + decay(0.4) | 全部 signals |

权重为初始值，后续可调。

## 数据模型

### 新增表

```sql
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL REFERENCES signals(id),
    action TEXT NOT NULL CHECK(action IN ('like', 'dislike', 'save')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
CREATE INDEX idx_feedback_signal ON feedback(signal_id);

CREATE TABLE IF NOT EXISTS preference_weights (
    dimension TEXT NOT NULL,  -- 'source' / 'tag' / 'entity' / 'layer'
    key TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    PRIMARY KEY (dimension, key)
);
```

### 现有表变更

无。复用 `sources.enabled` 作为 follow/unfollow 状态。

## 路由设计

### 前端路由（新增 prism/web/routes.py）

| 方法 | 路径 | 用途 | 返回 |
|------|------|------|------|
| GET | `/` | 主页面 | 完整 HTML |
| GET | `/feed?tab=&page=&per_page=` | Feed 分页 | HTML 片段（HTMX） |
| POST | `/feedback` | 提交反馈 | HTML 片段（按钮状态更新） |
| GET | `/channel/{source_key}` | 频道页 | 完整 HTML |
| POST | `/channel/{source_key}/unfollow` | 取消关注 | HTMX 重定向 |
| POST | `/channel/{source_key}/follow` | 重新关注 | HTMX 重定向 |

### 现有 API 路由

`/api/*` 保持不变，不受影响。

## 文件结构

```
prism/web/
├── routes.py              # 前端路由
├── ranking.py             # 排序引擎
├── static/
│   └── style.css          # X 风格暗色主题
└── templates/
    ├── base.html           # 基础布局（head、导航）
    ├── feed.html           # 主页面（Tab + Feed 容器）
    ├── channel.html        # 频道页
    └── partials/
        ├── card.html       # 单条卡片片段
        └── card_actions.html  # 操作栏片段（反馈后局部更新）
```

## 开机自启动

新增 launchd plist：`com.prism.web.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.prism.web</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PROJECT_ROOT/.venv/bin/prism</string>
        <string>serve</string>
        <string>--port</string>
        <string>8000</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_ROOT</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$PROJECT_ROOT/data/web.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJECT_ROOT/data/web.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$PROJECT_ROOT/.venv/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

安装：`cp com.prism.web.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.prism.web.plist`

## 不在本次范围

- 多用户/认证（单人使用，无需登录）
- 推送通知
- 协同过滤（只有单用户，用不上）
- 替换现有 Notion 发布流程（并行保留）
- 替换现有 launchd hourly/daily 调度
