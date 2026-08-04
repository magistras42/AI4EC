"""Registered experiment specifications."""

from __future__ import annotations

from integration.experiment.corpora.elgamal import ElGamalCorpus
from integration.experiment.corpora.joy import JoyCorpus
from integration.experiment.informal import InformalConfig
from integration.experiment.mutations.tactics import TacticMutationSet
from integration.experiment.protocols import (
    BrokenFormalConfig,
    ExperimentSpec,
    ExperimentSpecRegistry,
    ReplayBootstrapConfig,
)

SPECS = ExperimentSpecRegistry()


def register_default_specs(data_dir) -> None:
    from pathlib import Path

    data = Path(data_dir)
    if "joy-tactic-repair" not in list(SPECS.names()):
        SPECS.register(
            ExperimentSpec(
                name="joy-tactic-repair",
                corpus=JoyCorpus(data_dir=data),
                mutations=TacticMutationSet(),
            )
        )
    if "joy-informal-repair" not in list(SPECS.names()):
        SPECS.register(
            ExperimentSpec(
                name="joy-informal-repair",
                corpus=JoyCorpus(data_dir=data),
                mutations=None,
                informal=InformalConfig(),
            )
        )
    if "elgamal-broken-repair" not in list(SPECS.names()):
        SPECS.register(
            ExperimentSpec(
                name="elgamal-broken-repair",
                corpus=ElGamalCorpus(data_dir=data),
                mutations=None,
                broken_formal=BrokenFormalConfig(),
            )
        )
    if "elgamal-changelog-repair" not in list(SPECS.names()):
        # Same corpus as elgamal-broken-repair, but replays the original
        # tactic script tactic-by-tactic (preserving whatever prefix still
        # applies) instead of admitting everything and reconstructing from
        # scratch -- see integration/experiment/repair_bootstrap.py.
        #
        # Both version endpoints are left unset so they are DETECTED per trial
        # (integration/agent/ec_version.py). The target is genuinely knowable
        # -- ask the installed fork's source tree -- and the previous
        # hardcoded r2026.07 was one release ahead of what is actually built
        # here. The source is not knowable for this 2020-era corpus and
        # detection reports that honestly rather than guessing, which leaves
        # the range fail-open exactly as before.
        SPECS.register(
            ExperimentSpec(
                name="elgamal-changelog-repair",
                corpus=ElGamalCorpus(data_dir=data),
                mutations=None,
                replay_bootstrap=ReplayBootstrapConfig(),
            )
        )


register_default_specs("data")
