"""Entity tagging: load entity dictionary and tag text."""

from pathlib import Path
import yaml


def load_entities(yaml_path: Path) -> dict:
    """Load entities dict from YAML file.

    Expected format:
        project: [vLLM, SGLang]
        org: [OpenAI, Anthropic]
        person: [{handle: karpathy, name: Andrej Karpathy}]
    """
    data = yaml.safe_load(yaml_path.read_text()) or {}
    return data


def tag_entities(title: str, body: str, entities: dict) -> set[str]:
    """Case-insensitive substring match against entity names.

    Returns set of matched entity names.
    """
    text = f"{title} {body}".lower()
    matched = set()

    for category, items in entities.items():
        for item in items:
            if isinstance(item, dict):
                # Person-style entry: check both name and handle
                name = item.get("name", "")
                handle = item.get("handle", "")
                if name and name.lower() in text:
                    matched.add(name)
                if handle and handle.lower() in text:
                    matched.add(name or handle)
            else:
                # Simple string entry
                if str(item).lower() in text:
                    matched.add(str(item))

    return matched
