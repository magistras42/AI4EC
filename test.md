# ElGamal import-repair verification

Run: `2026-07-30T23:07:21Z` · host `Linux 6.17.0-35-generic` · repo `shannon-llm-integration` @ `f18ce3d7`

## Step 0 — fetch upstream release tags

```console
$ git -C integration/extern/easycrypt fetch https://github.com/EasyCrypt/easycrypt.git 'refs/tags/*:refs/tags/*'
(no output = tags already present)

$ git -C integration/extern/easycrypt tag | sort | tr '\n' ' '
r2022.04 r2023.09 r2024.01 r2024.09 r2025.02 r2025.03 r2025.08 r2025.10 r2025.11 r2026.02 r2026.03 r2026.05 r2026.06 r2026.07 
```

## Step 1 — Python environment

```console
$ python3 -m venv .venv && .venv/bin/pip install -r integration/agent/requirements-agent.txt
Collecting openai>=1.0.0 (from -r integration/agent/requirements-agent.txt (line 1))
  Using cached openai-2.51.0-py3-none-any.whl.metadata (36 kB)
Collecting numpy>=1.24.0 (from -r integration/agent/requirements-agent.txt (line 2))
  Using cached numpy-2.5.1-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl.metadata (6.6 kB)
Collecting pytest>=7.0.0 (from -r integration/agent/requirements-agent.txt (line 3))
  Using cached pytest-9.1.1-py3-none-any.whl.metadata (7.6 kB)
Collecting anyio<5,>=3.5.0 (from openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached anyio-4.14.2-py3-none-any.whl.metadata (4.6 kB)
Collecting distro<2,>=1.7.0 (from openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached distro-1.9.0-py3-none-any.whl.metadata (6.8 kB)
Collecting httpx<1,>=0.23.0 (from openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached httpx-0.28.1-py3-none-any.whl.metadata (7.1 kB)
Collecting jiter<1,>=0.10.0 (from openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached jiter-0.16.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata (5.2 kB)
Collecting pydantic<3,>=1.9.0 (from openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached pydantic-2.13.4-py3-none-any.whl.metadata (109 kB)
Collecting sniffio (from openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached sniffio-1.3.1-py3-none-any.whl.metadata (3.9 kB)
Collecting tqdm>4 (from openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached tqdm-4.70.0-py3-none-any.whl.metadata (57 kB)
Collecting typing-extensions<5,>=4.14 (from openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached typing_extensions-4.16.0-py3-none-any.whl.metadata (3.3 kB)
Collecting iniconfig>=1.0.1 (from pytest>=7.0.0->-r integration/agent/requirements-agent.txt (line 3))
  Using cached iniconfig-2.3.0-py3-none-any.whl.metadata (2.5 kB)
Collecting packaging>=22 (from pytest>=7.0.0->-r integration/agent/requirements-agent.txt (line 3))
  Using cached packaging-26.2-py3-none-any.whl.metadata (3.5 kB)
Collecting pluggy<2,>=1.5 (from pytest>=7.0.0->-r integration/agent/requirements-agent.txt (line 3))
  Using cached pluggy-1.6.0-py3-none-any.whl.metadata (4.8 kB)
Collecting pygments>=2.7.2 (from pytest>=7.0.0->-r integration/agent/requirements-agent.txt (line 3))
  Using cached pygments-2.20.0-py3-none-any.whl.metadata (2.5 kB)
Collecting idna>=2.8 (from anyio<5,>=3.5.0->openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached idna-3.18-py3-none-any.whl.metadata (6.1 kB)
Collecting certifi (from httpx<1,>=0.23.0->openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached certifi-2026.7.22-py3-none-any.whl.metadata (2.5 kB)
Collecting httpcore==1.* (from httpx<1,>=0.23.0->openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached httpcore-1.0.9-py3-none-any.whl.metadata (21 kB)
Collecting h11>=0.16 (from httpcore==1.*->httpx<1,>=0.23.0->openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached h11-0.16.0-py3-none-any.whl.metadata (8.3 kB)
Collecting annotated-types>=0.6.0 (from pydantic<3,>=1.9.0->openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached annotated_types-0.8.0-py3-none-any.whl.metadata (15 kB)
Collecting pydantic-core==2.46.4 (from pydantic<3,>=1.9.0->openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached pydantic_core-2.46.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata (6.6 kB)
Collecting typing-inspection>=0.4.2 (from pydantic<3,>=1.9.0->openai>=1.0.0->-r integration/agent/requirements-agent.txt (line 1))
  Using cached typing_inspection-0.4.2-py3-none-any.whl.metadata (2.6 kB)
Using cached openai-2.51.0-py3-none-any.whl (1.7 MB)
Using cached numpy-2.5.1-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl (16.7 MB)
Using cached pytest-9.1.1-py3-none-any.whl (386 kB)
Using cached anyio-4.14.2-py3-none-any.whl (125 kB)
Using cached distro-1.9.0-py3-none-any.whl (20 kB)
Using cached httpx-0.28.1-py3-none-any.whl (73 kB)
Using cached httpcore-1.0.9-py3-none-any.whl (78 kB)
Using cached iniconfig-2.3.0-py3-none-any.whl (7.5 kB)
Using cached jiter-0.16.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (343 kB)
Using cached packaging-26.2-py3-none-any.whl (100 kB)
Using cached pluggy-1.6.0-py3-none-any.whl (20 kB)
Using cached pydantic-2.13.4-py3-none-any.whl (472 kB)
Using cached pydantic_core-2.46.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (2.1 MB)
Using cached pygments-2.20.0-py3-none-any.whl (1.2 MB)
Using cached tqdm-4.70.0-py3-none-any.whl (80 kB)
Using cached typing_extensions-4.16.0-py3-none-any.whl (45 kB)
Using cached sniffio-1.3.1-py3-none-any.whl (10 kB)
Using cached annotated_types-0.8.0-py3-none-any.whl (13 kB)
Using cached idna-3.18-py3-none-any.whl (65 kB)
Using cached typing_inspection-0.4.2-py3-none-any.whl (14 kB)
Using cached certifi-2026.7.22-py3-none-any.whl (136 kB)
Using cached h11-0.16.0-py3-none-any.whl (37 kB)
Installing collected packages: typing-extensions, tqdm, sniffio, pygments, pluggy, packaging, numpy, jiter, iniconfig, idna, h11, distro, certifi, annotated-types, typing-inspection, pytest, pydantic-core, httpcore, anyio, pydantic, httpx, openai
Successfully installed annotated-types-0.8.0 anyio-4.14.2 certifi-2026.7.22 distro-1.9.0 h11-0.16.0 httpcore-1.0.9 httpx-0.28.1 idna-3.18 iniconfig-2.3.0 jiter-0.16.0 numpy-2.5.1 openai-2.51.0 packaging-26.2 pluggy-1.6.0 pydantic-2.13.4 pydantic-core-2.46.4 pygments-2.20.0 pytest-9.1.1 sniffio-1.3.1 tqdm-4.70.0 typing-extensions-4.16.0 typing-inspection-0.4.2
```

