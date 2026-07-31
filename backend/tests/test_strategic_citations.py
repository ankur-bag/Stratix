from datetime import timedelta

import pytest

from app.agents.errors import StrategicAgentError
from app.agents.validation import validate_strategic_report
from tests.fakes import InMemoryPersistence
from tests.strategic_helpers import strategy_evidence, strategy_output
from tests.test_strategic_agent import agent_context


@pytest.mark.anyio
async def test_citation_validation_reauthorizes_canonical_evidence() -> None:
    persistence = InMemoryPersistence()
    evidence = strategy_evidence()
    persistence.intelligence_evidence.items[evidence.id] = evidence
    validated = await validate_strategic_report(
        report=strategy_output(),
        context=agent_context(),
        additional_evidence_ids=[],
        persistence=persistence,
    )
    assert [item.id for item in validated] == ["evidence-1"]


@pytest.mark.anyio
async def test_foreign_or_unknown_citation_is_rejected() -> None:
    persistence = InMemoryPersistence()
    foreign = strategy_evidence("evidence-foreign", organization_id="org_foreign")
    persistence.intelligence_evidence.items[foreign.id] = foreign
    report = strategy_output().model_copy(
        update={
            "situation_assessment": [
                strategy_output()
                .situation_assessment[0]
                .model_copy(update={"evidence_ids": ["evidence-foreign"]})
            ]
        }
    )
    with pytest.raises(StrategicAgentError) as error:
        await validate_strategic_report(
            report=report,
            context=agent_context(),
            additional_evidence_ids=[],
            persistence=persistence,
        )
    assert error.value.code == "STRATEGIC_AGENT_CITATION_INVALID"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"active": False},
        {"active": False, "deleted": True},
        {"company_profile_id": "company-foreign"},
        {"competitor_id": "competitor-foreign"},
        {"observed_at": agent_context().date_from - timedelta(seconds=1)},
        {"observed_at": agent_context().analysis_as_of + timedelta(seconds=1)},
    ],
    ids=[
        "inactive",
        "deleted",
        "wrong-company",
        "wrong-competitor",
        "before-range",
        "after-cutoff",
    ],
)
async def test_citation_scope_edge_cases_are_rejected(changes: dict[str, object]) -> None:
    persistence = InMemoryPersistence()
    evidence = strategy_evidence().model_copy(update=changes)
    persistence.intelligence_evidence.items[evidence.id] = evidence

    with pytest.raises(StrategicAgentError) as error:
        await validate_strategic_report(
            report=strategy_output(),
            context=agent_context(),
            additional_evidence_ids=[],
            persistence=persistence,
        )

    assert error.value.code == "STRATEGIC_AGENT_CITATION_INVALID"


@pytest.mark.anyio
async def test_report_cannot_attach_a_foreign_competitor_to_valid_evidence() -> None:
    persistence = InMemoryPersistence()
    evidence = strategy_evidence()
    persistence.intelligence_evidence.items[evidence.id] = evidence
    report = strategy_output()
    report = report.model_copy(
        update={
            "situation_assessment": [
                report.situation_assessment[0].model_copy(
                    update={"competitor_ids": ["competitor-foreign"]}
                )
            ]
        }
    )

    with pytest.raises(StrategicAgentError) as error:
        await validate_strategic_report(
            report=report,
            context=agent_context(),
            additional_evidence_ids=[],
            persistence=persistence,
        )

    assert error.value.code == "STRATEGIC_AGENT_OUTPUT_INVALID"
