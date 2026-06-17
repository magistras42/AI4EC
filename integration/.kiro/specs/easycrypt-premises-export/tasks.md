# Implementation Plan: easycrypt-premises-export

## Overview

Patch the EasyCrypt OCaml source to add a `-premises` flag to the `llm` subcommand.
The flag, when combined with `-upto LINE`, causes the binary to emit all globally
accessible lemmas and axioms to stdout after the goal-state block, separated by a
fixed marker line.  A pytest suite in `integration/tests/` verifies the behaviour.

All OCaml edits are confined to
`/Users/k323lee/git/AI4EC/integration/extern/easycrypt/src/`.
All test files live under `/Users/k323lee/git/AI4EC/integration/tests/`.

---

## Tasks

- [x] 1. Extend `llm_option` record and register the `-premises` CLI flag in `ecOptions.ml`
  - [x] 1.1 Add `llmo_premises : bool` field to the `llm_option` record
    - In the `and llm_option = { … }` record definition (around line 50 of
      `ecOptions.ml`), append `llmo_premises : bool;` after `llmo_upto`.
    - Default value is `false`; OCaml record syntax handles this via the
      absence of the flag in the arg parser.
    - _Requirements: 1.1_
    - _Design: §Components/1a_

  - [x] 1.2 Register the `-premises` spec entry in `xp_commands`
    - In the `"llm"` entry of `specs.xp_commands`, add
      `\`Spec ("premises", \`Flag, "Print all accessible lemmas/axioms after goal state (requires -upto)")`
      immediately after the existing `"upto"` spec line.
    - _Requirements: 1.2_
    - _Design: §Components/1b_

  - [x] 1.3 Populate `llmo_premises` in `llm_options_of_values`
    - In `llm_options_of_values`, add `llmo_premises = get_flag "premises" values;`
      to the record construction expression.
    - _Requirements: 1.3_
    - _Design: §Components/1c_

  - [ ]* 1.4 Write property test for `llmo_premises` flag parsing
    - **Property 1: `llmo_premises` reflects the presence of `-premises`**
    - For any valid `llm` argument vector, `llmo_premises` SHALL equal `true`
      iff `-premises` appears in the vector, and `false` otherwise.
    - Implement as a pytest parametrise test (or Hypothesis `@given`) in
      `integration/tests/test_premises.py` that calls the binary with and without
      `-premises` and checks the flag's downstream effect (separator present /
      absent).
    - **Validates: Requirements 1.1, 1.3**
    - _Design: §Correctness Properties/Property 1_

- [x] 2. Implement `pp_accessible_lemmas` in `ecCommands.ml` and declare it in `ecCommands.mli`
  - [x] 2.1 Add `pp_accessible_lemmas` function to `ecCommands.ml`
    - After the `pp_all_goals` definition (around line 1072), add:
      ```ocaml
      (* -------------------------------------------------------------------- *)
      let pp_accessible_lemmas (fmt : Format.formatter) =
        let env = EcScope.env (current ()) in
        let ax  = EcEnv.Ax.all
                    ~check:(fun _ ax ->
                      EcDecl.is_lemma ax.ax_kind ||
                      EcDecl.is_axiom ax.ax_kind)
                    env in
        let ppe = EcPrinting.PPEnv.ofenv env in
        EcPrinting.pp_by_theory ppe EcPrinting.pp_axiom fmt ax
      ```
    - Do **not** wrap in a try/with — exceptions must propagate (Requirement 2.2).
    - _Requirements: 2.1, 2.2, 2.3_
    - _Design: §Components/2a_

  - [x] 2.2 Declare `pp_accessible_lemmas` in `ecCommands.mli`
    - In the `pp_*` section of `ecCommands.mli` (after `val pp_all_goals`), add:
      ```ocaml
      val pp_accessible_lemmas : Format.formatter -> unit
      ```
    - _Requirements: 2.4_
    - _Design: §Components/2b_

- [x] 3. Checkpoint — build the OCaml changes so far
  - Run `dune build` from `integration/extern/easycrypt/`.
  - The build must succeed with zero errors before proceeding.
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Extend `State.t` and wire `-premises` into `ec.ml`
  - [x] 4.1 Add `premises : bool` field to `State.t`
    - Inside `module State = struct`, extend the `type t = { … }` record with
      `(*---*) premises : bool;` after the `upto` field.
    - _Requirements: 3.1_
    - _Design: §Components/3a_

  - [x] 4.2 Initialise `premises` in the `\`Llm` branch and set `false` in all others
    - In the `` `Llm llmopts -> begin … end `` branch of `let state : State.t`,
      add `; premises = llmopts.llmo_premises` to the record construction.
    - In every other branch (`` `Cli ``, `` `Compile ``, `` `DocGen ``), add
      `; premises = false` so the record remains exhaustive.
    - _Requirements: 3.1_
    - _Design: §Components/3b_

  - [x] 4.3 Add `-premises` without `-upto` validation guard
    - After `let state : State.t = …` is fully constructed, insert:
      ```ocaml
      if state.premises && Option.is_none state.upto then begin
        Format.eprintf
          "easycrypt llm: -premises requires -upto; \
           please supply -upto LINE or -upto LINE:COL@.";
        exit 1
      end;
      ```
    - _Requirements: 1.4_
    - _Design: §Components/3c, §Error Handling_

  - [x] 4.4 Extend the `past_upto` exit block to emit the premises section
    - Locate the existing `if past_upto loc then begin … exit 0 end;` block in
      the main loop and replace it with:
      ```ocaml
      if past_upto loc then begin
        T.finalize terminal;
        EcCommands.pp_current_goal_or_noproof ~all:true Format.std_formatter;
        if state.premises then begin
          Printf.printf "(* --- premises --- *)\n";
          EcCommands.pp_accessible_lemmas Format.std_formatter;
          Format.pp_print_flush Format.std_formatter ()
        end;
        exit 0
      end;
      ```
    - _Requirements: 3.2, 3.3, 3.4_
    - _Design: §Components/3d, §Data Flow_

