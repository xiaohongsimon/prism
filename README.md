<p align="center">
  <img src="prism/web/static/icon-192.png" width="80" alt="Prism">
</p>

<h1 align="center">Prism</h1>

<p align="center">
  <b>AI 驱动的个人推荐引擎 — 用 Pairwise Comparison 学你的品味</b>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#核心理念">核心理念</a> ·
  <a href="#架构">架构</a> ·
  <a href="#功能">功能</a> ·
  <a href="#路线图">路线图</a>
</p>

---

## 为什么做 Prism？

AI 从业者每天被淹没在 X/HN/arXiv/YouTube/GitHub 的信息洪流里。现有方案要么是 RSS 全量推送（太多），要么是算法黑箱推荐（不透明）。

**Prism 的做法不一样**：AI 先把信息读完、聚类、摘要，然后每次只给你看 **两条信号**，你选更感兴趣的那个。每一次选择都是一个训练信号，系统从中学习你的偏好，动态调整"去哪找"和"怎么排"。

```
┌─────────────┐     ┌─────────────┐
│   信号 A     │     │   信号 B     │
│ Claude 4.6   │ VS  │ Llama 4 开源 │
│ 发布新推理模型 │     │ 405B 权重    │
└──────┬──────┘     └──────┬──────┘
       │    你选了 A → ELO 更新    │
       └───────────┬───────────┘
                   ▼
         偏好模型持续进化
```

## 核心理念

> **信息应该被学习，而非推荐。**

- **不猜你想看什么** — 从真实的 pairwise 选择中学习，不靠用户画像
- **透明可追溯** — 所有自动决策记录在 Decision Log，支持回溯
- **自演化** — 源权重根据胜率动态调整，无需手动配置
- **单人使用** — 不做 SaaS，跑在你自己的机器上，数据完全属于你

## 功能

- **Pairwise 对比 UI** — 每次两条信号，选择/跳过/都要/都不要，可附文字反馈
- **10+ 信号源** — X、Hacker News、arXiv、YouTube、GitHub Trending/Releases、Reddit、Product Hunt 等
- **Bradley-Terry ELO 评分** — 经典偏好模型，<50 行 Python 实现
- **多维偏好向量** — topic/source/author 权重从 pairwise 同步更新
- **动态源权重** — 每个源根据其信号的胜率自动调整采集频率，零 LLM 开销
- **外部投喂** — 随时投喂链接/话题作为强正反馈（权重 3x）
- **LLM 分析** — 自动聚类、摘要、中文翻译、信号分层（actionable/strategic/noise）
- **每日简报** — 自动生成全局概览 + Notion 发布
- **292 个测试** — pytest 覆盖核心逻辑

## 架构

```
信号源 (X/HN/arXiv/...)          用户
        │                         │
        ▼                         ▼
   ┌─────────┐             ┌──────────┐
   │  召回层   │             │ Pairwise │
   │ "去哪找"  │             │   UI     │
   │          │             │          │
   │ sync →   │             │ 选择 A/B  │
   │ cluster →│◄────────────│ 文字反馈  │
   │ analyze  │  ELO 更新    │ 外部投喂  │
   └────┬─────┘             └──────────┘
        │                         │
        ▼                         ▼
   ┌─────────┐             ┌──────────┐
   │  排序层   │             │ Decision │
   │ "怎么排"  │             │   Log    │
   │          │             │          │
   │ BT-ELO   │             │ 追溯所有  │
   │ 多维权重  │             │ 自动决策  │
   └──────────┘             └──────────┘
```

### 两层闭环

| 层 | 职责 | 实现 |
|---|---|---|
| **召回层** | 从哪找信息 | 源适配器 + 源权重动态调整（Phase 1: 规则调整，Phase 2: LLM 推荐，Phase 3: 自动试运行） |
| **排序层** | 怎么排序 | Bradley-Terry ELO + 多维权重 + 探索策略（70% 高分+不确定 / 20% 双新 / 10% 随机） |

### 反馈权重

| 信号类型 | 权重 | 说明 |
|---------|------|------|
| 外部投喂链接 | 3.0 | 最强正反馈 |
| 保存/星标 | 2.0 | 明确喜欢 |
| Pairwise 选择 | 1.0 | 标准对比信号 |
| 两个都行 | 0.3 | 弱正信号 |
| 两个都不要 | -0.5 | 负反馈 |

## 技术栈

| 组件 | 选型 | 理由 |
|------|------|------|
| 后端 | FastAPI + Uvicorn | 异步、轻量、适合单人使用 |
| 数据库 | SQLite | 零运维、单文件、够用 |
| 前端 | Jinja2 + HTMX + Vanilla CSS | 无构建工具、服务端渲染、PWA 支持 |
| CLI | Click | 标准 Python CLI 框架 |
| LLM | OpenAI 兼容 API | 本地 omlx / 云端 API 均可 |
| 测试 | pytest + pytest-asyncio | 292 个测试，覆盖核心逻辑 |

