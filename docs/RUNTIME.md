# Prism RUNTIME — 活文档

> 本文档是**当前真实在跑的系统**的单一 source of truth。排查问题从这里开始。
> 改动架构（加/砍 feature、改调度、换依赖）**必须**同步更新本文件。
> `docs/specs/` 存意图（该怎么做），本文件存现状（实际在做什么）。
>
> Last updated: 2026-04-21

---

## TL;DR — 半夜 3 点网站挂了看这里

1. `curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/` — 本地服务
2. `launchctl print gui/$(id -u)/com.prism.web | grep -E "state|last exit"` — launchd 状态
3. `tail -30 data/web.err` — 最近的 Python 异常
4. `curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8002/v1/models` — omlx LLM 后端
5. `launchctl kickstart -k gui/$(id -u)/com.prism.web` — 重启 web

常见挂法 → 见末尾 **Tripwires** 表。

---

## Runtime Topology

```
  Internet
     ↓
  Cloudflare (prism.simon-ai.net)
     ↓  cloudflared tunnel  ← com.cloudflare.prism-tunnel (launchd)
  127.0.0.1:8080
     ↓
  uvicorn (prism serve)  ← com.prism.web (launchd, KeepAlive=true)
     │
     ├─ FastAPI app  (prism/api/app.py)
     │    ├─ /api/*           → prism/api/routes.py
     │    └─ /*               → prism/web/routes.py
     │
     └─ SQLite @ data/prism.sqlite3   (单连接，全 schema 在 prism/db.py::init_db)

  Scheduled jobs (launchd)
     ├─ com.prism.hourly   every 1h   → scheduling/hourly.sh
     ├─ com.prism.fast     every 3h   → scheduling/fast.sh
     └─ com.prism.daily    06:00      → scheduling/daily.sh

  External deps
     ├─ omlx backend @ 127.0.0.1:8002   (需 auth)
     ├─ omlx gateway @ 127.0.0.1:8003   (不稳，尽量直连 8002)
     ├─ bird CLI                         (X 抓取，需 HTTPS_PROXY + NODE_USE_ENV_PROXY=1)
     └─ yt-dlp / youtube-transcript-api  (YouTube)
```

---

## Live Features (生产在跑)

### Web UI (users care about these)
| 路由 | 模块 | 说明 |
|---|---|---|
| `GET /` → redirect `/feed` | routes.py:286 | 入口 |
| `GET /feed`, `/feed/following`, `/feed/saved`, `/feed/more` | routes.py | 主 feed (pairwise + CTR ranking) |
| `POST /feed/action`, `/feed/click`, `/feedback` | routes.py | 用户反馈闭环 |
| `GET /creator/{source_key}`, `/channel/{source_key}` | routes.py | Creator/Channel 主页 |
| `POST /channel/.../follow`, `.../unfollow` | routes.py | 关注管理 |
| `GET /article/{id}` + like/unlike | routes.py | 详情页 |
| `GET /translate/{item_id}` | routes.py | 单条翻译 |
| `GET /briefing` | routes.py | 每日简报 |
| `GET /board` | web/board.py | Board 视图（新增） |
| `GET /showcase`, `/quality`, `/decisions/weekly` | routes.py | 展示/健康/决策日志 |
| `GET /pairwise/liked`, `/pairwise/sources`, `/pairwise/profile` | routes.py | Pairwise 管理 |
| `GET /login`, `/register`, `/auth/*` | routes.py | 认证 |
| `GET /sw.js` | routes.py:950 | Service worker |

### API (`/api/*`)
`/signals`, `/trends`, `/clusters/{id}`, `/briefing`, `/search`, `/sources` CRUD (api/routes.py)

### Pipeline (CLI-invoked; no daemon besides web)
`sync → expand-links → cluster → analyze (triage/expand/incremental/daily) → articlize → trends → briefing → publish → quality-scan → translate-bodies → enrich-youtube → sync-follows → publish-videos → cleanup`

### Source Adapters (prism/sources/)
arxiv · claude_sessions · follow_builders · git_practice · github · github_home · github_releases · hackernews · hn_search · producthunt · reddit · subtitles · x · x_home · xiaoyuzhou · youtube · youtube_home · model_economics · link_expander

### Scheduled Jobs
| Agent | 触发 | 做什么 | 日志 |
|---|---|---|---|
| `com.prism.web` | RunAtLoad, KeepAlive | uvicorn serve :8080 | `data/web.log` + `data/web.err` |
| `com.prism.hourly` | 每 1h + RunAtLoad | health + sync + expand-links + cluster + analyze (triage→expand) + quality-scan | `data/sync.log` |
| `com.prism.fast` | 每 3h | high-velocity sources (x/hn/reddit/ph) + translate + cluster + analyze --incremental + quality-scan | `data/sync.log` |
| `com.prism.daily` | 06:00 | full sync-follows + sync + expand + cluster + analyze (inc→daily) + articlize + trends + briefing + publish + cleanup + adjust source weights | `data/daily.log` |
| `com.cloudflare.prism-tunnel` | RunAtLoad | cloudflared 反代 prism.simon-ai.net → :8080 | (cloudflared 管理) |

