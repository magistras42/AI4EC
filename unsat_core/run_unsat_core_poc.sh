#!/usr/bin/env bash
# run_unsat_core_poc.sh — unsat-core extraction + hint pinning pipeline (PoC)
#
# Single-file proof of concept:
#   0. baseline  compile the original file per solver, measuring time
#   1. dump      with EC_SMT_DEBUG=1, dump the exact SMT-LIB2 input Why3
#                sends to the solvers (<Prover>.smt in cwd; overwritten per
#                smt call, so the "last call" is the target)
#   2. name      wrap every assert in the dump as (! ... :named <comment>__k)
#                + insert (get-unsat-core) after (check-sat)  [smtcore.py name]
#   3. core      run z3 unsat_core=true / cvc5 --produce-unsat-cores
#                directly, parse the unsat core                [smtcore.py core]
#   4. demangle  map mangled core names back to EC lemma names. Candidates =
#                the call's own hints; for bare smt, the `smt selected`
#                output                                     [smtcore.py demangle]
#   5. pin       rewrite the call as smt(<core lemmas>), re-run all solvers,
#                measure time
#
# Usage: ./run_unsat_core_poc.sh [-f file.ec] [-t smt_timeout] [-k]
#   -f  target .ec file (default: built-in demo — the List.size_cat proof,
#       where only 1 of its 2 hints is actually used)
#   -t  SMT timeout in seconds (default 10)
#   -k  keep the working directory
#
# Requirements: ~/ec-env.sh (puts easycrypt, z3, cvc5, alt-ergo on PATH)
# Known limits (PoC scope):
#   - Only the file's LAST smt call is targeted (EC_SMT_DEBUG overwrites
#     per call).
#   - Rewriting bare+option forms like `smt 30.` is unsupported
#     (parenthesized / pure bare only).
#   - Alt-Ergo 2.4.3 cannot produce usable cores (name-mapping bug), so it
#     is included only for the pinned re-run.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SMTCORE="$SCRIPT_DIR/smtcore.py"

EC_TIMEOUT=10
KEEP=0
FILE=""
while getopts "f:t:kh" opt; do
  case $opt in
    f) FILE=$OPTARG ;;
    t) EC_TIMEOUT=$OPTARG ;;
    k) KEEP=1 ;;
    h) sed -n '2,26p' "$0"; exit 0 ;;
    *) exit 2 ;;
  esac
done
HARD_TIMEOUT=$((EC_TIMEOUT * 6 + 60))

# ec-env.sh appends to possibly-unset PATH-like vars; relax -u while sourcing
set +u
# shellcheck disable=SC1090
source "$HOME/ec-env.sh"
set -u
for bin in easycrypt z3 cvc5 alt-ergo python3; do
  command -v "$bin" >/dev/null || { echo "ERROR: $bin not in PATH" >&2; exit 1; }
done

WORK=$(mktemp -d /tmp/unsat_core_poc.XXXXXX)
cleanup() { [ "$KEEP" = 1 ] && echo "workdir kept: $WORK" || rm -rf "$WORK"; }
trap cleanup EXIT
echo "workdir: $WORK"

# ---------------------------------------------------------------- target file
IDIR=()
if [ -z "$FILE" ]; then
  cat > "$WORK/target.ec" <<'EOF'
require import AllCore List.

lemma demo (s1 s2 : int list) :
  size (s1 ++ s2) = size s1 + size s2.
proof. smt(size_cat size_ge0). qed.
EOF
  echo "target: (built-in demo) $WORK/target.ec"
else
  cp "$FILE" "$WORK/target.ec"
  IDIR=(-I "$(cd "$(dirname "$FILE")" && pwd)")
  echo "target: $FILE"
fi
TARGET=$WORK/target.ec

# run_ec CWD SOLVER FILE LOG [EXTRA_ENV] -> sets RUN_OK (1/0), RUN_MS
run_ec() {
  local cwd=$1 solver=$2 file=$3 log=$4 extra=${5:-}
  local t0 t1 rc
  t0=$(date +%s%N)
  set +e
  ( cd "$cwd" && timeout "$HARD_TIMEOUT" env $extra \
      easycrypt compile -no-eco -p "$solver" -timeout "$EC_TIMEOUT" \
      ${IDIR[@]+"${IDIR[@]}"} "$file" ) >"$log" 2>&1
  rc=$?
  set -e
  t1=$(date +%s%N)
  RUN_MS=$(( (t1 - t0) / 1000000 ))
  RUN_OK=$([ $rc -eq 0 ] && echo 1 || echo 0)
}

# ------------------------------------------------- target call classification
mapfile -t CALLINFO < <(python3 "$SMTCORE" last-call "$TARGET")
KIND=${CALLINFO[0]}
HINTS=${CALLINFO[1]:-}
CALLTEXT=${CALLINFO[2]:-}
echo "last smt call: '$CALLTEXT' (kind=$KIND)"

# ------------------------------------------------------------ stage 0: baseline
declare -A BASE_OK BASE_MS PIN_OK PIN_MS
echo; echo "== stage 0: baseline (original file) =="
for S in Z3 CVC5 Alt-Ergo; do
  run_ec "$WORK" "$S" "$TARGET" "$WORK/base_$S.log"
  BASE_OK[$S]=$RUN_OK; BASE_MS[$S]=$RUN_MS
  echo "  $S: ok=${RUN_OK} ${RUN_MS}ms"
done

