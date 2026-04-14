"""OpenAI-compatible LLM client and prompt templates."""

import json
import logging
import subprocess
import time
from typing import Optional

import httpx

from prism.config import settings

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"

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
             timeout: int = 300, max_tokens: int = 2048) -> str:
    """Call OpenAI-compatible LLM API, return response text."""
    base_url = base_url or settings.llm_base_url
    api_key = api_key or settings.llm_api_key
    model = model or settings.llm_model

    if not base_url or not api_key:
        raise ValueError("LLM base_url and api_key must be configured")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": 0.3, "max_tokens": max_tokens,
               "repetition_penalty": 1.2}

    for attempt in range(4):
        try:
            result = subprocess.run(
                ["curl", "-sS", "--max-time", str(timeout),
                 url,
                 "-H", f"Authorization: Bearer {api_key}",
                 "-H", "Content-Type: application/json",
                 "-d", json.dumps(payload, ensure_ascii=False)],
                capture_output=True, text=True, timeout=timeout + 10,
            )
            if result.returncode != 0:
                raise RuntimeError(f"curl failed: {result.stderr}")
            body = json.loads(result.stdout)
            if "error" in body:
                raise RuntimeError(f"API error: {body['error']}")
            return body["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, RuntimeError) as exc:
            wait = 2 ** attempt
            logger.warning("LLM call failed (attempt %d/4): %s, retrying in %ds", attempt + 1, exc, wait)
            time.sleep(wait)

    raise RuntimeError("LLM call failed after 4 attempts")


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
                  timeout: int = 300, max_tokens: int = 2048) -> dict:
    """Call LLM and parse JSON from response."""
    text = call_llm(prompt, system, model, base_url, api_key, timeout=timeout,
                    max_tokens=max_tokens)

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
