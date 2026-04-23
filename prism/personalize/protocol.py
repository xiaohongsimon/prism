"""ReRanker Protocol — the single contract the web layer sees.

Why a Protocol, not an ABC: structural typing means a future ranker (e.g.
one that lives in a separate package, or a test double) just needs the
right method shape — no inheritance coupling. Tech-stack v7 §5 is explicit
that this is the seam.

The types deliberately stay small. `FeedCandidate` is a thin view over
whatever the feed query already selected — rankers should not need to
re-query the DB. `UserContext` carries session info (user_id, is_anonymous)
plus the tab name so one ranker impl can behave differently on /feed vs
/feed/following if needed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class FeedCandidate:
    """One feed row before ranking.

    `payload` is whatever the caller wants to round-trip through the ranker
    untouched (usually the template dict). Rankers may read `signal_id` /
    `source_key` / `heat` / `published_at` to compute scores, but they must
    return the same `payload` back — no mutation.
    """
    signal_id: int
    source_key: str | None
    heat: float
    published_at: str | None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UserContext:
    user_id: int | None
    is_anonymous: bool
    tab: str  # "feed" | "following" | "saved" | …


class ReRanker(Protocol):
    """Takes candidates + context, returns a (possibly re-ordered, possibly
    filtered) list of candidates. Must be pure with respect to the DB —
    side effects belong in the pipeline layer.
    """

    def rank(
        self,
        candidates: list[FeedCandidate],
        ctx: UserContext,
    ) -> list[FeedCandidate]: ...
