import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.errors import StrategicAgentError
from app.agents.strategic import (
    DeepStrategicAgent,
    DisabledStrategicAgent,
    FakeStrategicAgent,
)
from app.core.config import Settings
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


class StubToolFactory:
    def runtime(self, context: StrategicAgentContext, active_check: Any) -> Any:
        del context, active_check
        return SimpleNamespace(
            tool_call_count=0,
            role_call_counts={},
            additional_evidence_ids=set(),
            authorized_evidence_ids=set(),
        )

    def build(self, runtime: Any, *, role: str) -> list[Any]:
        del runtime, role
        return []


class StubGraph:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        del config
        self.calls.append(payload)
        return self.responses.pop(0)


def deep_agent_settings(*, repairs: int = 1) -> Settings:
    return Settings(
        app_env="testing",
        strategic_agent_enabled=True,
        strategic_agent_model="gemini-test",
        strategic_agent_max_repair_attempts=repairs,
        gemini_api_key="test-key",
        _env_file=None,
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


@pytest.mark.anyio
async def test_deep_agent_allows_exactly_one_structured_output_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = StubGraph(
        [
            {"structured_response": {"invalid": True}},
            {"structured_response": strategy_output()},
        ]
    )
    monkeypatch.setattr("app.agents.strategic._register_secure_google_harness", lambda: None)
    monkeypatch.setattr("app.agents.strategic.ChatGoogleGenerativeAI", lambda **_: object())
    monkeypatch.setattr("app.agents.strategic.create_deep_agent", lambda **_: graph)
    agent = DeepStrategicAgent(deep_agent_settings(), StubToolFactory())  # type: ignore[arg-type]

    result = await agent.analyze(
        request=agent_request(),
        context=agent_context(),
        active_check=lambda: asyncio.sleep(0, result=True),
    )

    assert result.report.title == "Pricing response brief"
    assert len(graph.calls) == 2
    repair_text = graph.calls[1]["messages"][0]["content"]
    assert "only repair attempt" in repair_text
    assert "evidence-1" in repair_text


@pytest.mark.anyio
async def test_deep_agent_rejects_a_second_malformed_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = StubGraph(
        [
            {"structured_response": {"invalid": True}},
            {"structured_response": {"still_invalid": True}},
        ]
    )
    monkeypatch.setattr("app.agents.strategic._register_secure_google_harness", lambda: None)
    monkeypatch.setattr("app.agents.strategic.ChatGoogleGenerativeAI", lambda **_: object())
    monkeypatch.setattr("app.agents.strategic.create_deep_agent", lambda **_: graph)
    agent = DeepStrategicAgent(deep_agent_settings(), StubToolFactory())  # type: ignore[arg-type]

    with pytest.raises(StrategicAgentError) as error:
        await agent.analyze(
            request=agent_request(),
            context=agent_context(),
            active_check=lambda: asyncio.sleep(0, result=True),
        )

    assert error.value.code == "STRATEGIC_AGENT_OUTPUT_INVALID"
    assert len(graph.calls) == 2
