"""Personalization seam — re-ranking layer that sits between the feed
query and the template.

Tech-stack v7 §5 / mission NN3 mandate:
- All preference-aware code lives here, not in `prism/web/` or pipelines
- Core (sync / cluster / analyze / briefing) must not import this package
- Web layer consumes via the `ReRanker` Protocol — never the concrete impl —
  so swapping in a new ranker is zero-diff outside this directory

Current contents: `IdentityReRanker` pass-through (Wave 1 post-BT-removal
default). Future rankers (embedding re-rank, LLM judge, etc.) ship here.
"""
from prism.personalize.protocol import ReRanker, FeedCandidate, UserContext
from prism.personalize.identity import IdentityReRanker

__all__ = ["ReRanker", "FeedCandidate", "UserContext", "IdentityReRanker"]
