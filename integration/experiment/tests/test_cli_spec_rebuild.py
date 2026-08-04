"""The CLI must not lose ExperimentSpec fields when it rebuilds a spec.

This is the regression that should have caught the bug in
docs/PROOF_REPAIR_HANDOFF.md 6.2: ``_build_spec`` and ``_with_sandbox_dir``
rebuilt ``ExperimentSpec`` field-by-field to inject ``--data-dir`` / the
sandbox path, and when ``replay_bootstrap`` was added as a fifth mode nobody
added it to those two constructors. It silently fell back to its dataclass
default of ``None``, so ``run_trial`` saw every mode field unset and dispatched
``--spec elgamal-changelog-repair`` down the *mutation* path with
``spec.mutations = None``.

The tests below are deliberately written against ``dataclasses.fields`` rather
than a hardcoded field list, so a sixth mode added later is covered the day it
is added rather than the day someone notices it never ran.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from integration.experiment.__main__ import _build_spec, _with_sandbox_dir
from integration.experiment.protocols import ExperimentSpec
from integration.experiment.runner import _experiment_mode
from integration.experiment.specs import SPECS, register_default_specs

# Rebuilt on purpose (that is the whole point of the CLI rebuild); every other
# field must survive byte-identical.
_REBUILT_FIELDS = {"corpus"}

_CARRIED_FIELDS = [f.name for f in fields(ExperimentSpec) if f.name not in _REBUILT_FIELDS]


@pytest.fixture(scope="module")
def spec_names() -> list[str]:
    register_default_specs(Path("data"))
    return list(SPECS.names())


def test_registry_is_not_empty(spec_names):
    assert spec_names, "no specs registered; the rest of this module is vacuous"


@pytest.mark.parametrize("field_name", _CARRIED_FIELDS)
def test_build_spec_carries_every_non_corpus_field(field_name, spec_names):
    """Every registered spec keeps every non-corpus field through _build_spec."""
    for name in spec_names:
        registered = SPECS.get(name)
        rebuilt = _build_spec(name, Path("data"))
        assert getattr(rebuilt, field_name) == getattr(registered, field_name), (
            f"spec {name!r} lost field {field_name!r} in _build_spec"
        )


@pytest.mark.parametrize("field_name", _CARRIED_FIELDS)
def test_with_sandbox_dir_carries_every_non_corpus_field(field_name, tmp_path, spec_names):
    """Same guarantee through the second rebuild (_with_sandbox_dir)."""
    for name in spec_names:
        registered = SPECS.get(name)
        rebuilt = _with_sandbox_dir(
            _build_spec(name, Path("data")), Path("data"), tmp_path / "sandboxes"
        )
        assert getattr(rebuilt, field_name) == getattr(registered, field_name), (
            f"spec {name!r} lost field {field_name!r} in _with_sandbox_dir"
        )


def test_replay_bootstrap_survives_the_full_cli_path(tmp_path):
    """The specific field, mode, and spec that 6.2 broke.

    Kept as an explicit case alongside the generic ones above so the failure
    message names the actual regression rather than a parametrized field id.
    """
    register_default_specs(Path("data"))
    registered = SPECS.get("elgamal-changelog-repair")
    assert registered.replay_bootstrap is not None, "fixture spec lost its mode marker"

    spec = _with_sandbox_dir(
        _build_spec("elgamal-changelog-repair", Path("data")),
        Path("data"),
        tmp_path / "sandboxes",
    )

    assert spec.replay_bootstrap is not None
    assert spec.replay_bootstrap == registered.replay_bootstrap
    # The version endpoints ride along whatever they are. They are None by
    # design on this spec (meaning "detect per trial" -- see ec_version.py),
    # so assert the field survives rather than that it holds a value.
    assert (
        spec.replay_bootstrap.source_ec_version
        == registered.replay_bootstrap.source_ec_version
    )
    assert (
        spec.replay_bootstrap.target_ec_version
        == registered.replay_bootstrap.target_ec_version
    )


def test_corpus_is_rebuilt_against_the_cli_data_dir(tmp_path, spec_names):
    """The rebuild still has to actually do its job."""
    for name in spec_names:
        spec = _with_sandbox_dir(
            _build_spec(name, tmp_path / "data"), tmp_path / "data", tmp_path / "sand"
        )
        assert Path(spec.corpus.data_dir) == tmp_path / "data"


@pytest.mark.parametrize(
    "spec_name,expected_mode",
    [
        ("joy-tactic-repair", "mutation"),
        ("joy-informal-repair", "informal"),
        ("elgamal-broken-repair", "broken_formal"),
        ("elgamal-changelog-repair", "replay_bootstrap"),
    ],
)
def test_experiment_mode_labels_every_spec(spec_name, expected_mode, spec_names):
    """summary.json's `mode` must agree with the dispatch run_trial performs."""
    assert _experiment_mode(SPECS.get(spec_name)) == expected_mode


