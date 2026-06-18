# EasyCrypt API/CLI Capability Report

## Q1: Does the source expose any API or CLI utility that can output the proof state of a file at a given point in the file?

### Short answer: **Yes — via the `llm` subcommand with `-upto`.**

---

### The `llm` subcommand

EasyCrypt ships a dedicated `llm` subcommand specifically designed for
non-interactive, machine-friendly batch processing. It suppresses
progress bars and does not write `.eco` cache files.

```
easycrypt llm [OPTIONS] FILE.ec
```

#### `-upto LINE` / `-upto LINE:COL`

This is the primary mechanism for extracting proof state at a specific
file location:

```bash
easycrypt llm -upto 42 MyProof.ec
easycrypt llm -upto 42:8 MyProof.ec
```

**Behaviour:**  
EasyCrypt compiles the file command-by-command. As soon as it encounters
a command whose start location is at or past the specified line (and
optional column), it stops, prints the **current proof goal state to
stdout**, and exits with code 0.

If no proof is active at that point, it prints:
```
No active proof.
```

**Output format:** the full human-readable goal display, including all
open sub-goals when the `-all` flag is effective. This is the same
output Proof-General shows in the interactive Emacs mode.

**Implementation** (`src/ec.ml`, ~line 784):
```ocaml
if past_upto loc then begin
  T.finalize terminal;
  EcCommands.pp_current_goal_or_noproof ~all:true Format.std_formatter;
  exit 0
end;
```
`pp_current_goal_or_noproof` calls `EcCoreGoal.opened pf` on the active
proof to get the focused goal plus all remaining sub-goals, then
pretty-prints them via `EcPrinting.pp_goal`.

#### `-lastgoals`

```bash
easycrypt llm -lastgoals MyProof.ec
```

**Behaviour:** processes the entire file. On the first failing tactic,
it prints the **goal state that existed just before the failing tactic**
to stdout, prints the error message to stderr, and exits with code 1.

This is useful for understanding what a failing tactic was supposed to
prove.

**Implementation** (`src/ecTerminal.ml`):
```ocaml
| `ST_Failure e -> begin
    ...
    if lastgoals then
      EcCommands.pp_current_goal_or_noproof ~all:true Format.std_formatter;
    self#_notice ... `Critical msg;
    ...
  end
```

#### `-trace` (compile mode)

The `compile` subcommand has a `-trace` flag:
```bash
easycrypt compile -trace MyProof.ec
```

This saves **all goal states and messages** for every command position
into the `.eco` cache file. The `.eco` format is an OCaml-printable
record containing a `eco_trace` field: a list of
`(char_position, { goals: string list; messages: string })` pairs —
one entry per processed top-level command. This is the richest
machine-accessible record of the entire proof execution.

The `.eco` file is written by `EcEco` and can be parsed back.

#### Emacs / Proof-General interactive mode

```bash
easycrypt cli -emacs
```

This is the protocol used by the EasyCrypt Emacs mode and
Proof-General. After processing each command, it calls
`pp_maybe_current_goal` to emit the current goal in PG format. This is
a real-time interactive mode rather than a batch extraction tool, but it
is a working programmatic interface for incremental proof state queries.

---

### Relevant internal API (OCaml)

`src/ecCommands.mli` exposes:

```ocaml
val pp_current_goal             : ?all:bool -> Format.formatter -> unit
val pp_current_goal_or_noproof  : ?all:bool -> Format.formatter -> unit
val pp_maybe_current_goal       : Format.formatter -> unit
val pp_all_goals                : unit -> string list
```

`pp_all_goals` returns every open sub-goal rendered as a string, one
per list element. This is used internally by `-trace` mode.

The underlying goal types are in `src/ecCoreGoal.mli`:

```ocaml
type pregoal = {
  g_uid   : handle;
  g_hyps  : LDecl.hyps;   (* local hypothesis context *)
  g_concl : form;          (* conclusion formula *)
}
```

---

## Q2: Does the source expose any API/CLI utility that can output all accessible proof premises at a given point?

### Short answer: **No dedicated CLI utility, but the internal API is complete — it would need a small patch to expose it.**

---

### What "accessible premises" means

There are two categories of premises accessible inside a proof at a
given file location:

**A. Local hypotheses** — the named hypotheses in scope for the current
goal (e.g., `H : P`, `n : int`). These are part of the proof goal
state itself and are already printed by `-upto` and `-lastgoals` as
part of the goal display. Each goal's `g_hyps : LDecl.hyps` contains
them:

```ocaml
(* src/ecBaseLogic.ml *)
type local_kind =
  | LD_var    of ty * form option    (* local variable *)
  | LD_mem    of memtype             (* memory variable *)
  | LD_modty  of mty_mr              (* module type *)
  | LD_hyp    of form                (* logical hypothesis *)
  | LD_abs_st of abs_uses            (* abstract statement *)

