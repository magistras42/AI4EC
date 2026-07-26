#!/usr/bin/env python3
"""
compute_exposure_score.py

Estimate how "exposed" a downloaded EasyCrypt proof repository is to
breaking changes between whatever version it was last developed against
and a target version -- for building a proof-repair difficulty ladder.

Two things are computed and combined:

1. Breaking-change exposure: sum of weighted changelog entries crossed
   between the repo's (detected or estimated) source version and the
   target version, using the structured changelog from
   process_changelog.py.

2. Automation-reliance: how much the repo's proofs lean on solver-backed
   tactics (smt, auto, algebra). This AMPLIFIES (1) multiplicatively rather
   than being scored independently -- total_exposure = breaking_change_score
   * (1 + automation_ratio * automation_multiplier). SMT/solver-backend
   drift (Why3, Alt-Ergo, Z3 versions bundled with each EasyCrypt release)
   isn't tracked in EasyCrypt's own changelog at all, so heavy automation
   reliance is a real, otherwise-invisible risk multiplier on top of
   whatever changelog-visible breakage a repo has crossed -- but it
   shouldn't manufacture risk for a repo that has crossed almost nothing
   (e.g. one kept continuously in sync with EasyCrypt), since there's
   little for it to amplify.

A repo whose detected version predates our changelog's earliest cataloged
release (r2022.04 -- EasyCrypt's own GitHub Releases only go back that far,
even though the project is much older) is flagged predates_changelog=true.
Its breaking-change sum only covers 2022 onward and is known to understate
true exposure, so an additive penalty is applied AND -- more importantly --
ranking (print_ladder / rank_repos.py) always sorts these repos as harder
than any repo with real changelog coverage, regardless of the raw number.

Version detection, in priority order (see module docstring in the repo
README / chat writeup for rationale on why filesystem mtimes are
deliberately NOT used):

  a) Explicit pin: an EasyCrypt git submodule (commit SHA read via
     `git ls-tree`, optionally resolved to a release tag if
     --easycrypt-repo is given), or a version string found in common
     config/doc files (dune-project, CI workflows, README, opam files).
  b) Git commit date fallback: if the repo has a `.git` directory but no
     explicit pin, use `git log -1 --format=%aI` (the actual last commit's
     author date -- real VCS metadata, not a filesystem mtime) mapped to
     the latest EasyCrypt release published on or before that date.
  c) Content-based bracketing fallback: if there's no `.git` at all (e.g.
     a bare source snapshot), infer a plausible version *range* from which
     changelog-tracked identifiers the repo's proof scripts actually use
     (lemma_added entries give a lower bound; lemma_removed/syntax_change
     entries whose identifiers still appear in the repo give an upper
     bound).

Usage:
    # already-downloaded local repo -- scored directly, no cloning
    python compute_exposure_score.py \\
        --repo-path ./downloaded_repos/some_proof_repo \\
        --changelog changelog.yaml \\
        --target-version r2026.07 \\
        --easycrypt-repo ./easycrypt

    # single repo, cloned from a URL
    python compute_exposure_score.py \\
        --repo-url https://github.com/someorg/some-proof-repo \\
        --changelog changelog.yaml \\
        --target-version r2026.07 \\
        --easycrypt-repo ./easycrypt

    # batch, from a CSV of Name,URL rows -- each repo is cloned into
    # ./eval/<sanitized-name>/ and scored; a ranked ladder is printed
    python compute_exposure_score.py \\
        --csv repos.csv \\
        --changelog changelog.yaml \\
        --target-version r2026.07 \\
        --easycrypt-repo ./easycrypt \\
        --out exposure_results.json
"""

import argparse
import json
import math
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

# --- tunable weights --------------------------------------------------------

# Weight per changelog "kind", applied per matching entry in the crossed
# version range. lemma_added is weighted low since additions rarely break
# *existing* proofs (only matters if the repo would benefit from using the
# new lemma, which isn't really "breakage").
KIND_WEIGHTS = {
    "mechanism_change": 5.0,
    "syntax_change": 4.0,
    "lemma_removed": 4.0,
    "lemma_renamed": 3.0,
    "tactic_change": 3.0,
    "lemma_added": 1.0,
    "documentation": 0.0,
    "internal": 0.0,
}

RELEVANCE_MULTIPLIER = {"high": 1.0, "medium": 0.6, "low": 0.1, "unknown": 0.3}

# Automation reliance AMPLIFIES breaking-change exposure (multiplicatively)
# rather than being scored as an independent additive term -- see score_repo
# for the full rationale. At the default of 1.0, a repo whose proofs are
# 100% smt/auto/algebra calls has its breaking-change score doubled; a repo
# with near-zero real exposure (e.g. one kept continuously in sync with
# EasyCrypt) stays near-zero regardless of its automation ratio, since
# there's little drift for automation reliance to actually amplify.
AUTOMATION_MULTIPLIER_DEFAULT = 1.0

# Additive penalty applied when a repo's version predates our changelog's
# earliest cataloged release (EasyCrypt's GitHub Releases only go back to
# r2022.04, even though the project itself is much older) -- see
# TERMINAL_UNRESOLVED_METHODS and score_repo. This is SCALED by the repo's
# measured proof complexity (see compute_proof_complexity): a repo with
# only trivial one-line lemmas gets a small fraction of this penalty, while
# one with several deep, automation-heavy lemmas gets a multiple of it. The
# constant below is the penalty at "reference" complexity (scaling = 1.0).
PREDATES_CHANGELOG_PENALTY_DEFAULT = 20.0
# depth_complexity value (see compute_proof_complexity) treated as "typical
# moderate" -- scaling = 1.0 at this point, scaling grows/shrinks linearly
# from there, clamped to [MIN_SCALE, MAX_SCALE].
PREDATES_CHANGELOG_COMPLEXITY_REFERENCE = 15.0
PREDATES_CHANGELOG_MIN_SCALE = 0.2
PREDATES_CHANGELOG_MAX_SCALE = 2.0