- [x] 5. Checkpoint — rebuild and smoke-test the patched binary
  - Run `dune build` from `integration/extern/easycrypt/`.
  - Manually invoke `_build/default/src/ec.exe llm -premises` (no `-upto`) and
    verify it exits non-zero with a non-empty stderr message.
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Create the EasyCrypt fixture file and the pytest infrastructure
  - [x] 6.1 Create `integration/tests/fixtures/test_premises.ec`
    - Content must:
      - Open `require import AllCore.`
      - Declare `lemma myfirstlemma (n : int) : n + 0 = n.` with `proof. by ring. qed.`
      - Declare `lemma mysecondlemma (n : int) : 0 + n = n.` with `proof. by ring. qed.`
      - Have a trailing comment line (e.g., `(* STOP_LINE *)`) at line 10 so
        `STOP_LINE = 10` is the stop point used in tests.
    - _Requirements: 5.3_
    - _Design: §Testing/fixtures/test_premises.ec_

  - [x] 6.2 Create `integration/tests/conftest.py`
    - Implement a session-scoped `easycrypt_bin` fixture that:
      1. Returns `pathlib.Path(os.environ["EASYCRYPT"])` if the env var is set.
      2. Otherwise derives the path as
         `REPO_ROOT / "integration" / "extern" / "easycrypt" / "_build" / "default" / "src" / "ec.exe"`,
         where `REPO_ROOT` is `pathlib.Path(__file__).resolve().parents[2]`.
    - _Requirements: 5.2, 5.8_
    - _Design: §Testing/conftest.py_

- [x] 7. Write the pytest test module `integration/tests/test_premises.py`
  - [x] 7.1 Implement example-based tests (happy path and error path)
    - `test_separator_present_and_exit_zero` — invoke with `-upto STOP_LINE
      -premises`; assert exit 0 and separator present (Requirement 5.4).
    - `test_no_separator_without_premises_flag` — invoke with `-upto STOP_LINE`
      only; assert exit 0 and separator absent (Requirement 5.6 / Property 4).
    - `test_premises_without_upto_exits_nonzero` — invoke with `-premises` only
      (no `-upto`); assert exit ≠ 0 and `stderr.strip() != ""` (Requirement 5.7).
    - `test_lemma_names_in_premises_block` — invoke with `-upto STOP_LINE
      -premises`; split stdout on separator; assert `"myfirstlemma"` and
      `"mysecondlemma"` appear in the premises section (Requirement 5.5 /
      Property 6).
    - All subprocess calls must use `timeout=120`.
    - _Requirements: 5.1, 5.4, 5.5, 5.6, 5.7_
    - _Design: §Testing/test_premises.py_

  - [ ]* 7.2 Write property test: separator absent for any stop-line ≥ STOP_LINE (Property 4)
    - **Property 4: Separator is absent when `-premises` is not set**
    - Use `@given(stop_line=st.integers(min_value=STOP_LINE, max_value=STOP_LINE + 10))`
      with `@settings(max_examples=20, deadline=30_000)`.
    - For each generated `stop_line`, invoke `easycrypt llm -upto stop_line
      test_premises.ec` (no `-premises`) and assert separator is absent when
      `returncode == 0`.
    - Tag: `# Feature: easycrypt-premises-export, Property 4: Separator absent without -premises`
    - **Validates: Requirements 3.4, 5.6**
    - _Design: §Correctness Properties/Property 4, §Testing/property-based tests_

  - [x] 7.3 Write property test: stdout splits into exactly two parts (Property 5)
    - **Property 5: stdout splits into exactly two parts on the separator**
    - Use `@given(stop_line=st.integers(min_value=STOP_LINE, max_value=STOP_LINE + 10))`
      with `@settings(max_examples=20, deadline=30_000)`.
    - For each generated `stop_line`, invoke with `-upto stop_line -premises`;
      assert `returncode == 0` and `result.stdout.split(SEPARATOR + "\n", 1)`
      yields exactly 2 parts.
    - Tag: `# Feature: easycrypt-premises-export, Property 5: stdout splits into two parts`
    - **Validates: Requirements 4.1**
    - _Design: §Correctness Properties/Property 5_

- [x] 8. Final checkpoint — run the full pytest suite
  - Run `pytest test_premises.py -v --tb=short` from `integration/tests/`.
  - All tests must pass before this workflow is complete.
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP.
- Each task references specific requirements and design sections for traceability.
- Checkpoints (tasks 3, 5, 8) validate incremental progress via `dune build` or `pytest`.
- Property tests (7.2, 7.3) use Hypothesis; install with `pip install pytest hypothesis`.
- The binary path is `integration/extern/easycrypt/_build/default/src/ec.exe`; override
  with `EASYCRYPT=/path/to/ec.exe pytest …` for alternate builds.
- All OCaml edits are in `integration/extern/easycrypt/src/`; no files outside
  `integration/` may be created or modified.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1", "2.2"] },
    { "id": 2, "tasks": ["1.4", "4.1"] },
    { "id": 3, "tasks": ["4.2", "4.3"] },
    { "id": 4, "tasks": ["4.4"] },
    { "id": 5, "tasks": ["6.1", "6.2"] },
    { "id": 6, "tasks": ["7.1"] },
    { "id": 7, "tasks": ["7.2", "7.3"] }
  ]
}
```
