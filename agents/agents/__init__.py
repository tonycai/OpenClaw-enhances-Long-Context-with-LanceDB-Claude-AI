"""Agent definitions for the LanceDB code search team."""

from agents.deployer import DEPLOYER_AGENT
from agents.devops import DEVOPS_AGENT
from agents.indexer import INDEXER_AGENT
from agents.memory import MEMORY_AGENT
from agents.planner import PLANNER_AGENT
from agents.qa import QA_AGENT
from agents.reviewer import REVIEWER_AGENT
from agents.searcher import SEARCHER_AGENT
from agents.security import SECURITY_AGENT

ALL_AGENTS = {
    "indexer": INDEXER_AGENT,
    "searcher": SEARCHER_AGENT,
    "reviewer": REVIEWER_AGENT,
    "qa": QA_AGENT,
    "deployer": DEPLOYER_AGENT,
    "memory": MEMORY_AGENT,
    "security": SECURITY_AGENT,
    "devops": DEVOPS_AGENT,
    "planner": PLANNER_AGENT,
}

__all__ = [
    "INDEXER_AGENT",
    "SEARCHER_AGENT",
    "REVIEWER_AGENT",
    "QA_AGENT",
    "DEPLOYER_AGENT",
    "MEMORY_AGENT",
    "SECURITY_AGENT",
    "DEVOPS_AGENT",
    "PLANNER_AGENT",
    "ALL_AGENTS",
]