# Applied instead of PREDATES_CHANGELOG_PENALTY_DEFAULT when a version is
# merely unresolved (no pin, no signal either way) rather than confidently
# known to predate the changelog. Smaller than the predates-changelog
# penalty because we have no positive evidence the repo is actually old --
# it could be a recently-maintained repo we simply couldn't fingerprint
# (see version_unknown in score_repo's output). Uses the same
# complexity-based scaling as the predates-changelog penalty.
VERSION_UNKNOWN_PENALTY_DEFAULT = 8.0

FRAGILE_TACTICS = {"smt", "auto", "algebra"}
# a broad approximation of "tactic invocation" tokens, used only as the
# denominator for a ratio -- doesn't need to be a precise EasyCrypt parser.
TACTIC_TOKEN_RE = re.compile(
    r"\b(smt|auto|algebra|rewrite|simplify|apply|move|have|suff|case|elim|"
    r"subst|clear|exact|congr|trivial|split|left|right|exists|call|wp|sp|"
    r"skip|proc|inline|seq|if|while|conseq|phoare|hoare|equiv|byequiv|"
    r"byphoare|admit|done)\b"
)

VERSION_TAG_RE = re.compile(r"\br20\d\d\.\d\d\b")


# --- version detection: (a) explicit pin ------------------------------------

