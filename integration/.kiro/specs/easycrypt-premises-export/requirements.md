# Requirements Document

## Introduction

This feature adds a `-premises` flag to the EasyCrypt `llm` subcommand. When combined with
`-upto LINE`, the flag causes the tool to emit all globally accessible lemmas and axioms
(in addition to the current proof goal state) to stdout before exiting. This gives LLM-based
agents a complete picture of the available proof vocabulary at a given point in a file,
enabling them to select relevant lemmas without having to search interactively.

All source changes are confined to the EasyCrypt fork at
`integration/extern/easycrypt` (relative to the repo root at
`/Users/k323lee/git/AI4EC`), and all test files live under `integration/tests`.

## Glossary

- **EasyCrypt**: The proof assistant being patched; the binary produced after building the fork.
- **`llm` subcommand**: The existing EasyCrypt batch-mode subcommand designed for non-interactive,
  machine-friendly use.
- **`-upto LINE`**: An existing `llm` flag that compiles the given `.ec` file up to (not
  including) the command starting at `LINE` and prints the current proof goal state to stdout.
- **`-premises`**: The new flag introduced by this feature.
- **Global lemma / axiom**: A completed `lemma` or `axiom` declaration that has been loaded into
  the current `EcEnv.env` environment (i.e., is accessible via `EcEnv.Ax.all`).
- **Premises block**: The section of stdout output that lists all accessible global lemmas/axioms,
  printed after the goal state block.
- **`EcEnv.Ax.all`**: The internal OCaml function `EcEnv.Ax.all : ?check:(path -> t -> bool) ->
  ?name:qsymbol -> env -> (path * t) list` that enumerates every axiom/lemma in the environment.
- **`EcDecl.is_lemma`**: The predicate `EcDecl.is_lemma : axiom_kind -> bool` that returns `true`
  when an entry is a proved lemma (not an unproved axiom declaration).
- **`EcDecl.is_axiom`**: The predicate `EcDecl.is_axiom : axiom_kind -> bool` that returns `true`
  when an entry is an axiom declaration (unproved assumption).
- **`EcPrinting.pp_axiom`**: The existing OCaml pretty-printer for a single `(path * axiom)` pair.
- **`EcPrinting.pp_by_theory`**: The existing OCaml function that groups a list of `(path * t)` pairs
  by theory and pretty-prints them.
- **Separator line**: The fixed string `(* --- premises --- *)` (followed by a newline) printed to
  stdout between the goal state block and the premises block so that downstream parsers can split
  the two sections.
- **Python test**: A pytest test module placed under `integration/tests/` that invokes the patched
  `easycrypt` binary and asserts on the stdout output.

---

## Requirements

### Requirement 1: New `-premises` CLI flag on the `llm` subcommand

**User Story:** As an LLM agent, I want to pass `-premises` alongside `-upto LINE` when invoking
`easycrypt llm`, so that I receive a list of globally accessible lemmas/axioms without having to
query the tool interactively.

#### Acceptance Criteria

1. THE `ecOptions` module SHALL expose a `llmo_premises : bool` field in the `llm_option` record,
   with a default value of `false` when the flag is absent from the command line.
2. THE `ecOptions` module SHALL register a `-premises` spec entry (kind `Flag`) in the `llm`
   command group of `xp_commands`, so that `easycrypt llm -premises ...` is accepted by the
   argument parser without an error.
3. WHEN `llm_options_of_values` is called, THE `ecOptions` module SHALL populate `llmo_premises`
   from the `"premises"` flag in the parsed values map.
4. IF `-premises` is supplied without `-upto`, THEN THE `llm` subcommand SHALL write a message to
   stderr that identifies the unsupported flag combination and states that `-premises` requires
   `-upto`, terminate immediately, and exit with a non-zero exit code.
5. WHEN both `-premises` and `-upto LINE` are supplied and the file compiles up to `LINE` without
   error, THE `llm` subcommand SHALL write the premises block to stdout and exit with code 0.

---

### Requirement 2: `pp_accessible_lemmas` printer in `ecCommands`

**User Story:** As a developer integrating the patch, I want a single well-defined function that
formats all accessible global lemmas and axioms from the current scope, so that the output can be
reused in future extensions.

#### Acceptance Criteria

1. THE `ecCommands` module SHALL expose a function
   `pp_accessible_lemmas : Format.formatter -> unit`.
2. WHEN `pp_accessible_lemmas` is called, THE function SHALL retrieve all globally accessible
   axioms and lemmas from the current EasyCrypt environment using `EcEnv.Ax.all` with a predicate
   that returns `true` for entries where `EcDecl.is_lemma ax.ax_kind` is `true` OR
   `EcDecl.is_axiom ax.ax_kind` is `true`, and `false` for all other entries.
   IF `EcEnv.Ax.all` raises an exception, THE function SHALL propagate the exception to the caller
   without swallowing it.
3. WHEN `pp_accessible_lemmas` is called, THE function SHALL format the resulting list using the
   same pretty-printing path as the existing interactive `pr_axioms` command — specifically by
   invoking `EcPrinting.pp_by_theory` with `EcPrinting.pp_axiom` as the per-item printer.
