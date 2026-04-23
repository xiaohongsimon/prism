"""OpenAI-compatible LLM client and prompt templates.

OMLX calls go through omlx_sdk.OmlxSyncClient so that every request
emits a telemetry bill to omlx-manager (caller/project/session_id/intent
dimensions). Claude calls still go through curl to the premium proxy —
untouched here.
"""

import atexit
import json
import logging
import re
import subprocess
import time
from typing import Any, Optional

from omlx_sdk import OmlxSyncClient, SdkSettings
from omlx_sdk.types import OmlxSdkError

from prism.config import settings
from prism.pipeline.llm_tasks import Scope, Task

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"

# Module-level SDK client. Lazily constructed on first call so import-time
# config errors don't break tests that never call LLM.
_omlx_client: Optional[OmlxSyncClient] = None


def _strip_v1_suffix(base_url: str) -> str:
    """prism's env has base_url ending in /v1; SDK wants just the host."""
    return re.sub(r"/v1/?$", "", base_url.rstrip("/"))


def _get_client() -> OmlxSyncClient:
    global _omlx_client
    if _omlx_client is not None:
        return _omlx_client
    if not settings.llm_base_url or not settings.llm_api_key:
        raise ValueError("LLM base_url and api_key must be configured")
    env_defaults = SdkSettings.from_env()
    sdk_settings = SdkSettings(
        endpoint=_strip_v1_suffix(settings.llm_base_url),
        api_key=settings.llm_api_key,
        # Manager ingest URL comes from env (OMLX_MANAGER_INGEST_URL) or
        # defaults to http://127.0.0.1:8003/v1/ingest.
        manager_ingest_url=env_defaults.manager_ingest_url,
        manager_enabled=env_defaults.manager_enabled,
        # HTTP timeout honors OMLX_REQUEST_TIMEOUT_S (default 600s). Dense
        # reasoning clusters occasionally need 15+ min, so hourly.sh bumps
        # this to 1800 for the expand stage.
        request_timeout_s=env_defaults.request_timeout_s,
        ingest_timeout_s=1.0,
    )
    _omlx_client = OmlxSyncClient(caller="prism", settings=sdk_settings)
    atexit.register(_omlx_client.close)
    return _omlx_client

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

INCREMENTAL_SYSTEM = """你是 Prism 信号分析系统。分析给定的信息聚类，产出结构化信号判断。
输出必须是 JSON 格式，包含以下字段：
- summary: 一句话中文摘要
- content_zh: 原文内容的完整中文翻译（保留原文结构和语气，忠实翻译而非概括）
- signal_layer: actionable | strategic | noise
- signal_strength: 1-5 整数
- why_it_matters: 为什么这个信号重要（中文）
- action: 建议的行动（中文，如无则填"无"）
- tl_perspective: 从 TL 视角的解读（中文）
- tags: 相关标签列表"""

INCREMENTAL_USER_TEMPLATE = """请分析以下信息聚类：

主题：{topic_label}
包含 {item_count} 条信息

内容摘要：
{merged_context}

请输出 JSON 格式的分析结果。"""

# ── Stage 1 (triage): fast, light-weight signal classification ──
# Run against a cheap model (gemma-4-26b-a4b-it-8bit) for every cluster.
# Drops `content_zh` and `tl_perspective` — those are the expensive fields
# that bloat output length on dense papers / long changelogs. The expand
# stage adds them back for high-strength signals only.
INCREMENTAL_TRIAGE_SYSTEM = """你是 Prism 信号分诊系统。快速判断信息聚类的信号价值。
输出必须是 JSON，严格只包含这 5 个字段：
- summary: 一句话中文摘要（不超过 60 字）
- signal_layer: actionable | strategic | noise
- signal_strength: 1-5 整数
- why_it_matters: 一句话说明（不超过 40 字，如 signal_layer=noise 可填"无"）
- tags: 相关中文标签列表（最多 5 个）

要求：简短精准，不要长篇推理，不要翻译原文。"""

INCREMENTAL_TRIAGE_USER_TEMPLATE = """请分诊以下信息聚类：

主题：{topic_label}
包含 {item_count} 条信息

内容摘要：
{merged_context}

输出 JSON，只包含 5 个字段，避免冗长解释。"""

# ── Stage 2 (expand): deep translation + TL perspective ──
# Run against the reasoning model (Qwen3.6-35B-A3B-8bit), but only for
# signals triaged as high-value. Inputs include the triage summary so the
# model knows "why this one matters" without redoing the judgment.
INCREMENTAL_EXPAND_SYSTEM = """你是 Prism 信号深度解读系统。对已判定为高价值的信号，补充深度解读。
输出必须是 JSON，严格只包含这 3 个字段：
- content_zh: 原文内容的完整中文翻译（保留原结构和语气，忠实翻译而非概括）
- tl_perspective: 从技术管理者视角的解读（2-3 句，含具体观点和判断）
- action: 建议行动（一句话，如无可填"无"）

要求：content_zh 忠实翻译，tl_perspective 有判断不空洞。"""