## 快速开始

### 前置要求

- Python 3.10+
- OpenAI 兼容的 LLM API（如阿里云百炼、本地 Ollama/omlx）

### 安装

```bash
git clone https://github.com/xiaohongsimon/prism.git
cd prism
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 配置

```bash
cp .env.example .env
```

编辑 `.env`：

```env
# LLM API（必填）
PRISM_LLM_BASE_URL=https://your-api-endpoint.com/v1
PRISM_LLM_API_KEY=your-api-key
PRISM_LLM_MODEL=qwen-plus          # 主分析模型
PRISM_LLM_CHEAP_MODEL=qwen-turbo   # 轻量任务模型

# 可选
PRISM_ADMIN_PASSWORD=your-password  # Web UI 登录密码
NOTION_API_KEY=                     # Notion 发布
```

### 启动

```bash
# 同步信号源
prism sync

# 聚类 + 分析
prism cluster
prism analyze --incremental

# 启动 Web UI
prism serve --port 8080
```

打开 http://localhost:8080 开始 Pairwise 对比。

## 常用命令

```bash
prism sync                      # 从所有源同步
prism sync --source x           # 只同步 X/Twitter
prism cluster                   # 聚类新内容
prism analyze --incremental     # 增量 LLM 分析
prism analyze --daily           # 每日全局分析 + 简报叙述
prism generate-slides --limit 50 # 生成信号卡片
prism briefing --save           # 生成并保存每日简报
prism publish --notion          # 发布到 Notion
prism source list               # 查看源状态
prism serve --port 8080         # 启动 Web 服务
```

## 项目结构

```
prism/
├── cli.py                 # 命令行入口
├── config.py              # 配置管理（.env + YAML）
├── db.py                  # SQLite schema + 迁移
├── models.py              # 数据模型
├── source_manager.py      # 源生命周期管理
├── pipeline/
│   ├── sync.py            # 信号同步（含 429 退避）
│   ├── cluster.py         # 聚类（bigram Jaccard + 关键词）
│   ├── analyze.py         # LLM 分析 + 叙述生成
│   └── llm.py             # LLM 调用封装
├── sources/               # 17 个信号源适配器
│   ├── base.py            # SourceAdapter 协议
│   ├── x.py               # X / Twitter
│   ├── hn.py              # Hacker News
│   ├── arxiv.py           # arXiv RSS
│   ├── youtube.py         # YouTube
│   └── ...
├── web/
│   ├── routes.py          # FastAPI 路由
│   ├── pairwise.py        # Pairwise 配对 + ELO 更新
│   ├── ranking.py         # 排序 + 偏好阻断
│   ├── auth.py            # 邀请码 + Session 认证
│   └── templates/         # Jinja2 模板
├── output/
│   ├── briefing.py        # 每日简报生成
│   └── notion.py          # Notion 发布
└── scheduling/            # macOS launchd 调度配置
```

## 自定义信号源

编辑 `config/sources.yaml` 添加新源：

```yaml
sources:
  - type: x
    key: "x:your_handle"
    handle: your_handle
    display_name: "Your Handle"

  - type: hackernews
    key: "hn:front"
    min_score: 50
```

实现新的源适配器：

```python
# prism/sources/my_source.py
from prism.sources.base import SourceAdapter, SyncResult
from prism.models import RawItem

class MySourceAdapter(SourceAdapter):
    async def sync(self, config: dict) -> SyncResult:
        items = []  # fetch your data
        return SyncResult(
            source_key=config["key"],
            items=items,
            success=True,
        )
```

## 路线图

### v0.1 Alpha（当前）
- [x] Pairwise 对比 UI
- [x] Bradley-Terry ELO 评分
- [x] 10+ 信号源适配器
- [x] 源权重动态调整
- [x] LLM 聚类分析 + 中文翻译
- [x] 每日简报 + Notion 发布
- [x] Decision Log
- [ ] 用户测试反馈收集

### v0.2 Beta
- [ ] LLM 偏好分析 → 自动推荐新源
- [ ] 批量排序模式（拖拽 4-6 条）
- [ ] 偏好 Profile 可视化
- [ ] 公网部署 + 邀请码系统

### v1.0
- [ ] 自动源发现 + 试运行
- [ ] 多设备同步
- [ ] API 开放

## 贡献

欢迎 PR 和 Issue！特别欢迎：

- 新信号源适配器（尤其是中文社区：即刻、知乎、微信公众号等）
- 算法优化（探索策略、冷启动）
- UI/UX 改进
- 文档和翻译

## License

[MIT](LICENSE)
