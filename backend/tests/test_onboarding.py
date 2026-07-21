from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_authenticated_context
from app.core.auth import AuthenticatedContext
from tests.fakes import InMemoryPersistence, RecordingBackgroundExecutor


def valid_payload() -> dict[str, Any]:
    return {
        "company_name": "  Example AI  ",
        "website_url": "HTTPS://Example.com/",
        "one_line_description": "AI voice infrastructure for businesses",
        "detailed_description": "A platform for building production voice agents.",
        "industry": "Artificial Intelligence",
        "sub_industry": "Voice AI",
        "business_stage": "seed",
        "business_model": "saas",
        "headquarters_country": "India",
        "target_regions": ["India", "United States", "india", ""],
        "target_customer_segments": ["Startups", "SMBs"],
        "product_name": "Example Voice",
        "product_categories": ["Voice AI", "Developer Tools"],
        "primary_use_cases": ["Customer support automation", "Outbound calling"],
        "key_features": ["Real-time voice agents", "API integrations"],
        "differentiators": ["Low-latency multilingual calls"],
        "keywords": ["voice AI", "conversational AI", "Voice AI"],
        "known_competitors": ["Competitor One", "Competitor Two"],
        "strategic_goals": ["Track competitor pricing", "Detect feature launches"],
    }


def submit(
    client: TestClient,
    *,
    key: str = "onboarding-key-001",
    payload: dict[str, Any] | None = None,
) -> Any:
    return client.post(
        "/api/v1/onboarding/company",
        headers={"Idempotency-Key": key},
        json=payload or valid_payload(),
    )


def test_successful_onboarding_persists_all_records_and_returns_202(
    client: TestClient,
    persistence: InMemoryPersistence,
    background_executor: RecordingBackgroundExecutor,
) -> None:
    response = submit(client)

    assert response.status_code == 202
    body = response.json()
    assert body["company_profile"]["company_name"] == "Example AI"
    assert body["company_profile"]["website_url"] == "https://example.com"
    assert body["company_profile"]["onboarding_status"] == "submitted"
    assert body["initialization_job"]["status"] == "queued"
    assert body["initialization_job"]["progress_percent"] == 0
    assert len(persistence.company_profiles.items) == 1
    assert len(persistence.sources.items) == 1
    assert len(persistence.initialization_jobs.items) == 1
    assert len(persistence.idempotency_records.items) == 1
    source = next(iter(persistence.sources.items.values()))
    assert source.url == "https://example.com"
    assert source.status == "pending"
    assert background_executor.scheduled == [(body["initialization_job"]["id"], "org_test")]
    company = next(iter(persistence.company_profiles.items.values()))
    assert company.target_regions == ["India", "United States"]
    assert company.keywords == ["voice AI", "conversational AI"]


def test_same_idempotency_key_returns_original_without_duplicates(
    client: TestClient,
    persistence: InMemoryPersistence,
    background_executor: RecordingBackgroundExecutor,
) -> None:
    first = submit(client)
    second = submit(client)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert len(persistence.company_profiles.items) == 1
    assert len(persistence.sources.items) == 1
    assert len(persistence.initialization_jobs.items) == 1
    assert len(background_executor.scheduled) == 1


def test_idempotency_key_cannot_be_reused_for_different_payload(
    client: TestClient,
) -> None:
    assert submit(client).status_code == 202
    payload = valid_payload()
    payload["company_name"] = "Different Company"

    response = submit(client, payload=payload)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_second_onboarding_key_is_rejected(client: TestClient) -> None:
    assert submit(client).status_code == 202

    response = submit(client, key="onboarding-key-002")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COMPANY_PROFILE_ALREADY_EXISTS"


def test_company_retrieval_update_status_and_job_retrieval(
    client: TestClient, persistence: InMemoryPersistence
) -> None:
    created = submit(client).json()
    job_id = created["initialization_job"]["id"]

    company_response = client.get("/api/v1/company")
    update_response = client.patch(
        "/api/v1/company",
        json={
            "company_name": "  Updated AI ",
            "website_url": "https://updated.example.com/",
            "keywords": ["AI", "ai", "Strategy"],
        },
    )
    status_response = client.get("/api/v1/onboarding/status")
    job_response = client.get(f"/api/v1/initialization-jobs/{job_id}")

    assert company_response.status_code == 200
    assert company_response.json()["organization_id"] == "org_test"
    assert update_response.json()["company_name"] == "Updated AI"
    assert update_response.json()["website_url"] == "https://updated.example.com"
    assert update_response.json()["keywords"] == ["AI", "Strategy"]
    assert next(iter(persistence.sources.items.values())).url == "https://updated.example.com"
    assert status_response.json()["has_company_profile"] is True
    assert status_response.json()["latest_job"]["id"] == job_id
    assert job_response.status_code == 200
    assert job_response.json()["id"] == job_id


def test_company_and_job_queries_are_organization_scoped(
    client: TestClient, application: FastAPI
) -> None:
    job_id = submit(client).json()["initialization_job"]["id"]

    async def other_organization() -> AuthenticatedContext:
        return AuthenticatedContext(user_id="other_user", organization_id="other_org")

    application.dependency_overrides[get_authenticated_context] = other_organization

    company_response = client.get("/api/v1/company")
    job_response = client.get(f"/api/v1/initialization-jobs/{job_id}")

    assert company_response.status_code == 404
    assert company_response.json()["error"]["code"] == "COMPANY_PROFILE_NOT_FOUND"
    assert job_response.status_code == 404
    assert job_response.json()["error"]["code"] == "INITIALIZATION_JOB_NOT_FOUND"


def test_invalid_websites_return_stable_domain_error(client: TestClient) -> None:
    for url in (
        "ftp://example.com",
        "http://user:password@example.com",
        "http://127.0.0.1",
        "http://10.0.0.3",
        "http://169.254.169.254/latest/meta-data",
        "not a url",
        "https://bad host.example.com",
        "https://service.internal",
    ):
        payload = valid_payload()
        payload["website_url"] = url
        response = submit(client, payload=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_WEBSITE_URL"


def test_blank_company_and_missing_required_fields_are_rejected(
    client: TestClient,
) -> None:
    blank = valid_payload()
    blank["company_name"] = "   "
    missing = valid_payload()
    missing.pop("strategic_goals")

    blank_response = submit(client, payload=blank)
    missing_response = submit(client, payload=missing)

    assert blank_response.status_code == 422
    assert blank_response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert missing_response.status_code == 422
    assert missing_response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_invalid_idempotency_key_uses_stable_error(client: TestClient) -> None:
    response = submit(client, key="short")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"