## Step 2 — regenerate the manifest from git history

```console
$ python3 proof_corpus/scripts/analyze_library_history.py
Building release map over 14 tags (r2022.04 .. r2026.07) ...
  6177 commits attributed to a release
  AllCore        12 commits,  2 releases with symbol changes  theories/core/AllCore.ec
  Bool           41 commits,  2 releases with symbol changes  theories/core/Bool.ec
  Core           62 commits,  5 releases with symbol changes  theories/core/Core.ec
  CoreMap         3 commits,  1 releases with symbol changes  theories/core/CoreMap.ec
  CoreReal       21 commits,  1 releases with symbol changes  theories/core/CoreReal.ec
  DBool          27 commits,  3 releases with symbol changes  theories/distributions/DBool.ec
  DInterval      17 commits,  3 releases with symbol changes  theories/distributions/DInterval.ec
  Distr         170 commits,  6 releases with symbol changes  theories/distributions/Distr.ec
  FMap           57 commits,  3 releases with symbol changes  theories/datatypes/FMap.ec
  FSet          100 commits,  5 releases with symbol changes  theories/datatypes/FSet.ec
  Int           100 commits,  4 releases with symbol changes  theories/datatypes/Int.ec
  Logic          98 commits,  7 releases with symbol changes  theories/prelude/Logic.ec
  Pervasive      28 commits,  2 releases with symbol changes  theories/prelude/Pervasive.ec
  PROM           37 commits,  2 releases with symbol changes  theories/crypto/PROM.ec
  Real           76 commits,  5 releases with symbol changes  theories/datatypes/Real.ec
  SmtMap         34 commits,  4 releases with symbol changes  theories/datatypes/SmtMap.ec
Wrote /home/m8simmon/cs846/AI4EC/proof_corpus/output/library_history.json

$ python3 proof_corpus/scripts/build_ec_migrations.py
Wrote /home/m8simmon/cs846/AI4EC/proof_corpus/ec_migrations.toml
  15 migrations (9 require_semantics, 1 symbol_moved, 3 syntax_change, 1 theory_added, 1 theory_renamed), 16 libraries
  12 derived from git history, 3 curated engine rules
```

