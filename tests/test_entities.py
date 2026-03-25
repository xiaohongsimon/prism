from prism.pipeline.entities import load_entities, tag_entities


def test_load_entities(tmp_path):
    (tmp_path / "entities.yaml").write_text("""
project: [vLLM, SGLang]
org: [OpenAI, Anthropic]
person: [{handle: karpathy, name: Andrej Karpathy}]
""")
    entities = load_entities(tmp_path / "entities.yaml")
    assert "vLLM" in entities["project"]
    assert entities["person"][0]["name"] == "Andrej Karpathy"


def test_tag_entities():
    entities = {"project": ["vLLM", "SGLang"], "org": ["OpenAI"], "person": []}
    tags = tag_entities("New vLLM release from the OpenAI team", "", entities)
    assert "vLLM" in tags
    assert "OpenAI" in tags
    assert "SGLang" not in tags
