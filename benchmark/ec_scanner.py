"""Comment-aware scanner for lemma and axiom declarations in .ec files."""

from __future__ import annotations

import re
from dataclasses import dataclass

DECL_START_RE = re.compile(
    r"^\s*(?:(?:local|global)\s+)*(lemma|axiom)\s+(\w+)"
)
PROOF_LINE_RE = re.compile(r"^\s*proof\.")


@dataclass(frozen=True)
class ProofDeclaration:
    line: int
    kind: str
    name: str
    signature: str


def strip_comments(text: str) -> str:
    """Remove EasyCrypt comments, including nested (* ... *)."""
    out: list[str] = []
    i = 0
    n = len(text)
    depth = 0
    while i < n:
        if i + 1 < n and text[i : i + 2] == "(*":
            depth += 1
            i += 2
            continue
        if i + 1 < n and text[i : i + 2] == "*)":
            if depth > 0:
                depth -= 1
            i += 2
            continue
        if depth == 0:
            out.append(text[i])
        i += 1
    return "".join(out)


def _normalize_signature(parts: list[str]) -> str:
    text = " ".join(part.strip() for part in parts if part.strip())
    return re.sub(r"\s+", " ", text).strip()


def _signature_complete(accumulated: str) -> bool:
    if ":" not in accumulated:
        return False
    stripped = accumulated.rstrip()
    return stripped.endswith(".")


def scan_proofs(source: str) -> list[ProofDeclaration]:
    """Return lemma/axiom declarations with 1-based start lines."""
    declarations: list[ProofDeclaration] = []
    lines = source.splitlines()
    idx = 0

    while idx < len(lines):
        stripped = strip_comments(lines[idx]).strip()
        match = DECL_START_RE.match(stripped)
        if not match:
            idx += 1
            continue

        kind = match.group(1)
        name = match.group(2)
        start_line = idx + 1
        parts = [lines[idx]]
        idx += 1

        while idx < len(lines):
            line_stripped = strip_comments(lines[idx]).strip()
            if PROOF_LINE_RE.match(line_stripped):
                break
            parts.append(lines[idx])
            accumulated = _normalize_signature(parts)
            idx += 1
            if _signature_complete(accumulated):
                break

        signature = _normalize_signature(parts)
        if signature:
            declarations.append(
                ProofDeclaration(
                    line=start_line,
                    kind=kind,
                    name=name,
                    signature=signature,
                )
            )

    return declarations
