"""Tests for prism.pipeline.entity_extract."""

import json

import pytest

from prism.pipeline.entity_extract import (
    STOPLIST,
    build_extraction_prompt,
    deterministic_candidates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(summary="", why="", tags=None):
    return {
        "summary": summary,
        "why_it_matters": why,
        "tags_json": json.dumps(tags or []),
        "topic_label": "Test Topic",
        "source_types": "twitter",
    }


def _mentions(candidates):
    return {c["mention"] for c in candidates}


def _sources(candidates):
    return {c["source"] for c in candidates}


# ---------------------------------------------------------------------------
# deterministic_candidates — GitHub repo URLs
# ---------------------------------------------------------------------------

class TestGitHubRepo:
    def test_extracts_repo_path(self):
        signal = _make_signal(summary="Check out https://github.com/vllm-project/vllm for fast inference.")
        candidates = deterministic_candidates(signal)
        mentions = _mentions(candidates)
        assert "vllm-project/vllm" in mentions

    def test_extracts_repo_name(self):
        signal = _make_signal(summary="https://github.com/vllm-project/vllm is trending.")
        candidates = deterministic_candidates(signal)
        mentions = _mentions(candidates)
        assert "vllm" in mentions

    def test_source_is_repo_url(self):
        signal = _make_signal(summary="See https://github.com/openai/tiktoken for details.")
        candidates = deterministic_candidates(signal)
        repo_candidates = [c for c in candidates if c["source"] == "repo_url"]
        assert len(repo_candidates) >= 1

    def test_extracts_from_why_it_matters(self):
        signal = _make_signal(why="See https://github.com/ggerganov/llama.cpp for the impl.")
        candidates = deterministic_candidates(signal)
        mentions = _mentions(candidates)
        assert "ggerganov/llama.cpp" in mentions


# ---------------------------------------------------------------------------
# deterministic_candidates — @handles
# ---------------------------------------------------------------------------

class TestHandles:
    def test_extracts_handle(self):
        signal = _make_signal(summary="@karpathy shared a thread on tokenisation.")
        candidates = deterministic_candidates(signal)
        mentions = _mentions(candidates)
        assert "karpathy" in mentions

    def test_source_is_handle(self):
        signal = _make_signal(summary="Announced by @sama on Twitter.")
        candidates = deterministic_candidates(signal)
        handle_candidates = [c for c in candidates if c["source"] == "handle"]
        assert any(c["mention"] == "sama" for c in handle_candidates)

    def test_multiple_handles(self):
        signal = _make_signal(summary="Both @karpathy and @ylecun weighed in.")
        candidates = deterministic_candidates(signal)
        mentions = _mentions(candidates)
        assert "karpathy" in mentions
        assert "ylecun" in mentions


# ---------------------------------------------------------------------------
# deterministic_candidates — Proper nouns
# ---------------------------------------------------------------------------

class TestProperNouns:
    def test_extracts_openai(self):
        signal = _make_signal(summary="OpenAI released a new capability today.")
        candidates = deterministic_candidates(signal)
        mentions = _mentions(candidates)
        assert "OpenAI" in mentions

    def test_extracts_anthropic(self):
        signal = _make_signal(summary="Anthropic published safety research.")
        candidates = deterministic_candidates(signal)
        mentions = _mentions(candidates)
        assert "Anthropic" in mentions

    def test_source_is_proper_noun(self):
        signal = _make_signal(summary="Mistral released a new model.")
        candidates = deterministic_candidates(signal)
        pn_candidates = [c for c in candidates if c["source"] == "proper_noun"]
        assert any(c["mention"] == "Mistral" for c in pn_candidates)

    def test_all_caps_not_extracted_as_proper_noun(self):
        # ALL_CAPS acronyms should not match _RE_PROPER_NOUN (no lowercase after uppercase)
        signal = _make_signal(summary="CPU GPU RAM are common acronyms.")
        candidates = deterministic_candidates(signal)
        # These may come through tag/other routes but should be filtered by stoplist
        mentions_lower = {c["mention"].lower() for c in candidates}
        assert "cpu" not in mentions_lower
        assert "gpu" not in mentions_lower
        assert "ram" not in mentions_lower


# ---------------------------------------------------------------------------
# deterministic_candidates — tags_json
# ---------------------------------------------------------------------------

class TestTagsJson:
    def test_extracts_tags(self):
        signal = _make_signal(tags=["vLLM", "SGLang", "DeepSeek"])
        candidates = deterministic_candidates(signal)
        mentions = _mentions(candidates)
        assert "vLLM" in mentions
        assert "SGLang" in mentions
        assert "DeepSeek" in mentions

    def test_source_is_tag(self):
        signal = _make_signal(tags=["vLLM"])
        candidates = deterministic_candidates(signal)
        tag_candidates = [c for c in candidates if c["source"] == "tag"]
        assert any(c["mention"] == "vLLM" for c in tag_candidates)

    def test_invalid_tags_json_does_not_raise(self):
        signal = {"summary": "hello", "why_it_matters": "", "tags_json": "not-json"}
        candidates = deterministic_candidates(signal)  # must not raise
        assert isinstance(candidates, list)

    def test_empty_tags_json(self):
        signal = _make_signal(tags=[])
        candidates = deterministic_candidates(signal)
        assert isinstance(candidates, list)


# ---------------------------------------------------------------------------
# deterministic_candidates — stoplist filtering
# ---------------------------------------------------------------------------

class TestStoplist:
    def test_stoplist_terms_filtered(self):
        for term in ["AI", "LLM", "model", "training", "inference"]:
            signal = _make_signal(tags=[term])
            candidates = deterministic_candidates(signal)
            mentions_lower = {c["mention"].lower() for c in candidates}
            assert term.lower() not in mentions_lower, f"Stoplist term '{term}' should be filtered"

    def test_stoplist_proper_noun_filtered(self):
        # "Training" starts with uppercase but "training" is in stoplist
        signal = _make_signal(summary="Training is important for all models.")
        candidates = deterministic_candidates(signal)
        mentions_lower = {c["mention"].lower() for c in candidates}
        assert "training" not in mentions_lower


# ---------------------------------------------------------------------------
# deterministic_candidates — empty / generic text
# ---------------------------------------------------------------------------

class TestEmptyGeneric:
    def test_empty_signal_returns_empty(self):
        signal = _make_signal(summary="", why="", tags=[])
        candidates = deterministic_candidates(signal)
        assert candidates == []

    def test_generic_lowercase_text_no_entities(self):
        signal = _make_signal(
            summary="new release with some bug fixes and performance updates",
            why="this is important for training and inference",
        )
        candidates = deterministic_candidates(signal)
        # All terms should be filtered by stoplist or not match patterns
        assert len(candidates) == 0

    def test_missing_keys_do_not_raise(self):
        # Minimal signal with only some keys present
        signal = {"summary": "Hello World from OpenAI"}
        candidates = deterministic_candidates(signal)
        mentions = _mentions(candidates)
        assert "OpenAI" in mentions


# ---------------------------------------------------------------------------
# deterministic_candidates — deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_dedup_case_insensitive(self):
        # "openai" tag + "OpenAI" proper noun should yield only one entry
        signal = _make_signal(
            summary="OpenAI is leading.",
            tags=["openai"],
        )
        candidates = deterministic_candidates(signal)
        mentions_lower = [c["mention"].lower() for c in candidates]
        assert mentions_lower.count("openai") == 1

    def test_min_length_filter(self):
        signal = _make_signal(tags=["A", "XY", "OK"])
        candidates = deterministic_candidates(signal)
        # Single-char mention "A" must be filtered; 2-char "XY"/"OK" kept if not in stoplist
        mentions = _mentions(candidates)
        assert "A" not in mentions


# ---------------------------------------------------------------------------
# build_extraction_prompt
# ---------------------------------------------------------------------------

class TestBuildExtractionPrompt:
    def _base_signal(self):
        return {
            "summary": "vLLM reached 20k GitHub stars and @karpathy tweeted about it.",
            "why_it_matters": "This signals strong community adoption of fast inference engines.",
            "tags_json": '["vLLM", "inference"]',
            "topic_label": "Open-source inference",
            "source_types": "twitter,github",
        }

    def test_returns_string(self):
        signal = self._base_signal()
        candidates = deterministic_candidates(signal)
        prompt = build_extraction_prompt(signal, candidates, [], "2026-03-29")
        assert isinstance(prompt, str)

    def test_contains_summary(self):
        signal = self._base_signal()
        candidates = deterministic_candidates(signal)
        prompt = build_extraction_prompt(signal, candidates, [], "2026-03-29")
        assert signal["summary"] in prompt

    def test_contains_why_it_matters(self):
        signal = self._base_signal()
        candidates = deterministic_candidates(signal)
        prompt = build_extraction_prompt(signal, candidates, [], "2026-03-29")
        assert signal["why_it_matters"] in prompt

    def test_contains_candidates_text(self):
        signal = self._base_signal()
        candidates = deterministic_candidates(signal)
        prompt = build_extraction_prompt(signal, candidates, [], "2026-03-29")
        # At least one candidate mention should appear in the prompt
        assert any(c["mention"] in prompt for c in candidates)

    def test_contains_known_entities(self):
        signal = self._base_signal()
        candidates = deterministic_candidates(signal)
        known = [{"display_name": "Triton", "canonical_name": "triton", "category": "project"}]
        prompt = build_extraction_prompt(signal, candidates, known, "2026-03-29")
        assert "Triton" in prompt

    def test_contains_date(self):
        signal = self._base_signal()
        candidates = deterministic_candidates(signal)
        prompt = build_extraction_prompt(signal, candidates, [], "2026-03-29")
        assert "2026-03-29" in prompt

    def test_no_candidates_shows_none_placeholder(self):
        signal = self._base_signal()
        prompt = build_extraction_prompt(signal, [], [], "2026-03-29")
        assert "(none)" in prompt

    def test_no_known_entities_shows_none_placeholder(self):
        signal = self._base_signal()
        candidates = deterministic_candidates(signal)
        prompt = build_extraction_prompt(signal, candidates, [], "2026-03-29")
        assert "(none)" in prompt

    def test_known_entities_as_strings(self):
        signal = self._base_signal()
        candidates = deterministic_candidates(signal)
        prompt = build_extraction_prompt(signal, candidates, ["DeepSeek", "Mixtral"], "2026-03-29")
        assert "DeepSeek" in prompt
        assert "Mixtral" in prompt
