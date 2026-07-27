"""Unit tests for the EasyCrypt agent package."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from integration.agent.embeddings import rank_by_cosine
from integration.agent.error_history import ErrorHistory, normalize_goal, normalize_tactic
from integration.agent.llm import (
    LlmDecision,
    _action_text_from_message,
    _find_json_object,
    _parse_retrospective,
    _response_content,
    _response_thought,
    parse_action,
)
from integration.agent.premises import (
    load_cached_embeddings,
    parse_premises,
    save_cached_embeddings,
)
from integration.agent.proof_file import ProofFile, create_working_copy
from integration.agent.prompt import build_prompt

FIXTURES = Path(__file__).resolve().parent / "fixtures"

SAMPLE_PREMISES = """
========== Top ==========

lemma myfirstlemma: forall (n : int), n + 0 = n.
lemma mysecondlemma: forall (n : int), 0 + n = n.

========== Core ==========

axiom addr0: forall (x : domain), add x rzero = x.
lemma bij_inj ['a, 'b]: forall (f : 'b -> 'a), bijective f => injective f.
"""


def test_parse_premises_extracts_qualified_keys_and_theory():
    premises = parse_premises(SAMPLE_PREMISES)
    assert set(premises) == {
        "Top.myfirstlemma",
        "Top.mysecondlemma",
        "Core.addr0",
        "Core.bij_inj",
    }
    assert "[Top]" in premises["Top.myfirstlemma"]
    assert "[Core]" in premises["Core.addr0"]


def test_parse_premises_keeps_same_basename_in_different_theories():
    text = """
========== RField ==========

lemma exprM: forall (x : real) (m n : int), exp x (m * n) = exp (exp x m) n.

========== Ring.IntID ==========

