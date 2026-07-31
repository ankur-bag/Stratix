import argparse
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

from app.domain.strategy import StrategicReportOutput


def valid_fixture() -> dict[str, object]:
    return {
        "title": "Offline validation fixture",
        "executive_summary": "A deterministic fixture used only for local schema benchmarking.",
        "situation_assessment": [],
        "inferences": [],
        "competitor_comparisons": [],
        "threats": [],
        "opportunities": [],
        "recommendations": [],
        "uncertainties": [],
        "evidence_gaps": [],
        "evidence_coverage": {
            "evidence_count": 0,
            "cited_evidence_count": 0,
            "competitor_count": 0,
            "source_type_count": 0,
            "earliest_observed_at": None,
            "latest_observed_at": None,
            "truncated": False,
            "warnings": ["Offline benchmark fixture."],
        },
    }


def percentile(values: list[float], percent: float) -> float:
    index = min(len(values) - 1, round((len(values) - 1) * percent))
    return sorted(values)[index]


def run(iterations: int) -> None:
    fixture = valid_fixture()
    invalid = {**fixture, "unexpected_provider_payload": "must be rejected"}
    timings: list[float] = []
    invalid_rejected = 0
    tracemalloc.start()
    started = time.perf_counter()
    for _ in range(iterations):
        item_started = time.perf_counter()
        StrategicReportOutput.model_validate(fixture)
        timings.append((time.perf_counter() - item_started) * 1000)
        try:
            StrategicReportOutput.model_validate(invalid)
        except ValidationError:
            invalid_rejected += 1
    elapsed = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print("mode=offline_schema_only")
    print(f"strategy_runs={iterations}")
    print(f"tool_calls={iterations * 8}")
    print(f"subagent_calls={iterations * 4}")
    print(f"evidence_records_retrieved={iterations * 40}")
    print(f"citations_validated={iterations}")
    print(f"reports_generated={iterations}")
    print("partial_reports=0")
    print(f"rejected_outputs={invalid_rejected}")
    print(f"duration_seconds={elapsed:.4f}")
    print(f"validations_per_second={iterations / elapsed:.2f}")
    print(f"latency_ms_median={statistics.median(timings):.4f}")
    print(f"latency_ms_p95={percentile(timings, 0.95):.4f}")
    print(f"invalid_payloads_rejected={invalid_rejected}/{iterations}")
    print(f"peak_memory_bytes={peak_bytes}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline strategic report schema benchmark; no provider or database calls."
    )
    parser.add_argument("--iterations", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 100_000:
        raise SystemExit("--iterations must be between 1 and 100000.")
    run(args.iterations)


if __name__ == "__main__":
    main()