## Step 3 — baseline: the untouched 2020-era file

```console
$ cp data/derens99-ElGamal-proof/hashedelgamal.ec /tmp/heg.ec
$ integration/extern/easycrypt/_build/default/src/ec.exe llm -lastgoals /tmp/heg.ec
[critical] [/tmp/heg.ec: line 108 (8)] parse error
$ echo $?  ->  1
```

## Step 4 — run import repair

```console
$ .venv/bin/python -m integration.agent.import_repair /tmp/heg.ec \
      --source-version r2022.04 --target-version r2026.07
{
  "changed": true,
  "loads_before": false,
  "loads_after": false,
  "improved": true,
  "error_line_before": 108,
  "error_line_after": 453,
  "considered": [
    "smtmap-symbols-moved-to-fmap-r2025.02",
    "proc-star-removed",
    "declare-module-ascription",
    "old-module-restriction-sets"
  ],
  "applied": [
    {
      "id": "smtmap-symbols-moved-to-fmap-r2025.02",
      "kind": "symbol_moved",
      "confidence": "high",
      "actions": [
        "add_require FMap"
      ],
      "kept": true,
      "reason": "kept: no regression (first error still at 108)",
      "summary": "125 declarations moved from SmtMap to FMap in r2025.02. A file that requires SmtMap\n  and uses any of them must also require FMap."
    },
    {
      "id": "proc-star-removed",
      "kind": "syntax_change",
      "confidence": "high",
      "actions": [
        "replace_regex '\\\\bproc\\\\s+\\\\*\\\\s' (2)"
      ],
      "kept": true,
      "reason": "first error moved later (108 -> 357)",
      "summary": "The `proc *` marker (a distinguished/initialising procedure in a module type) is no\n  longer parsed. Drop the star; current EasyCrypt infers this from usage."
    },
    {
      "id": "declare-module-ascription",
      "kind": "syntax_change",
      "confidence": "high",
      "actions": [
        "replace_regex '\\\\bdeclare\\\\s+module\\\\s+(\\\\w+)\\\\s*:\\\\s' (1)"
      ],
      "kept": true,
      "reason": "kept: no regression (first error still at 357)",
      "summary": "`declare module X : T` (bare ascription) is now `declare module X <: T`."
    },
    {
      "id": "old-module-restriction-sets",
      "kind": "syntax_change",
      "confidence": "medium",
      "actions": [
        "add_pragma +old_mem_restr"
      ],
      "kept": true,
      "reason": "first error moved later (357 -> 453)",
      "summary": "Unprefixed module-restriction sets like `{RO, Adv}` are no longer accepted; current\n  syntax is `{-RO, -Adv}`. The `old_mem_restr` pragma restores the old reading without\n  touching every site, which keeps line numbers stable."
    }
  ],
  "error_before": "[critical] [/tmp/heg.import_repair.ec: line 108 (8)] parse error",
  "error_after": "[warning] [/tmp/heg.import_repair.ec:362] global axiom Adv_choose_ll in section\n[warning] [/tmp/heg.import_repair.ec:366] global axiom Adv_guess_ll in section\n[critical] [/tmp/heg.import_repair.ec: line 453 (0) to line 454 (53)] invalid `position' parameter",
  "notes": [
    "bulk apply did not make the file load; retrying incrementally and keeping only migrations EasyCrypt shows progress on"
  ]
}
$ echo $?  ->  1   (1 = file still does not fully compile; see Step 5)
```

## Step 5 — confirm what remains is a TACTIC error, not an import error

```console
$ integration/extern/easycrypt/_build/default/src/ec.exe llm -lastgoals /tmp/heg.import_repair.ec 2>&1 | grep critical
[critical] [/tmp/heg.import_repair.ec: line 453 (0) to line 454 (53)] invalid `position' parameter

$ sed -n '453,454p' /tmp/heg.import_repair.ec
    seq 1 1 : (={glob Adv, choice,x1,x2} /\ q{1} = q1{2} /\ r{1} = q2{2} /\
      RO.mp{1} = RO_track.mp{2} /\ pubk{1} = g^q{1}).
```

