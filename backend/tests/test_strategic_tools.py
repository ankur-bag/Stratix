import asyncio

import pytest

from app.agents.errors import StrategicAgentError
from app.agents.tools import (
    APPROVED_STRATEGIC_TOOL_NAMES,
    DISABLED_DEEP_AGENT_TOOLS,
    StrategicToolFactory,
)
from app.core.config import Settings
from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import IntelligenceSearchService
from app.services.vector_store import FakeVectorStore
from tests.fakes import InMemoryPersistence
from tests.intelligence_helpers import company_profile, competitor
from tests.strategic_helpers import strategy_evidence
from tests.test_strategic_agent import agent_context


def tool_factory(persistence: InMemoryPersistence, settings: Settings) -> StrategicToolFactory:
    return StrategicToolFactory(
        persistence,
        IntelligenceSearchService(
            persistence=persistence,
            embeddings=FakeEmbeddingProvider(),
            vectors=FakeVectorStore(),
            settings=settings,
        ),
        settings,
    )


@pytest.mark.anyio
async def test_only_approved_read_only_application_tools_are_exposed() -> None:
    persistence = InMemoryPersistence()
    settings = Settings(app_env="testing", _env_file=None)
    runtime = tool_factory(persistence, settings).runtime(
        agent_context(), lambda: asyncio.sleep(0, result=True)
    )
    tools = tool_factory(persistence, settings).build(runtime, role="primary")
    names = {item.name for item in tools}
    assert names == APPROVED_STRATEGIC_TOOL_NAMES
    assert names.isdisjoint(DISABLED_DEEP_AGENT_TOOLS)
    assert names.isdisjoint({"requests", "http", "mongo", "pinecone", "sql"})


@pytest.mark.anyio
async def test_evidence_details_are_tenant_checked_and_vector_free() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    persistence.competitors.items["competitor-1"] = competitor()
    evidence = strategy_evidence()
    persistence.intelligence_evidence.items[evidence.id] = evidence
    settings = Settings(app_env="testing", _env_file=None)
    factory = tool_factory(persistence, settings)
    runtime = factory.runtime(agent_context(), lambda: asyncio.sleep(0, result=True))
    details = next(
        item
        for item in factory.build(runtime, role="primary")
        if item.name == "get_evidence_details"
    )
    output = await details.ainvoke({"evidence_ids": ["evidence-1"]})
    serialized = str(output).casefold()
    assert "evidence-1" in serialized
    assert "vector" not in serialized
    assert "api_key" not in serialized
    with pytest.raises(StrategicAgentError):
        await details.ainvoke({"evidence_ids": ["foreign-id"]})


@pytest.mark.anyio
async def test_total_and_per_specialist_tool_budgets_are_enforced() -> None:
    persistence = InMemoryPersistence()
    persistence.company_profiles.items["company-1"] = company_profile()
    settings = Settings(
        app_env="testing",
        strategic_agent_max_tool_calls=1,
        strategic_agent_max_tool_calls_per_subagent=1,
        _env_file=None,
    )
    factory = tool_factory(persistence, settings)
    runtime = factory.runtime(agent_context(), lambda: asyncio.sleep(0, result=True))
    company_tool = next(
        item
        for item in factory.build(runtime, role="product-pricing")
        if item.name == "get_company_context"
    )
    await company_tool.ainvoke({})
    with pytest.raises(StrategicAgentError) as error:
        await company_tool.ainvoke({})
    assert error.value.code == "STRATEGIC_AGENT_TOOL_LIMIT_EXCEEDED"
