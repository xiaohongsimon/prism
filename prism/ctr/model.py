"""Thin inference wrapper around a trained XGBoost ranker.

Kept separate from train.py so the feed code path can import this
without pulling scikit-learn / pandas. Only `xgboost` and `numpy` are
needed at inference time.

Usage in the feed:

    ranker = CTRRanker.load()           # None if no model on disk
    if ranker:
        signals = ranker.rerank(conn, signals)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from prism.ctr.features import FEATURE_NAMES, _load_pref_map, extract

DEFAULT_MODEL_PATH = Path("data/ctr/model.json")


class CTRRanker:
    def __init__(self, booster):
        self._booster = booster

    @classmethod
    def load(cls, path: Path | str = DEFAULT_MODEL_PATH) -> "CTRRanker | None":
        p = Path(path)
        if not p.exists():
            return None
        try:
            import xgboost as xgb  # local import — optional dep
        except ImportError:
            return None
        booster = xgb.Booster()
        booster.load_model(str(p))
        return cls(booster)

    def score(self, feature_rows: Iterable[dict[str, float]]) -> list[float]:
        import numpy as np
        import xgboost as xgb

        matrix = np.array(
            [[row.get(n, 0.0) for n in FEATURE_NAMES] for row in feature_rows],
            dtype=float,
        )
        if matrix.size == 0:
            return []
        dmat = xgb.DMatrix(matrix, feature_names=FEATURE_NAMES)
        return [float(x) for x in self._booster.predict(dmat)]

    def rerank(
        self,
        conn: sqlite3.Connection,
        signals: list[dict],
        *,
        blend: float = 0.5,
    ) -> list[dict]:
        """Re-rank a feed page using ctr_score blended with heuristic feed_score.

        blend = 0 → pure heuristic (ignore model), blend = 1 → pure CTR.
        """
        if not signals:
            return signals
        pref_map = _load_pref_map(conn)
        feats = []
        for s in signals:
            sid = s.get("signal_id")
            if sid is None:
                feats.append({n: 0.0 for n in FEATURE_NAMES})
                continue
            feats.append(
                extract(conn, int(sid), feed_score=float(s.get("feed_score", 0.0)), pref_map=pref_map)
            )
        scores = self.score(feats)
        for s, sc in zip(signals, scores):
            s["ctr_score"] = sc
            s["final_score"] = (1 - blend) * float(s.get("feed_score", 0.0)) + blend * sc
        signals.sort(key=lambda s: s.get("final_score", 0.0), reverse=True)
        return signals