Plist 位置：`~/Library/LaunchAgents/com.prism.*.plist`。

---

## Frozen / Deprecated (代码还在，但**不在用**)

| 模块 | 状态 | 说明 |
|---|---|---|
| `prism/pipeline/entity_*.py` (extract/link/lifecycle/normalize) + `entities.py` | **暂停** | 2026-03-29 entity system spec 冻结；代码保留但 CLI 入口还在 (`entity-link`)。重启前先读 `docs/specs/2026-03-29-prism-v2-entity-system.md` |
| `prism/pipeline/xyz_queue.py` | **实验** | Xiaoyuzhou 批量队列探索，当前策略是人肉单条加（见 memory `feedback_xiaoyuzhou_on_demand`） |
| Bradley-Terry/Elo 排序 | **放弃** | 2026-04-01 辩论后确认排序层 = CTR (skip-above)，不用 BT。`signal_scores` 表里 `bt_score` 字段保留但只是 fallback 默认值 1500.0 |
| LLM 源发现（Phase 2/3） | **未启动** | CLAUDE.md 写的三阶段动态召回只实现了 Phase 1 (规则层源权重调整) |

### Recently Removed (最近砍掉的)
| 日期 | 模块 | 原因 |
|---|---|---|
| 2026-04-21 | **Slides 生成** (`prism/web/slides.py` + `signal_slides` 表 + `generate-slides` CLI + 后台 worker + 4 个调度调用) | horse-race 时代遗留；UI 早已下线但生成管线仍在烧算力 + 污染 25MB web.err |

---

## Tripwires (已知坑 + 排查手册)

| 症状 | 根因 | 修法 |
|---|---|---|
| 公网 502 / 本地 :8080 不通 | `com.prism.web` exit != 0。常见：import 错误（新依赖没装） | `tail data/web.err` 看 traceback；`uv add <missing>`；`launchctl kickstart -k gui/$(id -u)/com.prism.web` |
| launchd `last exit = 78 (EX_CONFIG)` | Python import 失败 | 同上 |
| launchd `last exit = 127` | bash 找不到 `prism` — `.venv/bin/prism` 没装 | `uv sync` 或 `uv pip install -e .` 重装项目 |
| `data/web.err` 无限增长 | 日志 append 模式不轮转 | `: > data/web.err` 手动清；长期方案：加 logrotate |
| `Slides generation failed` 刷屏 | **不应再出现**（2026-04-21 已删） | 若再看到 → 说明某分支回带了 slides，优先合并本 main |
| `OMLX unreachable: Connection refused` | omlx 后端 8002 挂了，或 prism + Claude Code 并发把 8002 打到 503 | `curl http://127.0.0.1:8002/v1/models` 确认；让 analyze/articlize 错峰 |
| `bird` 抓 X 失败 | 少了代理环境变量 | `NODE_USE_ENV_PROXY=1` + `HTTPS_PROXY=...`，x_cookies.env 也要 source |
| LLM 返回 `<think>...</think>` 前缀乱了 | reasoning 模型输出未清洗 | `prism/pipeline/llm.py` 已处理；如果新模型又坏了，改这里 |
| `sources.yaml` 权重被自动改 | `daily.sh` 末尾 `adjust_source_weights` 会根据胜率改权重 | 正常行为；若不想改，注释 daily.sh 最后那段 |

---

## Data & State

- **SQLite**: `data/prism.sqlite3` — 唯一业务 DB，schema 全在 `prism/db.py::init_db()`
- **YAML**: `config/sources.yaml` — 信号源配置的权威；DB `sources` 表只是运行时镜像（启动时 reconcile）
- **Env**: `.env`（PRISM_DB_PATH, PRISM_ADMIN_PASSWORD, PRISM_LLM_MODEL, etc.）
- **X cookies**: `~/.config/prism/x_cookies.env`（gitignore，bird 需要）
- **Logs**: `data/web.{log,err}`, `data/sync.log`, `data/daily.log`, `data/launchd-*.{log,err}`

---

## How to Extend

- 加新源：`prism/sources/<name>.py` 实现 SourceAdapter 协议 + `config/sources.yaml` 注册
- 加新 CLI：`prism/cli.py` 加 `@cli.command()`；如需定时，改对应 `scheduling/*.sh`
- 加新 web 路由：`prism/web/routes.py`；如是公开路径，加到 `_PUBLIC_PATHS`
- 砍功能：**先在本文件 Deprecated 段登记，再动代码**；砍完从 Live 移到 Recently Removed

---

## Reference

- 架构意图：`CLAUDE.md`（两层闭环 + Decision Log）
- 设计历史：`docs/specs/` + `docs/reviews/synthesis/`
- Web 前端约定：无构建工具，Jinja2 + HTMX + vanilla CSS
- 测试：`pytest tests/`（单独跑时注意 8 个 pre-existing 失败是 auth/数据 setup 问题，与业务无关）
