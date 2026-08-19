#!/usr/bin/env python3
"""True-EasyCrypt-path validation: pin ranked hints at real smt call sites.

For each target file and hint system (oracle = union of extracted cores,
embed = union of the embedding ranking's top-k over the file's eval goals):
  1. demangle the system's lemma names to EC names (longest-suffix match
     against a lemma dictionary from the EasyCrypt stdlib + the file's dir;
     Why3 artifacts <op>'def / *_ho_spec / *_sort are dropped — they are
     translation-generated, auto-included, and not hintable)
  2. rewrite EVERY smt call site in a temp copy to smt(<union>)
  3. compile with DEFAULT provers (-timeout 10); on failure:
       - error mentions a hint name  -> drop that name from the union, restart
       - 'cannot prove goal' at line -> revert that site to original, retry
     (max rounds capped)
  4. record sites pinned/reverted, final ok, wall time; baseline = untouched
     file compiled identically.

NOTE the site-pin regime: on this path Why3 re-adds hint-closure 'def
axioms per hint, so small k (2~4) works and large k re-bloats (paper 4.1).

Usage:
  source ~/ec-env.sh
  python3 ec_path_eval.py --files F1 F2 ... --systems oracle embed \
      --preds embed=eval/preds_evalcands_qe4.json --topk 4
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smtcore

CORPUS = HERE.parent / "smt_benchmark" / "corpus"
STDLIB = Path.home() / "ectools" / "easycrypt" / "theories"

DECL_RE = re.compile(
    r"^\s*(?:local\s+|declare\s+)?(?:lemma|axiom)\s+(?:nosmt\s+)?"
    r"([A-Za-z_][A-Za-z0-9_']*)", re.M)
ERRLINE_RE = re.compile(r"line (\d+)")


def build_lemma_dict(extra_dirs):
    names = set()
    for root in [STDLIB, *extra_dirs]:
        for p in Path(root).rglob("*.ec"):
            try:
                names.update(DECL_RE.findall(p.read_text(errors="replace")))
            except OSError:
                pass
    return names


def demangle(mangled, dic):
    """Top_List_size_cat -> size_cat (longest dictionary suffix), or None."""
    m = mangled[4:] if mangled.startswith("Top_") else mangled
    if m.endswith("'def") or m.endswith("_ho_spec") or m.endswith("_sort"):
        return None
    cuts = [0] + [i + 1 for i, ch in enumerate(m) if ch == "_"]
    for i in sorted(cuts):                       # longest suffix first
        cand = m[i:]
        if cand in dic:
            return cand
    return None


def smt_sites(text):
    masked = smtcore.mask_ec_comments(text)
    return [(mm.start(), mm.end(), text[mm.start():mm.end()])
            for mm in smtcore._SMT_CALL_RE.finditer(masked)]


def render(text, sites, enabled, hint_str):
    """Return (pinned_text, line_of_each_site) with enabled sites pinned."""
    out = text
    for idx in range(len(sites) - 1, -1, -1):
        s, e, _ = sites[idx]
        if enabled[idx]:
            out = out[:s] + f"smt({hint_str})" + out[e:]
    lines = []
    # recompute site line numbers in the rendered text (front-to-back)
    pos_shift = 0
    for idx, (s, e, orig) in enumerate(sites):
        new_s = s + pos_shift
        lines.append(out.count("\n", 0, new_s) + 1)
        if enabled[idx]:
            pos_shift += len(f"smt({hint_str})") - (e - s)
    return out, lines


def compile_ec(path, idir, timeout_hard):
    cmd = ["easycrypt", "compile", "-no-eco", "-timeout", "10",
           "-I", str(idir), str(path)]
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=path.parent, timeout=timeout_hard,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return p.returncode == 0, p.stdout.decode(errors="replace"), \
            time.monotonic() - t0
    except subprocess.TimeoutExpired:
        return False, "[hard timeout]", time.monotonic() - t0


def run_system(relfile, hints, args):
    """Pin all smt sites with `hints`; revert failures. Returns result dict."""
    orig = CORPUS / relfile
    text = orig.read_text(errors="replace")
    sites = smt_sites(text)
    enabled = [True] * len(sites)
    union = list(hints)

    with tempfile.TemporaryDirectory(prefix="econline_") as td:
        tmp = Path(td) / orig.name
        rounds, out, ok, secs = 0, "", False, 0.0
        while rounds < args.max_rounds:
            rounds += 1
            pinned_text, site_lines = render(text, sites, enabled,
                                             " ".join(union))
            tmp.write_text(pinned_text)
            ok, out, secs = compile_ec(tmp, orig.parent, args.hard_timeout)
            if ok:
                break
            tail = out[-800:]
            dropped = next((h for h in union if h in tail
                            and "cannot prove" not in tail), None)
            if dropped and ("unknown" in tail or "lookup" in tail
                            or "parse" in tail or "cannot infer" in tail):
                union.remove(dropped)
                continue
            m = ERRLINE_RE.search(tail)
            if m:
                errline = int(m.group(1))
                cand = [i for i, ln in enumerate(site_lines)
                        if enabled[i] and ln <= errline]
                if cand:
                    enabled[max(cand)] = False
                    continue
            break                                    # unrecognized failure

        return {"file": relfile, "sites": len(sites),
                "kept": sum(enabled), "reverted": enabled.count(False),
                "union_size": len(union), "rounds": rounds,
                "ok": int(ok), "seconds": round(secs, 2),
                "tail": "" if ok else " ".join(out[-200:].split())}


def run_baseline(relfile, args):
    """Untouched file, default provers — the comparison row."""
    orig = CORPUS / relfile
    with tempfile.TemporaryDirectory(prefix="econline_") as td:
        tmp = Path(td) / orig.name
        shutil.copyfile(orig, tmp)
        ok, _, secs = compile_ec(tmp, orig.parent, args.hard_timeout)
    return {"file": relfile, "system": "default", "ok": int(ok),
            "seconds": round(secs, 2),
            "sites": len(smt_sites(orig.read_text(errors="replace"))),
            "kept": "-", "reverted": "-", "union_size": "-",
            "rounds": 1, "tail": ""}


def system_hints(system, goals, preds, dic, topk):
    """Union of a system's picks over the file's goals, demangled to EC
    names; returns (hints, n_dropped_by_demangle)."""
    if system == "oracle":
        raw = {n for g in goals for n in ("Top_" + x for x in g["answer"])}
    else:
        p = preds[system]
        raw = {"Top_" + x for g in goals for x in p.get(g["id"], [])[:topk]}
    hints, dropped = [], []
    for n in sorted(raw):
        d = demangle(n, dic)
        (hints.append(d) if d and d not in hints else dropped.append(n))
    return hints, len(dropped)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--systems", nargs="+", default=["oracle"])
    ap.add_argument("--preds", nargs="*", default=[],
                    help="system=predsfile.json (display names per goal id)")
    ap.add_argument("--topk", type=int, default=8)
    ap.add_argument("--max-rounds", type=int, default=15)
    ap.add_argument("--hard-timeout", type=int, default=600)
    args = ap.parse_args()

    preds = {}
    for spec in args.preds:
        name, path = spec.split("=", 1)
        preds[name] = json.load(open(path))

    ev = [json.loads(l) for l in open(HERE / "eval" / "eval.jsonl")]
    by_file = {}
    for r in ev:
        by_file.setdefault(r["file"], []).append(r)

    dic = build_lemma_dict({(CORPUS / f).parent for f in args.files})
    print(f"lemma dictionary: {len(dic)} names")

    results = []
    for f in args.files:
        res = run_baseline(f, args)
        results.append(res)
        print(f"[default] {f}: ok={res['ok']} {res['seconds']}s", flush=True)

        for system in args.systems:
            hints, n_dropped = system_hints(system, by_file.get(f, []),
                                            preds, dic, args.topk)
            res = run_system(f, hints, args)
            res["system"] = system
            res["demangle_dropped"] = n_dropped
            results.append(res)
            print(f"[{system}] {f}: ok={res['ok']} {res['seconds']}s "
                  f"sites={res['sites']} kept={res['kept']} "
                  f"reverted={res['reverted']} union={res['union_size']} "
                  f"(demangle dropped {n_dropped}) rounds={res['rounds']}",
                  flush=True)
            if res["tail"]:
                print(f"    tail: {res['tail'][:180]}", flush=True)

    out = HERE / "eval" / "online_results.json"
    json.dump(results, open(out, "w"), indent=1)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
