from prism.sources.x import XAdapter
from prism.sources.arxiv import ArxivAdapter
from prism.sources.github import GithubAdapter
from prism.sources.follow_builders import FollowBuildersAdapter
from prism.sources.hackernews import HackernewsAdapter
from prism.sources.github_releases import GithubReleasesAdapter
from prism.sources.youtube import YoutubeAdapter
from prism.sources.model_economics import ModelEconomicsAdapter

ADAPTERS = {
    "x": XAdapter,
    "arxiv": ArxivAdapter,
    "github_trending": GithubAdapter,
    "follow_builders": FollowBuildersAdapter,
    "hackernews": HackernewsAdapter,
    "github_releases": GithubReleasesAdapter,
    "youtube": YoutubeAdapter,
    "model_economics": ModelEconomicsAdapter,
}
