import argparse
import asyncio
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import get_settings
from app.repositories.mongo import MongoPersistence


def print_configuration() -> None:
    settings = get_settings()
    configured = bool(settings.strategic_agent_model and settings.gemini_api_key)
    print(f"enabled={str(settings.strategic_agent_enabled).lower()}")
    print(f"configured={str(configured).lower()}")
    print(f"provider={settings.strategic_agent_provider}")
    print(f"model={settings.strategic_agent_model or 'not_configured'}")
    print(f"agent_version={settings.strategic_agent_version}")
    print(f"prompt_version={settings.strategic_agent_prompt_version}")
    print(f"tool_schema_version={settings.strategic_agent_tool_schema_version}")
    print(f"max_subagents={settings.strategic_agent_max_subagents}")
    print(f"max_tool_calls={settings.strategic_agent_max_tool_calls}")
    print(f"max_iterations={settings.strategic_agent_max_iterations}")
    print(f"timeout_seconds={settings.strategic_agent_timeout_seconds}")


async def print_runs(organization_id: str) -> None:
    settings = get_settings()
    persistence = MongoPersistence(settings)
    await persistence.start()
    try:
        if not await persistence.ping():
            raise SystemExit("MongoDB is unavailable.")
        runs = await persistence.strategy_runs.list(
            organization_id,
            status=None,
            cursor=None,
            limit=1000,
        )
        reports = await persistence.strategy_reports.list(
            organization_id,
            cursor=None,
            limit=1000,
        )
        statuses = Counter(run.status.value for run in runs)
        print(f"organization_id={organization_id}")
        print(f"run_count={len(runs)}")
        print(f"report_count={len(reports)}")
        for name, count in sorted(statuses.items()):
            print(f"runs_{name}={count}")
        print(
            "deterministic_fallback_reports="
            f"{sum(report.deterministic_fallback for report in reports)}"
        )
        print(
            "reports_with_recommendations="
            f"{sum(bool(report.recommendations) for report in reports)}"
        )
    finally:
        await persistence.close()


async def live_provider_smoke() -> None:
    settings = get_settings()
    if os.getenv("STRATIX_LIVE_STRATEGIC_TESTS", "").lower() != "true":
        raise SystemExit(
            "Set STRATIX_LIVE_STRATEGIC_TESTS=true to acknowledge a live provider call."
        )
    if not settings.strategic_agent_model or not settings.gemini_api_key:
        raise SystemExit("The strategic Gemini model is not configured.")
    model = ChatGoogleGenerativeAI(
        model=settings.strategic_agent_model,
        api_key=settings.gemini_api_key,
        temperature=0,
        retries=0,
        request_timeout=min(settings.strategic_agent_timeout_seconds, 60),
        include_thoughts=False,
    )
    response = await model.ainvoke("Connectivity check only. Return exactly the single word READY.")
    content = response.content if isinstance(response.content, str) else str(response.content)
    print(f"provider_responded={str(bool(content.strip())).lower()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only strategic-agent configuration and run diagnostics."
    )
    parser.add_argument(
        "command",
        choices=["status", "configuration", "runs"],
        nargs="?",
        default="status",
    )
    parser.add_argument("--organization-id")
    parser.add_argument(
        "--live-provider-smoke",
        action="store_true",
        help="Make one explicit no-data Gemini connectivity call.",
    )
    args = parser.parse_args()
    print_configuration()
    if args.command == "runs":
        if not args.organization_id:
            raise SystemExit("--organization-id is required for run diagnostics.")
        asyncio.run(print_runs(args.organization_id))
    if args.live_provider_smoke:
        asyncio.run(live_provider_smoke())


if __name__ == "__main__":
    main()
