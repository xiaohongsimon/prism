"""XGBoost ranker trainer for the CTR model.

Pipeline:
  1. build_samples()    — skip-above groups keyed by save event.
  2. extract()          — per-signal feature vector.
  3. XGBRanker          — rank:pairwise objective over those groups.
  4. NDCG@5/@10         — on a chronological held-out tail.
  5. Persist            — JSON booster dump + feature_names metadata.

Optional deps (xgboost / pandas / numpy / scikit-learn) are imported
lazily so the rest of the app still works without them installed.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from prism.ctr.features import FEATURE_NAMES, _load_pref_map, extract
from prism.ctr.samples import Sample, build_samples, summarize

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np
    import pandas as pd


DEFAULT_MODEL_DIR = Path("data/ctr")
DEFAULT_MODEL_PATH = DEFAULT_MODEL_DIR / "model.json"
DEFAULT_META_PATH = DEFAULT_MODEL_DIR / "meta.json"

# Chronological test fraction. On small datasets we cap the test set so
# we don't lose too many training groups.
TEST_FRACTION = 0.2
MIN_TEST_GROUPS = 2
MIN_TRAIN_GROUPS = 3


@dataclass
class TrainReport:
    total_samples: int
    total_groups: int
    train_groups: int
    test_groups: int
    ndcg_at_5: float | None
    ndcg_at_10: float | None
    feature_importance: dict[str, float]
    model_path: str


def _build_frame(conn: sqlite3.Connection, samples: list[Sample]):
    """Turn Sample records into a pandas DataFrame of features + label + group.

    Groups are kept contiguous (samples are already sorted by save_event
    from build_samples), which is what XGBRanker's `group` arg expects.
    """
    import pandas as pd  # local import — optional dep

    pref_map = _load_pref_map(conn)
    rows = []
    for s in samples:
        feats = extract(
            conn,
            s.signal_id,
            feed_score=s.feed_score,
            pref_map=pref_map,
        )
        feats["_label"] = s.label
        feats["_group"] = s.group_id
        feats["_served_at"] = s.served_at
        rows.append(feats)
    df = pd.DataFrame(rows, columns=FEATURE_NAMES + ["_label", "_group", "_served_at"])
    return df


def _chronological_split(df):
    """Split by save-event time — older groups train, newer groups test.

    Using group-level time prevents a leak where part of a group ends up
    in train and part in test.
    """
    import numpy as np  # noqa: F401

    group_time = (
        df.groupby("_group")["_served_at"].min().sort_values()
    )
    groups_ordered = list(group_time.index)
    n_groups = len(groups_ordered)
    if n_groups < MIN_TRAIN_GROUPS + MIN_TEST_GROUPS:
        return df, None  # too little data — evaluate on training itself

    n_test = max(MIN_TEST_GROUPS, int(round(n_groups * TEST_FRACTION)))
    n_test = min(n_test, n_groups - MIN_TRAIN_GROUPS)
    train_groups = set(groups_ordered[:-n_test])
    test_groups = set(groups_ordered[-n_test:])

    train_df = df[df["_group"].isin(train_groups)].copy()
    test_df = df[df["_group"].isin(test_groups)].copy()
    return train_df, test_df


def _group_sizes(df):
    """Return group sizes in the order rows appear in df.

    XGBRanker wants a list of group lengths such that sum == len(df).
    We preserve df's current row order and just count consecutive
    same-group runs.
    """
    sizes = []
    current = None
    count = 0
    for g in df["_group"].tolist():
        if g != current:
            if current is not None:
                sizes.append(count)
            current = g
            count = 1
        else:
            count += 1
    if current is not None:
        sizes.append(count)
    return sizes


def _ndcg_at_k(y_true, y_score, k: int) -> float:
    """NDCG@k for a single group. Binary labels (0/1) → idcg = 1.0."""
    import numpy as np

    order = np.argsort(-np.asarray(y_score))
    top = np.asarray(y_true)[order][:k]
    gains = (2 ** top - 1).astype(float)
    discounts = 1.0 / np.log2(np.arange(2, top.size + 2))
    dcg = float((gains * discounts).sum())

    ideal = np.sort(np.asarray(y_true))[::-1][:k]
    igains = (2 ** ideal - 1).astype(float)
    idiscounts = 1.0 / np.log2(np.arange(2, ideal.size + 2))
    idcg = float((igains * idiscounts).sum())
    return dcg / idcg if idcg > 0 else 0.0


def _mean_ndcg(model, df, k: int) -> float:
    X = df[FEATURE_NAMES].values
    scores = model.predict(X)
    scores_by_group: dict[int, list[tuple[int, float]]] = {}
    for label, group, s in zip(df["_label"].tolist(), df["_group"].tolist(), scores):
        scores_by_group.setdefault(group, []).append((int(label), float(s)))

    ndcgs = []
    for pairs in scores_by_group.values():
        if not pairs:
            continue
        y_true = [p[0] for p in pairs]
        if sum(y_true) == 0:
            continue
        y_score = [p[1] for p in pairs]
        ndcgs.append(_ndcg_at_k(y_true, y_score, k))
    return float(sum(ndcgs) / len(ndcgs)) if ndcgs else 0.0


def train(
    db_path: str,
    *,
    model_path: Path | str = DEFAULT_MODEL_PATH,
    meta_path: Path | str = DEFAULT_META_PATH,
    params: dict | None = None,
) -> TrainReport:
    """Train a CTR ranker end-to-end. Returns a TrainReport."""
    import xgboost as xgb  # local import — optional dep

    model_path = Path(model_path)
    meta_path = Path(meta_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        samples = build_samples(conn)
        if not samples:
            raise RuntimeError(
                "No training samples — need at least one save with a matching impression."
            )
        df = _build_frame(conn, samples)
    finally:
        conn.close()

    train_df, test_df = _chronological_split(df)
    train_df = train_df.sort_values("_group", kind="stable").reset_index(drop=True)
    if test_df is not None:
        test_df = test_df.sort_values("_group", kind="stable").reset_index(drop=True)

    X_train = train_df[FEATURE_NAMES].values
    y_train = train_df["_label"].values
    train_groups = _group_sizes(train_df)

    default_params = {
        "objective": "rank:pairwise",
        "learning_rate": 0.1,
        "n_estimators": 200,
        "max_depth": 4,
        "min_child_weight": 1,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "reg_lambda": 1.0,
        "eval_metric": ["ndcg@5", "ndcg@10"],
        "tree_method": "hist",
    }
    if params:
        default_params.update(params)

    model = xgb.XGBRanker(**default_params)
    fit_kwargs = {"group": train_groups}
    if test_df is not None and len(test_df) > 0:
        fit_kwargs["eval_set"] = [(test_df[FEATURE_NAMES].values, test_df["_label"].values)]
        fit_kwargs["eval_group"] = [_group_sizes(test_df)]
        fit_kwargs["verbose"] = False
    model.fit(X_train, y_train, **fit_kwargs)

    eval_df = test_df if test_df is not None else train_df
    ndcg5 = _mean_ndcg(model, eval_df, 5)
    ndcg10 = _mean_ndcg(model, eval_df, 10)

    # Persist.
    booster = model.get_booster()
    booster.save_model(str(model_path))
    importance = booster.get_score(importance_type="gain")
    # Map fN indices back to feature names.
    name_importance: dict[str, float] = {}
    for k, v in importance.items():
        try:
            idx = int(k.lstrip("f"))
            name_importance[FEATURE_NAMES[idx]] = float(v)
        except (ValueError, IndexError):
            name_importance[k] = float(v)

    meta = {
        "feature_names": FEATURE_NAMES,
        "params": default_params,
        "sample_summary": summarize(samples),
        "train_groups": len(set(train_df["_group"].tolist())),
        "test_groups": len(set(test_df["_group"].tolist())) if test_df is not None else 0,
        "ndcg_at_5": ndcg5,
        "ndcg_at_10": ndcg10,
        "feature_importance": name_importance,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    return TrainReport(
        total_samples=len(samples),
        total_groups=len({s.group_id for s in samples}),
        train_groups=meta["train_groups"],
        test_groups=meta["test_groups"],
        ndcg_at_5=ndcg5,
        ndcg_at_10=ndcg10,
        feature_importance=name_importance,
        model_path=str(model_path),
    )


def report_dict(report: TrainReport) -> dict:
    return asdict(report)
