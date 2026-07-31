from app.core.config import Settings


def test_strategic_resource_limits_are_bounded() -> None:
    settings = Settings(app_env="testing", _env_file=None)
    assert settings.strategic_agent_max_subagents <= 4
    assert (
        settings.strategic_agent_max_tool_calls_per_subagent
        <= settings.strategic_agent_max_tool_calls
    )
    assert (
        settings.strategic_agent_max_evidence_per_tool_call
        <= settings.strategic_agent_max_evidence_items
    )
    assert settings.strategic_agent_max_concurrent_runs_global == 2
