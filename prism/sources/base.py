"""Base types for source adapters."""

from dataclasses import dataclass, field
from typing import Optional, Protocol

from prism.models import RawItem


@dataclass
class SyncResult:
    source_key: str
    items: list[RawItem]
    success: bool
    error: str = ""
    stats: Optional[dict] = None  # e.g. {"thread_detected": 5, "thread_expanded": 4}


class SourceAdapter(Protocol):
    async def sync(self, config: dict) -> SyncResult: ...
