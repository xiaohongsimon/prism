"""
Deterministic candidate extraction + LLM-based entity extraction for Prism signals.

Pipeline:
  1. deterministic_candidates()  — fast regex/JSON pass, zero LLM cost
  2. extract_entities_llm()      — optional LLM enrichment using call_llm_json
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stop-list: common terms that are NOT interesting entities
# ---------------------------------------------------------------------------

STOPLIST: frozenset = frozenset({
    "ai", "ml", "llm", "api", "gpu", "cpu", "ram",
    "the", "new", "model", "data", "code", "test", "bug", "fix",
    "update", "release", "version", "performance",
    "training", "inference", "benchmark", "evaluation",
    "paper", "research", "open", "source",
    "machine", "learning", "deep", "neural", "network",
    "transformer", "attention", "token", "embedding",
})

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_RE_GITHUB_REPO = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
)
_RE_HANDLE = re.compile(r"@([A-Za-z0-9_]{1,50})")
# Title-case: starts with uppercase, followed by at least one lowercase letter
# Must be ≥ 2 chars total and not an ALL_CAPS acronym
_RE_PROPER_NOUN = re.compile(r"\b([A-Z][a-z][A-Za-z0-9]*(?:[A-Z][a-z][A-Za-z0-9]*)*)\b")


# ---------------------------------------------------------------------------
# deterministic_candidates
# ---------------------------------------------------------------------------

def deterministic_candidates(signal: dict) -> list:
    """Extract candidate entity mentions from a signal dict without an LLM.

    Parameters
    ----------
    signal : dict
        Must contain keys "summary", "why_it_matters", "tags_json".

    Returns
    -------
    list[dict]
        Each item: {"mention": str, "source": str}
        source is one of: "repo_url" | "handle" | "tag" | "proper_noun"
    """
    summary = signal.get("summary") or ""
    why = signal.get("why_it_matters") or ""
    tags_json = signal.get("tags_json") or "[]"
    full_text = f"{summary} {why}"

    seen: dict = {}  # lowercased mention -> first dict encountered

    def _add(mention: str, source: str) -> None:
        """Add candidate if it passes the filters."""
        stripped = mention.strip()
        if len(stripped) < 2:
            return
        if stripped.lower() in STOPLIST:
            return
        key = stripped.lower()
        if key not in seen:
            seen[key] = {"mention": stripped, "source": source}

    # --- GitHub repo URLs -------------------------------------------------
    for match in _RE_GITHUB_REPO.finditer(full_text):
        repo_path = match.group(1)          # e.g. "vllm-project/vllm"
        _add(repo_path, "repo_url")
        # Also add the repo name alone (part after /)
        repo_name = repo_path.split("/", 1)[-1]
        _add(repo_name, "repo_url")

    # --- @handles ---------------------------------------------------------
    for match in _RE_HANDLE.finditer(full_text):
        _add(match.group(1), "handle")

    # --- tags_json --------------------------------------------------------
    try:
        tags = json.loads(tags_json)
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str):
                    _add(tag, "tag")
    except (json.JSONDecodeError, TypeError):
        logger.debug("Could not parse tags_json: %r", tags_json)

    # --- Proper nouns (Title-case) ----------------------------------------
    # Run AFTER repo/handle/tag so those take priority for dedup
    for match in _RE_PROPER_NOUN.finditer(full_text):
        _add(match.group(1), "proper_noun")

    return list(seen.values())


# ---------------------------------------------------------------------------
# LLM prompt templates
# ---------------------------------------------------------------------------

ENTITY_EXTRACT_SYSTEM = """You are an entity extraction engine for the Prism signal intelligence system.

Your task: identify named entities that are meaningful signals in the AI/tech domain.

Entity categories:
- project   : open-source projects, repos, frameworks, tools (e.g. vLLM, SGLang, Triton)
- model     : AI model names/versions (e.g. GPT-4o, Claude 3.5 Sonnet, Llama-3)
- org       : companies, labs, organisations (e.g. OpenAI, DeepMind, Mistral)
- person    : researchers, engineers, executives (e.g. Andrej Karpathy, Sam Altman)
- technique : methods, algorithms, architectures (e.g. LoRA, RLHF, FlashAttention)
- dataset   : datasets and benchmarks (e.g. MMLU, HumanEval, RedPajama)

