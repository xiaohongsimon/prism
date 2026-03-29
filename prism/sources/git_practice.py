"""Git practice adapter — tracks daily commit activity across local repos.

For each configured repo path, runs `git log` and `git diff --stat` to produce
a daily activity summary as a RawItem. Useful for self-reflection and practice
tracking in the Prism briefing pipeline.
"""

import json
import logging
import subprocess
from datetime import date, datetime
from pathlib import Path

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)


def _run_git(args: list[str], cwd: str) -> str:
    """Run a git command in the given directory, return stdout as string."""
    result = subprocess.run(
        ["git", "-C", cwd] + args,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def _parse_commits(log_output: str) -> list[dict]:
    """Parse git log --format='%H|%s|%an|%ai' output into list of dicts."""
    commits = []
    for line in log_output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        commits.append({
            "hash": parts[0],
            "subject": parts[1],
            "author": parts[2],
            "date": parts[3],
        })
    return commits


def _build_body(repo_name: str, commits: list[dict], diff_stat: str) -> str:
    """Build a human-readable body from commit list and diff stat."""
    lines = [f"## {repo_name} — {date.today().isoformat()}"]
    lines.append(f"\n### Recent commits ({len(commits)})")
    for c in commits:
        lines.append(f"- [{c['hash'][:8]}] {c['subject']} ({c['author']}, {c['date'][:10]})")
    if diff_stat:
        lines.append("\n### Last 5 commits file changes")
        lines.append(diff_stat)
    return "\n".join(lines)


def sync_repo(repo_path: str, lookback_hours: int) -> RawItem | None:
    """Sync a single git repo. Returns None if no recent commits."""
    path = Path(repo_path)
    if not path.exists():
        logger.warning("git_practice: repo path does not exist: %s", repo_path)
        return None

    repo_name = path.name

    # Get commits since lookback window
    try:
        log_output = _run_git(
            [
                "log",
                f"--since={lookback_hours} hours ago",
                "--format=%H|%s|%an|%ai",
            ],
            str(path),
        )
    except RuntimeError as e:
        logger.warning("git_practice: git log failed for %s: %s", repo_path, e)
        return None

    commits = _parse_commits(log_output)
    if not commits:
        logger.debug("git_practice: no recent commits in %s", repo_path)
        return None

    # Get diff stat for last 5 commits
    diff_stat = ""
    try:
        diff_stat = _run_git(["diff", "--stat", "HEAD~5..HEAD"], str(path))
    except RuntimeError as e:
        logger.debug("git_practice: diff --stat failed for %s: %s", repo_path, e)

    today = date.today().isoformat()
    body = _build_body(repo_name, commits, diff_stat)

    return RawItem(
        url=f"git:{repo_name}:{today}",
        title=f"[Practice] {repo_name} daily activity",
        body=body,
        author="",
        raw_json=json.dumps(
            {
                "repo": repo_path,
                "repo_name": repo_name,
                "date": today,
                "commits": commits,
                "diff_stat": diff_stat,
            },
            ensure_ascii=False,
        ),
    )


class GitPracticeAdapter:
    """Source adapter that tracks daily git commit activity across local repos."""

    async def sync(self, config: dict) -> SyncResult:
        """Generate daily git activity RawItems for each configured repo.

        Config keys:
            key (str): source key used in SyncResult
            repos (list[str]): list of local repository paths
            lookback_hours (int): how many hours back to check for commits (default: 24)
        """
        source_key = config.get("key", "practice:git")
        repos: list[str] = config.get("repos", [])
        lookback_hours: int = int(config.get("lookback_hours", 24))

        items: list[RawItem] = []
        skipped = 0

        for repo_path in repos:
            item = sync_repo(repo_path, lookback_hours)
            if item is not None:
                items.append(item)
            else:
                skipped += 1

        logger.info(
            "git_practice: %d repos → %d items, %d skipped",
            len(repos),
            len(items),
            skipped,
        )
        return SyncResult(
            source_key=source_key,
            items=items,
            success=True,
            stats={"repos": len(repos), "items": len(items), "skipped": skipped},
        )
