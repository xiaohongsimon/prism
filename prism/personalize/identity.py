"""IdentityReRanker — the default post-Wave-1 ranker.

Behavior: return the input list unchanged.

This is not a placeholder: it is the chosen product behavior. Prism's
current positioning is "multi-channel subscription reader, not a
recommender" (mission §0). The feed ranking is handled upstream by
`prism/web/ranking.py` (heat + preference + time decay) — the ReRanker
slot exists so a future personalization layer can plug in without
touching core code, not because the feed needs re-ranking today.
"""
from __future__ import annotations

from prism.personalize.protocol import FeedCandidate, UserContext


class IdentityReRanker:
    """Pass-through. See module docstring for why this is the default."""

    def rank(
        self,
        candidates: list[FeedCandidate],
        ctx: UserContext,
    ) -> list[FeedCandidate]:
        return candidates
