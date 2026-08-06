#!/usr/bin/env python3
"""Text-parsing primitives shared by the whole pipeline (stdlib only).

Two halves: SMT-LIB dump surgery (naming asserts, unsat-core parsing, goal
and env-lemma extraction) and EasyCrypt source surgery (comment masking,
smt-call finding/rewriting). Imported by every stage; also a CLI driven by
run_unsat_core_poc.sh.

Subcommands:
  name IN OUT               Wrap every (assert ...) in an EC_SMT_DEBUG SMT-LIB2 dump
                            with (! ... :named <why3-comment>__k) and insert
                            (get-unsat-core) right after (check-sat). Every assert
                            gets a name (unnamed essentials are silently dropped
                            from cores by both Z3 and CVC5).
  core FILE                 Parse solver stdout: expect a standalone `unsat` line,
                            then the first non-(error ...) paren group is the core.
                            Prints deduplicated base names (``__k`` stripped), one
                            per line. Exits 1 if the solver did not answer unsat.
  demangle COREFILE --candidates "lem1 lem2 ..."
                            Match mangled core names against candidate EC lemma
                            names (the call's own hints, or `smt selected` output).
                            Prints two lines: `lemmas: ...` and `other: ...`.
  last-call FILE.ec         Print 3 lines about the LAST smt call outside comments:
                            kind (PAREN|EMPTY|BARE), hints (space-separated), text.
  pin FILE.ec --hints "lem1 lem2" -o OUT
                            Rewrite the last smt call to smt(lem1 lem2).
  select FILE.ec -o OUT     Rewrite a BARE last smt call to `smt selected` so
                            EasyCrypt prints `selected lemmas: <EC names>`.
  selected LOGFILE          Parse the (line-wrapped) `selected lemmas:` warning
                            out of an easycrypt log; prints the names on one line.
"""

import argparse
import re
import sys


# ---------------------------------------------------------------- SMT-LIB side

def _scan_form(text, i):
    """text[i] == '('; return end index (exclusive) of the balanced form.

    Skips ;-comments, "..." strings and |...| quoted symbols while counting.
    """
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == ';':
            nl = text.find('\n', i)
            i = n if nl < 0 else nl
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 1
        elif c == '|':
            i += 1
            while i < n and text[i] != '|':
                i += 1
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise SystemExit('unbalanced parens in SMT input')


_COMMENT_NAME_RE = re.compile(r'"([^"]*)"')
# SMT-LIB simple symbol (no quoting needed)
_SIMPLE_SYM_RE = re.compile(r'[A-Za-z~!@$%^&*_\-+=<>.?/][A-Za-z0-9~!@$%^&*_\-+=<>.?/]*\Z')
_HEAD_TOKEN_RE = re.compile(r'[^\s()]+')


def name_asserts(text):
    """Return the dump with every assert named and (get-unsat-core) inserted."""
    out = []
    i, n = 0, len(text)
    last_comment = None
    counts = {}
    while i < n:
        c = text[i]
        if c == ';':
            nl = text.find('\n', i)
            nl = n if nl < 0 else nl
            m = _COMMENT_NAME_RE.search(text, i, nl)
            if m:
                last_comment = m.group(1)
            out.append(text[i:nl])
            i = nl
        elif c == '(':
            j = _scan_form(text, i)
            form = text[i:j]
            head = form[1:].lstrip()
            tok_m = _HEAD_TOKEN_RE.match(head)
            tok = tok_m.group(0).rstrip(')') if tok_m else ''
            if tok == 'assert' and ':named' not in form:
                base = (last_comment or 'anon').replace('|', '_').replace('\\', '_')
                counts[base] = counts.get(base, 0) + 1
                nm = '%s__%d' % (base, counts[base])
                sym = nm if _SIMPLE_SYM_RE.match(nm) else '|%s|' % nm
                body = form[form.index('assert') + len('assert'):-1].strip()
                out.append('(assert (! %s :named %s))' % (body, sym))
            else:
                out.append(form)
                if tok == 'check-sat':
                    out.append('\n(get-unsat-core)')
            last_comment = None
            i = j
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def parse_core(text):
    """Extract core base names from solver stdout; SystemExit(1) if not unsat."""
    if not re.search(r'^unsat\s*$', text, re.M):
        status = 'no output'
        for line in text.splitlines():
            s = line.strip()
            if s in ('sat', 'unknown') or 'error' in s.lower():
                status = s
                break
        raise SystemExit('solver did not answer unsat (%s)' % status)
    m = re.search(r'^unsat\s*$', text, re.M)
    rest = text[m.end():]
    k = 0
    while True:
        p = rest.find('(', k)
        if p < 0:
            raise SystemExit('no core group found after unsat '
                             '(was core production enabled?)')
        e = _scan_form(rest, p)
        grp = rest[p + 1:e - 1]
        if grp.strip().startswith('error'):
            k = e
            continue
        break
    toks = re.findall(r'\|[^|]*\||[^\s()]+', grp)
    bases = []
    for t in toks:
        base = re.sub(r'__\d+$', '', t.strip('|'))
        if base not in bases:
            bases.append(base)
    return bases


