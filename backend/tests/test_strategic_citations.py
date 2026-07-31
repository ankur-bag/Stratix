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
