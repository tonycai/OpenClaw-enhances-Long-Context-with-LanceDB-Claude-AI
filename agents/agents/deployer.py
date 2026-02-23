"""Deployer agent — manages Docker builds, config validation, and environment setup."""

from claude_agent_sdk import AgentDefinition

from config import load_prompt

DEPLOYER_AGENT = AgentDefinition(
    description="Manages deployment of the LanceDB MCP server: Docker builds, configuration validation, environment setup, and .mcp.json generation.",
    prompt=load_prompt("deployer.md"),
    model="sonnet",
    tools=[
        "Read",
        "Grep",
        "Glob",
    ],
)
