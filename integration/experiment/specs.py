"""Registered experiment specifications."""

from __future__ import annotations

from integration.experiment.corpora.joy import JoyCorpus
from integration.experiment.mutations.tactics import TacticMutationSet
from integration.experiment.protocols import ExperimentSpec, ExperimentSpecRegistry

SPECS = ExperimentSpecRegistry()


def register_default_specs(data_dir) -> None:
    from pathlib import Path

    data = Path(data_dir)
    if "joy-tactic-repair" in list(SPECS.names()):
        return
    SPECS.register(
        ExperimentSpec(
            name="joy-tactic-repair",
            corpus=JoyCorpus(data_dir=data),
            mutations=TacticMutationSet(),
        )
    )


register_default_specs("data")
