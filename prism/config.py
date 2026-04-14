from dataclasses import dataclass, field
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("PRISM_DB_PATH", "data/prism.sqlite3")))
    source_config: Path = field(default_factory=lambda: Path(os.getenv("PRISM_SOURCE_CONFIG", "config/sources.yaml")))
    entity_config: Path = field(default_factory=lambda: Path(os.getenv("PRISM_ENTITY_CONFIG", "config/entities.yaml")))
    llm_base_url: str = field(default_factory=lambda: os.getenv("PRISM_LLM_BASE_URL", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("PRISM_LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("PRISM_LLM_MODEL", "qwen-plus"))
    llm_cheap_model: str = field(default_factory=lambda: os.getenv("PRISM_LLM_CHEAP_MODEL", "qwen-turbo"))
    llm_premium_base_url: str = field(default_factory=lambda: os.getenv("PRISM_LLM_PREMIUM_BASE_URL", ""))
    llm_premium_api_key: str = field(default_factory=lambda: os.getenv("PRISM_LLM_PREMIUM_API_KEY", ""))
    llm_premium_model: str = field(default_factory=lambda: os.getenv("PRISM_LLM_PREMIUM_MODEL", ""))
    api_token: str = field(default_factory=lambda: os.getenv("PRISM_API_TOKEN", ""))
    notion_api_key: str = field(default_factory=lambda: os.getenv("NOTION_API_KEY", ""))
    notion_parent_page_id: str = field(default_factory=lambda: os.getenv("NOTION_BRIEFING_PARENT_PAGE_ID", ""))


settings = Settings()
