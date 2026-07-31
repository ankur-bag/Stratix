from app.agents.validation import cited_evidence_ids
from tests.strategic_helpers import strategy_output


def test_synthetic_evaluation_has_complete_citation_allowlist() -> None:
    report = strategy_output()
    assert set(cited_evidence_ids(report)) == {"evidence-1"}
    assert all(item.evidence_ids for item in report.situation_assessment)
    assert all(item.evidence_ids for item in report.recommendations)
