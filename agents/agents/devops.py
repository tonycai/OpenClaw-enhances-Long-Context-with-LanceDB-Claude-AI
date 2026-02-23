"""DevOps agent — health monitoring, diagnostics, and token optimization."""

from claude_agent_sdk import AgentDefinition

from config import load_prompt

DEVOPS_AGENT = AgentDefinition(
    description="Monitors infrastructure health, runs diagnostics, and optimizes token usage for the LanceDB MCP server and agent team.",
    prompt=load_prompt("devops.md"),
    model="haiku",
    tools=[
        "mcp__lancedb-code__index_status",
        "mcp__lancedb-code__list_projects",
        "Bash",
        "Read",
        "Grep",
    ],
)
