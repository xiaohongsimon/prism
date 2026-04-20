# Spec: X Follows Discovery (`prism sync-follows`)

> 2026-04-20 — 召回层 Phase 2 的第一个落地组件
> 用 `bird` CLI 把"用户在 X 上关注了谁"作为 prism 召回层的事实数据源

## Why

当前 `config/sources.yaml` 中 X handle 全靠手动维护（~30 个）。问题：
- 用户在 X 上关注/取关的动作不会自动反映到 prism
- 新发现一个值得追的账号 → 要手动编辑 yaml + 提交（高摩擦）
- 想取关 → 同样要手动改 yaml

**核心洞察**：用户在 X 上的"关注"行为本身就是一个**强偏好信号**（权重应 ≥ 外部投喂 3.0）。把这个信号用起来，召回层就从"静态配置"升级到"用户行为驱动"。

这是 CLAUDE.md 召回层 Phase 2 的最小可用形式：
- Phase 2 全貌 = "LLM 分析偏好 profile 生成候选源 + 用户确认"
- 本 spec = "用户在 X 上的关注列表 → 直接同步到 sources.yaml"（更轻、更直接、零 LLM）

## Non-Goals

- ❌ 不做 For You 流的全自动消费（fragile，留给 Phase 3）
- ❌ 不做"自动取关"（用户在 prism pairwise 里点一两次低分不应该影响 X 主账号）
- ❌ 不做关注列表的实时同步（每天一次足够）

## Scope

新增一个 CLI 子命令 `prism sync-follows`，行为如下：

1. 调 `bird following --all --json`，拿到当前用户关注的全部账号
2. 解析出 `{handle, display_name}` 列表
3. 与 `config/sources.yaml` 中现有的 `type: x` 条目 diff
4. **新增**：在 X 关注但 yaml 没有 → 提议添加（默认 dry-run）
5. **缺失**：yaml 有但 X 已取关 → **仅日志提示，不自动删除**（行为 1）
6. 所有决策写入 `decision_log` 表（layer=`recall`, action=`x_follow_added` / `x_follow_orphan`）

### CLI 接口

```
prism sync-follows                 # dry-run，仅打印 diff
prism sync-follows --apply         # 真正写入 sources.yaml
prism sync-follows --max-new 20    # 单次最多新增 N 个（防爆量）
prism sync-follows --depth tweet   # 新增条目的 depth 字段（默认 thread）
```

### bird 调用约束

- 必须传 `--all` 自动分页（关注数 > 20 是常态）
- 加 `--max-pages 50` 兜底防止失控
- subprocess 超时 90s
- 非零退出 → 优雅失败，输出"bird credential check failed, run `bird check`"提示，**不抛异常**（每日 cron 不能因这步挂掉）

### Cookie 缺失处理

`bird` 依赖 Safari/Chrome cookie 或 `AUTH_TOKEN`/`CT0` env。失效场景：
- Safari 沙箱：用户首次需在 macOS 设置里给 Terminal/launchd full disk access
- 长期失效：cookie 过期，需重新登录 x.com

我们的策略：
- 检测到 credential missing → 写一条 `decision_log` 类型 `x_follow_blocked` + reason
- CLI 输出明确的修复指引（哪三个选项）
- 不重试（瞎重试不会变好）

## Data Flow

```
bird following --all --json
        │
        ▼
  parse_follows() ─→ List[FollowEntry{handle, display_name, user_id}]
        │
        ▼
  diff against current sources.yaml (type: x entries)
        │
        ├─ to_add:    在 X 上关注，yaml 没有
        └─ orphans:   yaml 有，X 已取关
        │
        ▼ (--apply only)
  for each in to_add:
      add_source(conn, yaml, type="x", handle=h, config={"depth": "thread", "display_name": d})
      decision_log("recall", "x_follow_added", reason=f"auto-added @{h}", ...)
  for each in orphans:
      decision_log("recall", "x_follow_orphan", reason=f"@{h} unfollowed on X", ...)
      # 不删！只记录
```

## Schedule

挂在现有 `daily.sh` 中，**不开新 plist**：

```bash
# In daily.sh, 在 prism sync 之前
prism sync-follows --apply --max-new 30 >> "$LOG" 2>&1 || true
```

理由：
- 多一个 plist = 多一个失败点
- daily.sh 已在 8am 跑，sync-follows 在最前面跑可以让当天 sync 立刻拿到新关注的内容
- `|| true` 保证 bird 挂了不影响主链路

