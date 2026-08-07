from typing import Any, Optional

from dataclasses import dataclass

@dataclass
class AgentToolConfig:
    """Configuration for an agent tool."""
    agent_name: str = None
    tag: str = None
    tool_prompt: str = None

class BaseAgent:
    def __init__(self, config: Any, logger: Optional[Any] = None):
        self.config = config
        self.logger = logger

    def execute(self, query: str):
        raise NotImplementedError("Subclasses must implement this method")