def demangle(bases, candidates):
    """Split core base names into (matched EC lemmas, unmatched/other).

    Why3 mangling of a qualified EC path: dots -> underscores, 'Top_' prefix
    (e.g. Top.List.size_cat -> Top_List_size_cat). We match candidates by
    mangled suffix, so unqualified names like `size_cat` work too.
    """
    lemmas, other = [], []
    for b in bases:
        if b in ('goal', 'anon'):
            other.append(b)
            continue
        hit = None
        for cand in candidates:
            mang = cand.replace('.', '_')
            if b == 'Top_' + mang or b == mang or b.endswith('_' + mang):
                hit = cand
                break
        if hit:
            if hit not in lemmas:
                lemmas.append(hit)
        else:
            other.append(b)
    return lemmas, other


def extract_goal(dump_text, max_len=4000):
    """Return the assert body following the ';; Goal' comment, truncated.

    This is the embedding query text: the raw Why3-translated obligation,
    whitespace-normalized, Top_ prefixes intact.
    """
    m = re.search(r'^;;\s*Goal\b.*$', dump_text, re.M)
    if not m:
        return ""
    i = dump_text.find("(", m.end())
    if i < 0:
        return ""
    try:
        j = _scan_form(dump_text, i)
    except SystemExit:
        return ""
    return " ".join(dump_text[i:j].split())[:max_len]


def collect_env_asserts(text):
    """(name -> [assert bodies]) for Top_* env axioms in a dump.

    Names come from the ;; "..." comment Why3 writes before each assert,
    demangled the same way the candidate lists are built. These bodies are
    the embedding documents (one lemma may contribute several asserts).
    """
    out = {}
    i, n = 0, len(text)
    last_comment = None
    while i < n:
        c = text[i]
        if c == ';':
            nl = text.find('\n', i)
            nl = n if nl < 0 else nl
            m = _COMMENT_NAME_RE.search(text, i, nl)
            if m:
                last_comment = m.group(1)
            i = nl
        elif c == '(':
            j = _scan_form(text, i)
            form = text[i:j]
            head = form[1:].lstrip()
            tok_m = _HEAD_TOKEN_RE.match(head)
            tok = tok_m.group(0).rstrip(')') if tok_m else ''
            if tok == 'assert':
                base = ((last_comment or 'anon')
                        .replace('|', '_').replace('\\', '_'))
                if base.startswith('Top_'):
                    out.setdefault(base, []).append(' '.join(form.split()))
            i = j
        else:
            i += 1
    return out


# --------------------------------------------------------------- EasyCrypt side

def mask_ec_comments(text):
    """Blank out nested (* ... *) comments and "..." strings, preserving offsets."""
    out = list(text)
    depth = 0
    i, n = 0, len(text)
    while i < n:
        if text[i:i + 2] == '(*':
            depth += 1
            out[i] = out[i + 1] = ' '
            i += 2
        elif depth > 0 and text[i:i + 2] == '*)':
            depth -= 1
            out[i] = out[i + 1] = ' '
            i += 2
        elif depth > 0:
            if text[i] != '\n':
                out[i] = ' '
            i += 1
        elif text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] != '\n':
                    out[j] = ' '
                j += 1
            i = j + 1
        else:
            i += 1
    return ''.join(out)


