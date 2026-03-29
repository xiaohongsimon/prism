from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Source:
    id: Optional[int] = None
    source_key: str = ""
    type: str = ""
    handle: str = ""
    config_yaml: str = ""
    enabled: bool = True
    origin: str = "yaml"  # yaml | cli | yaml_removed
    disabled_reason: Optional[str] = None  # auto | manual | None
    last_synced_at: Optional[datetime] = None
    consecutive_failures: int = 0
    auto_retry_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class RawItem:
    id: Optional[int] = None
    source_id: int = 0
    url: str = ""
    title: str = ""
    body: str = ""
    author: str = ""
    published_at: Optional[datetime] = None
    raw_json: str = ""
    thread_partial: bool = False
    created_at: Optional[datetime] = None


@dataclass
class Cluster:
    id: Optional[int] = None
    date: str = ""  # YYYY-MM-DD
    topic_label: str = ""
    item_count: int = 0
    merged_context: str = ""
    created_at: Optional[datetime] = None


@dataclass
class Signal:
    id: Optional[int] = None
    cluster_id: int = 0
    summary: str = ""
    signal_layer: str = ""  # actionable | strategic | noise
    signal_strength: int = 0
    why_it_matters: str = ""
    action: str = ""
    tl_perspective: str = ""
    tags_json: str = "[]"
    analysis_type: str = ""  # incremental | daily
    model_id: str = ""
    prompt_version: str = ""
    job_run_id: Optional[int] = None
    created_at: Optional[datetime] = None
    is_current: bool = True


@dataclass
class CrossLink:
    id: Optional[int] = None
    cluster_a_id: int = 0
    cluster_b_id: int = 0
    relation_type: str = ""
    reason: str = ""
    job_run_id: Optional[int] = None
    is_current: bool = True


@dataclass
class Trend:
    id: Optional[int] = None
    topic_label: str = ""
    date: str = ""
    heat_score: float = 0.0
    delta_vs_yesterday: float = 0.0
    job_run_id: Optional[int] = None
    is_current: bool = True


@dataclass
class Briefing:
    id: Optional[int] = None
    date: str = ""
    html: str = ""
    markdown: str = ""
    generated_at: Optional[datetime] = None


@dataclass
class JobRun:
    id: Optional[int] = None
    job_type: str = ""  # sync | cluster | analyze_incremental | analyze_daily | briefing
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    status: str = ""  # ok | partial | failed
    stats_json: str = "{}"


@dataclass
class EntityProfile:
    id: Optional[int] = None
    canonical_name: str = ""
    display_name: str = ""
    category: str = ""
    status: str = "emerging"
    summary: str = ""
    needs_review: bool = True
    first_seen_at: Optional[datetime] = None
    last_event_at: Optional[datetime] = None
    event_count_7d: int = 0
    event_count_30d: int = 0
    event_count_total: int = 0
    m7_score: float = 0.0
    m30_score: float = 0.0
    metadata_json: str = "{}"


@dataclass
class EntityAlias:
    alias_norm: str = ""
    entity_id: int = 0
    surface_form: str = ""
    source: str = "llm"


@dataclass
class EntityCandidate:
    name_norm: str = ""
    display_name: str = ""
    category: str = ""
    mention_count: int = 1
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    sample_signals_json: str = "[]"
    expires_at: Optional[datetime] = None


@dataclass
class EntityEvent:
    id: Optional[int] = None
    entity_id: int = 0
    signal_id: Optional[int] = None
    date: str = ""
    event_type: str = ""
    role: str = "subject"
    impact: str = "medium"
    confidence: float = 0.8
    description: str = ""
    metadata_json: str = "{}"
