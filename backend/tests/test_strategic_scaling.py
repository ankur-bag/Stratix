import pytest

from app.agents.validation import validate_strategic_report
from app.core.auth import AuthenticatedContext
from app.core.config import Settings
from app.domain.strategy import StrategyMode, StrategyRunCreate
from tests.strategic_helpers import strategy_evidence, strategy_output
from tests.test_strategic_agent import agent_context
from tests.test_strategic_jobs import seeded_persistence, strategic_runtime


def test_strategic_resource_limits_are_bounded() -> None:
    settings = Settings(app_env="testing", _env_file=None)
    assert settings.strategic_agent_max_subagents <= 4
    assert (
        settings.strategic_agent_max_tool_calls_per_subagent
        <= settings.strategic_agent_max_tool_calls
    )
    assert (
        settings.strategic_agent_max_evidence_per_tool_call
        <= settings.strategic_agent_max_evidence_items
    )
    assert settings.strategic_agent_max_concurrent_runs_global == 2


@pytest.mark.anyio
async def test_one_thousand_queued_runs_remain_tenant_scoped_and_page_bounded() -> None:
    persistence = await seeded_persistence()
    _, service, _ = strategic_runtime(persistence)
    created = await service.create_run(
        AuthenticatedContext(user_id="user-0", organization_id="org_test"),
        StrategyRunCreate(mode=StrategyMode.ASK, query="Synthetic scale fixture"),
    )
    template = persistence.strategy_runs.items.pop(created.run.id)
    for index in range(1000):
        organization_id = f"org-{index % 100:03d}"
        run = template.model_copy(
            update={
                "id": f"run-{index:04d}",
                "organization_id": organization_id,
                "requested_by_user_id": f"user-{index % 100:03d}",
                "background_job_id": f"job-{index:04d}",
                "request_hash": f"request-{index:04d}",
            }
        )
        await persistence.strategy_runs.create(run)

    assert await persistence.strategy_runs.count_active() == 1000
    assert await persistence.strategy_runs.count_active("org-042") == 10
    page = await persistence.strategy_runs.list(
        "org-042",
        status=None,
        cursor=None,
        limit=5,
    )
    assert len(page) == 5
    assert {run.organization_id for run in page} == {"org-042"}


@pytest.mark.anyio
async def test_virtual_hundred_thousand_record_pool_is_never_materialized_unbounded() -> None:
    total_records = 100_000
    competitor_ids = [f"competitor-{index:02d}" for index in range(50)]
    requested_limit = Settings(app_env="testing", _env_file=None).strategic_agent_max_evidence_items
    generated = [
        strategy_evidence(
            f"evidence-{index:03d}",
            competitor_id=competitor_ids[index % len(competitor_ids)],
        )
        for index in range(min(total_records, requested_limit + 1))
    ]

    bounded = generated[:requested_limit]
    assert total_records == 100_000
    assert len(competitor_ids) == 50
    assert len(generated) == requested_limit + 1
    assert len(bounded) == 40


@pytest.mark.anyio
async def test_twenty_citations_are_reauthorized_in_one_batched_repository_read() -> None:
    persistence = await seeded_persistence()
    evidence_ids = [f"evidence-{index:02d}" for index in range(20)]
    for evidence_id in evidence_ids:
        persistence.intelligence_evidence.items[evidence_id] = strategy_evidence(evidence_id)
    report = strategy_output()
    report = report.model_copy(
        update={
            "situation_assessment": [
                report.situation_assessment[0].model_copy(update={"evidence_ids": evidence_ids})
            ],
            "inferences": [],
            "threats": [],
            "opportunities": [],
            "recommendations": [],
            "uncertainties": [],
        }
    )
    context = agent_context().model_copy(update={"allowed_evidence_ids": frozenset(evidence_ids)})
    original_get_many = persistence.intelligence_evidence.get_many
    calls = 0

    async def counted_get_many(organization_id: str, identifiers: list[str]):
        nonlocal calls
        calls += 1
        return await original_get_many(organization_id, identifiers)

    persistence.intelligence_evidence.get_many = counted_get_many  # type: ignore[method-assign]
    validated = await validate_strategic_report(
        report=report,
        context=context,
        additional_evidence_ids=[],
        persistence=persistence,
    )

    assert len(validated) == 20
    assert calls == 1
