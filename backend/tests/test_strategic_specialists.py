from app.agents.prompts import SPECIALIST_PROMPTS
from app.agents.strategic import DeepStrategicAgent
from app.agents.tools import DISABLED_DEEP_AGENT_TOOLS, StrategicToolFactory
from app.core.config import Settings
from app.services.embeddings import FakeEmbeddingProvider
from app.services.intelligence import IntelligenceSearchService
from app.services.vector_store import FakeVectorStore
from tests.fakes import InMemoryPersistence


def test_specialist_roster_is_bounded_and_non_recursive() -> None:
    assert len(SPECIALIST_PROMPTS) == 4
    assert set(SPECIALIST_PROMPTS) == {
        "product-pricing",
        "engineering-product-signals",
        "gtm-positioning",
        "opportunity-risk",
    }
    assert all("read-only tools" in prompt for _, prompt in SPECIALIST_PROMPTS.values())


def test_deep_agent_declares_secure_tool_surface() -> None:
    settings = Settings(app_env="testing", _env_file=None)
    persistence = InMemoryPersistence()
    intelligence = IntelligenceSearchService(
        persistence=persistence,
        embeddings=FakeEmbeddingProvider(),
        vectors=FakeVectorStore(),
        settings=settings,
    )
    agent = DeepStrategicAgent(settings, StrategicToolFactory(persistence, intelligence, settings))
    assert agent.disabled_tools == DISABLED_DEEP_AGENT_TOOLS
    assert agent.enabled_tools == {
        "task",
        "get_company_context",
        "get_competitor_profiles",
        "search_intelligence_evidence",
        "get_evidence_details",
        "get_competitor_timeline",
        "get_intelligence_trends",
        "get_intelligence_correlations",
        "compare_competitors",
    }