lemma exprM: forall (x m n : int), x ^ (m * n) = x ^ m ^ n.
"""
    premises = parse_premises(text)
    assert "RField.exprM" in premises
    assert "Ring.IntID.exprM" in premises
    assert "real" in premises["RField.exprM"]
    assert "int" in premises["Ring.IntID.exprM"]


def test_proof_file_append_and_undo(tmp_path):
    source = FIXTURES / "incomplete_proof.ec"
    copy = create_working_copy(source, tmp_path / "work.ec")
    proof = ProofFile(copy)

    bounds = proof.bounds()
    assert bounds.proof_start_line == 4
    assert bounds.qed_line is None
    assert bounds.cursor_upto == 4

    line_no = proof.append_tactic("by rewrite addr0.")
    lines = proof.read_lines()
    assert "by rewrite addr0." in lines[line_no - 1]

    assert proof.undo_last_tactic() == 1
    assert "by rewrite addr0." not in "\n".join(proof.read_lines())

    assert proof.undo_last_tactic() == 0


def test_proof_file_undo_multiple_tactics(tmp_path):
    source = FIXTURES / "incomplete_proof.ec"
    copy = create_working_copy(source, tmp_path / "work.ec")
    proof = ProofFile(copy)

    proof.append_tactic("move => H.")
    proof.append_tactic("rewrite addr0.")
    proof.append_tactic("trivial.")
    text = "\n".join(proof.read_lines())
    assert "move => H." in text
    assert "rewrite addr0." in text
    assert "trivial." in text

    assert proof.undo_last_tactic(2) == 2
    text = "\n".join(proof.read_lines())
    assert "move => H." in text
    assert "rewrite addr0." not in text
    assert "trivial." not in text

    # Request more undos than remain: undo what's available, stop at proof.
    assert proof.undo_last_tactic(5) == 1
    assert "move => H." not in "\n".join(proof.read_lines())
    assert proof.undo_last_tactic(3) == 0


def test_proof_file_append_tactic_collapses_embedded_newlines(tmp_path):
    """Regression: a degenerate LLM generation containing raw newlines must
    never be written as multiple physical lines. Otherwise, a rollback via
    `remove_lines(inserted_line)` (which only deletes 1 line) leaves the
    rest behind, permanently corrupting the proof file."""
    source = FIXTURES / "incomplete_proof.ec"
    copy = create_working_copy(source, tmp_path / "work.ec")
    proof = ProofFile(copy)

    before_line_count = len(proof.read_lines())
    garbage = "smt()." + ("\n" * 50) + "garbage\x08tail"
    line_no = proof.append_tactic(garbage)
    after_line_count = len(proof.read_lines())
    assert after_line_count == before_line_count + 1

    proof.remove_lines(line_no)
    assert len(proof.read_lines()) == before_line_count


def test_rank_by_cosine_orders_by_similarity():
    index = {
        "a": np.array([1.0, 0.0]),
        "b": np.array([0.9, 0.1]),
        "c": np.array([0.0, 1.0]),
    }
    goal = np.array([1.0, 0.0])
    ranked = rank_by_cosine(index, goal, k=2)
    assert ranked[0][0] == "a"
    assert ranked[1][0] == "b"


def test_parse_action_tactic_and_undo():
    from integration.agent.llm import LlmFormatError

    tactic = parse_action(
        '{"action": "tactic", "tactic": "by ring.", "name": ""}'
    )
    assert tactic.kind == "tactic"
    assert tactic.tactic == "by ring."

    undo = parse_action('{"action": "undo", "tactic": "", "name": ""}')
    assert undo.kind == "undo"
    assert undo.count == 1

    undo_empty = parse_action(
        '{"action": "undo", "tactic": "", "name": "", "query": "", "count": ""}'
    )
    assert undo_empty.count == 1

    undo_str = parse_action(
        '{"action": "undo", "tactic": "", "name": "", "query": "", "count": "3"}'
    )
    assert undo_str.count == 3

    undo_int = parse_action(
        '{"action": "undo", "tactic": "", "name": "", "query": "", "count": 2}'
    )
    assert undo_int.count == 2

    with pytest.raises(LlmFormatError, match="undo count"):
        parse_action(
            '{"action": "undo", "tactic": "", "name": "", "query": "", "count": "0"}'
        )
    with pytest.raises(LlmFormatError, match="undo count"):
        parse_action(
            '{"action": "undo", "tactic": "", "name": "", "query": "", "count": "x"}'
        )


def test_parse_action_lookup_lemma():
    lookup = parse_action(
        '{"action": "lookup_lemma", "tactic": "", "name": "addr0"}'
    )
    assert lookup.kind == "lookup_lemma"
    assert lookup.name == "addr0"


def test_parse_action_search_lemmas():
    search = parse_action(
        '{"action": "search_lemmas", "tactic": "", "name": "", '
        '"query": "commutative integer addition"}'
    )
    assert search.kind == "search_lemmas"
    assert search.query == "commutative integer addition"
    assert search.mode == "semantic"


def test_parse_action_search_lemmas_modes():
    search = parse_action(
        '{"action": "search_lemmas", "tactic": "", "name": "substring", '
        '"query": "lnM"}'
    )
    assert search.mode == "substring"
    assert search.query == "lnM"
    exact = parse_action(
        '{"action": "search_lemmas", "tactic": "", "name": "exact", '
        '"query": "lnM"}'
    )
    assert exact.mode == "exact"


def test_parse_action_search_lemmas_requires_query():
    with pytest.raises(ValueError, match="missing query"):
        parse_action(
            '{"action": "search_lemmas", "tactic": "", "name": "", "query": ""}'
        )


def test_parse_action_rejects_prose_reasoning():
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_action("Let's see if there's a better way.")


def test_parse_action_rejects_empty_content():
    with pytest.raises(ValueError, match="Empty LLM content"):
        parse_action("")


def test_parse_action_repairs_unescaped_easycrypt_backslash():
    """Models often emit `/\\` as a single backslash inside JSON strings."""
    raw = (
        '{"action": "tactic", '
        '"tactic": "while (x = 2 /\\ 0 <= i /\\ i <= n); auto; smt().", '
        '"name": "", "query": ""}'
    )
    action = parse_action(raw)
    assert action.kind == "tactic"
    assert action.tactic == "while (x = 2 /\\ 0 <= i /\\ i <= n); auto; smt()."


def test_parse_action_accepts_fenced_json_block():
    action = parse_action(
        'Preamble text\n```json\n{"action": "tactic", "tactic": "by ring.", "name": ""}\n```\n'
    )
    assert action.kind == "tactic"
    assert action.tactic == "by ring."


def test_find_json_object_extracts_embedded_action():
    text = (
        "Some analysis...\n"
        '{"action": "tactic", "tactic": "skip.", "name": ""}\n'
        "Trailing note."
    )
    assert _find_json_object(text) == '{"action": "tactic", "tactic": "skip.", "name": ""}'


def test_action_text_from_message_falls_back_to_reasoning_json():
    message = type(
        "Message",
        (),
        {
            "content": "",
            "reasoning_content": (
                "The goal is arithmetic.\n"
                "```json\n"
                '{"action": "tactic", "tactic": "by smt().", "name": ""}\n'
                "```"
            ),
        },
    )()
    assert _action_text_from_message(message) == (
        '{"action": "tactic", "tactic": "by smt().", "name": ""}'
    )


def test_response_content_never_uses_reasoning():
    message = type(
        "Message",
        (),
        {
            "content": "",
            "reasoning_content": (
                "Let's see if there's a better way.\n"
                '{"action": "tactic", "tactic": "trivial.", "name": ""}'
            ),
        },
    )()
    assert _response_content(message) == ""
    with pytest.raises(ValueError, match="Empty LLM content"):
        parse_action(_response_content(message))


def test_response_thought_extracts_reasoning_content():
    message = type(
        "Message",
        (),
        {"content": '{"action": "tactic", "tactic": "trivial."}', "reasoning_content": "Try trivial first."},
    )()
    assert _response_thought(message) == "Try trivial first."


def test_response_thought_extracts_reasoning_field():
    message = type(
        "Message",
        (),
        {
            "content": '{"action": "tactic", "tactic": "by ring.", "name": ""}',
            "reasoning_content": "",
            "reasoning": "The goal is an equality; ring should close it.",
        },
    )()
    assert _response_thought(message) == "The goal is an equality; ring should close it."


def test_response_thought_prefers_reasoning_content_over_reasoning():
    message = type(
        "Message",
        (),
        {
            "content": '{"action": "tactic", "tactic": "trivial.", "name": ""}',
            "reasoning_content": "Primary channel.",
            "reasoning": "Secondary channel.",
        },
    )()
    assert _response_thought(message) == "Primary channel."


def test_response_thought_extracts_nested_reasoning_dict():
    message = type(
        "Message",
        (),
        {
            "content": '{"action": "tactic", "tactic": "trivial.", "name": ""}',
            "reasoning": {"content": "Nested provider reasoning."},
        },
    )()
    assert _response_thought(message) == "Nested provider reasoning."


def test_response_thought_returns_none_when_absent():
    message = type("Message", (), {"content": "hello", "reasoning_content": ""})()
    assert _response_thought(message) is None


def test_build_prompt_includes_repair_hint_and_lookup_tool():
    prompt = build_prompt(
        goal="n + 0 = n",
        top_premises={},
        failed_tactics=[],
        proof_tail="proof.",
        repair_hint="  by broken.",
        lookup_notes=["addr0: axiom addr0: ..."],
        enable_lemma_lookup=True,
    )
    assert "Repair hint" in prompt
    assert "by broken." in prompt
    assert "lookup_lemma" in prompt
    assert "search_lemmas" in prompt
    assert "Lemma lookup results" in prompt
    assert "Required response shape" in prompt


def test_search_lemmas_ranks_catalog_by_cosine_similarity():
    from integration.agent.loop import _search_lemmas

    catalog = {
        "addC": "lemma addC: forall x y, x + y = y + x.",
        "mulC": "lemma mulC: forall x y, x * y = y * x.",
        "addr0": "lemma addr0: forall x, x + 0 = x.",
    }

    class FakeEmbedder:
        def embed(self, query):
            assert query == "addition commutativity"
            return np.array([1.0, 0.0])

    index = {
        "addC": np.array([1.0, 0.0]),
        "addr0": np.array([0.8, 0.2]),
        "mulC": np.array([0.0, 1.0]),
    }
    result = _search_lemmas(
        catalog,
        FakeEmbedder(),
        index,
        "addition commutativity",
        top_k=2,
    )

    assert "addC (cosine=1.0000)" in result
    assert "addr0" in result
    assert "mulC" not in result


def test_substring_and_exact_lemma_search_find_short_names():
    from integration.agent.lemma_search import search_lemmas

    catalog = {
        "RealExp.lnM": "[RealExp] lemma lnM: forall x y, ...",
        "RealExp.lnV": "[RealExp] lemma lnV: ...",
        "RealExp.le_ln_up": "[RealExp] axiom le_ln_up: ...",
    }
    exact = search_lemmas(catalog, None, None, "lnM", mode="exact", top_k=5)
    assert "RealExp.lnM:" in exact
    substr = search_lemmas(catalog, None, None, "ln", mode="substring", top_k=5)
    assert "RealExp.lnM" in substr
    assert "RealExp.lnV" in substr
    prefix = search_lemmas(catalog, None, None, "lnM", mode="prefix", top_k=5)
    assert "RealExp.lnM" in prefix


def test_substring_search_returns_same_basename_across_theories():
    from integration.agent.lemma_search import search_lemmas

    catalog = {
        "RField.exprM": "[RField] lemma exprM: forall (x : real) ...",
        "Ring.IntID.exprM": "[Ring.IntID] lemma exprM: forall (x m n : int) ...",
        "RField.exprMn": "[RField] lemma exprMn: ...",
    }
    result = search_lemmas(catalog, None, None, "exprM", mode="substring", top_k=5)
    assert "RField.exprM" in result
    assert "Ring.IntID.exprM" in result


def test_theory_filter_scopes_any_search_mode():
    from integration.agent.lemma_search import search_lemmas, split_theory_filter

    assert split_theory_filter("theory:RField exprM") == ("RField", "exprM")
    catalog = {
        "RField.exprM": "[RField] lemma exprM: real",
        "Ring.IntID.exprM": "[Ring.IntID] lemma exprM: int",
        "RField.addr0": "[RField] lemma addr0: ...",
    }
    scoped = search_lemmas(
        catalog, None, None, "theory:RField exprM", mode="substring", top_k=5
    )
    assert "RField.exprM" in scoped
    assert "Ring.IntID.exprM" not in scoped
    listing = search_lemmas(
        catalog, None, None, "theory:Ring.IntID", mode="substring", top_k=5
    )
    assert "Ring.IntID.exprM" in listing
    assert "RField.exprM" not in listing


def test_goal_shape_hint_for_implication_before_proc():
    from integration.agent.loop import _enrich_error

    goal = (
        "Current goal\n\nx0: int\nn0: int\n"
        "------------------------------------------------------------------------\n"
        "0 <= n0 => hoare[ Exp.exp : x = x0 /\\ n = n0 ==> res = x0 ^ n0 ]"
    )
    err = (
        "expecting a goal of the form: hoare[F], ehoare[F], phoare[F], equiv[F]"
    )
    enriched = _enrich_error(err, "proc.", goal)
    assert "move =>" in enriched
    assert "P => judgment" in enriched or "bare judgment" in enriched


def test_hl_hint_on_ambient_goal_still_distinct():
    from integration.agent.loop import _enrich_error

    goal = "Current goal\n\n------------------------------------------------------------------------\n0 <= x"
    err = "expecting a goal of the form: hoare[F], ehoare[F], phoare[F], equiv[F]"
    enriched = _enrich_error(err, "proc.", goal)
    assert "ambient logic" in enriched.lower()
    assert "move =>" not in enriched
    assert "NOT ambient" not in enriched


def test_hl_shape_mismatch_hint_for_program_logic_goal():
    from integration.agent.loop import _enrich_error

    goal = (
        "Current goal\n\n"
        "------------------------------------------------------------------------\n"
        "pre = x{1} = x{2}\n\n"
        "while (y < z) {            (1--)  if (y < z) {\n"
        "  y <- y + 1               (1.1)    x <- z + 1\n"
        "}                          (1--)  }\n\n"
        "post = res{1} = res{2}"
    )
    err = "expecting a goal of the form: equiv[S]"
    enriched = _enrich_error(err, "unroll 1.", goal)
    assert "NOT ambient logic" in enriched
    assert "ambient logic already" not in enriched.lower()
    assert "move =>" not in enriched


def test_first_tactic_prefix_and_compound_detection():
    from integration.agent.loop import (
        _first_tactic_prefix,
        _is_oneshot_while_compound,
        _split_tactic_segments,
    )

    assert _split_tactic_segments("proc; while (true); auto; smt().") == [
        "proc.",
        "while (true).",
        "auto.",
        "smt().",
    ]
    assert _first_tactic_prefix("proc; while (true); auto; smt().") == "proc."
    assert _first_tactic_prefix("while (true).") is None
    assert _split_tactic_segments("while (true).") == []
    assert _is_oneshot_while_compound("while (true); auto; smt().")


def test_probe_compound_walks_segments_until_failure(
    tmp_path, easycrypt_bin, monkeypatch
):
    """Diagnostic replay should report each successful segment, then the failure."""
    from integration.agent.config import AgentConfig
    from integration.agent import loop as loop_mod
    from integration.agent.easycrypt import LlmResult
    from integration.agent.loop import _probe_prefix_subgoal
    from integration.agent.proof_file import ProofFile, create_working_copy

    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    create_working_copy(source, work_copy)
    config = AgentConfig(easycrypt_bin=easycrypt_bin)
    proof = ProofFile(work_copy)
    before = work_copy.read_text(encoding="utf-8")

    calls = {"n": 0}

    def fake_validate(_path, _config):
        calls["n"] += 1
        # First two segments OK; third fails.
        rc = 0 if calls["n"] <= 2 else 1
        return LlmResult(
            returncode=rc,
            stdout="",
            stderr="" if rc == 0 else "[critical] cannot prove goal (strict)",
        )

    goals = [
        "Current goal\n------------------------------------------------------------------------\nafter-seg-1",
        "Current goal\n------------------------------------------------------------------------\nafter-seg-2",
    ]

    def fake_resolve(_proof, _config):
        return goals[min(calls["n"], len(goals)) - 1]

    monkeypatch.setattr(loop_mod, "validate_file", fake_validate)
    monkeypatch.setattr(loop_mod, "resolve_goal", fake_resolve)

    diagnostic = _probe_prefix_subgoal(
        proof, "wp; skip; smt().", config
    )
    after = work_copy.read_text(encoding="utf-8")
    assert after == before  # fully rolled back
    assert diagnostic is not None
    assert "Step 1/3 `wp.` OK" in diagnostic
    assert "after-seg-1" in diagnostic
    assert "Step 2/3 `skip.` OK" in diagnostic
    assert "after-seg-2" in diagnostic
    assert "Step 3/3 `smt().` FAILED" in diagnostic
    assert "cannot prove goal" in diagnostic


def test_build_prompt_includes_goal_shape_and_simplify_guidance():
    prompt = build_prompt(
        goal="n + 0 = n",
        top_premises={},
        failed_tactics=[],
        proof_tail="proof.",
        fewshot="example",
        enable_lemma_lookup=True,
    )
    assert "Goal shape before program-logic" in prompt
    assert "Program-logic tactic menu" in prompt
    assert "When to simplify" in prompt
    assert "Finding algebraic identities" in prompt
    assert "Active goal-shape hints" in prompt
    assert "AMBIENT-LOGIC" in prompt
    assert "substring" in prompt
    assert "Search budget warning" not in prompt
    warned = build_prompt(
        goal="n + 0 = n",
        top_premises={},
        failed_tactics=[],
        proof_tail="proof.",
        fewshot="example",
        search_warning="WARNING: consecutive retrieval #4 of 5.",
    )
    assert "Search budget warning" in warned
    assert "consecutive retrieval #4" in warned


def test_active_goal_shape_hints_are_proactive_for_program_logic():
    from integration.agent.prompt import format_active_goal_shape_hints

    proc_goal = (
        "Current goal\n\n"
        "------------------------------------------------------------------------\n"
        "pre = x{1} = x{2}\n\n"
        "    Compiler.left ~ Compiler.right \n\n"
        "post = res{1} = res{2}"
    )
    hints = format_active_goal_shape_hints(proc_goal)
    assert "PROGRAM-LOGIC" in hints
    assert "proc*" in hints or "proc*." in hints

    while_goal = (
        "Current goal\n\n"
        "------------------------------------------------------------------------\n"
        "pre = n = 2\n\n"
        "(1--)  while (i < n) {\n"
        "(1.1)    i <- i + 1\n"
        "(1--)  }\n\n"
        "post = i = 2"
    )
    while_hints = format_active_goal_shape_hints(while_goal)
    assert "while" in while_hints.lower()
    assert "unroll" in while_hints.lower()

    call_goal = (
        "Current goal\n\n"
        "------------------------------------------------------------------------\n"
        "pre = (glob A){1} = (glob A){2}\n\n"
        "    Abstract_game(A).one ~ Abstract_game(A).one \n\n"
        "post = res{1} = res{2}"
    )
    call_header_hints = format_active_goal_shape_hints(call_goal)
    assert "proc*" in call_header_hints or "proc*." in call_header_hints
    assert "little or no code" not in call_header_hints.lower()

    call_body = (
        "Current goal\n\n"
        "------------------------------------------------------------------------\n"
        "pre = (glob A){1} = (glob A){2}\n\n"
        "(1)  x <@ A.step()            (1)  x <@ A.step()\n\n"
        "post = res{1} = res{2}"
    )
    call_hints = format_active_goal_shape_hints(call_body)
    assert "call (_: true)" in call_hints
    assert "inline" in call_hints.lower()

    asymmetric = (
        "Current goal\n\n"
        "------------------------------------------------------------------------\n"
        "pre = ={y,z}\n\n"
        "while (y < z) {            (1--)  if (y < z) {\n"
        "  y <- y + 1               (1.1)    x <- z + 1\n"
        "}                          (1--)  }\n\n"
        "post = ={x,y,z}"
    )
    asym_hints = format_active_goal_shape_hints(asymmetric)
    assert "asymmetric" in asym_hints.lower()
    assert "seq" in asym_hints.lower()

    ambient = (
        "Current goal\n\n"
        "------------------------------------------------------------------------\n"
        "x <> 0 => x ^ a * x ^ b = x ^ (a + b)"
    )
    ambient_hints = format_active_goal_shape_hints(ambient)
    assert "AMBIENT-LOGIC" in ambient_hints
    assert "nonlinear" in ambient_hints.lower()
    assert "proc" not in ambient_hints.lower() or "Do not apply `proc`" in ambient_hints

    prompt = build_prompt(
        goal=while_goal,
        top_premises={},
        failed_tactics=[],
        proof_tail="proof.",
        fewshot="example",
    )
    # Hints must sit with the current goal, not only after failures.
    goal_idx = prompt.index("## Current goal")
    hints_idx = prompt.index("## Active goal-shape hints")
    failed_idx = prompt.index("## Previously failed at this goal")
    assert goal_idx < hints_idx < failed_idx
    assert "(none)" in prompt[failed_idx : failed_idx + 80]


def test_fewshot_covers_missing_program_logic_patterns_without_joy_lemmas():
    from integration.agent.prompt import load_fewshot_examples

    text = load_fewshot_examples()
    for needle in (
        "proc*.",
        "call (_: true)",
        "inline *",
        "unroll",
        "rcondf",
        "After skip",
        "corpus-specific",
    ):
        assert needle in text
    for banned in (
        "ten_to_two",
        "optimisation_correct",
        "eavesdrop_reflex",
        "games_quadruple",
        "two_to_ten",
        "exp_product",
    ):
        assert banned not in text.lower()


def test_instruction_list_not_empty_error_hint():
    from integration.agent.loop import _enrich_error

    enriched = _enrich_error(
        "left instruction list is not empty",
        "skip.",
        "Current goal\npre = true\n\n(1) x <- 1\n\npost = true",
    )
    assert "inline" in enriched.lower() or "while" in enriched.lower()
    assert "skip only applies" in enriched.lower() or "empty" in enriched.lower()


def test_lemma_search_index_is_reused_for_same_model_and_catalog():
    from integration.agent.loop import (
        _LEMMA_SEARCH_INDEX_CACHE,
        _build_lemma_search_index,
    )

    _LEMMA_SEARCH_INDEX_CACHE.clear()
    catalog = {"addC": "lemma addC: x + y = y + x."}

    class FakeEmbedder:
        builds = 0

        def _resolve_model(self):
            return "mock-embed"

        def build_index(self, premises):
            self.builds += 1
            return {name: np.array([1.0]) for name in premises}

    embedder = FakeEmbedder()
    first = _build_lemma_search_index(catalog, embedder)
    second = _build_lemma_search_index(catalog, embedder)

    assert first is second
    assert embedder.builds == 1
    _LEMMA_SEARCH_INDEX_CACHE.clear()


def test_build_prompt_includes_bounded_reasoning_history():
    prompt = build_prompt(
        goal="n + 0 = n",
        top_premises={},
        failed_tactics=[],
        proof_tail="proof.",
        past_steps=[
            {
                "step": 1,
                "action": "tactic",
                "tactic": "smt().",
                "outcome": "failed",
                "thought": "The arithmetic solver may close this.",
                "error": "cannot prove goal (strict)",
            }
        ],
    )
    assert "Recent reasoning and outcomes" in prompt
    assert "The arithmetic solver may close this." in prompt
    assert "cannot prove goal (strict)" in prompt


def test_parse_retrospective_accepts_fenced_json():
    payload = _parse_retrospective(
        '```json\n{"summary":"x","prevented_by":[],"wished_for":{}}\n```'
    )
    assert payload["summary"] == "x"


@pytest.mark.integration
def test_agent_stuck_on_repeated_failed_tactics(tmp_path, easycrypt_bin, monkeypatch):
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=50,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        stuck_limit=3,
    )

    class FakeLlm:
        def decide(self, _prompt, **_kwargs):
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(action=TacticAction(tactic="by obviously_invalid_tactic."))

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: FakeLlm())
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)
    assert result.reason == ExitReason.STUCK
    # First attempt fails in EasyCrypt (+1 stuck); the identical retry is
    # hard-rejected with repeat_stuck_weight=2, so stuck_limit=3 trips on
    # step 2 rather than burning three EasyCrypt calls.
    assert result.steps == 2


@pytest.mark.integration
def test_agent_stuck_on_repeated_noop_undo(tmp_path, easycrypt_bin, monkeypatch):
    """An agent that only issues no-op undos must abort quickly rather than

    burn the full step budget, even with a generous stuck_limit."""
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.llm import LlmDecision, UndoAction
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=50,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        # Deliberately generous so the noop-undo cap is what trips, not the
        # general stuck_limit.
        stuck_limit=50,
        max_consecutive_noop_undos=3,
    )

    class FakeLlm:
        def decide(self, _prompt, **_kwargs):
            return LlmDecision(action=UndoAction(count=1))

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: FakeLlm())
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)
    assert result.reason == ExitReason.STUCK
    assert "max_consecutive_noop_undos" in result.message
    assert result.steps == 3


@pytest.mark.integration
def test_agent_hard_rejects_normalized_duplicate_tactics(
    tmp_path, easycrypt_bin, monkeypatch
):
    """Whitespace / && vs /\\ variants of a failed tactic must not reach EC."""
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    log_path = tmp_path / "run.json"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        stuck_limit=10,
        identical_fail_limit=3,
        log_file=log_path,
    )

    tactics = [
        "while (0 <= i /\\ i <= n); auto; smt().",
        "while (0 <= i && i <= n); auto; smt().",
        "while (0 <= i /\\ i <= n);  auto; smt().",
    ]

    class FakeLlm:
        calls = 0

        def decide(self, _prompt, **_kwargs):
            from integration.agent.llm import LlmDecision, TacticAction

            tactic = tactics[min(self.calls, len(tactics) - 1)]
            self.calls += 1
            return LlmDecision(action=TacticAction(tactic=tactic))

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    fake = FakeLlm()
    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: fake)
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)
    assert result.reason == ExitReason.STUCK
    assert "identical_fail_limit" in result.message
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    outcomes = [
        event.get("outcome")
        for event in payload["events"]
        if event.get("event") == "iteration"
    ]
    assert outcomes[0] == "failed"
    assert outcomes[1] == "rejected"
    assert outcomes[2] == "rejected"


@pytest.mark.integration
def test_agent_limits_continuous_searches_with_warning(
    tmp_path, easycrypt_bin, monkeypatch
):
    """Five consecutive searches are allowed; the 4th warns; the 6th is blocked."""
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    log_path = tmp_path / "run.json"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.llm import LlmDecision, SearchLemmasAction, TacticAction
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=20,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        stuck_limit=20,
        max_continuous_searches=5,
        identical_fail_limit=None,
        log_file=log_path,
    )

    class FakeLlm:
        def __init__(self):
            self.calls = 0
            self.prompts: list[str] = []

        def decide(self, prompt, **_kwargs):
            self.prompts.append(prompt)
            self.calls += 1
            if self.calls <= 6:
                return LlmDecision(
                    action=SearchLemmasAction(
                        query=f"query-{self.calls}", mode="substring"
                    )
                )
            return LlmDecision(
                action=TacticAction(tactic="by rewrite addr0.")
            )

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    fake = FakeLlm()
    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: fake)
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    run_agent(source, config, work_copy=work_copy)
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    iterations = [
        e for e in payload["events"] if e.get("event") == "iteration"
    ]
    search_outcomes = [
        e.get("outcome") for e in iterations if e.get("action") == "search_lemmas"
    ]
    assert search_outcomes[:5] == ["search"] * 5
    assert "search_limited" in search_outcomes
    # 4th search result carries the penultimate warning.
    fourth = next(
        e for e in iterations if e.get("search_query") == "query-4"
    )
    assert "search-budget" in (fourth.get("search_result") or "")
    assert "WARNING" in (fourth.get("search_result") or "")
    # Prompt before the 4th retrieval warns.
    assert any(
        "Search budget warning" in p and "#4" in p for p in fake.prompts
    )


@pytest.mark.integration
def test_agent_recovers_from_malformed_json_instead_of_aborting(
    tmp_path, easycrypt_bin, monkeypatch
):
    """A bad JSON reply must not end the trial; the agent should retry."""
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    log_path = tmp_path / "run.json"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.llm import LlmDecision, LlmFormatError, TacticAction
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=5,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        stuck_limit=10,
        log_file=log_path,
    )

    class FakeLlm:
        calls = 0

        def decide(self, _prompt, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise LlmFormatError("LLM response is not valid JSON: '{bad}'")
            return LlmDecision(action=TacticAction(tactic="by rewrite addr0."))

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    fake = FakeLlm()
    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: fake)
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)
    assert result.reason in (ExitReason.COMPLETE, ExitReason.ALREADY_COMPLETE)
    assert fake.calls >= 2
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    outcomes = [
        event.get("outcome")
        for event in payload["events"]
        if event.get("event") == "iteration"
    ]
    assert "format_error" in outcomes


@pytest.mark.integration
def test_agent_rejects_admit_tactic_without_ever_writing_it(
    tmp_path, easycrypt_bin, monkeypatch
):
    """Regression: EasyCrypt's `admit` tactic marks a goal as assumed and is
    reported as a fully discharged proof (return code 0, no open goals) even
    for false statements. The agent must reject it outright rather than
    treat it as a real success."""
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=50,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        stuck_limit=3,
    )

    class FakeLlm:
        def decide(self, _prompt, **_kwargs):
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(action=TacticAction(tactic="admit."))

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: FakeLlm())
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)
    assert result.reason == ExitReason.STUCK
    assert "admit" not in work_copy.read_text(encoding="utf-8")


@pytest.mark.integration
def test_agent_rejects_degenerate_multiline_tactic_without_corrupting_file(
    tmp_path, easycrypt_bin, monkeypatch
):
    """Regression: a runaway/degenerate LLM generation (raw newlines mixed
    into the "tactic" text) must be rejected before ever being written to
    the proof file, since a naive write+rollback would otherwise leave
    orphaned garbage lines that permanently break every later goal fetch."""
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    create_working_copy(source, work_copy)
    original_line_count = len(work_copy.read_text(encoding="utf-8").splitlines())

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        stuck_limit=2,
    )

    class FakeLlm:
        def decide(self, _prompt, **_kwargs):
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(
                action=TacticAction(tactic="smt()." + ("\n" * 50) + "garbage\x08tail")
            )

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: FakeLlm())
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)
    assert result.reason == ExitReason.STUCK
    final_line_count = len(work_copy.read_text(encoding="utf-8").splitlines())
    assert final_line_count == original_line_count


@pytest.mark.integration
def test_agent_prompt_accumulates_all_past_failures_for_same_goal(
    tmp_path, easycrypt_bin, monkeypatch
):
    """Regression/wiring check: at each step, the prompt handed to the LLM
    must include every previously failed tactic (and its error) for the
    *current* goal, not just the latest one, and not silently drop earlier
    ones. This is what should stop a weak model from cycling through the
    same failed tactic forever."""
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        # High enough that identical_fail_limit aborts first after 3 attempts.
        stuck_limit=20,
        identical_fail_limit=3,
        repeat_stuck_weight=1,
    )

    seen_prompts: list[str] = []

    class FakeLlm:
        def decide(self, prompt, **_kwargs):
            from integration.agent.llm import LlmDecision, TacticAction

            seen_prompts.append(prompt)
            return LlmDecision(action=TacticAction(tactic="by obviously_invalid_tactic."))

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: FakeLlm())
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    run_agent(source, config, work_copy=work_copy)

    assert len(seen_prompts) == 3
    # Step 1: no prior failures yet.
    assert "(none)" in seen_prompts[0]
    assert "harness will REJECT" not in seen_prompts[0]
    # Steps 2 and 3: failed tactic remains visible (deduped), with ban text.
    for prompt in seen_prompts[1:]:
        assert "by obviously_invalid_tactic." in prompt
        assert "harness will REJECT" in prompt
        assert "Banned tactics at this goal:" in prompt
    # Deduped display: step 3 should show a 2x count for the repeated failure.
    assert "2x " in seen_prompts[2]

def test_error_history_normalizes_and_persists(tmp_path):
    path = tmp_path / "history.json"
    history = ErrorHistory(path)
    goal = "Current goal\n\nn: int\n--------\nn + 0 = n"
    history.add(goal, "error message", "by foo.")
    reloaded = ErrorHistory(path)
    assert reloaded.get(goal)[0] == ("error message", "by foo.")
    assert normalize_goal(goal) == normalize_goal("Current goal\n\nn: int\n--------\nn + 0 = n")


def test_normalize_tactic_collapses_near_duplicates():
    base = "while (0 <= i /\\ i <= n); auto; smt()."
    assert normalize_tactic(base) == normalize_tactic(
        "while (0 <= i && i <= n); auto; smt()."
    )
    assert normalize_tactic(base) == normalize_tactic(
        "while (0 <= i /\\ i <= n);  auto; smt()"
    )
    assert normalize_tactic("smt().") != normalize_tactic("trivial.")


def test_error_history_has_failed_uses_normalization(tmp_path):
    history = ErrorHistory(tmp_path / "history.json")
    goal = "n + 0 = n"
    history.add(goal, "boom", "while (0 <= i /\\ i <= n); auto; smt().")
    assert history.has_failed(goal, "while (0 <= i && i <= n); auto; smt().")
    assert history.failure_count(goal, "while (0 <= i /\\ i <= n); auto; smt().") == 1
    history.add(goal, "repeat", "while (0 <= i && i <= n); auto; smt().")
    assert history.failure_count(goal, "while (0 <= i /\\ i <= n); auto; smt().") == 2


def test_error_history_recent_other_survives_goal_change(tmp_path):
    history = ErrorHistory(tmp_path / "history.json", recent_limit=4)
    history.add("old goal A", "parse error", "while (true).")
    history.add("old goal A", "smt failed", "smt().")
    history.add("new goal B", "seq failed", "seq 0 1 : (={x}).")
    other = history.recent_other("new goal B")
    assert ("parse error", "while (true).") in other
    assert ("smt failed", "smt().") in other
    assert all(tactic != "seq 0 1 : (={x})." for _err, tactic in other)
    assert history.get("new goal B") == [("seq failed", "seq 0 1 : (={x}).")]
    reloaded = ErrorHistory(tmp_path / "history.json", recent_limit=4)
    assert reloaded.recent_other("new goal B") == other


def test_premises_cache_roundtrip(tmp_path):
    work_copy = tmp_path / "proof.agent.ec"
    work_copy.write_text("lemma x.", encoding="utf-8")
    premises = {"foo": "lemma foo: true."}
    embeddings = {"foo": [1.0, 0.0, 0.0]}
    save_cached_embeddings(work_copy, 10, "embed-model", 1, premises, embeddings)
    loaded = load_cached_embeddings(work_copy, 10, "embed-model", 1)
    assert loaded == embeddings


def test_build_prompt_includes_goal_and_failures():
    prompt = build_prompt(
        goal="n + 0 = n",
        top_premises={"addr0": "axiom addr0: ..."},
        failed_tactics=[("bad tactic", "by foo.")],
        proof_tail="proof.",
        fewshot="example",
    )
    assert "n + 0 = n" in prompt
    assert "addr0" in prompt
    assert "by foo." in prompt
    assert "example" in prompt
    assert "Anti-loop rule" in prompt
    assert "Failed tactics are rolled back" in prompt
    assert "SAME current goal" in prompt


def test_build_prompt_includes_recent_failures_from_earlier_goals():
    prompt = build_prompt(
        goal="new goal after seq",
        top_premises={},
        failed_tactics=[],
        proof_tail="proof.\n  proc.\n  seq 0 1 : (={y,z}).",
        fewshot="example",
        recent_failures=[
            ("parse error at (~", "while (={y,z} /\\ (y{1}<z{1} => x{1}=x{2}))."),
            ("left instruction list is not empty", "wp; skip; smt()."),
        ],
    )
    assert "Recent failures at earlier goals" in prompt
    assert "while (={y,z}" in prompt
    assert "wp; skip; smt()." in prompt
    assert "per-goal ban list above was reset" in prompt
    # Informational only — no hard ban for earlier-goal tactics alone.
    assert "Banned tactics at this goal:" not in prompt


def test_build_prompt_includes_all_past_failures_and_warns_against_repeats():
    """All recorded failures for the current goal must appear (not just the
    most recent one), and the prompt must explicitly warn against repeating
    them -- small/weak models have been observed to repeat a failed tactic
    verbatim otherwise."""
    failed_tactics = [
        ("cannot close goals", "by rewrite addz_gt0."),
        ("nothing to introduce", "move => h."),
        ("parse error", "smt(addz_gt0)."),
    ]
    prompt = build_prompt(
        goal="0 < x => 0 < x + 1",
        top_premises={},
        failed_tactics=failed_tactics,
        proof_tail="proof.",
    )
    for error, tactic in failed_tactics:
        assert tactic in prompt
        assert error in prompt
    assert "harness will REJECT" in prompt
    assert "Banned tactics at this goal:" in prompt


def test_build_prompt_dedups_repeated_failures():
    failed_tactics = [
        ("cannot prove goal (strict)", "while (0 <= i /\\ i <= n); auto; smt()."),
        ("Rejected: identical", "while (0 <= i && i <= n); auto; smt()."),
        ("Rejected: identical", "while (0 <= i /\\ i <= n); auto; smt()."),
    ]
    prompt = build_prompt(
        goal="loop goal",
        top_premises={},
        failed_tactics=failed_tactics,
        proof_tail="proof.",
        fewshot="example",
    )
    # One entry in the deduped failure list + one in the banned list.
    assert prompt.count("while (0 <= i") == 2
    assert "3x tactic" in prompt
    assert "cannot prove goal (strict)" in prompt


def test_build_prompt_omits_repeat_warning_when_no_failures_yet():
    prompt = build_prompt(
        goal="n + 0 = n",
        top_premises={},
        failed_tactics=[],
        proof_tail="proof.",
    )
    assert "(none)" in prompt
    assert "harness will REJECT" not in prompt


def test_while_oneshot_hint_overrides_generic_smt_hint():
    from integration.agent.loop import _enrich_error

    enriched = _enrich_error(
        "cannot prove goal (strict)",
        "while (0 <= i /\\ i <= n); auto; smt().",
        "pre = true\npost = true",
    )
    assert "while (inv)" in enriched
    assert "supply a hint — smt(lemma_name)" not in enriched


def test_generic_smt_hint_still_used_without_while_compound():
    from integration.agent.loop import _enrich_error

    enriched = _enrich_error(
        "cannot prove goal (strict)",
        "smt().",
        "0 < x",
    )
    assert "smt(lemma_name)" in enriched


@pytest.mark.integration
def test_agent_loop_with_mock_llm(tmp_path, easycrypt_bin, monkeypatch):
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=3,
        max_steps=5,
        max_premises=20,
        llm_model="mock",
        embed_model="mock-embed",
    )

    class FakeLlm:
        def decide(self, _prompt, **_kwargs):
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(action=TacticAction(tactic="by rewrite addr0."))

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: FakeLlm())
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)
    assert result.reason in (ExitReason.COMPLETE, ExitReason.ALREADY_COMPLETE)
    text = work_copy.read_text(encoding="utf-8")
    assert "by rewrite addr0." in text


def test_agent_run_log_written(tmp_path, easycrypt_bin, monkeypatch):
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    log_path = tmp_path / "run.json"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=5,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        log_file=log_path,
    )

    class FakeLlm:
        def decide(self, _prompt, **_kwargs):
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(action=TacticAction(tactic="by rewrite addr0."))

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: FakeLlm())
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)
    assert result.reason in (ExitReason.COMPLETE, ExitReason.ALREADY_COMPLETE)
    assert log_path.exists()

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    events = [event["event"] for event in payload["events"]]
    assert "startup" in events
    assert "iteration" in events
    assert "finish" in events
    assert payload["events"][-1]["reason"] == result.reason.name


@pytest.mark.integration
def test_agent_run_log_includes_llm_thought(tmp_path, easycrypt_bin, monkeypatch):
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    log_path = tmp_path / "run.json"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=5,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        log_file=log_path,
    )

    class FakeLlm:
        def decide(self, _prompt, **_kwargs):
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(
                action=TacticAction(tactic="by rewrite addr0."),
                thought="The goal is n + 0 = n; addr0 should rewrite it.",
                content='{"action": "tactic", "tactic": "by rewrite addr0.", "name": ""}',
            )

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: FakeLlm())
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)
    assert result.reason in (ExitReason.COMPLETE, ExitReason.ALREADY_COMPLETE)

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    iterations = [e for e in payload["events"] if e["event"] == "iteration"]
    assert iterations
    assert iterations[0]["thought"] == (
        "The goal is n + 0 = n; addr0 should rewrite it."
    )
    assert iterations[0]["content"] == (
        '{"action": "tactic", "tactic": "by rewrite addr0.", "name": ""}'
    )


@pytest.mark.integration
def test_agent_writes_timeout_retrospective_with_right_fix(
    tmp_path, easycrypt_bin, monkeypatch
):
    source = FIXTURES / "incomplete_proof.ec"
    work_copy = tmp_path / "incomplete_proof.agent.ec"
    retrospective_path = tmp_path / "timeout_retrospective.json"
    create_working_copy(source, work_copy)

    from integration.agent.config import AgentConfig
    from integration.agent.loop import ExitReason, run_agent

    config = AgentConfig(
        easycrypt_bin=easycrypt_bin,
        top_k=2,
        max_steps=1,
        max_premises=10,
        llm_model="mock",
        embed_model="mock-embed",
        right_fix="by rewrite addr0.",
        retrospective_file=retrospective_path,
    )

    class FakeLlm:
        def decide(self, _prompt, **_kwargs):
            from integration.agent.llm import LlmDecision, TacticAction

            return LlmDecision(
                action=TacticAction(tactic="by obviously_invalid_tactic."),
                thought="I expected this tactic to exist.",
            )

        def retrospect(self, *, right_fix, trajectory):
            assert right_fix == "by rewrite addr0."
            assert trajectory[0]["thought"] == "I expected this tactic to exist."
            return {
                "summary": "I did not identify the rewrite lemma.",
                "prevented_by": ["Poor lemma discovery"],
                "wished_for": {
                    "prompt": [],
                    "tools": ["Search by goal shape"],
                    "error_presentation": [],
                    "other": [],
                },
            }

    class FakeEmbedder:
        def _resolve_model(self):
            return "mock-embed"

        def embed(self, _text):
            return np.array([1.0, 0.0])

        def build_index(self, premises):
            return {name: np.array([1.0, 0.0]) for name in premises}

    monkeypatch.setattr("integration.agent.loop.LlmClient", lambda _cfg: FakeLlm())
    monkeypatch.setattr(
        "integration.agent.loop.EmbeddingClient", lambda _cfg: FakeEmbedder()
    )

    result = run_agent(source, config, work_copy=work_copy)
    assert result.reason == ExitReason.MAX_STEPS
    assert result.retrospective_file == retrospective_path

    payload = json.loads(retrospective_path.read_text(encoding="utf-8"))
    assert payload["right_fix"] == "by rewrite addr0."
    assert payload["response"]["wished_for"]["tools"] == ["Search by goal shape"]
    assert payload["trajectory"][0]["thought"] == "I expected this tactic to exist."


@pytest.mark.integration
def test_llm_client_default_mode_returns_reasoning_from_lm_studio():
    """Regression: strict response_format suppresses Gemma reasoning_content.

    Default agent mode should keep the two-channel design: JSON in content,
    reasoning in a separate provider field.
    """
    from openai import OpenAI

    from integration.agent.config import AgentConfig
    from integration.agent.llm import LlmClient
    from integration.agent.prompt import build_prompt

    client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
    try:
        models = client.models.list().data
    except Exception as exc:
        pytest.skip(f"LM Studio unavailable: {exc}")

    model_ids = [m.id for m in models]
    model = next((m for m in model_ids if "gemma" in m.lower()), None)
    if model is None:
        pytest.skip("No Gemma model loaded in LM Studio")

    prompt = build_prompt(
        goal="n: int\n--------\nn + 0 = n",
        top_premises={"addr0": "axiom addr0: forall (x : domain), add x rzero = x."},
        failed_tactics=[],
        proof_tail="proof.",
    )
    config = AgentConfig(llm_model=model, llm_json_mode=False, llm_max_tokens=1024)
    decision = LlmClient(config).decide(prompt)

    assert decision.action.kind == "tactic"
    assert decision.content.strip().startswith("{")
    assert decision.thought
    assert len(decision.thought) > 20
    assert "{" not in decision.thought[:20]
