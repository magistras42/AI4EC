"""Compatibility migration module.

Given a broken EasyCrypt proof and its source version (the EasyCrypt release
it was last known to compile against), emit the relevant subset of the
structured changelog that pertains to the tactics, theories, and mechanisms
actually used in the proof. The output is a compact markdown block suitable
for injection into the solver prompt.

Usage::

    from integration.agent.migration import build_migration_hints

    hints = build_migration_hints(
        broken_proof_text=broken_script,
        source_version="pre-r2022.04",
        target_version="r2026.07",        # default: latest in changelog
        max_entries=30,                    # budget for prompt tokens
    )
    # hints is a string (or empty if nothing relevant)

The module:
1. Loads the structured changelog (proof_corpus/output/changelog.yaml).
2. Determines which releases the proof must cross (source → target).
3. Extracts tactic/theory identifiers from the broken proof text.
4. Filters changelog entries to those that are (a) in the crossing window
   and (b) mention identifiers that appear in the proof or are high-relevance
   mechanism changes.
5. Renders a compact markdown migration guide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CHANGELOG_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "proof_corpus"
    / "output"
    / "changelog.yaml"
)

# ---------------------------------------------------------------------------
# Version ordering
# ---------------------------------------------------------------------------

# Canonical release order. "pre-r2022.04" is a synthetic sentinel for repos
# that predate the first formal release.
_RELEASE_ORDER: list[str] = [
    "pre-r2022.04",
    "r2022.04",
    "r2023.09",
    "r2024.01",
    "r2024.09",
    "r2025.02",
    "r2025.03",
    "r2025.08",
    "r2025.10",
    "r2025.11",
    "r2026.02",
    "r2026.03",
    "r2026.05",
    "r2026.06",
    "r2026.07",
]

_VERSION_INDEX: dict[str, int] = {v: i for i, v in enumerate(_RELEASE_ORDER)}


def version_index(version: str) -> int:
    """Return the ordinal position of a release tag.

    Accepts fuzzy inputs: "pre-r2022.04", "r2025.02", or just "2025.02".
    """
    v = version.strip().lower()
    if not v.startswith("r") and not v.startswith("pre"):
        v = "r" + v
    if v in _VERSION_INDEX:
        return _VERSION_INDEX[v]
    # Fallback: try prefix match
    for tag, idx in _VERSION_INDEX.items():
        if tag.endswith(v) or v.endswith(tag):
            return idx
    raise ValueError(
        f"Unknown EasyCrypt version {version!r}. "
        f"Known: {', '.join(_RELEASE_ORDER)}"
    )


def releases_crossed(source: str, target: str) -> list[str]:
    """Return the list of releases the proof must cross (exclusive of source,
    inclusive of target)."""
    src_idx = version_index(source)
    tgt_idx = version_index(target)
    if tgt_idx <= src_idx:
        return []
    return _RELEASE_ORDER[src_idx + 1 : tgt_idx + 1]


# ---------------------------------------------------------------------------
# Changelog loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangelogEntry:
    """One classified PR/change from the structured changelog."""

    id: str
    version: str
    title: str
    kind: str
    identifiers: tuple[str, ...]
    summary: str | None
    repair_hint: str | None
    relevance: str  # "high" | "medium" | "low"


@lru_cache(maxsize=1)
def load_changelog(path: Path | None = None) -> list[ChangelogEntry]:
    """Load and flatten the changelog YAML into a list of entries."""
    changelog_path = path or _CHANGELOG_PATH
    if not changelog_path.exists():
        return []
    with open(changelog_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    entries: list[ChangelogEntry] = []
    for release in data.get("releases", []):
        version = release.get("version", "")
        for raw in release.get("entries", []):
            entries.append(
                ChangelogEntry(
                    id=str(raw.get("id", "")),
                    version=version,
                    title=raw.get("title", ""),
                    kind=raw.get("kind", "unknown"),
                    identifiers=tuple(raw.get("identifiers") or []),
                    summary=raw.get("summary"),
                    repair_hint=raw.get("repair_hint"),
                    relevance=raw.get("relevance", "low"),
                )
            )
    return entries


# ---------------------------------------------------------------------------
# Tactic/identifier extraction from broken proof text
# ---------------------------------------------------------------------------

# EasyCrypt tactic heads and theory/module identifiers we look for.
_TACTIC_RE = re.compile(
    r"\b("
    r"proc|wp|sp|skip|call|inline|rnd|seq|if|while|unroll|rcondt|rcondf|"
    r"swap|sim|auto|smt|simplify|progress|trivial|split|left|right|"
    r"have|rewrite|apply|move|case|elim|exists|congr|subst|ring|field|"
    r"algebra|exfalso|byequiv|byphoare|bypr|fel|transitivity|"
    r"conseq|eager|outline|cfold|hoare|phoare|ehoare|ecall|"
    r"kill|alias|fission|fusion|exlim|clear|assumption|reflexivity|"
    r"byupto|async|splitwhile|proc\s*change|proc\s*rewrite"
    r")\b",
    re.IGNORECASE,
)

# Theory/module/operator names (capitalized identifiers, qualified paths).
_THEORY_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_']*)*)\b"
)

# Lemma-style identifiers (lowercase with underscores, optionally qualified).
_LEMMA_RE = re.compile(
    r"\b([a-z][a-z0-9_]*(?:'[a-z0-9_]*)?(?:\.[a-z][a-z0-9_']*)*)\b"
)

# Common EasyCrypt imports that signal which libraries are in play.
_IMPORT_RE = re.compile(
    r"\brequire\s+(?:import\s+)?(.+?)(?:\.|$)", re.MULTILINE
)


@dataclass
class ProofFingerprint:
    """Identifiers extracted from a broken proof for changelog matching."""

    tactics: set[str] = field(default_factory=set)
    theories: set[str] = field(default_factory=set)
    lemma_names: set[str] = field(default_factory=set)
    imports: set[str] = field(default_factory=set)
    all_identifiers: set[str] = field(default_factory=set)


def extract_proof_fingerprint(proof_text: str) -> ProofFingerprint:
    """Extract tactic names, theories, and identifiers from a proof script."""
    fp = ProofFingerprint()

    # Tactics
    for m in _TACTIC_RE.finditer(proof_text):
        fp.tactics.add(m.group(1).lower().strip())

    # Theory/module references (capitalized)
    for m in _THEORY_RE.finditer(proof_text):
        name = m.group(1)
        fp.theories.add(name)
        # Also add the root (e.g. "RealOrder" from "RealOrder.ler_trans")
        root = name.split(".")[0]
        fp.theories.add(root)

    # Lemma-style identifiers
    for m in _LEMMA_RE.finditer(proof_text):
        fp.lemma_names.add(m.group(1))

    # Imports
    for m in _IMPORT_RE.finditer(proof_text):
        for token in re.split(r"\s+", m.group(1).strip()):
            if token and token[0].isupper():
                fp.imports.add(token)

    fp.all_identifiers = fp.tactics | fp.theories | fp.lemma_names | fp.imports
    return fp


# ---------------------------------------------------------------------------
# Filtering logic
# ---------------------------------------------------------------------------


def _entry_matches_fingerprint(
    entry: ChangelogEntry, fp: ProofFingerprint
) -> bool:
    """Does this changelog entry likely affect the given proof?"""
    # High-relevance mechanism/syntax/tactic changes always match — they are
    # broadly impactful and hard to rule out without full analysis.
    if entry.relevance == "high" and entry.kind in (
        "mechanism_change",
        "syntax_change",
        "tactic_change",
    ):
        return True

    # Check if any changelog identifier appears in the proof fingerprint.
    entry_ids = set(entry.identifiers)
    if not entry_ids:
        return False

    # Lowercase comparison for tactic/lemma matching
    entry_ids_lower = {eid.lower() for eid in entry_ids}
    fp_lower = {ident.lower() for ident in fp.all_identifiers}

    # Direct overlap
    if entry_ids_lower & fp_lower:
        return True

    # Partial match: check if any entry identifier is a substring of an
    # identifier in the proof (catches e.g. "SmtMap" matching "SmtMap.get")
    for eid in entry_ids:
        eid_l = eid.lower()
        if len(eid_l) < 3:
            continue
        for fp_id in fp.all_identifiers:
            if eid_l in fp_id.lower():
                return True

    return False


def filter_relevant_entries(
    entries: list[ChangelogEntry],
    source_version: str,
    target_version: str,
    fingerprint: ProofFingerprint,
    *,
    include_medium: bool = True,
) -> list[ChangelogEntry]:
    """Filter changelog entries to those relevant to this proof migration."""
    crossing = set(releases_crossed(source_version, target_version))
    if not crossing:
        return []

    result: list[ChangelogEntry] = []
    for entry in entries:
        # Must be in the version window
        if entry.version not in crossing:
            continue
        # Skip low-relevance and internal/doc entries
        if entry.relevance == "low":
            continue
        if not include_medium and entry.relevance == "medium":
            continue
        if entry.kind in ("internal", "documentation"):
            continue
        # Must have a repair_hint to be useful in the prompt
        if not entry.repair_hint:
            continue
        # Must match the proof's fingerprint
        if not _entry_matches_fingerprint(entry, fingerprint):
            continue
        result.append(entry)

    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_migration_hints(
    entries: list[ChangelogEntry],
    source_version: str,
    target_version: str,
    *,
    max_entries: int = 30,
) -> str:
    """Render filtered changelog entries as a markdown block for the prompt."""
    if not entries:
        return ""

    # Sort: high relevance first, then by version order (earliest first)
    relevance_order = {"high": 0, "medium": 1, "low": 2}
    entries_sorted = sorted(
        entries,
        key=lambda e: (
            relevance_order.get(e.relevance, 2),
            _VERSION_INDEX.get(e.version, 99),
        ),
    )

    # Truncate to budget
    entries_sorted = entries_sorted[:max_entries]

    lines: list[str] = [
        f"This proof was written for EasyCrypt **{source_version}** and must "
        f"compile on **{target_version}**. The following breaking changes from "
        f"intervening releases are likely relevant to this proof:",
        "",
    ]

    current_version = ""
    for entry in entries_sorted:
        if entry.version != current_version:
            current_version = entry.version
            lines.append(f"### {current_version}")
            lines.append("")
        relevance_tag = f"[{entry.relevance.upper()}]" if entry.relevance == "high" else ""
        lines.append(
            f"- {relevance_tag} **{entry.kind}**: {entry.repair_hint}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_migration_hints(
    broken_proof_text: str,
    source_version: str,
    target_version: str = "r2026.07",
    *,
    max_entries: int = 30,
    include_medium: bool = True,
    changelog_path: Path | None = None,
    full_file_text: str | None = None,
) -> str:
    """Build a migration-hint block for the solver prompt.

    Parameters
    ----------
    broken_proof_text:
        The tactic script of the broken proof (what the solver sees as
        "informal_proof" in broken-formal mode).
    source_version:
        The EasyCrypt release the proof was last known to compile against.
        Use "pre-r2022.04" for repos from before April 2022.
    target_version:
        The EasyCrypt release the proof must compile against (default: latest).
    max_entries:
        Maximum number of changelog entries to include in the hint block.
    include_medium:
        Whether to include medium-relevance entries (default: True).
    changelog_path:
        Override path to changelog.yaml (for testing).
    full_file_text:
        If provided, also fingerprint the full file (imports, types, etc.)
        in addition to the broken proof text. This catches import-level
        breakage (e.g. SmtMap → FMap).

    Returns
    -------
    A markdown string ready for injection into the prompt, or "" if no
    relevant entries were found.
    """
    changelog = load_changelog(changelog_path)
    if not changelog:
        return ""

    # Build fingerprint from both the tactic script and (optionally) the
    # full file for import-level signals.
    text_to_fingerprint = broken_proof_text
    if full_file_text:
        text_to_fingerprint = full_file_text + "\n" + broken_proof_text

    fingerprint = extract_proof_fingerprint(text_to_fingerprint)

    relevant = filter_relevant_entries(
        changelog,
        source_version,
        target_version,
        fingerprint,
        include_medium=include_medium,
    )

    return render_migration_hints(
        relevant,
        source_version,
        target_version,
        max_entries=max_entries,
    )
