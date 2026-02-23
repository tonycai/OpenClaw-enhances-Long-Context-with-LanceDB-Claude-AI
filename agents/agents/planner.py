"""Planner agent — implementation plans, task decomposition, and explore-first methodology."""

from claude_agent_sdk import AgentDefinition

from config import load_prompt

PLANNER_AGENT = AgentDefinition(
    description="Creates implementation plans by exploring the codebase first, then designing step-by-step approaches with task decomposition.",
    prompt=load_prompt("planner.md"),
    model="opus",
    tools=[
        "mcp__lancedb-code__search_code",
        "Read",
        "Grep",
        "Glob",
        "Task",
    ],
)