INCREMENTAL_EXPAND_USER_TEMPLATE = """以下信号已经过初步分诊，判定为值得深度解读：

主题：{topic_label}
初步摘要：{summary}
为何重要：{why_it_matters}
强度：{signal_strength}/5

原文内容：
{merged_context}

请输出 JSON，只包含 content_zh + tl_perspective + action 三个字段。"""

# YouTube / long-form video content: extract key insights from transcript
VIDEO_SYSTEM = """你是 Prism 视频内容深度分析系统。你的任务是从视频字幕文本中提炼核心精华。
不要写"该视频讲述了..."这种笼统概括，而是直接输出视频中最有价值的观点和洞察。

输出必须是 JSON 格式：
- summary: 视频核心论点，2-3 句话，直击要害（中文）
- content_zh: 视频精华内容的结构化中文摘要（3-5 段，每段覆盖一个核心话题，包含具体数据和论据，不是笼统概括）
- key_insights: 3-5 条最精华的观点/洞察，每条 1-2 句话（中文字符串数组）
- signal_layer: actionable | strategic | noise
- signal_strength: 1-5 整数
- why_it_matters: 为什么值得看这个视频（中文）
- action: 看完后建议做什么（中文）
- tl_perspective: 从技术管理者视角的解读（中文）
- tags: 相关标签列表"""

VIDEO_USER_TEMPLATE = """请深度分析以下视频内容，提炼出最核心的洞察：

视频标题：{topic_label}

视频字幕全文：
{merged_context}

注意：不要笼统概括，要提炼出视频中最精彩、最有价值的具体观点和论据。"""

DAILY_BATCH_SYSTEM = """你是 Prism 每日信号分析系统。对今日所有聚类进行全局分析，产出：
1. 每个聚类的信号判断
2. 聚类间的关联关系 (cross_links)
3. 趋势判断 (trends)
4. 今日简报叙述 (briefing_narrative)

输出必须是 JSON 格式：
{
  "clusters": [{"cluster_id": int, "summary": str, "signal_layer": str, "signal_strength": int,
                "why_it_matters": str, "action": str, "tl_perspective": str, "tags": [str]}],
  "cross_links": [{"cluster_a_id": int, "cluster_b_id": int, "relation_type": str, "reason": str}],
  "trends": [{"topic_label": str, "heat_delta": str}],
  "briefing_narrative": "3-5 段高密度中文叙述，概括今日全局动态。要求：(1) 每段聚焦一个核心趋势 (2) 引用具体聚类时用 (Cluster N) 格式标注，读者可据此跳转原文 (3) 包含具体数据和事实，不要空洞概括 (4) 不要出现重复短语"
}"""

DAILY_BATCH_USER_TEMPLATE = """今日日期：{date}

昨日摘要：
{yesterday_summary}

今日聚类列表（共 {cluster_count} 个）：
{clusters_text}

请进行全局分析，输出 JSON。"""

# Narrative-only prompt: lightweight, focused on synthesis
NARRATIVE_SYSTEM = """你是 Prism 每日总结撰写系统。根据今日 top 信号，写一段高密度的中文全局概览。

要求：
1. 分 3-5 段，每段聚焦一个核心趋势或主题
2. 每提到一条具体信号时，用 (Cluster N) 标注其 cluster_id，读者可据此跳转原文
3. 包含具体数据、人名、产品名，不要空洞概括
4. 语气简洁利落，像给 CEO 写的晨间简报
5. 不要出现重复短语

输出纯文本，不要 JSON，不要 markdown 标题。直接输出 3-5 段中文。"""

NARRATIVE_USER_TEMPLATE = """今日日期：{date}

今日 top 信号（共 {signal_count} 条）：
{signals_text}

请写出今日全局概览。"""


# ---------------------------------------------------------------------------
# LLM call functions
# ---------------------------------------------------------------------------