def test_every_registered_spec_has_a_mode_label(spec_names):
    """A new spec must not silently inherit the 'mutation' fallback label."""
    mode_fields = [
        f.name
        for f in fields(ExperimentSpec)
        if f.name not in {"name", "corpus", "mutations"}
    ]
    for name in spec_names:
        spec = SPECS.get(name)
        mode = _experiment_mode(spec)
        if mode == "mutation":
            assert spec.mutations is not None, (
                f"spec {name!r} labels as 'mutation' but sets no mutation strategy; "
                f"_experiment_mode is probably missing a branch for one of {mode_fields}"
            )


# --- embeddings preflight ---------------------------------------------------
# Embeddings always run on LM Studio regardless of chat provider (Anthropic has
# no embeddings API at all), so a paid run whose embedder is unreachable would
# fail in _build_premise_index -- AFTER a human authorized the spend. The CLI
# checks first; these assert that ordering holds.


def test_preflight_reports_unreachable_endpoint():
    from integration.agent.config import AgentConfig
    from integration.experiment.__main__ import _embeddings_endpoint_status

    agent = AgentConfig()
    agent.lm_studio_base_url = "http://127.0.0.1:9/v1"  # discard port: always refuses
    ok, detail = _embeddings_endpoint_status(agent)
    assert ok is False
    assert detail


def test_preflight_rejects_a_server_with_no_embedding_model(monkeypatch):
    from integration.agent.config import AgentConfig
    from integration.experiment import __main__ as cli

    class _Models:
        data = [type("M", (), {"id": "some-chat-model"})()]

    class _Client:
        def __init__(self, **kwargs):
            pass

        @property
        def models(self):
            return type("X", (), {"list": staticmethod(lambda: _Models())})()

    monkeypatch.setattr("openai.OpenAI", _Client)
    ok, detail = cli._embeddings_endpoint_status(AgentConfig())
    assert ok is False
    assert "embed" in detail


def test_preflight_accepts_a_loaded_embedding_model(monkeypatch):
    from integration.agent.config import AgentConfig
    from integration.experiment import __main__ as cli

    class _Models:
        data = [type("M", (), {"id": "text-embedding-nomic"})()]

    class _Client:
        def __init__(self, **kwargs):
            pass

        @property
        def models(self):
            return type("X", (), {"list": staticmethod(lambda: _Models())})()

    monkeypatch.setattr("openai.OpenAI", _Client)
    ok, _ = cli._embeddings_endpoint_status(AgentConfig())
    assert ok is True


def test_paid_run_aborts_before_the_confirmation_when_embedder_is_down(
    monkeypatch, tmp_path, capsys
):
    """The ordering guarantee: no spend prompt for a run that cannot start."""
    from unittest.mock import patch

    from integration.experiment.__main__ import main

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    with patch(
        "integration.experiment.__main__.confirm_paid_provider_usage"
    ) as confirm, patch(
        "integration.experiment.__main__._embeddings_endpoint_status",
        return_value=(False, "refused"),
    ):
        code = main([
            "run", "--provider", "deepseek", "--trials", "1",
            "--data-dir", str(tmp_path), "--output-dir", str(tmp_path / "out"),
        ])

    assert code == 1
    confirm.assert_not_called()
    assert "No paid call was made" in capsys.readouterr().err


# --- remaining-original reference --------------------------------------------


def test_replay_bootstrap_shows_remaining_original_by_default():
    """The suffix of the original proof must reach the solver.

    Replay-bootstrap used to discard every original tactic after the break,
    so the model reconstructed the remaining structure from scratch while the
    2020 author's intent sat unused in the corpus file.
    """
    from integration.experiment.protocols import ReplayBootstrapConfig

    assert ReplayBootstrapConfig().show_remaining_original is True
    assert ReplayBootstrapConfig(show_remaining_original=False).show_remaining_original is False


def test_remaining_original_heading_does_not_claim_untested_tactics_are_broken():
    """Only the FIRST remaining tactic is known-broken.

    The generic `informal_proof_is_formal` heading says the script "does NOT
    compile", which is false for tactics that were never reached and would
    invite the model to discard usable structure.
    """
    from integration.agent.prompt import build_prompt

    heading = (
        "## Original proof from this point (reference — the FIRST line below "
        "is the tactic that just failed)"
    )
    prompt = build_prompt(
        goal="pre = x = 1\npost = res = 2",
        top_premises={},
        failed_tactics=[],
        proof_tail="proof.",
        informal_proof="seq 4 3 : (={glob Adv}).\nwp.\nskip.",
        informal_proof_is_formal=True,
        informal_proof_heading=heading,
    )
    assert heading in prompt
    assert "does NOT compile" not in prompt
    assert "seq 4 3 : (={glob Adv})." in prompt


def test_default_formal_heading_is_unchanged_without_an_override():
    """Other specs (elgamal-broken-repair) must keep their existing wording."""
    from integration.agent.prompt import build_prompt

    prompt = build_prompt(
        goal="g", top_premises={}, failed_tactics=[], proof_tail="",
        informal_proof="wp.", informal_proof_is_formal=True,
    )
    assert "## Broken formal proof" in prompt
