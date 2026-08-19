"""Registered experiment specifications."""

from __future__ import annotations

from integration.experiment.corpora.elgamal import ElGamalCorpus
from integration.experiment.corpora.joy import JoyCorpus
from integration.experiment.corpora.lq1 import LQ1Corpus
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
    if "lq1-broken-repair" not in list(SPECS.names()):
        SPECS.register(
            ExperimentSpec(
                name="lq1-broken-repair",
                corpus=LQ1Corpus(data_dir=data),
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


    if "lq1-changelog-repair" not in list(SPECS.names()):
        # Same corpus as lq1-broken-repair, but replay-until-failure rather
        # than admit-and-reconstruct -- so the agent starts from a VERIFIED
        # prefix and the prefix clamp / prompt note / net_tactics_vs_bootstrap
        # metric have something to act on. `broken_formal` strips every tactic
        # first, so none of that machinery is reachable there.
        #
        # Viable only because `sampling_bound`'s 5 tactics all replay while the
        # proof still does not close: `validate_file` exit 0 means the tactics
        # PARSED, not that the goal was discharged. That gives a 5-tactic
        # prefix plus real work left to do -- exactly the shape the clamp
        # needs. (Before the `is_proof_complete` fix in repair_bootstrap this
        # was miscounted as fully replayed and reported COMPLETE with steps=0.)
        SPECS.register(
            ExperimentSpec(
                name="lq1-changelog-repair",
                corpus=LQ1Corpus(data_dir=data),
                mutations=None,
                replay_bootstrap=ReplayBootstrapConfig(),
            )
        )
    if "joy-changelog-repair" not in list(SPECS.names()):
        # Joy under replay-until-failure. MEASURED, whole corpus, after the
        # comment-stripping fix in `_original_tactics`: all 33 cases replay
        # FULLY with 0 LLM calls (run 20260807T145511Z). The agent is never
        # invoked, so this spec does NOT exercise the prefix clamp -- use
        # `lq1-changelog-repair` or `elgamal-changelog-repair` for that.
        #
        # An intermediate version of this comment claimed the opposite, from a
        # run where `(* ... *)` comments were shredded into fake tactics and
        # cases appeared to break mid-replay. The fix removed 100% of those
        # breaks. Joy carries no version drift, so full replay is CORRECT.
        #
        # Registered anyway as a control: a corpus that should fully replay is
        # a regression detector for EasyCrypt and for the replay path itself.
        SPECS.register(
            ExperimentSpec(
                name="joy-changelog-repair",
                corpus=JoyCorpus(data_dir=data),
                mutations=None,
                replay_bootstrap=ReplayBootstrapConfig(),
            )
        )


register_default_specs("data")
