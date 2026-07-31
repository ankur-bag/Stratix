import pytest

from app.core.auth import AuthenticatedContext
from app.domain.strategy import StrategicReportFeedbackCreate, StrategyFeedbackValue
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
