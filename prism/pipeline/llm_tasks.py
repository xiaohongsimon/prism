"""Closed-set taxonomy for LLM call tagging.

Two orthogonal dimensions on top of ``intent`` (fast/reasoning/coding/default/vision):

- ``Task``: what semantic transformation this call performs
- ``Scope``: what input granularity it operates on

Both are StrEnums — the enum value is what lands in dashboards / ``tags`` /
``decision_log``. English slugs are the contract; frontend i18n layers map
them to display strings.

New tasks / scopes require a code change here. ``unassigned`` is not a
legal value; any call missing ``task=`` raises in ``pipeline/llm.py``.

Display-name mappings live in ``DISPLAY_NAMES_ZH`` below — keep in sync
with prism/web templates that render dashboard labels.
"""
from __future__ import annotations

from enum import StrEnum


class Task(StrEnum):
    """Semantic transformation types.

    Pipeline membership is *not* a task — a pipeline may emit multiple tasks
    (e.g. articlize pipeline produces STRUCTURIZE, a briefing pipeline may
    produce SUMMARIZE + CLASSIFY). Tag the call by what transform it does.
    """

    TRANSLATE = "translate"            # 外文 → 中文，语义保持
    ASR = "asr"                        # 音频 → 文本
    OCR = "ocr"                        # 图像 → 文本
    VIDEO_TRANSCRIBE = "video_transcribe"  # 视频 → 文本（ASR + 关键帧 OCR）
    SUMMARIZE = "summarize"            # 长 → 短摘要 / tl;dr / 综述
    POLISH = "polish"                  # 粗糙片段 → 通顺长文（不加结构）
    STRUCTURIZE = "structurize"        # 加章节 / 亮点 / 要点抽取（人读）
    EXTRACT = "extract"                # 文本 → JSON schema（机器读）
    CLASSIFY = "classify"              # 打标 / 归类 / 簇分诊
    JUDGE = "judge"                    # LLM-as-judge：NN6 门禁 / horse race 评委
    SOURCE_PROBE = "source_probe"      # mission §5 候选源生成


class Scope(StrEnum):
    """Input granularity for a task call."""

    ITEM = "item"                          # 单篇文章 / 推文 / 视频
    CLUSTER = "cluster"                    # 若干文章聚合
    DAILY = "daily"                        # 一天的信号全集
    SOURCE_PROFILE = "source_profile"      # 作者 / 账号层级画像
    CORPUS = "corpus"                      # 历史库 / 全量快照


# Zh display mapping for dashboards / templates. English slug → Chinese label.
# Keep keys aligned with Task / Scope enums above.
DISPLAY_NAMES_ZH: dict[str, str] = {
    # Task
    Task.TRANSLATE: "翻译",
    Task.ASR: "语音转文字",
    Task.OCR: "图像转文字",
    Task.VIDEO_TRANSCRIBE: "视频转文字",
    Task.SUMMARIZE: "摘要",
    Task.POLISH: "文章加工",
    Task.STRUCTURIZE: "结构化",
    Task.EXTRACT: "字段抽取",
    Task.CLASSIFY: "打标/分诊",
    Task.JUDGE: "质量门禁",
    Task.SOURCE_PROBE: "源探测",
    # Scope
    Scope.ITEM: "单篇",
    Scope.CLUSTER: "簇级",
    Scope.DAILY: "日级",
    Scope.SOURCE_PROFILE: "作者画像",
    Scope.CORPUS: "全量",
}


def display_name(value: str) -> str:
    """Return Chinese display name for a Task or Scope slug, or the slug itself."""
    return DISPLAY_NAMES_ZH.get(value, value)
