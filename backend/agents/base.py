"""
Agent base class and AgentRegistry.

所有智能体 MUST 继承 Agent 基类并实现 async run(state) → state 接口。
智能体之间通过明确定义的输入/输出契约通信（Constitution II）。

AgentRegistry provides agent discovery and retrieval by name.
"""

from abc import ABC, abstractmethod

from backend.utils.logging import get_logger

logger = get_logger(__name__)


class Agent(ABC):
    """
    Base class for all research agents.

    Each agent has:
    - name: Unique identifier (e.g., "planner", "retriever")
    - description: Human-readable purpose description
    - run(state) → state: Core execution method; receives and returns the shared state dict
    """

    name: str
    description: str

    @abstractmethod
    async def run(self, state: dict) -> dict:
        """
        Execute the agent's core logic.

        Args:
            state: Shared research workflow state dict (contains all stage inputs/outputs).

        Returns:
            Updated state dict with this agent's outputs merged in.
        """
        ...


class AgentRegistry:
    """
    Registry for agent discovery and retrieval.

    Agents are registered by name. The registry enables:
    - Lookup by name for orchestration
    - Listing all available agents
    - Future: dynamic agent loading from plugins
    """

    _agents: dict[str, Agent] = {}

    @classmethod
    def register(cls, agent: Agent) -> None:
        """Register an agent instance in the registry. Raises ValueError on duplicate."""
        if agent.name in cls._agents:
            logger.error("agent_already_registered", name=agent.name)
            raise ValueError(f"Agent '{agent.name}' is already registered")
        cls._agents[agent.name] = agent
        logger.info("agent_registered", name=agent.name)

    @classmethod
    def get(cls, name: str) -> Agent:
        """Retrieve an agent by name. Raises KeyError if not found."""
        if name not in cls._agents:
            available = list(cls._agents.keys()) or ["(none)"]
            logger.error("agent_not_found", requested=name, available=available)
            raise KeyError(f"Agent '{name}' not found. Available: {available}")
        return cls._agents[name]

    @classmethod
    def list_all(cls) -> list[dict[str, str]]:
        """List all registered agents with name and description."""
        return [
            {"name": a.name, "description": a.description}
            for a in cls._agents.values()
        ]

    @classmethod
    def clear(cls) -> None:
        """Remove all registered agents (useful for testing)."""
        logger.warning("agent_registry_cleared", count=len(cls._agents))
        cls._agents.clear()
