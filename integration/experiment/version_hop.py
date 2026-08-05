"""Find which EasyCrypt release actually broke a tactic (W7).

Roadmap item W7, designed in
[`docs/plans/ec_version_hopping_infrastructure.md`](../../docs/plans/ec_version_hopping_infrastructure.md).

`repair_bootstrap.run_replay_bootstrap_trial` replays a proof against ONE
binary and stops at the first tactic that fails. All that tells you is "broken
at the target". The changelog lookup then has to consider every release
between two guessed endpoints -- up to fourteen -- when the answer is one
transition. This module narrows it by re-running the same
:func:`~integration.agent.easycrypt.validate_file` check against each
release's own binary, provisioned by :mod:`ec_versions`.

Binary search, not a walk
-------------------------
The design doc's flowchart walks releases oldest-to-newest. That is up to N
builds, and a build is minutes; binary search over 14 releases is 4. The
result is identical **if** "the tactic holds" is monotone in version, which is
the same assumption `git bisect` makes and is wrong in the same way (a tactic
broken in r2024.09 and fixed in r2025.02 has two boundaries and bisection
finds one of them). `strategy="linear"` buys the exhaustive answer for the
cost of N builds; the default does not, and :attr:`VersionHopResult.strategy`
records which was used so a reader is never guessing.

The third answer
----------------
The doc frames each probe as a yes/no: does the tactic still hold? Run against
a four-year-old EasyCrypt, most probes are neither. A 2020 proof repaired to
load against r2026.06 requires FMap, and FMap did not exist before r2024.09 --
so the file does not LOAD at r2023.09, and the tactic is never reached. Read
as "broken here", that puts the break at the wrong release and scopes the
changelog to the wrong transition.

`ec_errors` (W4.1) is exactly what distinguishes them: a pre-proof failure
means INCONCLUSIVE, not BROKEN. Inconclusive versions are excluded from the
search rather than counted as either answer, and if too few conclusive probes
remain the honest result is no boundary at all.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from integration.agent.config import AgentConfig
from integration.agent.ec_errors import classify_error, strip_warning_lines
from integration.agent.easycrypt import validate_file

from .ec_versions import EcVersionProvisioner, ProvisioningError

logger = logging.getLogger(__name__)

#: One probe's answer at one release.
HOLDS = "holds"
BROKEN = "broken"
INCONCLUSIVE = "inconclusive"

STRATEGY_BISECT = "bisect"
STRATEGY_LINEAR = "linear"


class SupportsEnsure(Protocol):
    """The slice of :class:`EcVersionProvisioner` this module needs."""

    def ensure(self, version: str) -> Any: ...


@dataclass(frozen=True)
class VersionProbe:
    """What one release's binary said about the file."""

    version: str
    verdict: str
    error_kind: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "verdict": self.verdict,
            "error_kind": self.error_kind, "message": self.message[:300],
        }


@dataclass
class VersionHopResult:
    """Where the tactic stopped working, and how sure we are.

    ``last_good`` / ``first_broken`` are ``None`` when no boundary was
    established -- no conclusive probe on one side, every probe inconclusive,
    or provisioning failed. Callers must treat that as "keep the wide
    changelog range", the same fail-open convention `releases_in_range` and
    `select_by_version` use. A narrowed-to-the-wrong-release lookup is worse
    than a wide one.
    """

    last_good: str | None = None
    first_broken: str | None = None
    probes: list[VersionProbe] = field(default_factory=list)
    strategy: str = STRATEGY_BISECT
    builds: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def localized(self) -> bool:
        return self.first_broken is not None

    @property
    def changelog_range(self) -> tuple[str, str] | None:
        """The one transition to scope the changelog lookup to.

        ``(last_good, first_broken)`` is a half-open range in exactly the sense
        `releases_in_range` already means: the entries that landed AFTER
        `last_good` and up to and including `first_broken`.
        """
        if self.first_broken is None:
            return None
        return (self.last_good or self.first_broken, self.first_broken)

    def as_dict(self) -> dict[str, Any]:
        return {
            "last_good": self.last_good,
            "first_broken": self.first_broken,
            "localized": self.localized,
            "strategy": self.strategy,
            "builds": self.builds,
            "probes": [p.as_dict() for p in self.probes],
            "notes": self.notes,
        }


def probe_version(
    file_path: Path, binary: Path, config: AgentConfig
) -> VersionProbe:
    """Ask one release's binary whether `file_path` still checks out.

    `config` is copied, not mutated: the caller's target binary must survive
    a hop, and a shared config silently repointed at a 2022 build would break
    everything downstream of the hop in a way that is very hard to see.
    """
    hop_config = copy.copy(config)
    hop_config.easycrypt_bin = binary

    result = validate_file(file_path, hop_config)
    if result.returncode == 0:
        return VersionProbe(version="", verdict=HOLDS)

    output = strip_warning_lines(
        (result.stderr or "").strip() or (result.stdout or "").strip()
    )
    classified = classify_error(output)
    # A file that will not LOAD at this release says nothing about the tactic:
    # the tactic was never reached. `unknown` joins it, since an unrecognised
    # message is not evidence of a proof failure either.
    verdict = BROKEN if classified.is_in_proof else INCONCLUSIVE
    return VersionProbe(
        version="", verdict=verdict,
        error_kind=classified.kind, message=classified.message,
    )


