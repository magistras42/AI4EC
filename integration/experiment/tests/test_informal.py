"""Tests for the informal-proof repair pipeline."""

from __future__ import annotations

import random
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from integration.agent.config import AgentConfig
from integration.experiment.informal import (
    InformalConfig,
    InformalWriterError,
    build_labeled_manifest,
    build_lemma_manifest,
    extract_used_lemma_names,
    looks_contaminated,
    looks_truncated,
    select_red_herrings,
    write_informal_proof,
)


def test_extract_used_lemma_names_matches_word_boundaries():
    tactic_text = "  by rewrite addrC addr0.\n  smt(mulrC).\n"
    candidates = ["addrC", "addr0", "mulrC", "subrK", "addrA"]
    used = extract_used_lemma_names(tactic_text, candidates)
    assert used == ["addr0", "addrC", "mulrC"]


def test_extract_used_lemma_names_does_not_match_substrings():
    tactic_text = "by rewrite addrCextra."
    candidates = ["addrC"]
    assert extract_used_lemma_names(tactic_text, candidates) == []


def test_looks_contaminated_detects_easycrypt_syntax():
    assert looks_contaminated("First we `qed.` the argument.")
    assert looks_contaminated("Apply the tactic: by rewrite addrC.")
    assert looks_contaminated("```\nby smt.\n```")
    assert not looks_contaminated(
        "We apply the induction hypothesis to split the sum into two parts, "
        "and conclude by a direct computation."
    )


def test_build_lemma_manifest_is_alphabetical_and_merged():
    used = {"zeta_lemma": "sig-z", "alpha_lemma": "sig-a"}
    herrings = {"beta_lemma": "sig-b"}
    manifest = build_lemma_manifest(used, herrings)
    assert [name for name, _ in manifest] == ["alpha_lemma", "beta_lemma", "zeta_lemma"]


def test_build_labeled_manifest_marks_real_and_decoy():
    used = {"real_one": "sig-r"}
    herrings = {"decoy_one": "sig-d"}
    labeled = build_labeled_manifest(used, herrings)
    assert labeled["real_one"]["is_real"] is True
    assert labeled["decoy_one"]["is_real"] is False


def _fake_embed_index(premises: dict[str, str]) -> dict[str, np.ndarray]:
    """Deterministic fake embedding: closeness derived from name similarity."""
    vectors = {}
    for name in premises:
        seed = sum(ord(c) for c in name)
        rng = np.random.RandomState(seed % (2**31))
        vectors[name] = rng.rand(8)
    return vectors


@patch("integration.experiment.informal.EmbeddingClient")
def test_select_red_herrings_respects_ratio_and_excludes_used(mock_embed_cls):
    mock_embedder = MagicMock()
    mock_embed_cls.return_value = mock_embedder
    mock_embedder.build_index.side_effect = _fake_embed_index

    used = {"a": "sig-a", "b": "sig-b", "c": "sig-c"}
    catalog = {
        **used,
        "d": "sig-d",
        "e": "sig-e",
        "f": "sig-f",
        "g": "sig-g",
    }
    rng = random.Random(0)
    herrings = select_red_herrings(used, catalog, AgentConfig(), ratio=0.3, rng=rng)

    assert len(herrings) == max(1, round(0.3 * len(used)))
    assert not (set(herrings) & set(used))
    assert set(herrings).issubset(set(catalog) - set(used))


@patch("integration.experiment.informal.EmbeddingClient")
def test_select_red_herrings_empty_when_no_used_lemmas(mock_embed_cls):
    herrings = select_red_herrings({}, {"x": "sig-x"}, AgentConfig(), ratio=0.3, rng=random.Random(0))
    assert herrings == {}
    mock_embed_cls.assert_not_called()


def _fake_choice(content: str, finish_reason: str = "stop") -> MagicMock:
    return MagicMock(message=MagicMock(content=content), finish_reason=finish_reason)


def test_looks_truncated_detects_length_finish_reason_and_empty_text():
    assert looks_truncated("", "stop")
    assert looks_truncated("Some unfinished sentence, mid-thought", "length")
    assert not looks_truncated("A complete sentence.", "stop")
    # Unknown finish_reason: fall back to a punctuation heuristic.
    assert looks_truncated("this trails off without punctuation", None)
    assert not looks_truncated("This is complete.", None)


@patch("integration.experiment.informal.OpenAI")
def test_write_informal_proof_retries_on_contamination(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

    contaminated = MagicMock(choices=[_fake_choice("by rewrite addrC.")])
    clean = MagicMock(
        choices=[
            _fake_choice(
                "We reason by symmetry of addition to conclude the equality."
            )
        ]
    )
    mock_client.chat.completions.create.side_effect = [contaminated, clean]

    config = AgentConfig(llm_model="test-model")
    informal_config = InformalConfig()
    text = write_informal_proof("lemma foo : true.", "trivial.", config, informal_config)

    assert text == "We reason by symmetry of addition to conclude the equality."
    assert mock_client.chat.completions.create.call_count == 2


@patch("integration.experiment.informal.OpenAI")
def test_write_informal_proof_retries_with_larger_budget_on_truncation(mock_openai_cls):
    """Regression: local "thinking" models can spend most of `max_tokens` on
    hidden reasoning, silently truncating the visible answer
    (`finish_reason == "length"`, mid-sentence content). The writer must
    detect this and retry with a larger token budget rather than silently
    accepting a cut-off proof."""
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

    truncated = MagicMock(
        choices=[_fake_choice("To prove that x is pos", finish_reason="length")]
    )
    complete = MagicMock(
        choices=[_fake_choice("To prove that x is positive, we reason directly.")]
    )
    mock_client.chat.completions.create.side_effect = [truncated, complete]

    config = AgentConfig(llm_model="test-model")
    informal_config = InformalConfig(writer_max_tokens=100)
    text = write_informal_proof("lemma foo : true.", "trivial.", config, informal_config)

    assert text == "To prove that x is positive, we reason directly."
    assert mock_client.chat.completions.create.call_count == 2
    first_kwargs = mock_client.chat.completions.create.call_args_list[0].kwargs
    second_kwargs = mock_client.chat.completions.create.call_args_list[1].kwargs
    assert first_kwargs["max_tokens"] == 100
    assert second_kwargs["max_tokens"] > 100


@patch("integration.experiment.informal.OpenAI")
def test_write_informal_proof_raises_after_persistent_truncation(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

    always_truncated = MagicMock(
        choices=[_fake_choice("still cut off", finish_reason="length")]
    )
    mock_client.chat.completions.create.return_value = always_truncated

    config = AgentConfig(llm_model="test-model")
    informal_config = InformalConfig(writer_max_retries=2)

    with pytest.raises(InformalWriterError):
        write_informal_proof("lemma foo : true.", "trivial.", config, informal_config)

    assert mock_client.chat.completions.create.call_count == 3


@patch("integration.experiment.informal.OpenAI")
def test_write_informal_proof_raises_on_empty_response(mock_openai_cls):
    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client

    empty = MagicMock(choices=[_fake_choice("", finish_reason="length")])
    mock_client.chat.completions.create.return_value = empty

    config = AgentConfig(llm_model="test-model")
    informal_config = InformalConfig(writer_max_retries=1)

    with pytest.raises(InformalWriterError):
        write_informal_proof("lemma foo : true.", "trivial.", config, informal_config)
