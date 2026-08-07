"""A provider failure must not be counted as the model being stuck.

Measured across four DeepSeek trials, 79 of 85 recoverable errors were the
provider producing nothing usable (output budget spent reasoning). Counting
those as "unproductive iterations" made budget exhaustion indistinguishable
from a model that could not make progress -- one trial exited STUCK at 20
iterations of which 14 were empty replies.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from integration.agent.config import (
    THINKING_FAILURE_OUTCOMES,
    AgentConfig,
    apply_deepseek_provider,
    resolve_thinking_for_step,
)
from integration.agent.llm import ChatReply, LlmFormatError, LlmProviderError


def test_provider_error_is_a_format_error_subclass():
    """Existing `except LlmFormatError` handlers must keep catching it."""
    assert issubclass(LlmProviderError, LlmFormatError)
    assert isinstance(LlmProviderError("x"), LlmFormatError)


def test_provider_error_outcome_does_not_trigger_adaptive_thinking(monkeypatch):
    """The feedback loop: thinking exhausted the budget, so the harness turned
    thinking ON for the next step, exhausting it again."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    config = apply_deepseek_provider(AgentConfig(), thinking="adaptive")

    assert "provider_error" not in THINKING_FAILURE_OUTCOMES, (
        "a budget-exhaustion outcome must not switch thinking back on"
    )
    assert resolve_thinking_for_step(config, [{"outcome": "provider_error"}]) == "disabled"
    # A genuinely malformed answer still does enable thinking.
    assert "format_error" in THINKING_FAILURE_OUTCOMES
    assert resolve_thinking_for_step(config, [{"outcome": "format_error"}]) == "enabled"


class _FailingBackend:
    """Raises a scripted sequence, then returns a usable reply forever."""

    def __init__(self, errors):
        self._errors = list(errors)
        self.calls = 0

    def resolve_model(self):
        return "scripted"

    def complete(self, **kwargs):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return ChatReply(text='{"action": "tactic", "tactic": "trivial."}')


def _run(monkeypatch, tmp_path, backend, **overrides):
    from integration.agent import loop as loop_mod
    from integration.agent.llm import LlmClient

    proof = tmp_path / "p.ec"
    proof.write_text("lemma x : true.\nproof.\n", encoding="utf-8")
    config = AgentConfig(max_steps=overrides.pop("max_steps", 8), output_dir=tmp_path,
                         stuck_limit=overrides.pop("stuck_limit", 3), **overrides)

    monkeypatch.setattr(loop_mod, "LlmClient", lambda cfg: LlmClient(cfg, backend=backend))
    monkeypatch.setattr(loop_mod, "_startup", lambda *a, **k: None)
    monkeypatch.setattr(loop_mod, "_build_premise_index", lambda *a, **k: ({}, {}, {}))
    monkeypatch.setattr(loop_mod, "EmbeddingClient",
                        lambda *a, **k: SimpleNamespace(embed=lambda t: None))
    monkeypatch.setattr(loop_mod, "resolve_goal", lambda *a, **k: "goal: x = x")
    monkeypatch.setattr(loop_mod, "resolve_goal_cursor", lambda *a, **k: 1)
    monkeypatch.setattr(loop_mod, "is_proof_discharged", lambda *a, **k: False)
    monkeypatch.setattr(loop_mod, "tactic_discharged_proof", lambda *a, **k: False)
    monkeypatch.setattr(loop_mod, "is_no_active_proof", lambda *a, **k: False)
    monkeypatch.setattr(loop_mod, "rank_by_cosine", lambda *a, **k: [])
    monkeypatch.setattr(loop_mod, "top_premises", lambda *a, **k: {})
    monkeypatch.setattr(loop_mod, "fetch_goal", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="goal", stderr=""))
    monkeypatch.setattr(loop_mod, "is_proof_complete_at_cursor", lambda *a, **k: True)
    return loop_mod.run_agent(proof, config, work_copy=tmp_path / "w.agent.ec"), loop_mod


def test_provider_failures_do_not_exhaust_the_stuck_limit(monkeypatch, tmp_path):
    """3 provider failures with stuck_limit=3 must NOT end the run as STUCK."""
    backend = _FailingBackend([LlmProviderError("no choices")] * 3)
    result, loop_mod = _run(monkeypatch, tmp_path, backend, stuck_limit=3, max_steps=8)

    assert result.reason is not loop_mod.ExitReason.STUCK, (
        "provider failures were counted against the model"
    )
    assert backend.calls == 4, "the loop must keep trying past provider failures"


