from prism.sources.x import XAdapter
from prism.sources.x_home import XHomeAdapter
from prism.sources.arxiv import ArxivAdapter
from prism.sources.github import GithubAdapter
from prism.sources.follow_builders import FollowBuildersAdapter
from prism.sources.hackernews import HackernewsAdapter
from prism.sources.github_releases import GithubReleasesAdapter
from prism.sources.youtube import YoutubeAdapter
from prism.sources.model_economics import ModelEconomicsAdapter
from prism.sources.git_practice import GitPracticeAdapter
from prism.sources.claude_sessions import ClaudeSessionsAdapter
from prism.sources.hn_search import HnSearchAdapter
from prism.sources.reddit import RedditAdapter
from prism.sources.producthunt import ProductHuntAdapter
from prism.sources.xiaoyuzhou import XiaoyuzhouAdapter

ADAPTERS = {
    "x": XAdapter,
    "x_home": XHomeAdapter,
    "arxiv": ArxivAdapter,
    "github_trending": GithubAdapter,
    "follow_builders": FollowBuildersAdapter,
    "hackernews": HackernewsAdapter,
    "hn_search": HnSearchAdapter,
    "reddit": RedditAdapter,
    "producthunt": ProductHuntAdapter,
    "github_releases": GithubReleasesAdapter,
    "youtube": YoutubeAdapter,
    "model_economics": ModelEconomicsAdapter,
    "git_practice": GitPracticeAdapter,
    "claude_sessions": ClaudeSessionsAdapter,
    "xiaoyuzhou": XiaoyuzhouAdapter,
}