# ---------------------------------------------------- sent-lemma candidate list
echo; echo "== sent-lemma candidates =="
case $KIND in
  PAREN)
    CANDIDATES=$HINTS
    echo "  from the call's own hints: $CANDIDATES" ;;
  BARE)
    python3 "$SMTCORE" select "$TARGET" -o "$WORK/selected.ec"
    run_ec "$WORK" Z3 "$WORK/selected.ec" "$WORK/selected.log"
    CANDIDATES=$(python3 "$SMTCORE" selected "$WORK/selected.log")
    echo "  from \`smt selected\`: $(echo "$CANDIDATES" | wc -w) lemmas"
    echo "  ($(echo "$CANDIDATES" | cut -c1-160)...)" ;;
  EMPTY)
    CANDIDATES=""
    echo "  NOTE: smt() sends ZERO environment lemmas (maxlemmas=0);"
    echo "        the core will contain only hypotheses/builtins — nothing to pin." ;;
esac
N_SENT=$(echo "$CANDIDATES" | wc -w)

# ------------------------------------------- stages 1-4: dump -> name -> core
declare -A CORE_LEMMAS CORE_OTHER SOLVER_MS
for S in Z3 CVC5; do
  echo; echo "== stages 1-4 for $S =="
  mkdir -p "$WORK/dump_$S"
  run_ec "$WORK/dump_$S" "$S" "$TARGET" "$WORK/dump_$S.log" "EC_SMT_DEBUG=1"
  if [ ! -f "$WORK/dump_$S/$S.smt" ]; then
    echo "  ERROR: no dump produced (run ok=$RUN_OK); see $WORK/dump_$S.log" >&2
    CORE_LEMMAS[$S]="(dump failed)"; CORE_OTHER[$S]=""; continue
  fi
  echo "  dumped: dump_$S/$S.smt ($(wc -l < "$WORK/dump_$S/$S.smt") lines)"

  python3 "$SMTCORE" name "$WORK/dump_$S/$S.smt" "$WORK/$S.named.smt2"

  t0=$(date +%s%N)
  case $S in
    Z3)   z3 unsat_core=true "$WORK/$S.named.smt2" >"$WORK/$S.core.out" 2>&1 || true ;;
    CVC5) cvc5 --lang smt2 --produce-unsat-cores "$WORK/$S.named.smt2" \
            >"$WORK/$S.core.out" 2>&1 || true ;;
  esac
  t1=$(date +%s%N); SOLVER_MS[$S]=$(( (t1 - t0) / 1000000 ))

  if ! python3 "$SMTCORE" core "$WORK/$S.core.out" > "$WORK/$S.core.names"; then
    echo "  ERROR: core extraction failed; see $WORK/$S.core.out" >&2
    CORE_LEMMAS[$S]="(core failed)"; CORE_OTHER[$S]=""; continue
  fi
  echo "  raw core: $(tr '\n' ' ' < "$WORK/$S.core.names")(direct solver run: ${SOLVER_MS[$S]}ms)"

  DEMANGLED=$(python3 "$SMTCORE" demangle "$WORK/$S.core.names" --candidates "$CANDIDATES")
  CORE_LEMMAS[$S]=$(echo "$DEMANGLED" | sed -n 's/^lemmas: *//p')
  CORE_OTHER[$S]=$(echo "$DEMANGLED" | sed -n 's/^other: *//p')
  echo "  EC lemmas in core: ${CORE_LEMMAS[$S]:-<none>}"
  echo "  other core members (goal/hyps/builtins): ${CORE_OTHER[$S]:-<none>}"
done

# ------------------------------------------------------- stage 5: pin + rerun
PIN=$(echo "${CORE_LEMMAS[Z3]:-} ${CORE_LEMMAS[CVC5]:-}" | tr ' ' '\n' \
      | awk 'NF && $0 !~ /[()]/ && !seen[$0]++' | tr '\n' ' ' | sed 's/ *$//')
echo; echo "== stage 5: pinned re-run =="
if [ -z "$PIN" ]; then
  echo "  no environment lemmas in any core — nothing to pin; skipping re-run."
else
  echo "  pinned call: smt($PIN)"
  python3 "$SMTCORE" pin "$TARGET" --hints "$PIN" -o "$WORK/pinned.ec"
  for S in Z3 CVC5 Alt-Ergo; do
    run_ec "$WORK" "$S" "$WORK/pinned.ec" "$WORK/pin_$S.log"
    PIN_OK[$S]=$RUN_OK; PIN_MS[$S]=$RUN_MS
    echo "  $S: ok=${RUN_OK} ${RUN_MS}ms"
  done
fi

# --------------------------------------------------------------------- report
N_USED=$(echo "$PIN" | wc -w)
echo
echo "================================ REPORT ================================"
echo "target          : ${FILE:-built-in demo}"
echo "last smt call   : $CALLTEXT"
if [ "$N_SENT" -gt 12 ]; then
  echo "lemmas sent     : $N_SENT ($(echo "$CANDIDATES" | cut -d' ' -f1-12) ...)"
else
  echo "lemmas sent     : $N_SENT ($CANDIDATES)"
fi
echo "lemmas in core  : $N_USED ($PIN)"
echo "core (Z3)       : ${CORE_LEMMAS[Z3]:-—}  [other: ${CORE_OTHER[Z3]:-—}]"
echo "core (CVC5)     : ${CORE_LEMMAS[CVC5]:-—}  [other: ${CORE_OTHER[CVC5]:-—}]"
echo "------------------------------------------------------------------------"
printf '%-9s %-18s %-18s\n' "solver" "baseline ok/ms" "pinned ok/ms"
for S in Z3 CVC5 Alt-Ergo; do
  printf '%-9s %-18s %-18s\n' "$S" \
    "${BASE_OK[$S]:-?}/${BASE_MS[$S]:-?}" \
    "${PIN_OK[$S]:-—}/${PIN_MS[$S]:-—}"
done
echo "========================================================================"
