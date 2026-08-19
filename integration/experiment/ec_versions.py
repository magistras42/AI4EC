"""Provision one EasyCrypt binary per release tag, lazily and cached (W7).

Roadmap item W7 in [`docs/PROOF_REPAIR_HANDOFF.md`](../../docs/PROOF_REPAIR_HANDOFF.md),
designed in [`docs/plans/ec_version_hopping_infrastructure.md`](../../docs/plans/ec_version_hopping_infrastructure.md).

The replay-bootstrap repair mode talks to exactly one EasyCrypt build, so it
can say a tactic fails against the CURRENT target and nothing more. Which
release broke it is what scopes the changelog lookup from "everything between
two guessed endpoints" down to one transition, and answering that needs a
binary per release. This module provisions them; :mod:`version_hop` uses them.

Three properties the design doc calls out as non-negotiable, because a full
EasyCrypt build is minutes of CPU and an opam switch is hundreds of megabytes:

**Lazy.** Nothing is built until a hop actually needs to check that release.
Building the whole 14-release catalog for one repair would dwarf the repair.

**Cached.** The registry is the cache index. A version whose binary is still
on disk is never rebuilt, across runs.

**Bounded.** ``max_provisioned`` caps how many switches stay resident;
least-recently-used versions are evicted first. Without a cap this silently
grows to gigabytes on a laptop.

Worktrees, not clones
---------------------
Every version is a ``git worktree`` against the existing fork checkout, so all
of them share one object store. N full clones would multiply disk and network
by N for no benefit -- every release is an ancestor of the same repository.

Unpatched tags are the point, not a compromise
----------------------------------------------
The fork carries a ``-premises`` patch (``src/ecOptions.ml``, ``src/ec.ml``)
on its own HEAD; the release tags are upstream and do not have it. That is
fine and is what the design chose deliberately: hop validation only ever runs
``llm -lastgoals``, which is unpatched behaviour, so no patch has to be
rebased onto a four-year-old release to make this work. Only the target binary
-- the fork build the agent itself uses -- needs ``-premises``.

CLI::

    python3 -m integration.experiment.ec_versions --list
    python3 -m integration.experiment.ec_versions --version r2025.02 --dry-run
    python3 -m integration.experiment.ec_versions --version r2025.02
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from integration.agent.ec_version import VERSION_TAG_RE

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FORK = REPO_ROOT / "integration" / "extern" / "easycrypt"
DEFAULT_ROOT = REPO_ROOT / "integration" / "extern" / ".ec_versions"
DEFAULT_REGISTRY = DEFAULT_ROOT / "registry.json"

SCHEMA = "ai4ec.ec-version-registry/1"

#: Same compiler the working `cs846` switch uses. Pinned rather than "latest"
#: because EasyCrypt's own dependency bounds are what make 4.14.1 work.
DEFAULT_COMPILER = "ocaml-base-compiler.4.14.1"
DEFAULT_SWITCH_PREFIX = "cs846-ec"

#: How many built versions stay resident. Three covers a bisection over the
#: 14-release catalog (log2(14) ~ 4) without keeping every switch alive.
DEFAULT_MAX_PROVISIONED = 3

#: A build is minutes, not seconds. Generous, but not unbounded: a hung `dune`
#: must not wedge an overnight experiment.
BUILD_TIMEOUT_SECONDS = 3600


class ProvisioningError(RuntimeError):
    """A version could not be provisioned.

    Always recoverable at the call site: version hopping is an optional
    precision improvement, and a release that will not build must degrade to
    "this release is unknown", never fail the repair.
    """


@dataclass(frozen=True)
class ProvisionedVersion:
    version: str
    commit: str
    worktree: Path
    binary: Path
    switch: str
    built_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "worktree_path": str(self.worktree),
            "binary_path": str(self.binary),
            "opam_switch": self.switch,
            "built_at": self.built_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class VersionRegistry:
    """The on-disk index of what has been built, and when it was last used.

    Deliberately a plain JSON file rather than anything cleverer: it is a
    cache index that a human must be able to read, edit and delete when a
    build goes wrong, and it has to survive the process that wrote it.
    """

    def __init__(self, path: Path = DEFAULT_REGISTRY) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema": SCHEMA, "versions": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt cache index must not be fatal -- the worst case is
            # rebuilding, which is expensive but correct.
            logger.warning("version registry %s unreadable (%s); starting empty",
                           self.path, exc)
            return {"schema": SCHEMA, "versions": {}}
        data.setdefault("versions", {})
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @property
    def versions(self) -> dict[str, Any]:
        return self._data["versions"]

    def get(self, version: str) -> dict[str, Any] | None:
        return self.versions.get(version)

    def record(self, provisioned: ProvisionedVersion) -> None:
        entry = provisioned.as_dict()
        entry["last_used_at"] = _now()
        self.versions[provisioned.version] = entry
        self.save()

    def touch(self, version: str) -> None:
        entry = self.versions.get(version)
        if entry is None:
            return
        entry["last_used_at"] = _now()
        self.save()

    def forget(self, version: str) -> None:
        self.versions.pop(version, None)
        self.save()

    def least_recently_used(self, keep: set[str] | None = None) -> list[str]:
        """Resident versions, oldest use first, excluding `keep`."""
        keep = keep or set()
        rows = [
            (entry.get("last_used_at") or "", version)
            for version, entry in self.versions.items()
            if version not in keep
        ]
        return [version for _stamp, version in sorted(rows)]


class EcVersionProvisioner:
    """Builds and caches one EasyCrypt binary per release tag.

    Every external command goes through :meth:`_run`, which is the single seam
    tests replace -- a real build is minutes long, so nothing in the test suite
    may ever reach it.
    """

    def __init__(
        self,
        *,
        fork: Path = DEFAULT_FORK,
        root: Path = DEFAULT_ROOT,
        registry: VersionRegistry | None = None,
        compiler: str = DEFAULT_COMPILER,
        switch_prefix: str = DEFAULT_SWITCH_PREFIX,
        max_provisioned: int = DEFAULT_MAX_PROVISIONED,
        dry_run: bool = False,
        runner: Callable[[Sequence[str], Path | None], subprocess.CompletedProcess] | None = None,
    ) -> None:
        self.fork = Path(fork)
        self.root = Path(root)
        self.registry = registry if registry is not None else VersionRegistry(
            self.root / "registry.json"
        )
        self.compiler = compiler
        self.switch_prefix = switch_prefix
        self.max_provisioned = max_provisioned
        self.dry_run = dry_run
        self._runner = runner
        self.plan: list[list[str]] = []

    # --- plumbing -----------------------------------------------------------

    def _run(
        self, args: Sequence[str], cwd: Path | None = None, *,
        timeout: int = 600, mutating: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run one external command.

        `mutating=False` marks a pure query -- `git rev-parse`, `opam switch
        list`. Those run even under `--dry-run`, because a dry run whose
        lookups all return empty produces a plan built on nothing and reports
        failures that would not happen. Only commands that change the machine
        are suppressed.
        """
        if mutating:
            self.plan.append(list(args))
        if self.dry_run and mutating:
            return subprocess.CompletedProcess(list(args), 0, "", "")
        if self._runner is not None:
            return self._runner(args, cwd)
        return subprocess.run(
            list(args), cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout,
        )

    def _check(self, args: Sequence[str], cwd: Path | None = None,
               *, timeout: int = 600, what: str = "",
               mutating: bool = True) -> str:
        result = self._run(args, cwd, timeout=timeout, mutating=mutating)
        if result.returncode != 0:
            raise ProvisioningError(
                f"{what or ' '.join(args[:2])} failed ({result.returncode}): "
                f"{(result.stderr or result.stdout or '').strip()[:400]}"
            )
        return result.stdout or ""

    # --- naming -------------------------------------------------------------

    def switch_name(self, version: str) -> str:
        return f"{self.switch_prefix}-{version}"

    def worktree_path(self, version: str) -> Path:
        return self.root / version

    def binary_path(self, version: str) -> Path:
        return self.worktree_path(version) / "_build" / "default" / "src" / "ec.exe"

    # --- steps --------------------------------------------------------------

    def resolve_commit(self, version: str) -> str:
        """Resolve a release tag to a commit in the existing fork clone.

        No network. The fork was cloned from upstream, so it already carries
        every ``rYYYY.MM`` tag; asking a remote would add a failure mode for
        information that is on disk.
        """
        if not VERSION_TAG_RE.fullmatch(version):
            raise ProvisioningError(f"{version!r} is not a release tag (rYYYY.MM)")
        out = self._check(
            ["git", "-C", str(self.fork), "rev-parse", f"{version}^{{commit}}"],
            what=f"resolving {version}", mutating=False,
        ).strip()
        if not out:
            raise ProvisioningError(f"no commit for tag {version} in {self.fork}")
        return out

    def known_versions(self) -> list[str]:
        """Release tags the fork clone can actually build, oldest first."""
        try:
            out = self._check(["git", "-C", str(self.fork), "tag"],
                          what="listing tags", mutating=False)
        except ProvisioningError:
            return []
        return sorted(t.strip() for t in out.splitlines()
                      if VERSION_TAG_RE.fullmatch(t.strip()))

    def _ensure_worktree(self, version: str, commit: str) -> Path:
        path = self.worktree_path(version)
        if path.is_dir() and (path / "dune-project").is_file():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        # `--detach`: a worktree on a named branch would fight the fork's own
        # checkout, and nothing here ever commits.
        self._check(
            ["git", "-C", str(self.fork), "worktree", "add", "--detach",
             str(path), commit],
            what=f"creating worktree for {version}",
        )
        return path

    def _ensure_switch(self, version: str) -> str:
        name = self.switch_name(version)
        existing = self._run(["opam", "switch", "list", "--short"], mutating=False)
        if name in (existing.stdout or "").split():
            return name
        self._check(
            ["opam", "switch", "create", name, self.compiler, "--no-switch", "-y"],
            timeout=BUILD_TIMEOUT_SECONDS,
            what=f"creating opam switch for {version}",
        )
        return name

    def _build(self, version: str, worktree: Path, switch: str) -> Path:
        self._check(
            ["opam", "install", "--deps-only", "--switch", switch, "-y", "."],
            cwd=worktree, timeout=BUILD_TIMEOUT_SECONDS,
            what=f"installing deps for {version}",
        )
        self._check(
            ["opam", "exec", "--switch", switch, "--", "dune", "build"],
            cwd=worktree, timeout=BUILD_TIMEOUT_SECONDS,
            what=f"building {version}",
        )
        binary = self.binary_path(version)
        if not self.dry_run and self._runner is None and not binary.is_file():
            raise ProvisioningError(
                f"{version} built without error but {binary} does not exist"
            )
        return binary

    # --- the API ------------------------------------------------------------

    def cached(self, version: str) -> ProvisionedVersion | None:
        """A previously built version, if its binary is still on disk.

        The registry alone is not proof: a `git worktree remove` or a manual
        cleanup leaves an entry pointing at nothing. Checking the file is what
        keeps a stale index from being reported as a working build.
        """
        entry = self.registry.get(version)
        if not entry:
            return None
        binary = Path(entry.get("binary_path", ""))
        if not binary.is_file():
            logger.info("registry lists %s but %s is gone; forgetting it",
                        version, binary)
            self.registry.forget(version)
            return None
        self.registry.touch(version)
        return ProvisionedVersion(
            version=version,
            commit=str(entry.get("commit", "")),
            worktree=Path(entry.get("worktree_path", "")),
            binary=binary,
            switch=str(entry.get("opam_switch", "")),
            built_at=str(entry.get("built_at", "")),
        )

    def ensure(self, version: str) -> ProvisionedVersion:
        """Return a built binary for `version`, building it only if needed."""
        hit = self.cached(version)
        if hit is not None:
            logger.info("reusing cached EasyCrypt %s at %s", version, hit.binary)
            return hit

        logger.info("provisioning EasyCrypt %s (this takes minutes)", version)
        commit = self.resolve_commit(version)
        worktree = self._ensure_worktree(version, commit)
        switch = self._ensure_switch(version)
        binary = self._build(version, worktree, switch)

        provisioned = ProvisionedVersion(
            version=version, commit=commit, worktree=worktree,
            binary=binary, switch=switch, built_at=_now(),
        )
        self.registry.record(provisioned)
        self.enforce_cap(keep={version})
        return provisioned

    def enforce_cap(self, keep: set[str] | None = None) -> list[str]:
        """Evict least-recently-used versions beyond ``max_provisioned``."""
        if self.max_provisioned <= 0:
            return []
        resident = list(self.registry.versions)
        excess = len(resident) - self.max_provisioned
        if excess <= 0:
            return []
        evicted = []
        for version in self.registry.least_recently_used(keep=keep)[:excess]:
            self.evict(version)
            evicted.append(version)
        return evicted

    def evict(self, version: str) -> None:
        """Remove a version's worktree and switch, and forget it.

        Best-effort by design. A half-removed switch is a wasted gigabyte; a
        raised exception here would fail a repair that had already succeeded.
        """
        entry = self.registry.get(version) or {}
        worktree = entry.get("worktree_path")
        switch = entry.get("opam_switch")
        logger.info("evicting EasyCrypt %s (worktree %s, switch %s)",
                    version, worktree, switch)
        if worktree:
            self._run(["git", "-C", str(self.fork), "worktree", "remove",
                       "--force", str(worktree)])
            if not self.dry_run and self._runner is None:
                shutil.rmtree(worktree, ignore_errors=True)
        if switch:
            self._run(["opam", "switch", "remove", str(switch), "-y"],
                      timeout=BUILD_TIMEOUT_SECONDS)
        self.registry.forget(version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and cache per-release EasyCrypt binaries (W7)",
    )
    parser.add_argument("--version", action="append", default=None,
                        help="release tag to provision; repeatable")
    parser.add_argument("--list", action="store_true",
                        help="show buildable tags and what is already cached")
    parser.add_argument("--evict", action="append", default=None,
                        help="remove a provisioned version's worktree and switch")
    parser.add_argument("--fork", type=Path, default=DEFAULT_FORK)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--max-provisioned", type=int,
                        default=DEFAULT_MAX_PROVISIONED)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the commands that would run and change nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    provisioner = EcVersionProvisioner(
        fork=args.fork, root=args.root,
        registry=VersionRegistry(args.root / "registry.json"),
        max_provisioned=args.max_provisioned,
        dry_run=args.dry_run,
    )

    if args.list:
        cached = provisioner.registry.versions
        print(f"fork:     {args.fork}")
        print(f"registry: {provisioner.registry.path}")
        print(f"cap:      {args.max_provisioned} resident version(s)\n")
        for version in provisioner.known_versions():
            entry = cached.get(version)
            state = (f"built {entry.get('built_at', '?')}"
                     if entry else "not built")
            print(f"  {version:10s} {state}")
        return 0

    for version in args.evict or []:
        provisioner.evict(version)

    failures = 0
    for version in args.version or []:
        try:
            result = provisioner.ensure(version)
            print(f"{version}: {result.binary}")
        except ProvisioningError as exc:
            print(f"{version}: FAILED -- {exc}", file=sys.stderr)
            failures += 1

    if args.dry_run:
        print("\nWould run:", file=sys.stderr)
        for command in provisioner.plan:
            print("  " + " ".join(command), file=sys.stderr)

    if not (args.version or args.evict):
        parser.print_help()
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
