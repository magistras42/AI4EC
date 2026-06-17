import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]  # …/integration/tests -> …/AI4EC


@pytest.fixture(scope="session")
def easycrypt_bin():
    """Resolve the easycrypt binary path.

    Prefers the EASYCRYPT environment variable; falls back to the dune build
    output path relative to the repository root.
    """
    env_path = os.environ.get("EASYCRYPT")
    if env_path:
        return pathlib.Path(env_path)
    return (
        REPO_ROOT
        / "integration"
        / "extern"
        / "easycrypt"
        / "_build"
        / "default"
        / "src"
        / "ec.exe"
    )
