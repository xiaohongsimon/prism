# Token Tracker - AI Token Usage Tracking Proxy

**Date**: 2026-03-29
**Status**: Draft
**Project Location**: `~/work/token-tracker/`

## Purpose

A transparent recording proxy that sits between all LLM consumers (Claude Code, Codex CLI, Prism, etc.) and their upstream APIs. Intercepts every LLM request/response, extracts token usage from the response, and stores it in SQLite. Exposes a query API for the CEO dashboard to consume.

**Core metric**: Precise input/output token counts per request, aggregated by source, model, and time.

## Architecture

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Claude Code  │  │  Codex CLI   │  │    Prism     │
│ (Max/Zenmux) │  │  (OpenAI)    │  │ (omlx local) │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       ▼                 ▼                 ▼
┌──────────────────────────────────────────────────┐
│           token-tracker proxy (:8100)            │
│                                                  │
│  Route Layer: path-prefix → upstream             │
│  Record Layer: extract usage → SQLite            │
│  Query API: GET /api/stats/*                     │
└──────────────────────────────────────────────────┘
       │
       ▼
   SQLite (WAL mode)
```

## Route Table

Each path prefix maps to an upstream and a source label.

| Path Prefix | Upstream URL | Source Label | Protocol |
|---|---|---|---|
| `/anthropic/*` | `https://api.anthropic.com/*` | `claude-max` | Anthropic Messages API (SSE) |
| `/zenmux/*` | `https://zenmux.ai/api/anthropic/*` | `zenmux-{account}` | Anthropic Messages API (SSE) |
| `/openai/*` | `https://api.openai.com/*` | `codex` | OpenAI Chat Completions |
| `/omlx/*` | `http://127.0.0.1:8002/*` | `omlx-{model}` | OpenAI Chat Completions |
| `/bailian/*` | `https://coding.dashscope.aliyuncs.com/apps/anthropic/*` | `bailian-{model}` | Anthropic-compatible |

Route config is YAML-based so new upstreams can be added without code changes.

### Route Config Example

```yaml
# config/routes.yaml
routes:
  - prefix: /anthropic
    upstream: https://api.anthropic.com
    source: claude-max
    protocol: anthropic

  - prefix: /zenmux
    upstream: https://zenmux.ai/api/anthropic
    source: zenmux
    protocol: anthropic

  - prefix: /openai
    upstream: https://api.openai.com
    source: codex
    protocol: openai

  - prefix: /omlx
    upstream: http://127.0.0.1:8002
    source: omlx
    protocol: openai

  - prefix: /bailian
    upstream: https://coding.dashscope.aliyuncs.com/apps/anthropic
    source: bailian
    protocol: anthropic
```

## Proxy Behavior

### Request Flow

1. Incoming request hits `http://localhost:8100/{prefix}/v1/messages` (or `/v1/chat/completions`)
2. Strip prefix, forward to upstream with all original headers (Authorization, Content-Type, etc.)
3. If non-streaming: read full response, extract `usage`, record, return response
4. If streaming (SSE): stream events through to client, buffer the final event that contains usage, record after stream ends
5. On any error in recording: log warning, still return upstream response unmodified

### Streaming Support

**Anthropic SSE**: Token usage arrives in the `message_delta` event (type `message_delta`, `delta.stop_reason` present) with `usage.output_tokens`, and in `message_start` with `usage.input_tokens`.

**OpenAI SSE**: Token usage arrives in the final `[DONE]` chunk or in `usage` field if `stream_options.include_usage=true` is set. For streams without usage data, estimate from content length as fallback.

### Source Identification

- **Zenmux account**: Determined by the API key used. The proxy maintains a key→account mapping from config.
- **Model name**: Extracted from request body `model` field.
- **Source label**: From route config, refined by model/key when applicable.

## Database Schema

```sql
CREATE TABLE token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    source TEXT NOT NULL,           -- 'claude-max', 'zenmux-1', 'zenmux-2', 'codex', 'omlx', 'bailian'
    model TEXT NOT NULL,            -- actual model name from request/response
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,             -- request round-trip time
    upstream TEXT NOT NULL,          -- upstream URL hit
    request_path TEXT                -- original request path for debugging
);

CREATE INDEX idx_token_usage_ts ON token_usage(ts);
CREATE INDEX idx_token_usage_source ON token_usage(source);
CREATE INDEX idx_token_usage_date ON token_usage(substr(ts, 1, 10));
```

Single table, append-only. No updates, no deletes. Simple and fast.

## Query API

All endpoints return JSON. Used by the CEO dashboard.

### `GET /api/stats/daily?date=YYYY-MM-DD`

Returns aggregated totals for one day.

```json
{
  "date": "2026-03-29",
  "total_input_tokens": 1234567,
  "total_output_tokens": 456789,
  "total_tokens": 1691356,
  "total_requests": 342,
  "by_source": [
    {"source": "claude-max", "input_tokens": 800000, "output_tokens": 300000, "requests": 200},
    {"source": "codex", "input_tokens": 200000, "output_tokens": 100000, "requests": 80}
  ],
  "by_model": [
    {"model": "claude-opus-4-6", "input_tokens": 800000, "output_tokens": 300000, "requests": 200},
    {"model": "gpt-5.4", "input_tokens": 200000, "output_tokens": 100000, "requests": 80}
  ]
}
```

### `GET /api/stats/range?start=YYYY-MM-DD&end=YYYY-MM-DD`

Same structure as daily, but aggregated over a date range. Also includes `daily_breakdown` array.

### `GET /api/stats/trend?days=N`

Returns daily totals for the last N days, for charting.

```json
{
  "days": [
    {"date": "2026-03-28", "input_tokens": 1000000, "output_tokens": 400000, "requests": 300},
    {"date": "2026-03-29", "input_tokens": 1234567, "output_tokens": 456789, "requests": 342}
  ]
}
```

### `GET /api/stats/by-source?date=YYYY-MM-DD`

Breakdown by source for a given day.

### `GET /api/stats/by-model?date=YYYY-MM-DD`

Breakdown by model for a given day.

### `GET /api/health`

Health check. Returns upstream connectivity status.

## Integration Changes

### zenmux-switch.sh

```bash
# Before:
.env.ANTHROPIC_BASE_URL = "https://zenmux.ai/api/anthropic"

# After:
.env.ANTHROPIC_BASE_URL = "http://localhost:8100/zenmux"
```

Account identification: the existing `zenmux-switcher.sh` returns the API key. The proxy maps keys to account labels via config.

### Claude Max (direct subscription)

When not using Zenmux (i.e., using Claude Max directly via OAuth):

```bash
ANTHROPIC_BASE_URL=http://localhost:8100/anthropic
```

OAuth token passes through in the Authorization header unchanged.

### Prism .env

```bash
# Before:
PRISM_LLM_BASE_URL=http://127.0.0.1:8002/v1

# After:
PRISM_LLM_BASE_URL=http://127.0.0.1:8100/omlx/v1
```

### Codex CLI

Add to shell profile or codex config:

```bash
OPENAI_BASE_URL=http://localhost:8100/openai/v1
```

## Reliability

1. **Proxy failure must not block work**: If token recording fails (DB error, parse error), log warning and return upstream response unchanged.
2. **Upstream passthrough**: The proxy never modifies request or response content. It is purely observational.
3. **Graceful degradation**: If proxy process is down, consumers can be pointed directly at upstreams (just lose tracking).
4. **Health endpoint**: `/api/health` checks upstream connectivity for each configured route.
5. **WAL mode**: SQLite WAL for concurrent reads (dashboard) and writes (proxy) without locking.

## Tech Stack

- **Python 3.10+**
- **FastAPI** + **uvicorn**: async HTTP server
- **httpx**: async HTTP client for upstream forwarding
- **SQLite**: storage (WAL mode)
- **PyYAML**: route config

No heavy dependencies. Minimal footprint.

## Project Structure

```
~/work/token-tracker/
├── pyproject.toml
├── config/
│   └── routes.yaml           # route definitions
├── token_tracker/
│   ├── __init__.py
│   ├── app.py                # FastAPI app + startup
│   ├── proxy.py              # core proxy logic (route, forward, record)
│   ├── protocols/
│   │   ├── __init__.py
│   │   ├── anthropic.py      # Anthropic SSE usage extraction
│   │   └── openai.py         # OpenAI usage extraction
│   ├── db.py                 # SQLite connection + schema + insert
│   ├── stats.py              # query API handlers
│   └── config.py             # load routes.yaml
├── tests/
│   ├── test_proxy.py
│   ├── test_protocols.py
│   └── test_stats.py
└── data/
    └── token_tracker.sqlite3  # created at runtime
```

## Process Management

Run as a launchd service on macOS for 24/7 operation:

```xml
<!-- ~/Library/LaunchAgents/com.leehom.token-tracker.plist -->
<plist>
  <dict>
    <key>Label</key><string>com.leehom.token-tracker</string>
    <key>ProgramArguments</key>
    <array>
      <string>/path/to/python</string>
      <string>-m</string>
      <string>uvicorn</string>
      <string>token_tracker.app:app</string>
      <string>--host</string><string>127.0.0.1</string>
      <string>--port</string><string>8100</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
  </dict>
</plist>
```

## Out of Scope (for now)

- Cost calculation (USD equivalent) — can be added later with a price table
- Cognitive density index — future enhancement on top of raw token data
- Web dashboard — CEO dashboard is a separate project that consumes the API
- Per-conversation/session grouping — just raw per-request tracking for now
