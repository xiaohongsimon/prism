from unittest.mock import patch, MagicMock
from prism.output.notion import publish_briefing_to_notion


def test_publish_creates_notion_page():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "page-123"}
    with patch("prism.output.notion.httpx.post", return_value=mock_response) as mock_post:
        result = publish_briefing_to_notion(
            markdown="# Test Brief", date="2026-03-24",
            api_key="fake-key", parent_page_id="parent-123")
        assert result["id"] == "page-123"
        mock_post.assert_called_once()
