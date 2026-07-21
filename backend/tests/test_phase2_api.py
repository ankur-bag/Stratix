import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_authenticated_context
from app.core.auth import AuthenticatedContext
from app.domain.onboarding import JobStage, JobStatus
from app.services.extraction import DeterministicCompanyIntelligenceExtractor
from tests.fakes import InMemoryPersistence, RecordingBackgroundExecutor
from tests.test_extraction import snapshot
from tests.test_onboarding import submit


def complete_original_job(persistence: InMemoryPersistence, job_id: str) -> None:
    job = persistence.initialization_jobs.items[job_id]
    persistence.initialization_jobs.items[job_id] = job.model_copy(
        update={
            "status": JobStatus.SUCCEEDED,
            "stage": JobStage.COMPLETED,
            "progress_percent": 100,
        }
    )


def test_reinitialize_is_idempotent_and_preserves_company(
    client: TestClient,
    persistence: InMemoryPersistence,
    background_executor: RecordingBackgroundExecutor,
) -> None:
    original = submit(client).json()
    complete_original_job(persistence, original["initialization_job"]["id"])
    company_id = original["company_profile"]["id"]
    source_id = next(iter(persistence.sources.items))

    first = client.post(
        "/api/v1/company/reinitialize",
        headers={"Idempotency-Key": "reinitialize-key-001"},
    )
    second = client.post(
        "/api/v1/company/reinitialize",
        headers={"Idempotency-Key": "reinitialize-key-001"},
    )

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert len(persistence.company_profiles.items) == 1
    assert next(iter(persistence.company_profiles.items)) == company_id
    assert len(persistence.sources.items) == 1
    assert next(iter(persistence.sources.items)) == source_id
    assert len(persistence.initialization_jobs.items) == 2
    assert background_executor.scheduled[-1] == (
        first.json()["initialization_job_id"],
        "org_test",
    )


def test_reinitialize_rejects_another_active_job(client: TestClient) -> None:
    submit(client)

    response = client.post(
        "/api/v1/company/reinitialize",
        headers={"Idempotency-Key": "reinitialize-key-002"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INITIALIZATION_ALREADY_RUNNING"


def test_snapshot_and_intelligence_endpoints_are_organization_scoped(
    client: TestClient,
    persistence: InMemoryPersistence,
    application: FastAPI,
) -> None:
    created = submit(client).json()
    company_id = created["company_profile"]["id"]
    company = persistence.company_profiles.items[company_id]
    source_id = next(iter(persistence.sources.items))
    stored_snapshot = snapshot().model_copy(
        update={"company_profile_id": company_id, "source_id": source_id}
    )
    asyncio.run(persistence.snapshots.create_many([stored_snapshot]))
    profile = asyncio.run(
        DeterministicCompanyIntelligenceExtractor().extract(
            company_profile=company, snapshots=[stored_snapshot]
        )
    )
    asyncio.run(persistence.company_intelligence_profiles.save(profile))

    snapshots_response = client.get("/api/v1/company/snapshots")
    profile_response = client.get("/api/v1/company/intelligence-profile")

    assert snapshots_response.status_code == 200
    assert [item["id"] for item in snapshots_response.json()] == [stored_snapshot.id]
    assert profile_response.status_code == 200
    assert profile_response.json()["source_snapshot_ids"] == [stored_snapshot.id]

    async def other_organization() -> AuthenticatedContext:
        return AuthenticatedContext(user_id="other", organization_id="other_org")

    application.dependency_overrides[get_authenticated_context] = other_organization
    isolated_snapshots = client.get("/api/v1/company/snapshots")
    isolated_profile = client.get("/api/v1/company/intelligence-profile")

    assert isolated_snapshots.status_code == 404
    assert isolated_snapshots.json()["error"]["code"] == "COMPANY_PROFILE_NOT_FOUND"
    assert isolated_profile.status_code == 404
    assert isolated_profile.json()["error"]["code"] == "COMPANY_PROFILE_NOT_FOUND"
