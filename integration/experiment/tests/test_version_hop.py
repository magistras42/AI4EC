"""Per-release EasyCrypt provisioning and break-version localization (W7).

Nothing here ever builds anything: a real provision is an opam switch and a
full OCaml build, minutes each. Both modules funnel their external commands
through one seam for exactly that reason, and these tests replace it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from integration.agent.config import AgentConfig
from integration.experiment import ec_versions, version_hop
from integration.experiment.ec_versions import (
    EcVersionProvisioner,
    ProvisionedVersion,
    ProvisioningError,
    VersionRegistry,
)
from integration.experiment.version_hop import (
    BROKEN,
    HOLDS,
    INCONCLUSIVE,
    STRATEGY_BISECT,
    STRATEGY_LINEAR,
    VersionProbe,
    find_break_version,
)

RELEASES = [
    "r2022.04", "r2023.09", "r2024.01", "r2024.09", "r2025.02",
    "r2025.03", "r2025.08", "r2025.10", "r2025.11", "r2026.02",
    "r2026.03", "r2026.05", "r2026.06", "r2026.07",
]


# --- provisioning -----------------------------------------------------------


class FakeShell:
    """Stands in for every external command.

    `answers` maps a substring of the joined command to (returncode, stdout).
    """

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.commands: list[list[str]] = []

    def __call__(self, args, cwd=None) -> subprocess.CompletedProcess:
        self.commands.append(list(args))
        joined = " ".join(args)
        for needle, (code, out) in self.answers.items():
            if needle in joined:
                return subprocess.CompletedProcess(list(args), code, out, "")
        return subprocess.CompletedProcess(list(args), 0, "", "")

    def ran(self, needle: str) -> bool:
        return any(needle in " ".join(c) for c in self.commands)


@pytest.fixture
def shell() -> FakeShell:
    return FakeShell({"rev-parse": (0, "abc123def\n")})


@pytest.fixture
def provisioner(tmp_path, shell) -> EcVersionProvisioner:
    return EcVersionProvisioner(
        fork=tmp_path / "fork",
        root=tmp_path / "versions",
        registry=VersionRegistry(tmp_path / "versions" / "registry.json"),
        runner=shell,
    )


def _pretend_built(provisioner: EcVersionProvisioner, version: str) -> Path:
    binary = provisioner.binary_path(version)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/false\n", encoding="utf-8")
    return binary


def test_a_release_tag_resolves_to_a_commit_in_the_existing_clone(provisioner, shell):
    """No network. The fork was cloned from upstream so it already carries
    every rYYYY.MM tag; asking a remote would add a failure mode for
    information that is already on disk."""
    assert provisioner.resolve_commit("r2025.02") == "abc123def"
    assert shell.ran("rev-parse r2025.02^{commit}")
    assert not shell.ran("ls-remote")


def test_a_non_release_string_is_refused_before_any_command_runs(provisioner, shell):
    with pytest.raises(ProvisioningError, match="not a release tag"):
        provisioner.resolve_commit("main")
    assert not shell.commands


def test_provisioning_uses_a_worktree_not_a_clone(provisioner, shell, tmp_path):
    """N clones would multiply disk and network by N for no benefit: every
    release is an ancestor of the same repository."""
    _pretend_built(provisioner, "r2025.02")
    provisioner.ensure("r2025.02")
    assert shell.ran("worktree add --detach")
    assert not shell.ran("git clone")


def test_a_cached_version_is_never_rebuilt(provisioner, shell):
    _pretend_built(provisioner, "r2025.02")
    first = provisioner.ensure("r2025.02")
    shell.commands.clear()
    second = provisioner.ensure("r2025.02")
    assert second.binary == first.binary
    assert not shell.ran("dune build")
    assert not shell.ran("opam switch create")


def test_a_registry_entry_whose_binary_is_gone_is_forgotten(provisioner):
    """The registry is a cache index, not proof. A `git worktree remove` or a
    manual cleanup leaves an entry pointing at nothing, and reporting that as
    a working build hands the hop harness a path it cannot run."""
    binary = _pretend_built(provisioner, "r2025.02")
    provisioner.ensure("r2025.02")
    binary.unlink()
    assert provisioner.cached("r2025.02") is None
    assert "r2025.02" not in provisioner.registry.versions


def test_a_failed_build_raises_rather_than_recording_a_bad_entry(tmp_path):
    shell = FakeShell({"rev-parse": (0, "abc\n"), "dune build": (1, "compile error")})
    provisioner = EcVersionProvisioner(
        fork=tmp_path / "fork", root=tmp_path / "v",
        registry=VersionRegistry(tmp_path / "v" / "registry.json"), runner=shell,
    )
    with pytest.raises(ProvisioningError, match="building r2025.02"):
        provisioner.ensure("r2025.02")
    assert "r2025.02" not in provisioner.registry.versions


def test_the_resident_cap_evicts_least_recently_used_versions(tmp_path, shell):
    """An opam switch is hundreds of megabytes. Without a cap this grows
    without bound on whatever machine the experiment runs on."""
    provisioner = EcVersionProvisioner(
        fork=tmp_path / "fork", root=tmp_path / "v",
        registry=VersionRegistry(tmp_path / "v" / "registry.json"),
        max_provisioned=2, runner=shell,
    )
    for version in ("r2022.04", "r2023.09", "r2024.09"):
        _pretend_built(provisioner, version)
        provisioner.ensure(version)

    resident = set(provisioner.registry.versions)
    assert len(resident) == 2
    assert "r2024.09" in resident          # just built, never evicted
    assert "r2022.04" not in resident      # least recently used
    assert shell.ran("switch remove cs846-ec-r2022.04")
    assert shell.ran("worktree remove")


def test_eviction_never_removes_the_version_being_provisioned(tmp_path, shell):
    provisioner = EcVersionProvisioner(
        fork=tmp_path / "fork", root=tmp_path / "v",
        registry=VersionRegistry(tmp_path / "v" / "registry.json"),
        max_provisioned=1, runner=shell,
    )
    for version in ("r2022.04", "r2025.02"):
        _pretend_built(provisioner, version)
        provisioner.ensure(version)
    assert set(provisioner.registry.versions) == {"r2025.02"}


def test_a_corrupt_registry_starts_empty_instead_of_raising(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{not json", encoding="utf-8")
    assert VersionRegistry(path).versions == {}


def test_the_registry_survives_the_process_that_wrote_it(tmp_path, shell):
    provisioner = EcVersionProvisioner(
        fork=tmp_path / "fork", root=tmp_path / "v",
        registry=VersionRegistry(tmp_path / "v" / "registry.json"), runner=shell,
    )
    _pretend_built(provisioner, "r2025.02")
    provisioner.ensure("r2025.02")
    reloaded = VersionRegistry(tmp_path / "v" / "registry.json")
    assert "r2025.02" in reloaded.versions
    assert reloaded.get("r2025.02")["commit"] == "abc123def"


def test_dry_run_still_answers_queries_but_changes_nothing(tmp_path):
    """A dry run whose lookups all return empty produces a plan built on
    nothing and reports failures that would not happen."""
    shell = FakeShell({"rev-parse": (0, "deadbeef\n")})
    provisioner = EcVersionProvisioner(
        fork=tmp_path / "fork", root=tmp_path / "v",
        registry=VersionRegistry(tmp_path / "v" / "registry.json"),
        dry_run=True, runner=shell,
    )
    provisioner.ensure("r2025.02")
    assert shell.ran("rev-parse")                       # query ran
    assert not shell.ran("dune build")                  # build did not
    assert any("dune build" in " ".join(c) for c in provisioner.plan)


# --- localization -----------------------------------------------------------


class FakeProvisioner:
    """Hands out a distinct path per version and counts builds."""

    def __init__(self, unbuildable: set[str] | None = None):
        self.unbuildable = unbuildable or set()
        self.built: list[str] = []

    def ensure(self, version: str) -> ProvisionedVersion:
        if version in self.unbuildable:
            raise ProvisioningError(f"{version} does not build here")
        self.built.append(version)
        return ProvisionedVersion(
            version=version, commit="x", worktree=Path("/w"),
            binary=Path(f"/bin/ec-{version}"), switch="s", built_at="now",
        )


def _verdicts(mapping: dict[str, str]):
    """A probe function driven by a version -> verdict table."""
    def probe(file_path: Path, binary: Path, config: AgentConfig) -> VersionProbe:
        version = binary.name.replace("ec-", "")
        verdict = mapping[version]
        return VersionProbe(
            version=version, verdict=verdict,
            error_kind="" if verdict == HOLDS else (
                "tactic_error" if verdict == BROKEN else "unknown_theory"
            ),
        )
    return probe


def _run(mapping, *, strategy=STRATEGY_BISECT, versions=None, unbuildable=None):
    provisioner = FakeProvisioner(unbuildable)
    result = find_break_version(
        file_path=Path("/tmp/x.ec"),
        versions=versions or RELEASES,
        config=AgentConfig(),
        provisioner=provisioner,
        strategy=strategy,
        probe=_verdicts(mapping),
    )
    return result, provisioner


def _table(broken_from: str, versions=RELEASES) -> dict[str, str]:
    index = versions.index(broken_from)
    return {v: (HOLDS if i < index else BROKEN) for i, v in enumerate(versions)}


def test_bisection_finds_the_exact_release_boundary():
    result, _ = _run(_table("r2025.02"))
    assert result.last_good == "r2024.09"
    assert result.first_broken == "r2025.02"
    assert result.localized


def test_bisection_costs_far_fewer_builds_than_a_walk():
    """The whole reason not to walk: a build is minutes. Over 14 releases
    binary search is ~4 probes where the linear walk is up to 14."""
    bisect, bisect_prov = _run(_table("r2026.03"))
    linear, linear_prov = _run(_table("r2026.03"), strategy=STRATEGY_LINEAR)

    assert bisect.first_broken == linear.first_broken == "r2026.03"
    assert bisect.last_good == linear.last_good
    assert len(bisect_prov.built) < len(linear_prov.built)
    assert len(bisect_prov.built) <= 6


def test_the_narrowed_range_is_one_transition_not_the_whole_span():
    result, _ = _run(_table("r2025.02"))
    assert result.changelog_range == ("r2024.09", "r2025.02")


def test_a_file_that_will_not_load_at_a_release_is_inconclusive_not_broken():
    """A 2020 proof repaired to load against r2026.06 requires FMap, and FMap
    did not exist before r2024.09 -- so the file does not LOAD at r2023.09 and
    the tactic is never reached. Counting that as "broken here" puts the
    boundary at the wrong release."""
    table = _table("r2025.02")
    for version in ("r2023.09", "r2024.01"):
        table[version] = INCONCLUSIVE
    result, _ = _run(table)
    assert result.first_broken == "r2025.02"
    assert result.last_good == "r2024.09"
    # Whatever was probed is reported with the verdict it actually gave --
    # inconclusive results are recorded, never silently recoded as broken.
    for probe in result.probes:
        assert probe.verdict == table[probe.version]


def test_an_all_inconclusive_middle_localizes_only_to_the_window():
    table = {v: INCONCLUSIVE for v in RELEASES}
    table[RELEASES[0]] = HOLDS
    table[RELEASES[-1]] = BROKEN
    result, _ = _run(table)
    assert result.last_good == RELEASES[0]
    assert result.first_broken == RELEASES[-1]
    assert any("no conclusive release" in n for n in result.notes)


def test_endpoints_that_do_not_bracket_a_break_refuse_to_narrow():
    """If the oldest release already fails, the break is older than anything
    we can check, and naming a boundary inside the range would be a guess.
    Fail open -- the same convention releases_in_range uses."""
    result, _ = _run({v: BROKEN for v in RELEASES})
    assert not result.localized
    assert result.changelog_range is None
    assert any("do not bracket" in n for n in result.notes)


def test_a_tactic_that_holds_everywhere_is_not_localized():
    result, _ = _run({v: HOLDS for v in RELEASES})
    assert not result.localized
    assert result.changelog_range is None


def test_a_release_that_will_not_build_is_skipped_not_fatal():
    """Version hopping is an optional precision improvement. A release that
    does not compile on this machine must degrade to "unknown", never fail
    the repair that asked."""
    result, _ = _run(_table("r2025.02"), unbuildable={"r2025.03", "r2024.09"})
    assert result.localized
    assert any("could not provision" in n for n in result.notes)


def test_every_release_unbuildable_gives_an_honest_nothing():
    result, _ = _run(_table("r2025.02"), unbuildable=set(RELEASES))
    assert not result.localized
    assert result.changelog_range is None


def test_fewer_than_two_releases_cannot_have_a_boundary():
    result, _ = _run({"r2025.02": BROKEN}, versions=["r2025.02"])
    assert not result.localized
    assert any("at least two releases" in n for n in result.notes)


def test_each_release_is_probed_at_most_once():
    """Builds are the expensive thing; re-probing a release the search
    already visited would pay for it twice."""
    _result, provisioner = _run(_table("r2025.11"))
    assert len(provisioner.built) == len(set(provisioner.built))


def test_the_result_records_which_strategy_produced_it():
    """Bisection assumes the tactic breaks once and stays broken. A reader
    comparing two runs must be able to tell which assumption was in play."""
    bisect, _ = _run(_table("r2025.02"))
    linear, _ = _run(_table("r2025.02"), strategy=STRATEGY_LINEAR)
    assert bisect.strategy == STRATEGY_BISECT
    assert linear.strategy == STRATEGY_LINEAR
    assert bisect.as_dict()["strategy"] == STRATEGY_BISECT


def test_the_probe_does_not_repoint_the_caller_s_config(monkeypatch, tmp_path):
    """A shared config silently repointed at a 2022 build would break
    everything downstream of the hop in a way that is very hard to see."""
    calls = []

    def fake_validate(path, config):
        calls.append(config.easycrypt_bin)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(version_hop, "validate_file", fake_validate)
    config = AgentConfig(easycrypt_bin=Path("/target/ec.exe"))
    version_hop.probe_version(tmp_path / "x.ec", Path("/old/ec.exe"), config)

    assert calls == [Path("/old/ec.exe")]
    assert config.easycrypt_bin == Path("/target/ec.exe")


@pytest.mark.parametrize(
    "output,expected",
    [
        ("[critical] [/x.ec: line 453 (2)] invalid `position' parameter", BROKEN),
        ("[critical] [/x.ec: line 108 (8)] cannot find theory: `FMap'", INCONCLUSIVE),
        ("[critical] [/x.ec: line 12 (0)] parse error", INCONCLUSIVE),
        ("something nobody has seen", INCONCLUSIVE),
    ],
)
def test_only_an_in_proof_failure_counts_as_the_tactic_breaking(
    monkeypatch, tmp_path, output, expected
):
    monkeypatch.setattr(
        version_hop, "validate_file",
        lambda path, config: type(
            "R", (), {"returncode": 1, "stdout": "", "stderr": output}
        )(),
    )
    probe = version_hop.probe_version(
        tmp_path / "x.ec", Path("/old/ec.exe"), AgentConfig()
    )
    assert probe.verdict == expected


# --- integration point ------------------------------------------------------
# The design is explicit that this is additive: replay_bootstrap must behave
# exactly as it did unless a run opts in. Building EasyCrypt is not something
# a default path may ever do.


def test_the_flag_is_off_by_default_on_every_registered_spec():
    from integration.experiment.specs import SPECS

    for name in SPECS.names():
        spec = SPECS.get(name)
        if spec.replay_bootstrap is None:
            continue
        assert spec.replay_bootstrap.version_hop is False, name


def test_the_cli_flag_reaches_the_spec():
    from integration.experiment.__main__ import main

    captured = {}

    def fake_run(spec, config):
        captured["spec"] = spec
        raise SystemExit(0)

    import integration.experiment.__main__ as cli

    original = cli.run_experiment
    cli.run_experiment = fake_run
    try:
        with pytest.raises(SystemExit):
            main(["run", "--spec", "elgamal-changelog-repair", "--version-hop",
                  "--version-hop-strategy", "linear", "--trials", "1"])
    finally:
        cli.run_experiment = original

    replay = captured["spec"].replay_bootstrap
    assert replay.version_hop is True
    assert replay.version_hop_strategy == "linear"


def test_the_cli_refuses_the_flag_on_a_spec_that_cannot_use_it():
    """Silently ignoring it would let someone believe a run was localized
    when nothing hopped."""
    from integration.experiment.__main__ import main

    assert main(["run", "--spec", "joy-tactic-repair", "--version-hop"]) == 2


def test_the_hop_range_is_bounded_by_the_changelog_catalog():
    """Localizing a break to a release the changelog has no entries for buys
    nothing: the only purpose of narrowing is a more precise lookup."""
    from integration.experiment.repair_bootstrap import _hop_releases

    releases = _hop_releases("r2024.09", "r2025.03")
    if not releases:
        pytest.skip("changelog catalog unreachable")
    assert releases[0] == "r2024.09"
    assert releases[-1] == "r2025.03"
    assert releases == sorted(releases)


def test_an_unknown_endpoint_widens_to_the_whole_catalog():
    from integration.experiment.repair_bootstrap import _hop_releases

    catalog = _hop_releases(None, None)
    if not catalog:
        pytest.skip("changelog catalog unreachable")
    assert _hop_releases("r1999.01", "r2025.02")[0] == catalog[0]