def find_break_version(
    *,
    file_path: Path,
    versions: Sequence[str],
    config: AgentConfig,
    provisioner: SupportsEnsure,
    strategy: str = STRATEGY_BISECT,
    probe: Callable[[Path, Path, AgentConfig], VersionProbe] | None = None,
) -> VersionHopResult:
    """Locate the release where `file_path` stopped checking out.

    `versions` must be in chronological order, oldest first -- the same order
    `releases_in_range` produces. `file_path` is the proof as the replay left
    it: every accepted tactic plus the one that failed.

    Never raises. A release that will not build, a probe that times out, or a
    file nothing can load all come back as an unlocalized result with a note.
    """
    probe = probe or probe_version
    result = VersionHopResult(strategy=strategy)
    ordered = list(versions)
    if len(ordered) < 2:
        result.notes.append(
            f"need at least two releases to find a boundary, got {len(ordered)}"
        )
        return result

    seen: dict[str, VersionProbe] = {}

    def check(version: str) -> VersionProbe:
        if version in seen:
            return seen[version]
        try:
            provisioned = provisioner.ensure(version)
        except ProvisioningError as exc:
            outcome = VersionProbe(version=version, verdict=INCONCLUSIVE,
                                   error_kind="unbuildable", message=str(exc))
            result.notes.append(f"{version}: could not provision -- {exc}")
        else:
            result.builds += 1
            raw = probe(file_path, Path(provisioned.binary), config)
            outcome = VersionProbe(
                version=version, verdict=raw.verdict,
                error_kind=raw.error_kind, message=raw.message,
            )
        seen[version] = outcome
        result.probes.append(outcome)
        logger.info("EasyCrypt %s: %s%s", version, outcome.verdict,
                    f" ({outcome.error_kind})" if outcome.error_kind else "")
        return outcome

    if strategy == STRATEGY_LINEAR:
        _linear(ordered, check, result)
    else:
        _bisect(ordered, check, result)

    if not result.localized and not result.notes:
        result.notes.append(
            "no release boundary found: every conclusive probe agreed, so the "
            "tactic either holds throughout the range or fails throughout it"
        )
    return result


def _linear(
    versions: list[str],
    check: Callable[[str], VersionProbe],
    result: VersionHopResult,
) -> None:
    """Every release, oldest first. N builds, but no monotonicity assumed."""
    last_good: str | None = None
    for version in versions:
        outcome = check(version)
        if outcome.verdict == HOLDS:
            last_good = version
        elif outcome.verdict == BROKEN:
            result.last_good = last_good
            result.first_broken = version
            return


def _bisect(
    versions: list[str],
    check: Callable[[str], VersionProbe],
    result: VersionHopResult,
) -> None:
    """Binary search for the holds -> broken boundary.

    Inconclusive midpoints are the whole reason this is not four lines. When
    a probe cannot answer, the search steps outward from the midpoint looking
    for a nearby release that can, and only gives up on the window when no
    release inside it is conclusive.
    """
    low, high = 0, len(versions) - 1

    first = check(versions[low])
    last = check(versions[high])
    # Without a conclusive HOLDS below a conclusive BROKEN there is no
    # boundary to find, and inventing one from an inconclusive endpoint is how
    # this reports the wrong release.
    if first.verdict != HOLDS or last.verdict != BROKEN:
        result.notes.append(
            f"endpoints do not bracket a break ({versions[low]}="
            f"{first.verdict}, {versions[high]}={last.verdict}); "
            "not narrowing the changelog range"
        )
        return

    while high - low > 1:
        middle = _conclusive_midpoint(versions, low, high, check)
        if middle is None:
            result.notes.append(
                f"no conclusive release between {versions[low]} and "
                f"{versions[high]}; boundary localized only to that window"
            )
            break
        index, outcome = middle
        if outcome.verdict == HOLDS:
            low = index
        else:
            high = index

    result.last_good = versions[low]
    result.first_broken = versions[high]


def _conclusive_midpoint(
    versions: list[str],
    low: int,
    high: int,
    check: Callable[[str], VersionProbe],
) -> tuple[int, VersionProbe] | None:
    """The release nearest the midpoint of ``(low, high)`` that can answer."""
    span = list(range(low + 1, high))
    if not span:
        return None
    midpoint = (low + high) // 2
    # Nearest-first: a conclusive probe close to the middle keeps the search
    # logarithmic, and one at the edge still makes progress.
    for index in sorted(span, key=lambda i: (abs(i - midpoint), i)):
        outcome = check(versions[index])
        if outcome.verdict in (HOLDS, BROKEN):
            return index, outcome
    return None
