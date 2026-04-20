"""CTR ranking model — XGBoost-based personalized fine-ranking layer.

Pipeline:
  impressions  ← logged at serve time (/feed/more)
  samples      ← (positive=save, negatives=skip-above peers in same session)
  features     ← numeric/one-hot per signal
  train        ← xgb.XGBRanker with rank:pairwise on save-event groups
  model        ← predict() used (optionally) by /feed/more for re-ranking
"""
