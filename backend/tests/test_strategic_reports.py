import pytest
from pydantic import ValidationError

from app.agents.validation import deterministic_fallback_report
from app.domain.strategy import StrategicClaim
from tests.strategic_helpers import strategy_evidence, strategy_output


def test_substantive_claim_requires_citation() -> None:
    with pytest.raises(ValidationError):
        StrategicClaim(statement="Unsupported claim", evidence_ids=[], confidence="low")


def test_fallback_has_no_fake_recommendations() -> None:
    fallback = deterministic_fallback_report(
        evidence=[strategy_evidence()],
        reason="The model was unavailable.",
        competitor_count=1,
        truncated=False,
    )
    assert fallback.recommendations == []
    assert "No AI-generated strategic recommendations" in fallback.executive_summary
    assert fallback.situation_assessment[0].evidence_ids == ["evidence-1"]


def test_report_schema_rejects_unknown_fields() -> None:
    payload = strategy_output().model_dump()
    payload["hidden_reasoning"] = "secret"
    with pytest.raises(ValidationError):
        type(strategy_output()).model_validate(payload)
