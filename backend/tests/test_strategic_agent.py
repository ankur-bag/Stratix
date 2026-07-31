import asyncio

import pytest

from app.agents.errors import StrategicAgentError
from app.agents.strategic import DisabledStrategicAgent, FakeStrategicAgent
from app.domain.strategy import StrategicAgentContext, StrategicAgentRequest, StrategyMode
from tests.strategic_helpers import NOW, strategy_output


def agent_context() -> StrategicAgentContext:
    return StrategicAgentContext(
        strategy_run_id="run-1",
        organization_id="org_test",
        company_profile_id="company-1",
        requested_by_user_id="user_test",
        analysis_as_of=NOW,
        date_from=NOW,
        date_to=NOW,
        allowed_competitor_ids=frozenset({"competitor-1"}),
        allowed_evidence_ids=frozenset({"evidence-1"}),
        company_context_source_ids=frozenset({"company-profile:company-1"}),
        initial_context="{}",
    )


def agent_request() -> StrategicAgentRequest:
    return StrategicAgentRequest(
        strategy_run_id="run-1",
        mode=StrategyMode.ASK,
        query="What changed?",
        competitor_ids=["competitor-1"],
        date_from=NOW,
        date_to=NOW,
    )


@pytest.mark.anyio
async def test_fake_agent_returns_strict_output_and_records_context() -> None:
    agent = FakeStrategicAgent(strategy_output(), tool_call_count=3, subagent_count=2)
    result = await agent.analyze(
        request=agent_request(),
        context=agent_context(),
        active_check=lambda: asyncio.sleep(0, result=True),
    )
    assert result.report.title == "Pricing response brief"
    assert result.tool_call_count == 3
    assert agent.calls[0][1].organization_id == "org_test"


@pytest.mark.anyio
async def test_disabled_agent_uses_stable_error() -> None:
    with pytest.raises(StrategicAgentError) as error:
        await DisabledStrategicAgent().analyze(
            request=agent_request(),
            context=agent_context(),
            active_check=lambda: asyncio.sleep(0, result=True),
        )
    assert error.value.code == "STRATEGIC_AGENT_DISABLED"
