from pathlib import Path

from prism.sources.arxiv import parse_rss, keyword_filter
from prism.models import RawItem

FIXTURE = Path(__file__).parent.parent / "fixtures" / "arxiv_rss_response.xml"


def test_parse_rss():
    xml_text = FIXTURE.read_text()
    papers = parse_rss(xml_text)
    assert len(papers) == 3
    assert all(p.url.startswith("http") for p in papers)


def test_parse_rss_fields():
    """Verify all fields are populated correctly."""
    xml_text = FIXTURE.read_text()
    papers = parse_rss(xml_text)
    first = papers[0]
    assert "LLM" in first.title
    assert first.author != ""
    assert first.body != ""
    assert first.url == "http://arxiv.org/abs/2603.12345"


def test_keyword_filter():
    items = [
        RawItem(title="A New LLM Architecture for Reasoning", body="We propose..."),
        RawItem(title="Optimal Transport in Protein Folding", body="Biology..."),
    ]
    filtered = keyword_filter(items)
    assert len(filtered) == 1
    assert "LLM" in filtered[0].title


def test_keyword_filter_matches_body():
    """Keywords in body should also match."""
    items = [
        RawItem(title="Some generic title", body="We use RLHF to improve alignment."),
    ]
    filtered = keyword_filter(items)
    assert len(filtered) == 1


def test_keyword_filter_case_insensitive():
    items = [
        RawItem(title="fine-tuning with lora adapters", body=""),
    ]
    filtered = keyword_filter(items)
    assert len(filtered) == 1


def test_keyword_filter_on_fixture():
    """Run keyword filter on the full fixture — should match 2 of 3 papers."""
    xml_text = FIXTURE.read_text()
    papers = parse_rss(xml_text)
    filtered = keyword_filter(papers)
    # Paper 1: LLM + mixture of experts -> match
    # Paper 2: protein folding -> no match
    # Paper 3: RLHF + alignment -> match
    assert len(filtered) == 2
    titles = [p.title for p in filtered]
    assert any("LLM" in t for t in titles)
    assert any("RLHF" in t for t in titles)
