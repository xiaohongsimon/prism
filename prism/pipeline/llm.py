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
  "briefing_narrative": "中文叙述，概括今日全局动态"
}"""

DAILY_BATCH_USER_TEMPLATE = """今日日期：{date}

昨日摘要：
{yesterday_summary}

今日聚类列表（共 {cluster_count} 个）：
{clusters_text}

请进行全局分析，输出 JSON。"""


# ---------------------------------------------------------------------------
# LLM call functions
# ---------------------------------------------------------------------------

def call_llm(prompt: str, system: str = "", model: Optional[str] = None,
             base_url: Optional[str] = None, api_key: Optional[str] = None,
             timeout: int = 300) -> str:
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
    payload = {"model": model, "messages": messages, "temperature": 0.3}

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


def call_llm_json(prompt: str, system: str = "", model: Optional[str] = None,
                  base_url: Optional[str] = None, api_key: Optional[str] = None,
                  timeout: int = 300) -> dict:
    """Call LLM and parse JSON from response."""
    text = call_llm(prompt, system, model, base_url, api_key, timeout=timeout)

    # Try to extract JSON from response (handle markdown code blocks)
    text = text.strip()
    if text.startswith("```"):
        # Remove code block markers
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    return json.loads(text)