4. THE `ecCommands.mli` interface file SHALL declare
   `val pp_accessible_lemmas : Format.formatter -> unit`.

---

### Requirement 3: Wire `-premises` into the `-upto` exit point in `ec.ml`

**User Story:** As an LLM agent, I want the premises block to appear in stdout immediately after
the goal state when I use `-upto` with `-premises`, so that I receive both in a single invocation.

#### Acceptance Criteria

1. THE `State` record (local module `State` in `ec.ml`) SHALL include a `premises : bool` field
   that is set to `llmopts.llmo_premises` when the `Llm` branch of the command-dispatch match
   initialises the state, and defaults to `false` in all other branches.
2. WHEN `past_upto` returns `true` for a command location, THE main loop SHALL call
   `EcCommands.pp_current_goal_or_noproof ~all:true Format.std_formatter` to emit the goal state,
   regardless of the value of `state.premises`.
3. WHEN `past_upto` returns `true` and `state.premises` is `true`, THE main loop SHALL then print
   the exact bytes `(* --- premises --- *)\n` to stdout, then call
   `EcCommands.pp_accessible_lemmas Format.std_formatter`, and finally call `exit 0`.
4. WHEN `past_upto` returns `true` and `state.premises` is `false`, THE main loop SHALL call
   `exit 0` immediately after the goal-state output, without printing the separator line or any
   premises, producing output identical to the unpatched tool (no additional bytes on stdout).

---

### Requirement 4: Premises output format

**User Story:** As an LLM agent, I want the premises block to be machine-parseable, so that I can
reliably split the goal state section from the premises section and further filter the list.

#### Acceptance Criteria

1. WHEN `-premises` is active and `past_upto` fires, THE complete stdout SHALL consist of the
   goal-state section followed by the premises section. Splitting the full stdout on the first
   occurrence of the byte sequence `(* --- premises --- *)\n` SHALL yield exactly two substrings:
   the goal-state string (before the separator) and the premises string (after the separator).
2. WHEN at least one accessible lemma or axiom exists in the environment, THE premises string SHALL
   be non-empty and SHALL be formatted using `EcPrinting.pp_by_theory` with
   `EcPrinting.pp_axiom`, grouping entries by theory name.
3. WHEN no accessible lemmas or axioms exist in the environment (e.g., `-boot` was used and no
   theories were loaded), THE premises string SHALL contain no characters after the separator
   line's terminating newline.
4. THE stdout output SHALL be encoded in UTF-8, consistent with the rest of EasyCrypt stdout
   output.
5. IF the body of any premise would contain the exact byte sequence `(* --- premises --- *)`,
   THE output SHALL transform or escape that occurrence so that the separator line remains unique
   in the full stdout, ensuring that splitting on the first occurrence yields exactly two
   substrings.
6. IF the runtime output stream does not support UTF-8 encoding, THE `ec.ml` process SHALL exit
   with a non-zero exit code and SHALL NOT emit any partial premises output to stdout.

---

### Requirement 5: Python integration tests

**User Story:** As a developer maintaining the patch, I want automated Python tests that invoke the
patched binary and verify the premises output, so that regressions are caught immediately.

#### Acceptance Criteria

1. THE test suite SHALL be located at `integration/tests/test_premises.py` (relative to the repo
   root) and SHALL be runnable with `pytest` from the `integration/tests` directory.
2. THE test suite SHALL include a `conftest.py` fixture (in the same directory) that resolves the
   path to the `easycrypt` binary: first by reading the `EASYCRYPT` environment variable if set,
   then by computing the path `integration/extern/easycrypt/_build/default/src/ec.exe` relative
   to the repo root.
3. THE test suite SHALL contain a fixture `.ec` file located at
   `integration/tests/fixtures/test_premises.ec` that (a) uses `require import AllCore`, (b)
   declares at least two named lemmas with proofs, and (c) has a line number `STOP_LINE` beyond
   the last lemma declaration, all of which are committed to the repository.
4. WHEN the test invokes `easycrypt llm -upto STOP_LINE -premises test_premises.ec`, THE test
   SHALL assert that stdout contains the separator line `(* --- premises --- *)` and that the
   process exits with code 0, with a subprocess timeout of at most 120 seconds.
5. WHEN the test invokes `easycrypt llm -upto STOP_LINE -premises test_premises.ec`, THE test
   SHALL assert that the names of both lemmas declared in the fixture file appear in the premises
   block (the portion of stdout after the separator line).
6. WHEN the test invokes `easycrypt llm -upto STOP_LINE test_premises.ec` (without `-premises`),
   THE test SHALL assert that stdout does NOT contain the separator line `(* --- premises --- *)`,
   and that the process exits with code 0.
7. WHEN the test invokes `easycrypt llm -premises test_premises.ec` (without `-upto`), THE test
   SHALL assert that the process exits with a non-zero exit code and that stderr is non-empty.
8. THE test module SHALL resolve the path to the `easycrypt` binary using the fixture defined in
   Criterion 2 so that tests are portable across build environments without hardcoded absolute
   paths.
