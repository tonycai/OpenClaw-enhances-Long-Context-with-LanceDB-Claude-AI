"""Security agent — vulnerability scanning, secrets detection, and blast radius assessment."""

from claude_agent_sdk import AgentDefinition

from config import load_prompt

SECURITY_AGENT = AgentDefinition(
    description="Conducts security audits: vulnerability scanning, secrets detection, input validation review, dependency analysis, and blast radius assessment.",
    prompt=load_prompt("security.md"),
    model="opus",
    tools=[
        "mcp__lancedb-code__search_code",
        "Read",
        "Grep",
        "Glob",
    ],
)
