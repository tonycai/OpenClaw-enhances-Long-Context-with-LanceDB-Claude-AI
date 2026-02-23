"""Memory agent — persistent cross-session memory using LanceDB."""

from claude_agent_sdk import AgentDefinition

from config import load_prompt

MEMORY_AGENT = AgentDefinition(
    description="Provides persistent cross-session memory: stores decisions, context, and knowledge in LanceDB for semantic recall across sessions.",
    prompt=load_prompt("memory.md"),
    model="sonnet",
    tools=[
        "mcp__lancedb-code__search_code",
        "mcp__lancedb-code__index_files",
        "mcp__lancedb-code__index_status",
        "mcp__lancedb-code__switch_project",
        "mcp__lancedb-code__list_projects",
        "Read",
        "Write",
    ],
)