_SELECTED_MARK = 'selected lemmas:'
# an EC lemma name token (possibly Theory.qualified, primed, or "op"-quoted);
# progress junk lines contain [..], %, or (..) and never match
_SELECTED_TOK_RE = re.compile(r'[A-Za-z0-9_.\'"<>=+*!&|^:/\\-]+\Z')


def parse_selected(text):
    """Collect the lemma names of the LAST `selected lemmas:` warning block."""
    names = []
    in_block = False
    for line in text.splitlines():
        idx = line.find(_SELECTED_MARK)
        if idx >= 0:
            names = line[idx + len(_SELECTED_MARK):].split()
            in_block = True
            continue
        if in_block:
            toks = line.split()
            if (line[:1] in (' ', '\t') and toks
                    and all(_SELECTED_TOK_RE.match(t) for t in toks)):
                names += toks
            else:
                in_block = False
    return names


_SMT_CALL_RE = re.compile(r'\bsmt\b(\s*\(([^()]*)\))?')


def find_last_smt(text):
    masked = mask_ec_comments(text)
    matches = list(_SMT_CALL_RE.finditer(masked))
    if not matches:
        raise SystemExit('no smt call found in file')
    return matches[-1]


def call_kind(m):
    if m.group(1) is None:
        return 'BARE'
    return 'PAREN' if m.group(2).strip() else 'EMPTY'


# ------------------------------------------------------------------------ main

def build_cli():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('name')
    p.add_argument('infile')
    p.add_argument('outfile')

    p = sub.add_parser('core')
    p.add_argument('outfile_of_solver')

    p = sub.add_parser('demangle')
    p.add_argument('corefile')
    p.add_argument('--candidates', default='')

    p = sub.add_parser('last-call')
    p.add_argument('ecfile')

    p = sub.add_parser('pin')
    p.add_argument('ecfile')
    p.add_argument('--hints', required=True)
    p.add_argument('-o', '--out', required=True)

    p = sub.add_parser('select')
    p.add_argument('ecfile')
    p.add_argument('-o', '--out', required=True)

    p = sub.add_parser('selected')
    p.add_argument('logfile')
    return ap


def run_smt_cmd(a):
    """Handle the SMT-LIB-side subcommands: name / core / demangle."""
    if a.cmd == 'name':
        with open(a.infile) as f:
            text = f.read()
        with open(a.outfile, 'w') as f:
            f.write(name_asserts(text))

    elif a.cmd == 'core':
        with open(a.outfile_of_solver) as f:
            for base in parse_core(f.read()):
                print(base)

    elif a.cmd == 'demangle':
        with open(a.corefile) as f:
            bases = [l.strip() for l in f if l.strip()]
        lemmas, other = demangle(bases, a.candidates.split())
        print('lemmas: %s' % ' '.join(lemmas))
        print('other: %s' % ' '.join(other))


def run_ec_cmd(a):
    """Handle the EasyCrypt-side subcommands: last-call / pin / select /
    selected."""
    if a.cmd == 'selected':
        with open(a.logfile) as f:
            names = parse_selected(f.read())
        if not names:
            raise SystemExit('no `selected lemmas:` warning found in log')
        print(' '.join(names))
        return

    with open(a.ecfile) as f:
        text = f.read()
    m = find_last_smt(text)

    if a.cmd == 'last-call':
        print(call_kind(m))
        print(' '.join((m.group(2) or '').split()))
        print(text[m.start():m.end()].replace('\n', ' '))

    elif a.cmd == 'pin':
        pinned = text[:m.start()] + 'smt(%s)' % a.hints.strip() + text[m.end():]
        with open(a.out, 'w') as f:
            f.write(pinned)

    elif a.cmd == 'select':
        if call_kind(m) != 'BARE':
            raise SystemExit('select only supports a bare `smt` call '
                             '(hinted calls already know their sent lemmas)')
        rewritten = text[:m.start()] + 'smt selected' + text[m.end():]
        with open(a.out, 'w') as f:
            f.write(rewritten)


def main():
    a = build_cli().parse_args()
    if a.cmd in ('name', 'core', 'demangle'):
        run_smt_cmd(a)
    else:
        run_ec_cmd(a)


if __name__ == '__main__':
    main()