type hyps = {
  h_tvar  : ty_params;
  h_local : l_local list;   (* (ident * local_kind) list *)
}
```

These are fully available in the `-upto` goal output.

**B. Global lemmas / axioms** — all completed proofs that have been
`require import`-ed into the current scope. These live in the global
`EcEnv.env`. There is no CLI flag to dump them, but the internal API
is:

```ocaml
(* src/ecEnv.mli *)
module Ax : sig
  val all : ?check:(path -> t -> bool) -> ?name:qsymbol -> env -> (path * t) list
  ...
end
```

`EcEnv.Ax.all env` returns every axiom and lemma accessible in the
current environment. The `~check` predicate lets you filter:

```ocaml
(* filter to only completed lemmas *)
EcEnv.Ax.all ~check:(fun _ ax -> EcDecl.is_lemma ax.ax_kind) env
```

`EcSearch.search` (`src/ecSearch.mli`) provides pattern- and
path-based search over the same environment:

```ocaml
val search : EcEnv.env -> search list -> search_result
(* where search_result = (path * EcDecl.axiom) list *)
```

This is what backs the interactive `search` tactic inside a proof.

---

### How to patch EasyCrypt to expose premises via CLI

The cleanest approach is to add a flag to `llm` mode (analogous to
`-upto`) that, after stopping at the target location, also dumps the
global lemma list. Here is a minimal sketch of the patch:

#### 1. Add an option flag in `src/ecOptions.ml`

```ocaml
and llm_option = {
  llmo_input     : string;
  llmo_provers   : prv_options;
  llmo_lastgoals : bool;
  llmo_upto      : (int * int option) option;
  llmo_premises  : bool;    (* <-- new flag *)
}
```

Register it in `xp_commands`:
```ocaml
`Spec  ("premises", `Flag, "With -upto: also print all accessible global lemmas")
```

#### 2. Implement the printer in `src/ecCommands.ml`

```ocaml
let pp_accessible_lemmas stream =
  let scope = current () in
  let env   = S.env scope in
  let ppe   = EcPrinting.PPEnv.ofenv env in
  let lemmas =
    EcEnv.Ax.all
      ~check:(fun _ ax -> EcDecl.is_lemma ax.ax_kind)
      env
  in
  List.iter (fun (p, ax) ->
    Format.fprintf stream "%s : %a@\n%!"
      (EcPath.tostring p)
      (EcPrinting.pp_form ppe) ax.EcDecl.ax_spec
  ) lemmas
```

#### 3. Call it in `src/ec.ml` at the `-upto` exit point

```ocaml
if past_upto loc then begin
  T.finalize terminal;
  EcCommands.pp_current_goal_or_noproof ~all:true Format.std_formatter;
  if state.premises then                       (* new *)
    EcCommands.pp_accessible_lemmas Format.std_formatter;
  exit 0
end;
```

#### Alternative: use `EcSearch.search` for filtered dumps

If you only need premises relevant to the current goal's conclusion,
`EcSearch.search` can be used to search by pattern or by path prefix,
returning a ranked `(path * axiom) list`. This is already called
internally by the `search` tactic in proofs. A patch here would be
lighter: just expose the `search` tactic result programmatically.

---

### Alternative without patching: use `-upto` + interactive search

If patching is not desirable in the short term, the `cli -emacs` mode
combined with scripted stdin feeding can simulate it: connect to the
process, step through commands to the desired point, then issue a
`search ...` or `print` command and capture the output. This is how
Proof-General already works. It is more brittle but requires no source
changes.

---

## Summary

| Capability | Available? | How |
|---|---|---|
| Proof state at file position (stdout) | **Yes** | `easycrypt llm -upto LINE FILE.ec` |
| Proof state on failure (stdout) | **Yes** | `easycrypt llm -lastgoals FILE.ec` |
| All goal states for every command | **Yes** | `easycrypt compile -trace FILE.ec` (in `.eco`) |
| Local hypotheses in scope | **Yes** | Included in goal output above |
| Global lemmas / all accessible premises | **No CLI** | Internal: `EcEnv.Ax.all env`; small patch needed |
| Pattern-based lemma search | **No CLI** | Internal: `EcSearch.search`; exposed as tactic only |
