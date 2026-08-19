"""Tests for the Message Batches classification pass in
`proof_corpus/scripts/process_changelog.py`.

Two properties matter here and neither is visible from the script's output:

1. **Batch results are unordered.** The API returns them in whatever order the
   requests finished, so the `custom_id` -> chunk map is the only thing tying a
   response back to the entries it describes. The stub deliberately shuffles.
2. **A failed chunk must not be cached.** `llm_cache.json` is keyed by
   (repo, id, title) and consulted on every rerun, so writing a row for a chunk
   whose JSON did not parse would pin `summary: null` in place permanently.

The script lives outside any importable package, so it is loaded by path -- the
same dynamic-load contract the rest of the harness uses.
"""
from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "proof_corpus" / "scripts"


def _load_script(name: str) -> types.ModuleType:
    path = SCRIPTS / name
    if not path.is_file():
        pytest.skip(f"{path} not present")
    spec = importlib.util.spec_from_file_location(f"_test_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pc() -> types.ModuleType:
    return _load_script("process_changelog.py")


# --- stub API surface -------------------------------------------------------


def _ns(**kw):
    return types.SimpleNamespace(**kw)


def _text_message(text: str):
    return _ns(content=[_ns(type="text", text=text)])


def _counts(succeeded=0, processing=0, errored=0, canceled=0, expired=0):
    return _ns(succeeded=succeeded, processing=processing, errored=errored,
               canceled=canceled, expired=expired)


class StubBatches:
    """Minimal `client.messages.batches` double.

    `responder(custom_id, chunk_payload) -> str | None` produces each request's
    reply text; returning None marks that request errored. Results are yielded
    reversed, so any code that relies on submission order fails here.
    """

    def __init__(self, responder, polls_before_end=2):
        self.responder = responder
        self.polls_before_end = polls_before_end
        self.submitted = None
        self.created = 0
        self.polls = 0

    def create(self, requests):
        self.created += 1
        self.submitted = list(requests)
        return _ns(id="msgbatch_stub", processing_status="in_progress",
                   request_counts=_counts(processing=len(requests)))

    def retrieve(self, batch_id):
        self.polls += 1
        ended = self.polls >= self.polls_before_end
        n = len(self.submitted)
        return _ns(
            id=batch_id,
            processing_status="ended" if ended else "in_progress",
            request_counts=_counts(succeeded=n if ended else 0,
                                   processing=0 if ended else n),
        )

    def results(self, batch_id):
        out = []
        for req in self.submitted:
            payload = json.loads(req["params"]["messages"][0]["content"].split("\n\n", 1)[1])
            text = self.responder(req["custom_id"], payload)
            if text is None:
                out.append(_ns(custom_id=req["custom_id"],
                               result=_ns(type="errored", error=_ns(type="api_error"))))
            else:
                out.append(_ns(custom_id=req["custom_id"],
                               result=_ns(type="succeeded", message=_text_message(text))))
        return reversed(out)   # order is not guaranteed by the real API


class StubMessages:
    def __init__(self, batches, create_responder=None):
        self.batches = batches
        self._create_responder = create_responder
        self.create_calls = 0

    def create(self, **params):
        self.create_calls += 1
        payload = json.loads(params["messages"][0]["content"].split("\n\n", 1)[1])
        return _text_message(self._create_responder(payload))


class StubClient:
    def __init__(self, messages):
        self.messages = messages


def _echo_classification(payload, kind="tactic_change", relevance="high"):
    return json.dumps([
        {"id": row["id"], "kind": kind, "identifiers": [row["id"]],
         "summary": f"summary for {row['id']}", "repair_hint": f"hint for {row['id']}",
         "relevance": relevance}
        for row in payload
    ])


def _entries(pc, n, start=1):
    return [(pc.Entry(id=str(i), title=f"change number {i}"), {})
            for i in range(start, start + n)]


# --- pure helpers -----------------------------------------------------------


def test_chunk_pending_splits_evenly_and_keeps_remainder(pc):
    pending = _entries(pc, 7)
    chunks = pc.chunk_pending(pending, 3)
    assert [len(c) for c in chunks] == [3, 3, 1]
    assert [e.id for c in chunks for e, _ in c] == [str(i) for i in range(1, 8)]


def test_request_params_carries_context_into_the_prompt(pc):
    entry = pc.Entry(id="42", title="tweak smt", source="commit")
    chunk = [(entry, {"changed_files": ["theories/Int.ec"], "labels": ["bug"]})]
    params = pc.request_params(chunk)

    assert params["model"] == pc.MODEL
    assert params["max_tokens"] == pc.MAX_TOKENS
    assert params["system"] == pc.LLM_SYSTEM_PROMPT
    payload = json.loads(params["messages"][0]["content"].split("\n\n", 1)[1])
    assert payload == [{"id": "42", "title": "tweak smt", "source": "commit",
                        "changed_files": ["theories/Int.ec"], "labels": ["bug"]}]


def test_request_params_defaults_missing_context_to_empty_lists(pc):
    params = pc.request_params([(pc.Entry(id="1", title="x"), {})])
    payload = json.loads(params["messages"][0]["content"].split("\n\n", 1)[1])
    assert payload[0]["changed_files"] == [] and payload[0]["labels"] == []


# --- apply_response ---------------------------------------------------------


def test_apply_response_fills_entries_and_clears_needs_llm(pc):
    chunk = _entries(pc, 2)
    ok = pc.apply_response(chunk, _echo_classification([{"id": "1"}, {"id": "2"}]), "c0")

    assert ok is True
    for entry, _ in chunk:
        assert entry.needs_llm is False
        assert entry.kind == "tactic_change"
        assert entry.summary == f"summary for {entry.id}"
        assert entry.repair_hint == f"hint for {entry.id}"
        assert entry.relevance == "high"


def test_apply_response_strips_markdown_fences(pc):
    chunk = _entries(pc, 1)
    fenced = "```json\n" + _echo_classification([{"id": "1"}]) + "\n```"
    assert pc.apply_response(chunk, fenced, "c0") is True
    assert chunk[0][0].kind == "tactic_change"


def test_apply_response_merges_identifiers_without_duplicates(pc):
    entry = pc.Entry(id="1", title="x", identifiers=["foo", "bar"])
    body = json.dumps([{"id": "1", "kind": "lemma_added", "identifiers": ["bar", "baz"],
                        "summary": "s", "repair_hint": "h", "relevance": "medium"}])
    pc.apply_response([(entry, {})], body, "c0")
    assert entry.identifiers == ["foo", "bar", "baz"]


def test_apply_response_rejects_unparseable_output_and_preserves_needs_llm(pc):
    """The cache-poisoning guard: a rejected chunk must stay unclassified."""
    chunk = _entries(pc, 3)
    ok = pc.apply_response(chunk, "I'm sorry, I can't help with that.", "c0")

    assert ok is False
    for entry, _ in chunk:
        assert entry.needs_llm is True
        assert entry.summary is None and entry.repair_hint is None


def test_apply_response_ignores_ids_not_in_the_chunk(pc):
    chunk = _entries(pc, 1)
    body = _echo_classification([{"id": "1"}, {"id": "999"}])
    assert pc.apply_response(chunk, body, "c0") is True
    assert chunk[0][0].needs_llm is False


def test_apply_response_leaves_omitted_entries_unclassified(pc):
    """A short response must not silently mark the missing entries done."""
    chunk = _entries(pc, 3)
    pc.apply_response(chunk, _echo_classification([{"id": "1"}]), "c0")
    assert [e.needs_llm for e, _ in chunk] == [False, True, True]


# --- classify_batched -------------------------------------------------------


def test_classify_batched_submits_one_job_for_all_chunks(pc, monkeypatch):
    monkeypatch.setattr(pc.time, "sleep", lambda _s: None)
    batches = StubBatches(lambda cid, payload: _echo_classification(payload))
    pending = _entries(pc, 10)

    pc.classify_batched(StubClient(StubMessages(batches)), pending, 4, 1, 60)

    assert batches.created == 1
    assert len(batches.submitted) == 3            # 4 + 4 + 2
    assert [r["custom_id"] for r in batches.submitted] == \
        ["chunk_00000", "chunk_00001", "chunk_00002"]
    assert all(e.needs_llm is False for e, _ in pending)


def test_classify_batched_matches_out_of_order_results_by_custom_id(pc, monkeypatch):
    """Each entry must get *its own* classification, not a positional one."""
    monkeypatch.setattr(pc.time, "sleep", lambda _s: None)

    def responder(custom_id, payload):
        # tag every row with the chunk it really belongs to
        return json.dumps([
            {"id": row["id"], "kind": "lemma_added", "identifiers": [],
             "summary": custom_id, "repair_hint": None, "relevance": "medium"}
            for row in payload
        ])

    batches = StubBatches(responder)
    pending = _entries(pc, 9)
    pc.classify_batched(StubClient(StubMessages(batches)), pending, 3, 1, 60)

    # entries 1-3 -> chunk_00000, 4-6 -> chunk_00001, 7-9 -> chunk_00002
    assert [e.summary for e, _ in pending] == \
        ["chunk_00000"] * 3 + ["chunk_00001"] * 3 + ["chunk_00002"] * 3


def test_classify_batched_polls_until_ended(pc, monkeypatch):
    sleeps = []
    monkeypatch.setattr(pc.time, "sleep", lambda s: sleeps.append(s))
    batches = StubBatches(lambda cid, payload: _echo_classification(payload),
                          polls_before_end=3)

    pc.classify_batched(StubClient(StubMessages(batches)), _entries(pc, 2), 5, 7, 60)

    assert batches.polls == 3
    assert sleeps == [7, 7, 7]


def test_classify_batched_times_out_without_hanging(pc, monkeypatch):
    monkeypatch.setattr(pc.time, "sleep", lambda _s: None)
    clock = iter([0.0, 10.0, 1e9])
    monkeypatch.setattr(pc.time, "monotonic", lambda: next(clock))
    batches = StubBatches(lambda cid, payload: _echo_classification(payload),
                          polls_before_end=10_000)

    with pytest.raises(TimeoutError, match="msgbatch_stub"):
        pc.classify_batched(StubClient(StubMessages(batches)), _entries(pc, 2), 5, 1, 60)


def test_classify_batched_survives_an_errored_chunk(pc, monkeypatch):
    monkeypatch.setattr(pc.time, "sleep", lambda _s: None)

    def responder(custom_id, payload):
        return None if custom_id == "chunk_00000" else _echo_classification(payload)

    batches = StubBatches(responder)
    pending = _entries(pc, 6)
    pc.classify_batched(StubClient(StubMessages(batches)), pending, 3, 1, 60)

    assert [e.needs_llm for e, _ in pending] == [True] * 3 + [False] * 3


# --- classify_inline --------------------------------------------------------


def test_classify_inline_makes_one_call_per_chunk(pc):
    messages = StubMessages(None, create_responder=_echo_classification)
    pending = _entries(pc, 7)

    pc.classify_inline(StubClient(messages), pending, 3)

    assert messages.create_calls == 3
    assert all(e.needs_llm is False for e, _ in pending)


# --- end-to-end through main() ---------------------------------------------


RAW = {
    "repo": "EasyCrypt/easycrypt",
    "releases": [{
        "tag_name": "r2025.01",
        "published_at": "2025-01-02T00:00:00Z",
        "body": ("- fix the crush tactic by @alice in #1\n"
                 "- new lemma foo_bar by @bob in #2\n"
                 "- internal: shuffle files around by @carol in #3\n"),
        "pr_details": {},
    }],
}


def _run_main(pc, monkeypatch, tmp_path, batches, cache_path=None):
    infile = tmp_path / "raw.json"
    infile.write_text(json.dumps(RAW), encoding="utf-8")
    out = tmp_path / "changelog.yaml"
    cache = cache_path or (tmp_path / "cache.json")

    client = StubClient(StubMessages(batches))
    monkeypatch.setattr(pc, "anthropic", _ns(Anthropic=lambda: client))
    monkeypatch.setattr(pc.time, "sleep", lambda _s: None)
    monkeypatch.setattr(pc.sys, "argv", [
        "process_changelog.py", "--in", str(infile), "--out", str(out),
        "--cache", str(cache), "--batch-size", "10",
    ])
    pc.main()
    return yaml.safe_load(out.read_text(encoding="utf-8")), json.loads(cache.read_text())


def test_main_classifies_and_caches_a_good_batch(pc, monkeypatch, tmp_path):
    batches = StubBatches(lambda cid, payload: _echo_classification(payload))
    doc, cache = _run_main(pc, monkeypatch, tmp_path, batches)

    entries = doc["releases"][0]["entries"]
    assert len(entries) == 3
    assert "needs_llm" not in entries[0]
    # the two non-trivial bullets went to the LLM; the "internal:" one did not
    assert len(cache) == 2
    assert all(row["summary"] is not None for row in cache.values())


def test_main_does_not_cache_a_chunk_that_failed_to_parse(pc, monkeypatch, tmp_path):
    """Regression: a null row here would suppress the retry on every rerun."""
    batches = StubBatches(lambda cid, payload: "not json at all")
    doc, cache = _run_main(pc, monkeypatch, tmp_path, batches)

    assert cache == {}
    entries = {e["id"]: e for e in doc["releases"][0]["entries"]}
    assert entries["1"]["summary"] is None      # written out, but unclassified
    assert entries["3"]["kind"] == "internal"   # rule-classified, never sent


def test_main_reuses_the_cache_without_calling_the_api(pc, monkeypatch, tmp_path):
    batches = StubBatches(lambda cid, payload: _echo_classification(payload))
    doc1, cache = _run_main(pc, monkeypatch, tmp_path, batches)
    assert batches.created == 1

    cache_path = tmp_path / "cache.json"
    second = StubBatches(lambda cid, payload: pytest.fail("cache miss: API was called"))
    doc2, _ = _run_main(pc, monkeypatch, tmp_path, second, cache_path=cache_path)

    assert second.created == 0
    assert doc1 == doc2
