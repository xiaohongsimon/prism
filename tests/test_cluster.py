from prism.pipeline.cluster import cluster_items, build_merged_context, cluster_eval_stats
from prism.models import RawItem


def test_cluster_by_url():
    items = [
        RawItem(id=1, url="https://github.com/vllm-project/vllm", title="vLLM trending", source_id=1),
        RawItem(id=2, url="https://github.com/vllm-project/vllm", title="vLLM is great", source_id=2),
    ]
    clusters = cluster_items(items, existing_clusters=[])
    assert len(clusters) == 1
    assert len(clusters[0]["item_ids"]) == 2


def test_cluster_by_repo_name():
    items = [
        RawItem(id=1, url="https://twitter.com/...", title="vllm-project/vllm just hit 30k stars", body="amazing", source_id=1),
        RawItem(id=2, url="https://github.com/vllm-project/vllm", title="vLLM trending", source_id=2),
    ]
    clusters = cluster_items(items, existing_clusters=[])
    assert len(clusters) == 1


def test_cluster_by_title_similarity():
    items = [
        RawItem(id=1, url="https://a.com/1", title="OpenAI releases GPT-5 with new reasoning", source_id=1),
        RawItem(id=2, url="https://b.com/2", title="GPT-5 released by OpenAI with advanced reasoning", source_id=2),
    ]
    clusters = cluster_items(items, existing_clusters=[])
    assert len(clusters) == 1


def test_no_false_merge():
    items = [
        RawItem(id=1, url="https://a.com/1", title="New LLM architecture paper", source_id=1),
        RawItem(id=2, url="https://b.com/2", title="Rust web framework release", source_id=2),
    ]
    clusters = cluster_items(items, existing_clusters=[])
    assert len(clusters) == 2


def test_cluster_eval_stats():
    clusters = [
        {"item_ids": [1, 2, 3]},
        {"item_ids": [4]},
        {"item_ids": [5]},
        {"item_ids": [6, 7]},
    ]
    stats = cluster_eval_stats(clusters)
    assert stats["cluster_count"] == 4
    assert stats["avg_size"] == 1.75
    assert stats["max_size"] == 3
    assert stats["singleton_ratio"] == 0.5  # 2 out of 4


def test_build_merged_context():
    items = [
        RawItem(id=1, body="First item body " * 100, published_at="2026-03-24T10:00:00", source_id=1),
        RawItem(id=2, body="Second item body " * 100, published_at="2026-03-24T11:00:00", source_id=2),
    ]
    context = build_merged_context(items, max_tokens=100)
    assert len(context) > 0
    # Newer item should appear first
    assert context.index("Second") < context.index("First")
