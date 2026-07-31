import pytest

from app.core.auth import AuthenticatedContext
from app.domain.strategy import (
    StrategicReportFeedbackCreate,
    StrategyFeedbackValue,
    StrategyMode,
    StrategyRunCreate,
)
from tests.intelligence_helpers import company_profile, competitor
from tests.strategic_helpers import strategy_evidence
from tests.test_strategic_jobs import seeded_persistence, strategic_runtime


@pytest.mark.anyio
async def test_strategy_history_feedback_and_tenant_isolation() -> None:
    persistence = await seeded_persistence()
    _, service, _ = strategic_runtime(persistence)
    context = AuthenticatedContext(user_id="user_test", organization_id="org_test")
    foreign = AuthenticatedContext(user_id="user_foreign", organization_id="org_foreign")
    reports = await service.list_reports(context, cursor=None, limit=25)
    assert reports.items == []
    # Feedback and report reads remain tenant-scoped even when IDs are known.
    with pytest.raises(Exception) as error:
        await service.submit_feedback(
            foreign,
            "copied-report-id",
            StrategicReportFeedbackCreate(feedback=StrategyFeedbackValue.HELPFUL),
        )
    assert getattr(error.value, "code", None) == "STRATEGIC_AGENT_REPORT_NOT_FOUND"


@pytest.mark.anyio
async def test_foreign_run_ids_cannot_be_read_cancelled_or_retried() -> None:
    persistence = await seeded_persistence()
    _, service, _ = strategic_runtime(persistence)
    owner = AuthenticatedContext(user_id="user_test", organization_id="org_test")
    foreign = AuthenticatedContext(user_id="user_foreign", organization_id="org_foreign")
    created = await service.create_run(
        owner,
        StrategyRunCreate(mode=StrategyMode.ASK, query="What changed?"),
    )

    for operation in (
        lambda: service.get_run(foreign, created.run.id),
        lambda: service.cancel_run(foreign, created.run.id),
        lambda: service.retry_run(foreign, created.run.id),
    ):
        with pytest.raises(Exception) as error:
            await operation()
        assert getattr(error.value, "code", None) == "STRATEGIC_AGENT_RUN_NOT_FOUND"

    owner_run = await service.get_run(owner, created.run.id)
    assert owner_run.run.status.value == "queued"


@pytest.mark.anyio
async def test_identical_questions_create_separate_tenant_scoped_request_hashes() -> None:
    persistence = await seeded_persistence()
    persistence.company_profiles.items["company-b"] = company_profile("org_foreign", "company-b")
    persistence.competitors.items["competitor-b"] = competitor(
        "competitor-b",
        organization_id="org_foreign",
        company_profile_id="company-b",
    )
    evidence_b = strategy_evidence(
        "evidence-b",
        organization_id="org_foreign",
        company_profile_id="company-b",
        competitor_id="competitor-b",
    )
    persistence.intelligence_evidence.items[evidence_b.id] = evidence_b
    _, service, _ = strategic_runtime(persistence)
    first = await service.create_run(
        AuthenticatedContext(user_id="user-a", organization_id="org_test"),
        StrategyRunCreate(mode=StrategyMode.ASK, query="What changed?"),
    )
    second = await service.create_run(
        AuthenticatedContext(user_id="user-b", organization_id="org_foreign"),
        StrategyRunCreate(mode=StrategyMode.ASK, query="What changed?"),
    )

    first_record = persistence.strategy_runs.items[first.run.id]
    second_record = persistence.strategy_runs.items[second.run.id]
    assert first.run.id != second.run.id
    assert first_record.organization_id != second_record.organization_id
    assert first_record.request_hash != second_record.request_hash
