from prism.sources.x import XAdapter
from prism.sources.arxiv import ArxivAdapter
from prism.sources.github import GithubAdapter

ADAPTERS = {
    "x": XAdapter,
    "arxiv": ArxivAdapter,
    "github_trending": GithubAdapter,
}