## What the repair actually changed

```console
$ diff -u /tmp/heg.ec /tmp/heg.import_repair.ec
--- /tmp/heg.ec	2026-07-30 19:07:58.433400208 -0400
+++ /tmp/heg.import_repair.ec	2026-07-30 19:08:20.443520043 -0400
@@ -1,4 +1,4 @@
-require import AllCore Distr SmtMap DBool FSet.
+pragma +old_mem_restr. require import AllCore Distr SmtMap DBool FSet FMap.
 require import StdOrder.  import RealOrder.
 
 type group.
@@ -105,7 +105,7 @@
 type cipher = group * text.
 
 module type RO = {
-  proc * init() : unit
+  proc init() : unit
 
   proc f(x : group) : text
 }.
@@ -264,7 +264,7 @@
 
 
   module type ADV (RO : RO) = {
-    proc * choose(pubk : group) : text * text {RO.f}
+    proc choose(pubk : group) : text * text {RO.f}
 
     proc guess(c : cipher) : bool {RO.f}
   }.
@@ -354,7 +354,7 @@
 
 section.
 
-declare module Adv : ADV{RO, Adv2LCDHAdv}.
+declare module Adv <: ADV{RO, Adv2LCDHAdv}.
 
 axiom Adv_choose_ll :
   forall (RO <: RO{Adv}),
```

```console
$ wc -l /tmp/heg.ec /tmp/heg.import_repair.ec   # line-preserving check
  829 /tmp/heg.ec
  829 /tmp/heg.import_repair.ec
 1658 total
```

## Verdict

| Check | Expected | Observed | |
|---|---|---|---|
| baseline first error | `line 108` | `line 108` | PASS |
| after repair, first error | `line 453` | `line 453` | PASS |
| improved | `True` | `True` | PASS |
| file changed | `True` | `True` | PASS |
| still fully compiles | `False` | `False` | PASS |
| migrations kept | `4` | `4` | PASS |

Migrations kept:

- `smtmap-symbols-moved-to-fmap-r2025.02` (symbol_moved, derived from git history) — kept: no regression (first error still at 108)
- `proc-star-removed` (syntax_change, curated engine rule) — first error moved later (108 -> 357)
- `declare-module-ascription` (syntax_change, curated engine rule) — kept: no regression (first error still at 357)
- `old-module-restriction-sets` (syntax_change, curated engine rule) — first error moved later (357 -> 453)

The remaining error at line 453 is `invalid \`position' parameter` on a
`seq 1 1 : ...` **tactic** — a broken proof, not a broken import. That is the
intended boundary: import repair makes the file loadable through its
declarations and stops where tactic-level repair begins.

Line count is unchanged (829 both sides), so the absolute lemma line numbers
`ProofCase` records still point at the right lemmas.
