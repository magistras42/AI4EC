# EasyCrypt Premises Export — Integration

This directory contains the patched EasyCrypt fork and the Python integration
test suite for the `easycrypt-premises-export` feature.

The feature adds a `-premises` flag to the `easycrypt llm` subcommand. When
combined with `-upto LINE`, it prints all globally accessible lemmas and axioms
to stdout after the proof-goal state, separated by `(* --- premises --- *)`.

---

## Directory Layout

```
integration/
├── extern/
│   └── easycrypt/          # EasyCrypt fork (OCaml source, patched)
└── tests/
    ├── conftest.py          # pytest fixture: resolves easycrypt binary path
    ├── test_premises.py     # integration test module
    └── fixtures/
        └── test_premises.ec # EasyCrypt fixture file used by tests
```

---

## Prerequisites

### 1. OCaml and opam (for building EasyCrypt)

EasyCrypt requires **OCaml >= 4.08 and < 5.0** and is built with **dune**.
The recommended setup uses [opam](https://opam.ocaml.org/).

**macOS (Homebrew):**
```bash
brew install opam
```

**Debian / Ubuntu:**
```bash
apt-get install opam
```

**Arch:**
```bash
pacman -S opam
```

**Fedora / openSUSE:**
```bash
dnf install opam
```

After installing opam, initialise it:
```bash
opam init
eval $(opam env)
```

### 2. EasyCrypt OCaml dependencies

Create a dedicated opam switch and install EasyCrypt's dependencies:

```bash
# Create an isolated switch (OCaml < 5.0 required)
opam switch --empty create easycrypt
opam switch set easycrypt
eval $(opam env)

# Pin the upstream EasyCrypt package so opam knows its dependency set
opam pin -yn add easycrypt https://github.com/EasyCrypt/easycrypt.git

# Install all required OCaml libraries
opam install --deps-only easycrypt
```

Key OCaml libraries installed by the above command include:
- `dune` (build system)
- `menhir` (parser generator)
- `batteries` >= 3 (standard library extension)
- `pcre` >= 7
- `zarith` >= 1.10
- `why3` >= 1.8, < 1.9

### 3. SMT solver (required by EasyCrypt at runtime)

At least one Why3-compatible SMT solver is needed to check proofs. The
simplest option is Alt-Ergo via opam:

```bash
opam install alt-ergo.2.6.0
```

Other compatible solvers: Z3, CVC4, CVC5. See
[EasyCrypt README](extern/easycrypt/README.md#compatibility) for the full
compatibility matrix.

### 4. Python 3 and pytest (for the test suite)

```bash
pip install pytest hypothesis
```

`hypothesis` is only required for the optional property-based tests (tasks 7.2
and 7.3). The example-based tests (task 7.1) need only `pytest`.

---

## Building the Patched EasyCrypt Binary

```bash
cd integration/extern/easycrypt
dune build 2>&1
```

On success the binary is at:
```
integration/extern/easycrypt/_build/default/src/ec.exe
```

Subsequent builds are incremental — only changed modules are recompiled.

---

## Running the Test Suite

```bash
cd integration/tests
pytest test_premises.py -v --tb=short
```

To point the tests at a custom binary (e.g., a system-installed EasyCrypt):
```bash
EASYCRYPT=/path/to/ec.exe pytest test_premises.py -v --tb=short
```

If `EASYCRYPT` is not set, the tests resolve the binary from the dune build
output path automatically.

---

## Quick Reference

| Step | Command |
|---|---|
| Install opam (macOS) | `brew install opam` |
| Initialise opam | `opam init && eval $(opam env)` |
| Create switch | `opam switch --empty create easycrypt && opam switch set easycrypt` |
| Install OCaml deps | `opam pin -yn add easycrypt https://github.com/EasyCrypt/easycrypt.git && opam install --deps-only easycrypt` |
| Install SMT solver | `opam install alt-ergo.2.6.0` |
| Install Python deps | `pip install pytest hypothesis` |
| Build binary | `cd integration/extern/easycrypt && dune build` |
| Run tests | `cd integration/tests && pytest test_premises.py -v` |