def run_git(repo_path: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def resolve_ec_sha(sha: str, easycrypt_repo: Path, source_label: str) -> dict:
    """Resolve a pinned EasyCrypt commit SHA to (in priority order): a
    release tag reachable as an ancestor, or -- if the commit predates
    EasyCrypt's r20YY.MM tagging scheme entirely (so no tag is an ancestor
    of it) -- the commit's own author date, reported as a terminal
    'predates known releases' result rather than an unresolved failure.
    This second case matters: a commit from, say, 2018 legitimately has no
    tag to resolve to (the tagging scheme didn't exist yet), and that is
    itself a confident, meaningful answer ("older than everything we
    track"), not grounds to fall through to a weaker heuristic."""
    described = run_git(easycrypt_repo, ["describe", "--tags", sha])
    if described:
        m = re.match(r"(r20\d\d\.\d\d)(-\d+-g[0-9a-f]+)?$", described)
        if m:
            return {"version": m.group(1), "confidence": "high",
                    "method": f"{source_label}_resolved", "detail": f"sha={sha}, describe={described}"}

    commit_date = run_git(easycrypt_repo, ["show", "-s", "--format=%aI", sha])
    if commit_date:
        return {"version": None, "confidence": "high",
                "method": f"{source_label}_predates_tagging_scheme", "commit_date": commit_date,
                "detail": f"sha={sha} dated {commit_date}; no release tag is an ancestor of this commit "
                          f"(it predates EasyCrypt's r20YY.MM tagging scheme) -- treat as older than the "
                          f"full known changelog history, i.e. maximally exposed"}

    return {"version": None, "confidence": "low", "method": f"{source_label}_unresolved",
            "detail": f"found pinned sha={sha} but could not resolve it at all -- "
                      f"the --easycrypt-repo clone may not contain this commit (try a full, non-shallow clone)"}


def detect_submodule_pin(repo_path: Path, easycrypt_repo: Path | None) -> dict | None:
    """Look for an EasyCrypt submodule and read its pinned commit SHA via
    `git ls-tree` (works even if the submodule was never checked out --
    the SHA is recorded in the parent repo's tree object). If an
    --easycrypt-repo clone is given, resolve that SHA to the nearest
    release tag with `git describe`."""
    gitmodules = repo_path / ".gitmodules"
    if not gitmodules.exists():
        return None

    text = gitmodules.read_text(errors="ignore")
    # crude .gitmodules parse: find [submodule "x"] blocks whose path/url
    # mentions easycrypt
    submodule_path = None
    for block in re.split(r"\[submodule", text):
        if "easycrypt" in block.lower():
            m = re.search(r"path\s*=\s*(\S+)", block)
            if m:
                submodule_path = m.group(1)
                break
    if not submodule_path:
        return None

    sha_line = run_git(repo_path, ["ls-tree", "HEAD", submodule_path])
    if not sha_line:
        return None
    parts = sha_line.split()
    if len(parts) < 3:
        return None
    sha = parts[2]

    if easycrypt_repo is not None:
        return resolve_ec_sha(sha, easycrypt_repo, "submodule_pin")

    return {"version": None, "confidence": "low",
            "method": "submodule_pin_no_reference_repo",
            "detail": f"found pinned submodule sha={sha}; pass --easycrypt-repo to resolve it to a tag"}


# EasyCrypt project version files, in priority order: a purpose-built
# EasyCrypt project config, or the plain-text "EC_VERSION" convention used
# by several older EasyCrypt-based proof repos (e.g. those mirrored for
# travis-ci from ci.easycrypt.info) to pin an exact upstream commit.
PROJECT_FILE_NAMES = ["easycrypt.project", ".easycrypt.project"]
VERSION_FILE_NAMES = ["EC_VERSION", "EASYCRYPT_VERSION", ".ec-version"]
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def detect_version_file(repo_path: Path, easycrypt_repo: Path | None) -> dict | None:
    """Check for a plain-text version-pin file (EC_VERSION and variants).
    Content may be either a literal release tag (r20YY.MM) or a bare git
    commit SHA -- older EasyCrypt-based repos predating the tagging scheme
    used this convention to pin an exact upstream commit, so both forms
    need to be handled."""
    for name in VERSION_FILE_NAMES:
        text = _read_text(repo_path / name)
        if text is None:
            continue
        content = text.strip()

        m = VERSION_TAG_RE.search(content)
        if m:
            return {"version": m.group(0), "confidence": "high",
                    "method": "version_file_tag", "detail": f"found in {name}"}

        if SHA_RE.match(content):
            if easycrypt_repo is not None:
                return resolve_ec_sha(content, easycrypt_repo, "version_file_sha")
            return {"version": None, "confidence": "low",
                    "method": "version_file_sha_no_reference_repo",
                    "detail": f"found pinned commit sha={content} in {name}; "
                              f"pass --easycrypt-repo to resolve it"}
    return None


CONFIG_SEARCH_FILES = [
    "README.md", "README.rst", "README", "dune-project", "easycrypt.opam",
    "Makefile", "CHANGELOG.md",
]
CONFIG_SEARCH_GLOBS = [".github/workflows/*.yml", ".github/workflows/*.yaml", "*.opam"]

# Docker-related files get their own dedicated, higher-priority detector
# (see detect_docker_version) rather than being folded into the generic
# config search -- image tags (e.g. `FROM easycrypt/easycrypt:r2025.10`) are
# a strong, deliberate version pin, not an incidental mention.
DOCKER_FILE_GLOBS = [
    "Dockerfile*", "**/Dockerfile*", "docker-compose*.yml", "docker-compose*.yaml",
    ".devcontainer/**/*",
]

# EasyCrypt's own project config file, when present, is as authoritative as
# a submodule pin -- check it before falling back to loosely-matched
# config/doc files. (PROJECT_FILE_NAMES is defined earlier, alongside
# VERSION_FILE_NAMES and detect_version_file.)

SOLVER_NAMES = ("z3", "alt-ergo", "alt_ergo", "cvc4", "cvc5", "why3")
SOLVER_VERSION_RE = re.compile(
    r"\b(" + "|".join(SOLVER_NAMES) + r")\b[^\n]{0,25}?(\d+\.\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _read_text(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return None


def detect_project_file_version(repo_path: Path) -> dict | None:
    """Check easycrypt.project (EasyCrypt's own project config file), when
    present -- as authoritative a pin as a submodule SHA."""
    for name in PROJECT_FILE_NAMES:
        text = _read_text(repo_path / name)
        if text is None:
            continue
        m = VERSION_TAG_RE.search(text)
        if m:
            return {"version": m.group(0), "confidence": "high",
                    "method": "easycrypt_project_file", "detail": f"found in {name}"}
    return None


def detect_docker_version(repo_path: Path) -> dict | None:
    """Search Dockerfiles / docker-compose / devcontainer configs for an
    EasyCrypt version tag -- most commonly a `FROM easycrypt/easycrypt:rYYYY.MM`
    image reference, but any r20YY.MM string in these files is a strong
    signal since they're written to pin a reproducible build environment."""
    seen = set()
    for pattern in DOCKER_FILE_GLOBS:
        for path in repo_path.glob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            text = _read_text(path)
            if text is None:
                continue
            m = VERSION_TAG_RE.search(text)
            if m:
                return {"version": m.group(0), "confidence": "high",
                        "method": "docker_config", "detail": f"found in {path.relative_to(repo_path)}"}
    return None


def detect_smt_solver_info(repo_path: Path) -> list[dict]:
    """Scan config/Docker/CI files for pinned SMT solver versions (z3,
    alt-ergo, cvc4/5, why3). This is informational, not currently folded
    into the exposure score itself: solver-version drift is exactly the
    fragility source the automation-reliance weighting is meant to guard
    against, but we don't yet have a changelog-style dataset of *which*
    solver version changes actually break which tactics, the way we do for
    EasyCrypt's own changelog. Recording what's pinned here means that
    data is available for manual review, or for a future scoring
    refinement once such a mapping exists."""
    candidates: list[Path] = [repo_path / f for f in CONFIG_SEARCH_FILES]
    for pattern in CONFIG_SEARCH_GLOBS + DOCKER_FILE_GLOBS:
        candidates.extend(repo_path.glob(pattern))
    for name in PROJECT_FILE_NAMES:
        candidates.append(repo_path / name)

    hits = []
    seen_files = set()
    for path in candidates:
        if path in seen_files or not path.exists() or not path.is_file():
            continue
        seen_files.add(path)
        text = _read_text(path)
        if text is None:
            continue
        for m in SOLVER_VERSION_RE.finditer(text):
            hits.append({
                "solver": m.group(1).lower().replace("_", "-"),
                "version": m.group(2),
                "source_file": str(path.relative_to(repo_path)),
            })
    return hits


def detect_config_version_string(repo_path: Path) -> dict | None:
    """Search a small set of common config/doc files for a literal EasyCrypt
    release tag string (e.g. 'r2025.10'). Lower priority than
    detect_project_file_version / detect_docker_version, which check more
    deliberate, purpose-built pins first."""
    candidates = [repo_path / f for f in CONFIG_SEARCH_FILES]
    for pattern in CONFIG_SEARCH_GLOBS:
        candidates.extend(repo_path.glob(pattern))

    for path in candidates:
        text = _read_text(path)
        if text is None:
            continue
        m = VERSION_TAG_RE.search(text)
        if m:
            return {"version": m.group(0), "confidence": "high",
                    "method": "config_string", "detail": f"found in {path.relative_to(repo_path)}"}
    return None


# --- version detection: (b) git commit date fallback ------------------------

def detect_git_commit_date_version(repo_path: Path, releases_by_date: list[dict]) -> dict | None:
    if not (repo_path / ".git").exists():
        return None
    commit_date = run_git(repo_path, ["log", "-1", "--format=%aI"])
    if not commit_date:
        return None

    # latest release published on or before the commit date
    candidate = None
    for rel in releases_by_date:  # sorted ascending by published_at
        if rel.get("published_at") and rel["published_at"] <= commit_date:
            candidate = rel
        else:
            break
    if candidate is None:
        return {"version": None, "confidence": "high", "method": "git_commit_date_predates_changelog",
                "commit_date": commit_date,
                "detail": f"last commit {commit_date} predates all known releases (earliest cataloged is "
                          f"{releases_by_date[0]['version'] if releases_by_date else 'unknown'}) -- "
                          f"treat as older than the full known changelog history, i.e. maximally exposed"}
    return {"version": candidate["version"], "confidence": "medium",
            "method": "git_commit_date",
            "detail": f"last commit {commit_date}, nearest preceding release {candidate['version']}"}


# --- version detection: (c) content-based identifier bracketing -------------

EC_FILE_EXTENSIONS = {".ec", ".eca"}

# Directory names (matched case-insensitively against any path component)
# whose contents are skipped when scanning for proof files. EasyCrypt repos
# that also involve Jasmin (or other extraction pipelines) commonly contain
# machine-generated .ec files here -- e.g. Jasmin-to-EasyCrypt extraction
# output -- which carry the .ec extension but aren't hand-written proof
# content, and would otherwise skew automation-reliance and proof-complexity
# metrics with boilerplate that isn't representative of real proof
# difficulty. Extend via --exclude-dirs for repo-specific layouts.
DEFAULT_EXCLUDE_DIRS = {
    "extraction", "extraction_ni", "_build", "_opam", ".git", "node_modules",
}


def iter_ec_files(repo_path: Path, exclude_dirs: set[str] | None = None):
    """Yield all .ec/.eca files under repo_path, recursing into
    subdirectories, but pruning any directory whose name (case-insensitive)
    is in exclude_dirs -- so generated/extraction output and build
    artifacts don't get scanned as if they were proof content."""
    excludes = {d.lower() for d in (exclude_dirs if exclude_dirs is not None else DEFAULT_EXCLUDE_DIRS)}
    import os
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d.lower() not in excludes]
        for fname in filenames:
            if Path(fname).suffix in EC_FILE_EXTENSIONS:
                yield Path(dirpath) / fname


def collect_repo_tokens(repo_path: Path, exclude_dirs: set[str] | None = None) -> set[str]:
    tokens = set()
    for path in iter_ec_files(repo_path, exclude_dirs):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        tokens.update(re.findall(r"\b[A-Za-z_][A-Za-z0-9_']*\b", text))
    return tokens


def detect_content_bracket_version(repo_tokens: set[str], releases_by_date: list[dict]) -> dict:
    """Infer a plausible [lower_bound, upper_bound) version bracket from
    which changelog-tracked identifiers actually appear in the repo's
    proof scripts. Low confidence by construction -- meant as a
    last-resort fallback, and flagged for manual spot-checking."""
    lower_bound = None  # latest release whose "lemma_added" identifier we see used
    upper_bound = None  # earliest release whose "lemma_removed"/"syntax_change" identifier we still see used

    for rel in releases_by_date:
        for entry in rel.get("entries", []):
            ids = {str(i) for i in (entry.get("identifiers") or [])}
            if not ids & repo_tokens:
                continue
            kind = entry.get("kind")
            if kind == "lemma_added":
                lower_bound = rel  # keep overwriting -> ends up as the latest match
            elif kind in ("lemma_removed", "syntax_change") and upper_bound is None:
                upper_bound = rel  # keep the earliest match

    if lower_bound is None and upper_bound is None:
        return {"version": None, "confidence": "unknown", "method": "content_bracket_no_signal",
                "detail": "no changelog-tracked identifiers found in repo's .ec files; "
                          "cannot estimate version from content alone -- needs manual inspection"}

    detail = (f"lower_bound(>=): {lower_bound['version'] if lower_bound else 'none'}; "
              f"upper_bound(<): {upper_bound['version'] if upper_bound else 'none'}")
    # best single-point estimate: the lower bound if we have one (repo must be
    # at least this new to use what it uses); otherwise fall back to the
    # release just before the upper bound.
    estimate = lower_bound["version"] if lower_bound else None
    return {"version": estimate, "confidence": "low", "method": "content_identifier_bracket",
            "detail": detail}


# --- combined version detection ---------------------------------------------

# Terminal methods: even though these report version=None, they represent a
# confident, meaningful conclusion ("this repo is older than everything we
# track") rather than a failed lookup -- so they should NOT be overridden by
# falling through to the much noisier content-based bracket heuristic below.
# Doing so previously caused old repos (predating EasyCrypt's r20YY.MM
# tagging scheme) to occasionally get a spurious, artificially-recent version
# estimate from an incidental identifier match, understating their true
# exposure. releases_between() already treats an unresolved version as "use
# full changelog history", which is the correct (maximally-exposed) behavior
# for a repo this old.
TERMINAL_UNRESOLVED_METHODS = {
    "git_commit_date_predates_changelog",
    "submodule_pin_predates_tagging_scheme",
    "version_file_sha_predates_tagging_scheme",
}


def detect_source_version(repo_path: Path, changelog: dict, easycrypt_repo: Path | None,
                           exclude_dirs: set[str] | None = None) -> dict:
    releases_by_date = sorted(changelog["releases"], key=lambda r: r.get("published_at") or "")

    result = detect_submodule_pin(repo_path, easycrypt_repo)
    if result and (result.get("version") or result.get("method") in TERMINAL_UNRESOLVED_METHODS):
        return result

    result = detect_version_file(repo_path, easycrypt_repo)
    if result and (result.get("version") or result.get("method") in TERMINAL_UNRESOLVED_METHODS):
        return result

    result = detect_project_file_version(repo_path)
    if result:
        return result

    result = detect_docker_version(repo_path)
    if result:
        return result

    result = detect_config_version_string(repo_path)
    if result:
        return result

    result = detect_git_commit_date_version(repo_path, releases_by_date)
    if result and (result.get("version") or result.get("method") in TERMINAL_UNRESOLVED_METHODS):
        return result

    tokens = collect_repo_tokens(repo_path, exclude_dirs)
    return detect_content_bracket_version(tokens, releases_by_date)


# --- automation reliance -----------------------------------------------------

def compute_automation_ratio(repo_path: Path, exclude_dirs: set[str] | None = None) -> dict:
    fragile_count = 0
    total_count = 0
    for path in iter_ec_files(repo_path, exclude_dirs):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for m in TACTIC_TOKEN_RE.finditer(text):
            total_count += 1
            if m.group(1) in FRAGILE_TACTICS:
                fragile_count += 1
    ratio = (fragile_count / total_count) if total_count else 0.0
    return {"fragile_tactic_count": fragile_count, "total_tactic_count": total_count, "ratio": ratio}


# --- proof complexity --------------------------------------------------------

PROOF_BLOCK_RE = re.compile(r"\bproof\.(.*?)\b(?:qed|abort|admit)\.", re.DOTALL)
# EasyCrypt also supports a terse inline form that never uses the word
# "proof" at all: `lemma foo : P by trivial.` -- very common for short,
# illustrative, one-tactic-per-example material (exactly what a
# one-tactic-per-lesson tutorial would lean on). The [^.]*? gap (no
# periods allowed) between "lemma" and "by" keeps this anchored to the
# CURRENT lemma's own statement -- a period would end that statement (or
# an earlier one) before "by" is reached, so this shouldn't cross into an
# unrelated lemma or match a "by" clause buried inside another lemma's
# separate proof...qed block.
INLINE_BY_RE = re.compile(r"\blemma\b[^.]*?\bby\b(.*?)\.", re.DOTALL)
# tactic-terminating periods within a proof body: a period followed by
# whitespace or end-of-string. Approximate (doesn't parse strings/comments
# out first) but good enough as a relative complexity signal, not an exact
# tactic count.
TACTIC_TERMINATOR_RE = re.compile(r"\.(?:\s|$)")

# A lemma at or above this depth counts as "long" for reporting purposes.
LONG_LEMMA_DEPTH_THRESHOLD = 15
# Fraction of a repo's lemmas (by depth, descending) averaged into the
# "top" component of depth_complexity -- biases the metric toward repos
# with SEVERAL long lemmas, not just one outlier, while a plain mean would
# get diluted by a long tail of trivial one-liners.
TOP_LEMMA_FRACTION = 0.25
# Blend between the overall mean and the top-fraction mean.
MEAN_WEIGHT = 0.4
TOP_MEAN_WEIGHT = 0.6


def extract_lemma_depths(repo_path: Path, exclude_dirs: set[str] | None = None) -> list[int]:
    """Depth per lemma = count of tactic-terminating periods inside its
    proof body -- whether that's an explicit proof...qed/abort/admit
    block, or the terser inline 'lemma ... by tactic.' shorthand that
    never uses the word "proof" at all. A rough proxy for 'how many
    tactics are required to close the goal', not an exact parse.

    NOTE: this is still a heuristic, not a real EasyCrypt parser -- edge
    cases (nested comments containing the word 'proof' or 'by', lemmas
    declared but never proved at all, unusual formatting) can still be
    missed or miscounted. Treat depth/lemma-count as directional signal,
    not ground truth."""
    depths = []
    for path in iter_ec_files(repo_path, exclude_dirs):
        text = _read_text(path)
        if text is None:
            continue

        proof_block_spans = []
        for m in PROOF_BLOCK_RE.finditer(text):
            proof_block_spans.append(m.span())
            depth = len(TACTIC_TERMINATOR_RE.findall(m.group(1)))
            if depth > 0:
                depths.append(depth)

        for m in INLINE_BY_RE.finditer(text):
            # skip if this "by" falls inside a proof...qed/abort/admit
            # block already counted above (a "by" used as a sub-tactic
            # mid-proof, e.g. "rewrite foo by smt()."), to avoid double
            # counting the same lemma or miscounting an internal step.
            if any(start <= m.start() < end for start, end in proof_block_spans):
                continue
            depth = len(TACTIC_TERMINATOR_RE.findall(m.group(1))) + 1  # +1 for the "by" clause itself
            depths.append(depth)
    return depths


def compute_proof_complexity(repo_path: Path, automation_ratio: float, automation_multiplier: float,
                              exclude_dirs: set[str] | None = None) -> dict:
    """Combine (a) a lemma-depth complexity measure biased toward repos with
    several long/deep lemmas -- not just their plain average, which a long
    tail of trivial one-liners would dilute -- with (b) automation
    reliance, which amplifies it: heavy smt/auto usage means more of the
    'closing' work is opaque to us and to the repair LLM, so it's treated
    as increasing effective complexity on top of raw depth, the same way
    it amplifies breaking-change exposure in score_repo."""
    depths = extract_lemma_depths(repo_path, exclude_dirs)
    if not depths:
        return {"lemma_count": 0, "mean_depth": 0.0, "top_fraction_mean_depth": 0.0,
                "long_lemma_count": 0, "depth_complexity": 0.0, "complexity_score": 0.0}

    depths_sorted = sorted(depths, reverse=True)
    k = min(math.ceil(len(depths_sorted) * TOP_LEMMA_FRACTION), len(depths_sorted))
    k = max(k, 1)
    top_mean = sum(depths_sorted[:k]) / k
    mean_all = sum(depths) / len(depths)
    long_count = sum(1 for d in depths if d >= LONG_LEMMA_DEPTH_THRESHOLD)

    depth_complexity = MEAN_WEIGHT * mean_all + TOP_MEAN_WEIGHT * top_mean
    complexity_score = depth_complexity * (1 + automation_ratio * automation_multiplier)

    return {
        "lemma_count": len(depths),
        "mean_depth": round(mean_all, 2),
        "top_fraction_mean_depth": round(top_mean, 2),
        "long_lemma_count": long_count,
        "depth_complexity": round(depth_complexity, 2),
        "complexity_score": round(complexity_score, 2),
    }


# --- breaking-change exposure score ------------------------------------------

def releases_between(releases_by_date: list[dict], source_version: str | None, target_version: str,
                      assume_full_history: bool = False) -> list[dict]:
    """assume_full_history should be True only when version detection
    returned a TERMINAL_UNRESOLVED_METHODS result -- i.e. we have actual
    evidence (a resolved commit date) that the repo predates our
    changelog's earliest cataloged release, so 'crossed everything we
    know about' is a justified conclusion. For a merely unresolved version
    (no pin found, no signal either way -- could be any age), assuming
    full history is an unjustified worst-case guess, not a finding: return
    no crossed releases instead, and let score_repo apply a smaller,
    clearly-flagged 'unknown version' penalty rather than silently
    inflating the score as if we'd confirmed something we haven't."""
    versions = [r["version"] for r in releases_by_date]
    if target_version not in versions:
        print(f"warning: target version {target_version} not found in changelog", file=sys.stderr)
        return []
    i_tgt = versions.index(target_version)
    if source_version is None or source_version not in versions:
        if assume_full_history:
            print(f"source version predates changelog history; using full history up to target "
                  f"as the exposure range", file=sys.stderr)
            return releases_by_date[: i_tgt + 1]
        print(f"warning: source version unresolved (no evidence either way) -- treating as 0 "
              f"confirmed crossed releases rather than assuming worst case; see version_unknown "
              f"in the output", file=sys.stderr)
        return []
    i_src = versions.index(source_version)
    lo, hi = sorted((i_src, i_tgt))
    return releases_by_date[lo + 1: hi + 1]


def compute_breaking_change_score(releases_in_range: list[dict]) -> dict:
    by_kind = {}
    total = 0.0
    for rel in releases_in_range:
        for entry in rel.get("entries", []):
            kind = entry.get("kind") or "unknown"
            weight = KIND_WEIGHTS.get(kind, 0.0)
            mult = RELEVANCE_MULTIPLIER.get(entry.get("relevance"), 0.3)
            score = weight * mult
            if score:
                by_kind.setdefault(kind, {"count": 0, "score": 0.0})
                by_kind[kind]["count"] += 1
                by_kind[kind]["score"] += score
                total += score
    return {"total": round(total, 2), "by_kind": by_kind}


# --- cloning -----------------------------------------------------------------

def sanitize_name(name: str) -> str:
    """Turn a CSV Name field (or a URL-derived name) into a filesystem-safe
    directory name: whitespace runs -> '-', collapse repeated '-', strip
    leading/trailing '-'."""
    name = name.strip()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    return name.strip("-")


def derive_name_from_url(url: str) -> str:
    """e.g. https://github.com/org/some-repo.git -> 'some-repo'."""
    last = url.rstrip("/").split("/")[-1]
    if last.endswith(".git"):
        last = last[: -len(".git")]
    return sanitize_name(last)


def clone_repo(url: str, target_dir: Path, force: bool = False) -> dict:
    """Clone the repo's main/master branch into target_dir. Tries 'main'
    first, then 'master', then falls back to whatever the remote's default
    branch is. Returns a dict describing what happened rather than raising,
    so a single bad URL in a CSV batch doesn't abort the whole run."""
    if target_dir.exists():
        if not force:
            return {"success": True, "skipped": True, "branch": None,
                     "message": f"{target_dir} already exists; skipping clone (use --force to re-clone)"}
        subprocess.run(["rm", "-rf", str(target_dir)], check=False)

    target_dir.parent.mkdir(parents=True, exist_ok=True)

    for branch in ("main", "master"):
        result = subprocess.run(
            ["git", "clone", "--branch", branch, "--single-branch", "--depth", "1", url, str(target_dir)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            return {"success": True, "skipped": False, "branch": branch, "message": "cloned"}

    # neither main nor master worked explicitly -- fall back to whatever the
    # remote's default branch is, rather than failing outright.
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(target_dir)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode == 0:
        default_branch = run_git(target_dir, ["rev-parse", "--abbrev-ref", "HEAD"]) or "unknown"
        return {"success": True, "skipped": False, "branch": default_branch,
                "message": "cloned default branch (neither 'main' nor 'master' matched explicitly)"}

    return {"success": False, "skipped": False, "branch": None,
            "message": f"clone failed: {result.stderr.strip()[-500:]}"}


def read_csv_pairs(csv_path: Path) -> list[tuple[str, str]]:
    import csv
    pairs = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # tolerate header casing/whitespace variance
        fieldmap = {k.strip().lower(): k for k in (reader.fieldnames or [])}
        name_key = fieldmap.get("name")
        url_key = fieldmap.get("url")
        if not name_key or not url_key:
            raise ValueError(f"CSV must have Name and URL columns; got header {reader.fieldnames}")
        for row in reader:
            name = (row.get(name_key) or "").strip()
            url = (row.get(url_key) or "").strip()
            if name and url:
                pairs.append((name, url))
    return pairs


# --- per-repo scoring (used for both single-repo and batch/CSV modes) -------

def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def score_repo(repo_path: Path, changelog: dict, releases_by_date: list[dict], target_version: str,
               easycrypt_repo: Path | None, automation_multiplier: float,
               predates_changelog_penalty: float, source_version_override: str | None,
               exclude_dirs: set[str] | None = None,
               version_unknown_penalty: float = VERSION_UNKNOWN_PENALTY_DEFAULT) -> dict:
    if source_version_override:
        version_info = {"version": source_version_override, "confidence": "high",
                         "method": "user_override", "detail": ""}
    else:
        version_info = detect_source_version(repo_path, changelog, easycrypt_repo, exclude_dirs)

    # Two distinct "we don't have a version" cases, handled differently:
    # predates_changelog = confident evidence (a resolved commit date) that
    # the repo is older than our earliest cataloged release -- "assume it
    # crossed everything we know about" is a justified conclusion here.
    # version_unknown = no pin, no signal either way -- could be any age,
    # including recently maintained. Assuming full history here would be an
    # unjustified worst-case guess, not a finding, so releases_between
    # instead returns zero confirmed crossed releases for this case, and a
    # smaller, distinctly-flagged penalty is applied below.
    predates_changelog = version_info.get("method") in TERMINAL_UNRESOLVED_METHODS
    version_unknown = version_info.get("version") is None and not predates_changelog

    in_range = releases_between(releases_by_date, version_info.get("version"), target_version,
                                 assume_full_history=predates_changelog)
    breaking_change = compute_breaking_change_score(in_range)

    automation = compute_automation_ratio(repo_path, exclude_dirs)
    # Automation reliance AMPLIFIES existing breaking-change exposure rather
    # than being an independent additive term: a repo with near-zero real
    # exposure (e.g. one kept continuously in sync with EasyCrypt) shouldn't
    # be penalized for a normal level of smt/auto usage, since there's
    # little drift for it to actually be exposed to. A repo with substantial
    # crossed exposure AND heavy automation reliance gets compounded risk,
    # which is the shape we actually want.
    automation_factor = 1.0 + automation["ratio"] * automation_multiplier
    breaking_and_automation = round(breaking_change["total"] * automation_factor, 2)

    complexity = compute_proof_complexity(repo_path, automation["ratio"], automation_multiplier, exclude_dirs)

    # Both "predates changelog" and "version unknown" penalties scale with
    # measured proof complexity -- a repo with only trivial one-line lemmas
    # gets a small bump, while one with several deep, automation-heavy
    # lemmas gets a much larger one. This lets the numeric
    # total_exposure_score do the ranking directly, rather than relying on
    # a hard override that can't distinguish a genuinely hard repo (of
    # unknown or old version) from a trivially simple one.
    predates_changelog_days = None
    if predates_changelog and version_info.get("commit_date") and releases_by_date:
        try:
            commit_dt = parse_iso(version_info["commit_date"])
            earliest_dt = parse_iso(releases_by_date[0]["published_at"])
            predates_changelog_days = (earliest_dt - commit_dt).days
        except (ValueError, KeyError):
            pass

    def scaled_penalty(base: float) -> float:
        scaling = complexity["complexity_score"] / PREDATES_CHANGELOG_COMPLEXITY_REFERENCE
        scaling = min(max(scaling, PREDATES_CHANGELOG_MIN_SCALE), PREDATES_CHANGELOG_MAX_SCALE)
        return round(base * scaling, 2)

    penalty_applied = 0.0
    if predates_changelog:
        penalty_applied = scaled_penalty(predates_changelog_penalty)
    elif version_unknown:
        penalty_applied = scaled_penalty(version_unknown_penalty)

    total_exposure = round(breaking_and_automation + penalty_applied, 2)

    return {
        "repo_path": str(repo_path),
        "target_version": target_version,
        "detected_source_version": version_info,
        "releases_crossed": [r["version"] for r in in_range],
        "breaking_change_score": breaking_change,
        "automation_reliance": {**automation, "multiplier": automation_multiplier,
                                 "amplified_total": breaking_and_automation},
        "proof_complexity": complexity,
        "smt_solver_info": detect_smt_solver_info(repo_path),
        "predates_changelog": predates_changelog,
        "predates_changelog_days": predates_changelog_days,
        "version_unknown": version_unknown,
        "unresolved_version_penalty_applied": penalty_applied,
        "total_exposure_score": total_exposure,
    }


def print_ladder(results: list[dict]) -> None:
    """Human-readable ranked summary to stderr, easiest (lowest exposure)
    first. predates_changelog is shown as an informational flag -- it no
    longer forces a repo to the bottom regardless of score, since the
    penalty contributing to total_exposure_score is itself scaled by
    measured proof complexity (see compute_proof_complexity / score_repo).
    A predates_changelog repo with only shallow lemmas can legitimately
    rank easier than a modern repo with deep, automation-heavy proofs."""
    scorable = [r for r in results if "total_exposure_score" in r]
    ranked = sorted(scorable, key=lambda r: r["total_exposure_score"])
    print("\n--- Proof repair ladder (easiest -> hardest) ---", file=sys.stderr)
    for rank, r in enumerate(ranked, 1):
        name = Path(r["repo_path"]).name
        src = r["detected_source_version"].get("version") or "?"
        conf = r["detected_source_version"].get("confidence", "?")
        flag = " [predates changelog]" if r.get("predates_changelog") else \
               (" [version unknown]" if r.get("version_unknown") else "")
        print(f"{rank:>3}. {name:<30} score={r['total_exposure_score']:>6}  "
              f"src={src} ({conf}){flag}", file=sys.stderr)


# --- main --------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source_group = ap.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--repo-url", default=None, help="URL of a single repository to clone and score")
    source_group.add_argument("--csv", default=None,
                               help="CSV file with Name,URL columns; every repo is cloned and scored")
    source_group.add_argument("--repo-path", default=None,
                               help="path to an already-downloaded local repository to score directly -- "
                                    "no cloning, no --eval-dir involved")
    ap.add_argument("--name", default=None,
                     help="directory name to use for --repo-url (default: derived from the URL); ignored with --csv/--repo-path")
    ap.add_argument("--eval-dir", default="eval", help="base folder repos are cloned into (default: ./eval)")
    ap.add_argument("--force", action="store_true", help="re-clone even if the target directory already exists")
    ap.add_argument("--changelog", required=True, help="path to changelog.yaml from process_changelog.py")
    ap.add_argument("--target-version", required=True)
    ap.add_argument("--source-version", default=None,
                     help="override version detection with a known value (applied to every repo if using --csv)")
    ap.add_argument("--easycrypt-repo", default=None,
                     help="path to a local EasyCrypt clone (with tags fetched) -- "
                          "used to resolve a pinned submodule commit SHA to a release tag")
    ap.add_argument("--automation-multiplier", type=float, default=AUTOMATION_MULTIPLIER_DEFAULT,
                     help=f"multiplier applied to breaking-change exposure based on automation-reliance ratio "
                          f"(default {AUTOMATION_MULTIPLIER_DEFAULT} -- total_exposure = breaking_change_score * "
                          f"(1 + ratio * multiplier); amplifies real exposure rather than adding an independent "
                          f"penalty, so a repo with near-zero crossed breaking changes isn't penalized for a "
                          f"normal level of smt/auto usage)")
    ap.add_argument("--predates-changelog-penalty", type=float, default=PREDATES_CHANGELOG_PENALTY_DEFAULT,
                     help=f"penalty (at reference proof complexity) applied when a repo's version is CONFIDENTLY "
                          f"known to predate our changelog's earliest cataloged release, i.e. real evidence such "
                          f"as a resolved commit date (default {PREDATES_CHANGELOG_PENALTY_DEFAULT}) -- scaled by "
                          f"measured proof complexity, see predates_changelog / proof_complexity in the output")
    ap.add_argument("--version-unknown-penalty", type=float, default=VERSION_UNKNOWN_PENALTY_DEFAULT,
                     help=f"smaller penalty (default {VERSION_UNKNOWN_PENALTY_DEFAULT}) applied when a repo's "
                          f"version could NOT be determined at all -- no pin, no signal either way, so it could "
                          f"be any age. Distinct from --predates-changelog-penalty, which requires actual "
                          f"evidence; see version_unknown in the output")
    ap.add_argument("--exclude-dirs", default=None,
                     help="comma-separated directory names to skip when scanning for .ec/.eca files (in addition "
                          f"to the defaults: {', '.join(sorted(DEFAULT_EXCLUDE_DIRS))}) -- useful for repos with "
                          f"Jasmin extraction output or other generated .ec files under a specific folder name")
    ap.add_argument("--out", default=None, help="write JSON results here (single object for --repo-url/--repo-path, "
                                                 "JSON array for --csv); default: stdout")
    args = ap.parse_args()

    eval_dir = Path(args.eval_dir).resolve()
    easycrypt_repo = Path(args.easycrypt_repo).resolve() if args.easycrypt_repo else None
    exclude_dirs = set(DEFAULT_EXCLUDE_DIRS)
    if args.exclude_dirs:
        exclude_dirs |= {d.strip() for d in args.exclude_dirs.split(",") if d.strip()}

    with open(args.changelog, encoding="utf-8") as f:
        changelog = yaml.safe_load(f)
    releases_by_date = sorted(changelog["releases"], key=lambda r: r.get("published_at") or "")

    if args.repo_path:
        # Local repo already on disk: score it directly, no cloning/eval-dir involved.
        repo_path = Path(args.repo_path).resolve()
        if not repo_path.exists():
            print(f"error: --repo-path {repo_path} does not exist", file=sys.stderr)
            sys.exit(1)
        result = score_repo(repo_path, changelog, releases_by_date, args.target_version,
                             easycrypt_repo, args.automation_multiplier,
                             args.predates_changelog_penalty, args.source_version, exclude_dirs,
                             args.version_unknown_penalty)
        result = {"name": args.name or repo_path.name, "url": None, **result}
        results = [result]
    else:
        eval_dir.mkdir(parents=True, exist_ok=True)
        if args.repo_url:
            pairs = [(args.name or derive_name_from_url(args.repo_url), args.repo_url)]
        else:
            pairs = read_csv_pairs(Path(args.csv))
            if not pairs:
                print("no valid Name,URL rows found in CSV", file=sys.stderr)
                sys.exit(1)

        results = []
        for name, url in pairs:
            target_dir = eval_dir / sanitize_name(name)
            print(f"[{name}] cloning {url} -> {target_dir}", file=sys.stderr)
            clone_info = clone_repo(url, target_dir, force=args.force)

            if not clone_info["success"]:
                print(f"  ! {clone_info['message']}", file=sys.stderr)
                results.append({"name": name, "url": url, "repo_path": str(target_dir),
                                 "error": clone_info["message"]})
                continue
            if clone_info.get("skipped"):
                print(f"  {clone_info['message']}", file=sys.stderr)
            else:
                print(f"  cloned branch '{clone_info['branch']}'", file=sys.stderr)

            result = score_repo(target_dir, changelog, releases_by_date, args.target_version,
                                 easycrypt_repo, args.automation_multiplier,
                                 args.predates_changelog_penalty, args.source_version, exclude_dirs,
                                 args.version_unknown_penalty)
            result = {"name": name, "url": url, **result}
            results.append(result)

        if len(pairs) > 1:
            print_ladder(results)

    payload = results[0] if len(results) == 1 else results
    out = json.dumps(payload, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()