def test_malformed_answers_still_count_toward_stuck(monkeypatch, tmp_path):
    """The model emitting garbage IS evidence it is floundering."""
    backend = _FailingBackend([LlmFormatError("bad json")] * 5)
    result, loop_mod = _run(monkeypatch, tmp_path, backend, stuck_limit=3, max_steps=8)
    assert result.reason is loop_mod.ExitReason.STUCK


def test_consecutive_provider_failures_are_still_bounded(monkeypatch, tmp_path):
    """A persistently broken endpoint must not spin to max_steps burning calls."""
    backend = _FailingBackend([LlmProviderError("no choices")] * 20)
    result, loop_mod = _run(monkeypatch, tmp_path, backend, stuck_limit=50, max_steps=30,
                            max_consecutive_provider_failures=4)
    assert result.reason is loop_mod.ExitReason.LLM_ERROR
    assert "4 times in a row" in result.message
    assert backend.calls == 4, "must stop at the limit, not keep paying"


def test_a_usable_reply_resets_the_provider_streak(monkeypatch, tmp_path):
    """Intermittent failures must not accumulate across a working call."""
    backend = _FailingBackend([
        LlmProviderError("a"), LlmProviderError("b"),   # 2
        # then a usable reply -> streak resets, so these 2 cannot reach the cap of 4
    ])
    result, loop_mod = _run(monkeypatch, tmp_path, backend, stuck_limit=50, max_steps=6,
                            max_consecutive_provider_failures=4)
    assert result.reason is not loop_mod.ExitReason.LLM_ERROR


# --- deliberate by default, back off when it is not converging --------------
# Latency on run 20260806T194914Z rose 93s -> 235s per step as the trajectory
# window filled and then plateaued, so depth is not free. `high_unless_stuck`
# spends it while the model is converting calls into progress and stops
# forcing it once the trajectory says otherwise.


def _traj(*outcomes):
    return [{"outcome": o} for o in outcomes]


def test_high_effort_while_the_model_is_converging(monkeypatch):
    from integration.agent.config import (
        THINKING_HIGH_UNLESS_STUCK,
        resolve_effort_for_step,
        resolve_thinking_for_step,
    )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    config = apply_deepseek_provider(
        AgentConfig(), thinking=THINKING_HIGH_UNLESS_STUCK
    )
    traj = _traj("accepted", "accepted", "failed", "accepted")
    assert resolve_thinking_for_step(config, traj) == "enabled"
    assert resolve_effort_for_step(config, traj) == "high"


def test_effort_is_released_once_the_model_is_struggling(monkeypatch):
    """4 unproductive steps in the 6-step window. A no-op counts: EasyCrypt
    accepted it, so it is not a failure, but it bought nothing."""
    from integration.agent.config import (
        THINKING_HIGH_UNLESS_STUCK,
        resolve_effort_for_step,
    )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    config = apply_deepseek_provider(
        AgentConfig(), thinking=THINKING_HIGH_UNLESS_STUCK
    )
    traj = _traj("failed", "no_op", "accepted", "undone", "failed", "accepted")
    assert resolve_effort_for_step(config, traj) is None


def test_one_bad_step_in_six_is_not_struggling(monkeypatch):
    """The median step on the measured runs is unproductive, so an `any`
    rule would report struggling essentially always."""
    from integration.agent.config import THINKING_HIGH_UNLESS_STUCK, is_struggling

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    config = apply_deepseek_provider(
        AgentConfig(), thinking=THINKING_HIGH_UNLESS_STUCK
    )
    assert not is_struggling(config, _traj("accepted", "failed", "accepted"))


def test_other_thinking_modes_keep_their_configured_effort(monkeypatch):
    """`resolve_effort_for_step` is called unconditionally by the loop, so it
    must be a no-op for every mode that does not opt in."""
    from integration.agent.config import resolve_effort_for_step

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    for mode in ("adaptive", "enabled", "disabled"):
        config = apply_deepseek_provider(
            AgentConfig(), thinking=mode, reasoning_effort="max"
        )
        assert resolve_effort_for_step(config, _traj("failed") * 6) == "max"


def test_an_unknown_thinking_mode_is_still_rejected():
    from integration.agent.config import resolve_thinking_for_step

    config = AgentConfig(llm_provider="deepseek", llm_thinking="sometimes")
    with pytest.raises(ValueError, match="llm_thinking must be one of"):
        resolve_thinking_for_step(config, [])


def test_the_history_window_was_cut_to_15():
    """Prompt size drove per-step latency (93s -> 235s, plateauing exactly
    where this window fills), and it is the only lever that shortens it."""
    assert AgentConfig().history_steps == 15
