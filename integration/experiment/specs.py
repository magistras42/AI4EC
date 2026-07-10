"""Registered experiment specifications."""

from __future__ import annotations

from integration.experiment.corpora.joy import JoyCorpus
from integration.experiment.informal import InformalConfig
from integration.experiment.mutations.tactics import TacticMutationSet
from integration.experiment.protocols import ExperimentSpec, ExperimentSpecRegistry

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


register_default_specs("data")
