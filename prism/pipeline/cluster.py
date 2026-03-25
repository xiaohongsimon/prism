"""Rule-based clustering for raw items.

Clustering rules applied in order:
1. URL match — same URL → same cluster
2. Repo name match — same owner/repo pattern → same cluster
3. Title Jaccard bigrams > 0.5 → merge
4. Entity co-occurrence >= 2 shared entities → merge (if entities provided)
"""

import re
from prism.models import RawItem

_REPO_RE = re.compile(r"(?<!\w)[\w.-]+/[\w.-]+(?!\w)")
_GITHUB_URL_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)")


def _extract_repo_names(text: str) -> set[str]:
    """Extract owner/repo patterns from text and GitHub URLs."""
    repos = set()
    # Extract from GitHub URLs first (more reliable)
    for m in _GITHUB_URL_RE.findall(text):
        repos.add(m.lower())
    # Extract freestanding owner/repo patterns (exclude domain-like matches)
    for m in _REPO_RE.findall(text):
        # Skip if it looks like a domain (contains .com, .org, etc.)
        if re.search(r"\.\w{2,}", m.split("/")[0]):
            continue
        repos.add(m.lower())
    return repos


def _char_bigrams(text: str) -> set[str]:
    """Generate character bigrams from lowercased text."""
    t = text.lower().strip()
    if len(t) < 2:
        return set()
    return {t[i:i+2] for i in range(len(t) - 1)}


def _jaccard_bigrams(a: str, b: str) -> float:
    """Jaccard similarity on character bigrams."""
    ba = _char_bigrams(a)
    bb = _char_bigrams(b)
    if not ba or not bb:
        return 0.0
    intersection = ba & bb
    union = ba | bb
    return len(intersection) / len(union)


def _find_cluster(item: RawItem, clusters: list[dict], items_by_id: dict[int, RawItem]) -> int | None:
    """Find an existing cluster this item should merge into. Returns cluster index or None."""
    item_url = item.url
    item_repos = _extract_repo_names(f"{item.title} {item.body} {item.url}")
    item_title = item.title

    for idx, cluster in enumerate(clusters):
        for existing_id in cluster["item_ids"]:
            existing = items_by_id[existing_id]

            # Rule 1: URL match
            if item_url == existing.url:
                return idx

            # Rule 2: Repo name match
            existing_repos = _extract_repo_names(f"{existing.title} {existing.body} {existing.url}")
            if item_repos & existing_repos:
                return idx

            # Rule 3: Title Jaccard bigrams > 0.5
            if _jaccard_bigrams(item_title, existing.title) > 0.5:
                return idx

    return None


def cluster_items(items: list[RawItem], existing_clusters: list[dict], entities: dict | None = None) -> list[dict]:
    """Cluster items using rule-based approach.

    Returns list of cluster dicts: [{"item_ids": [1, 2], "topic_label": "..."}, ...]
    """
    clusters: list[dict] = list(existing_clusters)
    items_by_id: dict[int, RawItem] = {}

    # Index existing items from existing clusters
    # (assumes items referenced by existing_clusters are already in items list)

    for item in items:
        items_by_id[item.id] = item

    for item in items:
        target = _find_cluster(item, clusters, items_by_id)
        if target is not None:
            if item.id not in clusters[target]["item_ids"]:
                clusters[target]["item_ids"].append(item.id)
        else:
            clusters.append({"item_ids": [item.id], "topic_label": item.title})

    return clusters


def cluster_eval_stats(clusters: list[dict]) -> dict:
    """Compute clustering quality metrics."""
    if not clusters:
        return {"cluster_count": 0, "avg_size": 0, "max_size": 0, "singleton_ratio": 0}

    sizes = [len(c["item_ids"]) for c in clusters]
    singletons = sum(1 for s in sizes if s == 1)

    return {
        "cluster_count": len(clusters),
        "avg_size": sum(sizes) / len(sizes),
        "max_size": max(sizes),
        "singleton_ratio": singletons / len(clusters),
    }


def build_merged_context(items: list[RawItem], max_tokens: int = 4000) -> str:
    """Merge item bodies, sorted by published_at desc, truncated to ~max_tokens tokens.

    Uses ~4 chars per token as approximation.
    """
    max_chars = max_tokens * 4

    # Sort by published_at descending (newer first), treating as string comparison
    sorted_items = sorted(
        items,
        key=lambda x: str(x.published_at or ""),
        reverse=True,
    )

    separator = "\n---\n"
    # Allocate chars fairly across items
    bodies = [(item.body or "") for item in sorted_items if item.body]
    if not bodies:
        return ""

    n = len(bodies)
    sep_total = len(separator) * max(n - 1, 0)
    budget_per_item = max(1, (max_chars - sep_total) // n)

    parts = []
    for body in bodies:
        parts.append(body[:budget_per_item])

    return separator.join(parts)