def call_llm(prompt: str, system: str = "", model: Optional[str] = None,
             base_url: Optional[str] = None, api_key: Optional[str] = None,
             timeout: int = 300, max_tokens: int = 2048,
             *,
             task: Task,
             scope: Scope = Scope.ITEM,
             source_key: Optional[str] = None,
             intent: Optional[str] = None,
             session_id: Optional[str] = None) -> str:
    """Call OMLX (OpenAI-compatible), return response text.

    Mandatory tagging:
        task:   Task enum — which semantic transformation this call performs
                (translate / summarize / structurize / extract / ...). Missing
                task = raise. ``unassigned`` is not a legal value.
        scope:  Scope enum — input granularity (item/cluster/daily/...).
                Defaults to Scope.ITEM.

    Optional:
        source_key: e.g. "x:karpathy" — surfaces in tags for per-source slicing
        intent:     e.g. "fast" — SDK resolves to a model from the intent table
        session_id: e.g. f"job-{job_run_id}" — groups related calls

    base_url/api_key kwargs are deprecated at this layer: the SDK client
    is constructed once from prism.config.settings. They are accepted for
    call-site compatibility but ignored with a debug log.
    """
    if base_url or api_key:
        logger.debug("call_llm: base_url/api_key args ignored (SDK uses module client)")

    if not isinstance(task, Task):
        raise TypeError(
            f"call_llm requires task: Task (got {type(task).__name__}). "
            "Use prism.pipeline.llm_tasks.Task — no free-form strings."
        )
    if not isinstance(scope, Scope):
        raise TypeError(
            f"call_llm requires scope: Scope (got {type(scope).__name__}). "
            "Use prism.pipeline.llm_tasks.Scope."
        )

    # model resolution: explicit model > intent > settings.llm_model
    resolved_model = model if model else (None if intent else settings.llm_model)

    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    client = _get_client()

    # Tag dims: `task` and `scope` are contract; `source_key` is optional slice.
    # `project=task.value` is kept for dashboards that still group by project.
    tags: dict[str, str] = {"task": task.value, "scope": scope.value}
    if source_key:
        tags["source_key"] = source_key

    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            # Note: `timeout` arg is the legacy caller-side hint; SDK's
            # actual HTTP timeout is SdkSettings.request_timeout_s.
            resp = client.chat(
                messages=messages,
                model=resolved_model,
                intent=intent,
                session_id=session_id,
                project=task.value,
                tags=tags,
                temperature=0.3,
                max_tokens=max_tokens,
                repetition_penalty=1.2,
            )
            return resp.content
        except OmlxSdkError as exc:
            last_exc = exc
            wait = 2 ** attempt
            logger.warning("LLM call failed (attempt %d/4): %s, retrying in %ds",
                           attempt + 1, exc, wait)
            time.sleep(wait)

    raise RuntimeError(f"LLM call failed after 4 attempts: {last_exc}")


def call_claude(prompt: str, system: str = "", model: str = "claude-sonnet-4-20250514",
                max_tokens: int = 4096, timeout: int = 120) -> str:
    """Call Claude via Anthropic Messages API (through token-tracker proxy)."""
    from prism.config import settings as _cfg
    base_url = _cfg.llm_premium_base_url or "http://localhost:8100/anthropic"
    api_key = _cfg.llm_premium_api_key or "prism"

    url = f"{base_url.rstrip('/')}/v1/messages"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }

    for attempt in range(3):
        try:
            result = subprocess.run(
                ["curl", "-sS", "--max-time", str(timeout),
                 url,
                 "-H", f"x-api-key: {api_key}",
                 "-H", "anthropic-version: 2023-06-01",
                 "-H", "Content-Type: application/json",
                 "-d", json.dumps(payload, ensure_ascii=False)],
                capture_output=True, text=True, timeout=timeout + 10,
            )
            if result.returncode != 0:
                raise RuntimeError(f"curl failed: {result.stderr}")
            body = json.loads(result.stdout)
            if body.get("type") == "error":
                raise RuntimeError(f"API error: {body['error']}")
            # Extract text from content blocks
            return "".join(b["text"] for b in body["content"] if b["type"] == "text")
        except (json.JSONDecodeError, KeyError, RuntimeError) as exc:
            wait = 2 ** attempt
            logger.warning("Claude call failed (attempt %d/3): %s, retrying in %ds", attempt + 1, exc, wait)
            time.sleep(wait)

    raise RuntimeError("Claude call failed after 3 attempts")


def call_claude_json(prompt: str, system: str = "", model: str = "claude-sonnet-4-20250514",
                     max_tokens: int = 4096) -> dict:
    """Call Claude and parse JSON from response."""
    import re
    text = call_claude(prompt, system, model, max_tokens)
    text = text.strip()
    # Remove markdown code blocks
    if "```" in text:
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    # Find JSON block
    start = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start >= 0:
        bracket = "{" if text[start] == "{" else "["
        close = "}" if bracket == "{" else "]"
        depth = 0
        for i in range(start, len(text)):
            if text[i] == bracket:
                depth += 1
            elif text[i] == close:
                depth -= 1
                if depth == 0:
                    text = text[start:i + 1]
                    break
    return json.loads(text)


def call_llm_json(prompt: str, system: str = "", model: Optional[str] = None,
                  base_url: Optional[str] = None, api_key: Optional[str] = None,
                  timeout: int = 300, max_tokens: int = 2048,
                  *,
                  task: Task,
                  scope: Scope = Scope.ITEM,
                  source_key: Optional[str] = None,
                  intent: Optional[str] = None,
                  session_id: Optional[str] = None) -> dict:
    """Call LLM and parse JSON from response. See ``call_llm`` for tagging contract."""
    text = call_llm(prompt, system, model, base_url, api_key, timeout=timeout,
                    max_tokens=max_tokens,
                    task=task, scope=scope, source_key=source_key,
                    intent=intent, session_id=session_id)

    # Extract JSON from response (handle thinking tags, code blocks, extra text)
    text = text.strip()

    # Strip <think>...</think> blocks (reasoning models)
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Remove markdown code block markers
    if "```" in text:
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # Find the first { ... } or [ ... ] block
    start = -1
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start >= 0:
        bracket = "{" if text[start] == "{" else "["
        close = "}" if bracket == "{" else "]"
        depth = 0
        for i in range(start, len(text)):
            if text[i] == bracket:
                depth += 1
            elif text[i] == close:
                depth -= 1
                if depth == 0:
                    text = text[start:i + 1]
                    break

    return json.loads(text)
