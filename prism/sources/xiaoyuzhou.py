"""Xiaoyuzhou (小宇宙) podcast adapter — no-op for the regular sync loop.

Episode discovery + ASR transcription is heavy (downloads + Metal GPU) and is
driven by separate on-demand scripts (see tmp/xyz_wave*_*.{py,sh}). This adapter
exists only so that:
  - reconcile_sources sees the source as YAML-managed and won't auto-disable it
  - run_sync's per-type queue does not raise "Unknown source type: xiaoyuzhou"
    and accumulate hard failures

It always returns SyncResult(success=True, items=[]).
"""

from prism.sources.base import SyncResult


class XiaoyuzhouAdapter:
    async def sync(self, config: dict) -> SyncResult:
        return SyncResult(
            source_key=config.get("source_key", ""),
            items=[],
            success=True,
        )
