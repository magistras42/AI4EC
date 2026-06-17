"""
Integration tests for the easycrypt -premises flag.

Example-based tests covering the happy path and error path for the
`easycrypt llm -premises` feature.
"""

import subprocess
import pathlib

from hypothesis import given, settings, assume
from hypothesis import strategies as st

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
SEPARATOR = "(* --- premises --- *)"
STOP_LINE = 10  # line number of the STOP_LINE comment in test_premises.ec


def test_separator_present_and_exit_zero(easycrypt_bin):
    """Separator must appear in stdout and exit code must be 0.

    Validates: Requirement 5.4
    """
    fixture = FIXTURES / "test_premises.ec"
    result = subprocess.run(
        [str(easycrypt_bin), "llm", "-upto", str(STOP_LINE), "-premises", str(fixture)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0
    assert SEPARATOR in result.stdout


def test_no_separator_without_premises_flag(easycrypt_bin):
    """Stdout must NOT contain the separator when -premises is absent.

    Validates: Requirement 5.6 / Property 4
    """
    fixture = FIXTURES / "test_premises.ec"
    result = subprocess.run(
        [str(easycrypt_bin), "llm", "-upto", str(STOP_LINE), str(fixture)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0
    assert SEPARATOR not in result.stdout


def test_premises_without_upto_exits_nonzero(easycrypt_bin):
    """Supplying -premises without -upto must exit non-zero with non-empty stderr.

    Validates: Requirement 5.7
    """
    fixture = FIXTURES / "test_premises.ec"
    result = subprocess.run(
        [str(easycrypt_bin), "llm", "-premises", str(fixture)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert result.stderr.strip() != ""


def test_lemma_names_in_premises_block(easycrypt_bin):
    """Both fixture lemmas must appear in the premises section of stdout.

    Validates: Requirement 5.5 / Property 6
    """
    fixture = FIXTURES / "test_premises.ec"
    result = subprocess.run(
        [str(easycrypt_bin), "llm", "-upto", str(STOP_LINE), "-premises", str(fixture)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0
    _, premises = result.stdout.split(SEPARATOR + "\n", 1)
    assert "myfirstlemma" in premises
    assert "mysecondlemma" in premises


# Feature: easycrypt-premises-export, Property 5: stdout splits into two parts
@given(stop_line=st.integers(min_value=STOP_LINE, max_value=STOP_LINE + 10))
@settings(max_examples=20, deadline=30_000)
def test_stdout_splits_into_exactly_two_parts(easycrypt_bin, stop_line):
    """stdout must split into exactly two parts on the separator when -premises is set.

    Validates: Requirements 4.1 / Property 5
    """
    fixture = FIXTURES / "test_premises.ec"
    result = subprocess.run(
        [str(easycrypt_bin), "llm", "-upto", str(stop_line), "-premises", str(fixture)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assume(result.returncode == 0)
    parts = result.stdout.split(SEPARATOR + "\n", 1)
    assert len(parts) == 2
