"""Backwards-compatible aliases for the DeepSeek confirmation gate.

The gate itself is now provider-generic and lives in
:mod:`integration.experiment.paid_confirm`, because Anthropic is also a paid
provider and needs exactly the same human-only confirmation. This module keeps
the original names working for existing callers and tests.

New code should import from ``paid_confirm`` directly.
"""

from __future__ import annotations

from integration.experiment.paid_confirm import (
    AGENT_NEVER_CONFIRM_NOTICE,
    CONFIRMATION_PHRASE,
    confirm_paid_provider_usage as confirm_deepseek_usage,
    format_paid_provider_warning as format_deepseek_warning,
)

__all__ = [
    "AGENT_NEVER_CONFIRM_NOTICE",
    "CONFIRMATION_PHRASE",
    "confirm_deepseek_usage",
    "format_deepseek_warning",
]
