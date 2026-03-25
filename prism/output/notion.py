"""Notion publishing: create briefing pages."""

import httpx


NOTION_API_URL = "https://api.notion.com/v1/pages"
NOTION_VERSION = "2022-06-28"


def _markdown_to_notion_blocks(markdown: str) -> list[dict]:
    """Convert markdown text to Notion block objects (simplified)."""
    blocks = []
    for line in markdown.split("\n"):
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("# "):
            blocks.append({
                "object": "block", "type": "heading_1",
                "heading_1": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]}
            })
        elif line.startswith("## "):
            blocks.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]}
            })
        elif line.startswith("### "):
            blocks.append({
                "object": "block", "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:]}}]}
            })
        elif line.startswith("- "):
            blocks.append({
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]}
            })
        else:
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": line}}]}
            })
    return blocks


def publish_briefing_to_notion(markdown: str, date: str, api_key: str,
                                parent_page_id: str) -> dict:
    """Create a Notion page with the briefing content.

    Returns the Notion API response dict.
    """
    blocks = _markdown_to_notion_blocks(markdown)

    payload = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {
                "title": [{"text": {"content": f"Prism Daily Brief — {date}"}}]
            }
        },
        "children": blocks[:100],  # Notion limit per request
    }

    resp = httpx.post(
        NOTION_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()