Rules:
- Return ONLY a JSON object with key "entities" containing a list of objects.
- Each entity object: {"name": str, "category": str, "confidence": float 0-1}
- Omit generic terms, stopwords, and anything not a proper named entity.
- Prefer canonical names (e.g. "vLLM" not "the vllm framework").
- confidence: 1.0 = certain, 0.7 = probable, 0.5 = possible.
- If no entities found, return {"entities": []}."""

ENTITY_EXTRACT_USER_TEMPLATE = """Date: {date}
Topic: {topic_label}

Signal summary:
{summary}

Why it matters:
{why_it_matters}

Tags: {tags}
Source types: {source_types}

Deterministic candidates already found (hints, may be incomplete or noisy):
{candidates_text}

Known entities in the system for context (do not duplicate unless genuinely present):
{known_entities_text}

Extract all named entities from the signal. Return JSON only."""


# ---------------------------------------------------------------------------
# build_extraction_prompt
# ---------------------------------------------------------------------------

def build_extraction_prompt(
    signal: dict,
    candidates: list,
    known_entities: list,
    date: str,
) -> str:
    """Render ENTITY_EXTRACT_USER_TEMPLATE from signal data.

    Parameters
    ----------
    signal : dict
        Signal dict with keys: summary, why_it_matters, tags_json,
        topic_label (optional), source_types (optional).
    candidates : list[dict]
        Output of deterministic_candidates().
    known_entities : list[str | dict]
        Existing entity names/rows from the DB for context.
    date : str
        ISO date string, e.g. "2026-03-29".

    Returns
    -------
    str
        Formatted user prompt ready to send to the LLM.
    """
    topic_label = signal.get("topic_label") or signal.get("topic") or "Unknown"
    summary = signal.get("summary") or ""
    why = signal.get("why_it_matters") or ""

    # Format tags
    try:
        tags_list = json.loads(signal.get("tags_json") or "[]")
        tags_str = ", ".join(str(t) for t in tags_list) if tags_list else "(none)"
    except (json.JSONDecodeError, TypeError):
        tags_str = "(none)"

    source_types = signal.get("source_types") or "(unknown)"

    # Format candidates
    if candidates:
        candidates_text = "\n".join(
            f"  - {c['mention']} [{c['source']}]" for c in candidates
        )
    else:
        candidates_text = "  (none)"

    # Format known entities
    if known_entities:
        entity_lines = []
        for e in known_entities:
            if isinstance(e, dict):
                entity_lines.append(
                    f"  - {e.get('display_name') or e.get('canonical_name') or str(e)}"
                    f" [{e.get('category', '?')}]"
                )
            else:
                entity_lines.append(f"  - {e}")
        known_entities_text = "\n".join(entity_lines)
    else:
        known_entities_text = "  (none)"

    return ENTITY_EXTRACT_USER_TEMPLATE.format(
        date=date,
        topic_label=topic_label,
        summary=summary,
        why_it_matters=why,
        tags=tags_str,
        source_types=source_types,
        candidates_text=candidates_text,
        known_entities_text=known_entities_text,
    )


# ---------------------------------------------------------------------------
# extract_entities_llm
# ---------------------------------------------------------------------------

def extract_entities_llm(
    signal: dict,
    candidates: list,
    known_entities: list,
    date: str,
    model: Optional[str] = None,
) -> dict:
    """Call the LLM to extract entities from a signal.

    Parameters
    ----------
    signal : dict
        Signal dict (see deterministic_candidates for expected keys).
    candidates : list[dict]
        Output of deterministic_candidates().
    known_entities : list
        Existing entity names/rows for context.
    date : str
        ISO date string.
    model : str, optional
        Override the default LLM model from settings.

    Returns
    -------
    dict
        Parsed JSON with key "entities" (list of entity dicts).
        Returns {"entities": []} on any failure.
    """
    from prism.pipeline.llm import call_llm_json  # local import to keep module testable

    prompt = build_extraction_prompt(signal, candidates, known_entities, date)
    try:
        result = call_llm_json(prompt, system=ENTITY_EXTRACT_SYSTEM, model=model)
        if not isinstance(result, dict) or "entities" not in result:
            logger.warning("LLM returned unexpected structure: %r", result)
            return {"entities": []}
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract_entities_llm failed: %s", exc)
        return {"entities": []}
