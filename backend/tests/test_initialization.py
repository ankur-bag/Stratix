import asyncio

from app.core.auth import AuthenticatedContext
from app.domain.onboarding import CompanyOnboardingRequest, JobStage, JobStatus
from app.services.initialization import CompanyInitializationRunner
from app.services.onboarding import CompanyOnboardingService
from tests.fakes import InMemoryPersistence, RecordingBackgroundExecutor
from tests.test_onboarding import valid_payload


def create_submission(
    persistence: InMemoryPersistence,
) -> tuple[str, str]:
    service = CompanyOnboardingService(persistence, RecordingBackgroundExecutor())
    response = asyncio.run(
        service.submit(
            context=AuthenticatedContext(user_id="user_test", organization_id="org_test"),
            idempotency_key="runner-test-key",
            request=CompanyOnboardingRequest.model_validate(valid_payload()),
        )
    )
    return response.company_profile.id, response.initialization_job.id


def test_runner_completes_deterministic_transitions(
    persistence: InMemoryPersistence,
) -> None:
    company_id, job_id = create_submission(persistence)

    asyncio.run(
        CompanyInitializationRunner(persistence).run(job_id=job_id, organization_id="org_test")
    )

    job = persistence.initialization_jobs.items[job_id]
    company = persistence.company_profiles.items[company_id]
    source = next(iter(persistence.sources.items.values()))
    assert job.status == JobStatus.SUCCEEDED
    assert job.stage == JobStage.COMPLETED
    assert job.progress_percent == 100
    assert persistence.initialization_jobs.stage_history == [
        JobStage.VALIDATING_COMPANY,
        JobStage.CREATING_COMPANY_SOURCE,
        JobStage.PREPARING_INTELLIGENCE_WORKSPACE,
        JobStage.READY_FOR_DISCOVERY,
        JobStage.COMPLETED,
    ]
    assert company.onboarding_status == "ready"
    assert company.workspace_metadata == {"discovery_state": "ready"}
    assert source.status == "ready"


def test_runner_records_safe_failure_transitions(
    persistence: InMemoryPersistence,
) -> None:
    company_id, job_id = create_submission(persistence)
    persistence.sources.items.clear()

    asyncio.run(
        CompanyInitializationRunner(persistence).run(job_id=job_id, organization_id="org_test")
    )

    job = persistence.initialization_jobs.items[job_id]
    company = persistence.company_profiles.items[company_id]
    assert job.status == JobStatus.FAILED
    assert job.stage == JobStage.FAILED
    assert job.error_code == "INITIALIZATION_FAILED"
    assert job.error_message == "Company workspace initialization could not be completed."
    assert company.onboarding_status == "failed"
