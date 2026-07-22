from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_authenticated_context
from app.core.auth import AuthenticatedContext
from app.domain.scanning import NumericChange
from app.domain.signals import CompetitorSignal
from tests.fakes import InMemoryPersistence
from tests.test_onboarding import submit, valid_payload
from tests.test_phase4_scanning import monitored_competitor, pricing_source


def use_organization(application: FastAPI, organization_id: str) -> None:
    async def context() -> AuthenticatedContext:
        return AuthenticatedContext(user_id="shared_user", organization_id=organization_id)

    application.dependency_overrides[get_authenticated_context] = context


def test_one_user_can_onboard_two_isolated_companies_with_the_same_idempotency_key(
    client: TestClient,
    application: FastAPI,
    persistence: InMemoryPersistence,
) -> None:
    use_organization(application, "org_alpha")
    alpha_payload = valid_payload()
    alpha_payload["company_name"] = "Alpha Labs"
    alpha = submit(client, key="shared-onboarding-key", payload=alpha_payload)

    use_organization(application, "org_beta")
    beta_payload = valid_payload()
    beta_payload["company_name"] = "Beta Systems"
    beta = submit(client, key="shared-onboarding-key", payload=beta_payload)

    assert alpha.status_code == beta.status_code == 202
    assert (
        persistence.company_profiles.items[alpha.json()["company_profile"]["id"]].organization_id
        == "org_alpha"
    )
    assert (
        persistence.company_profiles.items[beta.json()["company_profile"]["id"]].organization_id
        == "org_beta"
    )
    assert len(persistence.company_profiles.items) == 2
    assert len(persistence.idempotency_records.items) == 2

    beta_status = client.get("/api/v1/onboarding/status")
    beta_company = client.get("/api/v1/company")
    alpha_job_from_beta = client.get(
        f"/api/v1/initialization-jobs/{alpha.json()['initialization_job']['id']}"
    )
    assert beta_status.json()["company_profile_id"] == beta.json()["company_profile"]["id"]
    assert beta_company.json()["company_name"] == "Beta Systems"
    assert alpha_job_from_beta.status_code == 404

    use_organization(application, "org_alpha")
    alpha_company = client.get("/api/v1/company")
    beta_job_from_alpha = client.get(
        f"/api/v1/initialization-jobs/{beta.json()['initialization_job']['id']}"
    )
    assert alpha_company.json()["company_name"] == "Alpha Labs"
    assert beta_job_from_alpha.status_code == 404


def signal_for(
    *, signal_id: str, organization_id: str, competitor_id: str, source_id: str, scan_id: str
) -> CompetitorSignal:
    now = datetime.now(UTC)
    return CompetitorSignal(
        id=signal_id,
        organization_id=organization_id,
        competitor_id=competitor_id,
        source_id=source_id,
        scan_id=scan_id,
        type="pricing_change",
        title="Published pricing changed",
        summary="A published price changed.",
        observed_facts=["Published price changed from 49 to 59."],
        before_excerpt="Pro costs $49.",
        after_excerpt="Pro costs $59.",
        numeric_changes=[
            NumericChange(
                field="Published price",
                before="49",
                after="59",
                currency="USD",
                change_type="increase",
            )
        ],
        importance_score=0.8,
        confidence=0.95,
        source_url="https://competitor.example/pricing",
        previous_snapshot_id=f"{signal_id}-before",
        current_snapshot_id=f"{signal_id}-after",
        observed_at=now,
        created_at=now,
    )


def test_competitors_sources_scans_and_signals_never_cross_organizations(
    client: TestClient,
    application: FastAPI,
    persistence: InMemoryPersistence,
) -> None:
    alpha_competitor = monitored_competitor().model_copy(
        update={
            "id": "competitor-alpha",
            "organization_id": "org_alpha",
            "company_profile_id": "company-alpha",
            "name": "Alpha Rival",
        }
    )
    beta_competitor = monitored_competitor().model_copy(
        update={
            "id": "competitor-beta",
            "organization_id": "org_beta",
            "company_profile_id": "company-beta",
            "name": "Beta Rival",
        }
    )
    alpha_source = pricing_source().model_copy(
        update={
            "id": "source-alpha",
            "organization_id": "org_alpha",
            "competitor_id": "competitor-alpha",
        }
    )
    beta_source = pricing_source().model_copy(
        update={
            "id": "source-beta",
            "organization_id": "org_beta",
            "competitor_id": "competitor-beta",
        }
    )
    persistence.competitors.items.update(
        {alpha_competitor.id: alpha_competitor, beta_competitor.id: beta_competitor}
    )
    persistence.competitor_sources.items.update(
        {alpha_source.id: alpha_source, beta_source.id: beta_source}
    )

    use_organization(application, "org_alpha")
    alpha_scan = client.post(
        "/api/v1/competitors/competitor-alpha/scans",
        headers={"Idempotency-Key": "same-scan-key"},
        json={"scan_type": "rescan", "rediscover_sources": False},
    )
    assert alpha_scan.status_code == 202
    alpha_scan_id = alpha_scan.json()["scan"]["id"]
    alpha_signal = signal_for(
        signal_id="signal-alpha",
        organization_id="org_alpha",
        competitor_id="competitor-alpha",
        source_id="source-alpha",
        scan_id=alpha_scan_id,
    )
    persistence.competitor_signals.items[alpha_signal.id] = alpha_signal

    use_organization(application, "org_beta")
    beta_scan = client.post(
        "/api/v1/competitors/competitor-beta/scans",
        headers={"Idempotency-Key": "same-scan-key"},
        json={"scan_type": "rescan", "rediscover_sources": False},
    )
    assert beta_scan.status_code == 202
    beta_scan_id = beta_scan.json()["scan"]["id"]
    beta_signal = signal_for(
        signal_id="signal-beta",
        organization_id="org_beta",
        competitor_id="competitor-beta",
        source_id="source-beta",
        scan_id=beta_scan_id,
    )
    persistence.competitor_signals.items[beta_signal.id] = beta_signal

    assert [item["id"] for item in client.get("/api/v1/competitors").json()] == ["competitor-beta"]
    assert [
        item["id"]
        for item in client.get("/api/v1/competitors/competitor-beta/sources").json()["items"]
    ] == ["source-beta"]
    assert [
        item["id"]
        for item in client.get("/api/v1/competitors/competitor-beta/scans").json()["items"]
    ] == [beta_scan_id]
    assert [
        item["id"]
        for item in client.get("/api/v1/competitors/competitor-beta/signals").json()["items"]
    ] == ["signal-beta"]
    assert client.get("/api/v1/competitors/competitor-alpha").status_code == 404
    assert client.get(f"/api/v1/competitor-scans/{alpha_scan_id}").status_code == 404
    assert client.get("/api/v1/signals/signal-alpha").status_code == 404

    use_organization(application, "org_alpha")
    assert [item["id"] for item in client.get("/api/v1/competitors").json()] == ["competitor-alpha"]
    assert client.get("/api/v1/competitors/competitor-beta").status_code == 404
    assert client.get(f"/api/v1/competitor-scans/{beta_scan_id}").status_code == 404
    assert client.get("/api/v1/signals/signal-beta").status_code == 404