## File Layout

```
prism/discovery/
  __init__.py
  x_follows.py     # bird 调用 + diff 逻辑（核心）

prism/cli.py        # 新增 @cli.command("sync-follows")
docs/superpowers/specs/2026-04-20-x-follows-discovery.md  # 本文档
```

## Decision Log Conventions

| layer  | action               | reason 示例                           | context_json                    |
|--------|----------------------|---------------------------------------|---------------------------------|
| recall | x_follow_added       | auto-added @karpathy from X following | `{"handle": "karpathy", "user_id": "..."}` |
| recall | x_follow_orphan      | @swyx unfollowed on X                 | `{"handle": "swyx"}`            |
| recall | x_follow_blocked     | bird credentials missing              | `{"reason": "no cookies"}`      |
| recall | x_follow_scan        | scanned N follows, +M new, K orphans  | `{"total": N, "added": M, "orphan": K}` |

## Failure Modes & Mitigation

| 失败                          | 行为                                                   |
|-------------------------------|--------------------------------------------------------|
| `bird` not installed          | log + exit 0（daily 继续跑别的）                       |
| Cookie missing/expired        | log + 输出修复指引 + decision_log，exit 0              |
| bird 返回异常 JSON schema     | 防御式解析（多个 fallback 字段名），跳过坏条目           |
| 单次新增 > max_new            | 截断 + 输出"还有 X 个未导入，下次 cron 继续"             |
| sources.yaml 写失败           | rollback DB、重新抛出（这个不能静默）                  |
| handle 已存在（race）         | `add_source` 内部 `INSERT OR IGNORE`，无害              |

## Test Plan

只能写**离线单元测试**（bird 调用走 subprocess，DB 测试用 `:memory:`）：

1. `test_parse_follows_handles_minimal_schema` — 给定一个最小 bird JSON，提取出 handle 列表
2. `test_parse_follows_skips_malformed_entries` — 缺 `screen_name` 的条目应跳过不挂
3. `test_diff_basic` — yaml 有 [a,b]，bird 返回 [b,c,d] → to_add=[c,d], orphans=[a]
4. `test_diff_empty_yaml` — yaml 没 X 条目，全部 add
5. `test_max_new_truncates` — to_add=20 但 max_new=5，只取前 5
6. `test_apply_writes_yaml_and_decision_log` — mock bird，跑 --apply，验证 yaml + decision_log
7. `test_credentials_missing_exits_clean` — mock subprocess 返回非零 + 错误信息，命令仍 exit 0

## Known Limitation: bird returns incomplete view

**Observed 2026-04-20**: `bird following --all --max-pages 100` for an account
following ~150+ people returned only 108 entries with `nextCursor: null` after
~5 pages. Core accounts the user clearly still follows (karpathy, simonw,
huggingface, etc.) were missing from the output.

Conclusions:
- bird's `--all` flag respects X's `nextCursor` correctly, but X seems to truncate
  the GraphQL response — likely showing only "recent / algorithmically surfaced"
  follows, not the full friendship list
- This is upstream behavior, not a bird bug we can patch around easily
- **Add detection is still trustworthy** (every handle bird returns IS followed)
- **Orphan detection is NOT trustworthy** (an account being absent from bird's view
  doesn't mean unfollowed)

Mitigations in code:
- `check_orphans=False` is the **default** — orphans are only displayed in the diff
  output for human review, not written to `decision_log`
- Pass `--check-orphans` only when you trust bird's coverage (e.g. small follow lists,
  or after manual verification)

Future fix candidates: cross-reference `bird followers` (reciprocal), use multiple
calls with different sort orders, switch to a different scraping method entirely.

## Open Questions

1. **bird `following` 的真实 JSON schema** — 文档没明示，需要首次跑通后回填到 parse_follows 的 schema 注释
2. **是否需要白名单 / 黑名单** — 比如某些大 V 你关注但不想 prism 跟（噪音太多）。**首版不做**，等出现实际问题再加 `config/x_follows_blacklist.yaml`
3. **display_name 如何同步** — 如果 bird 返回的 display_name 跟 yaml 里的不一样，是否覆盖？**首版不覆盖**，只在新增时写
4. **多账号** — 用户可能有工作号 + 个人号。bird 默认用当前 cookie 对应的账号，**首版只支持单账号**
