"""
ACSDataAgent - wraps the ACS agent from scripts/tests/acs/acs_agent.py
so it can be used as a tool agent within the AIR Director workflow.

This class adheres to the BaseAgent interface and is meant to be
registered in STRING_TO_AGENT_MAP for use by the Director.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

from ..base import BaseAgent, AgentToolConfig
from ...utils.logger import Logger
from .packages.acs.acs_agent import ACSAgent


@dataclass
class ACSDataAgentConfig(AgentToolConfig):
    """
    Configuration for ACSDataAgent.

    Fields inherited from AgentToolConfig:
        agent_name: registry key used in STRING_TO_AGENT_MAP
        tag: tag used by the Director to invoke this agent (<tag>...</tag>)
        tool_prompt: short description shown to the Director about when to use it
    """
    output_dir: str = "./acs_downloads"
    log_dir: str = "logs"
    tool_prompt: str = """Description: Helper agent that downloads data from the U.S. Census American Community Survey (ACS).

DATA AVAILABLE (2009-2024, default 2024):
- Demographics: Population, age, sex, race, Hispanic origin, citizenship
- Economics: Household/family income, poverty status, earnings, employment, industry
- Education: Educational attainment, school enrollment
- Housing: Home values, rent, occupancy, costs, tenure (own/rent)
- Transportation: Commute times, means of transportation
- Social: Marital status, fertility, veteran status, health insurance, internet access
- Language: Languages spoken, English proficiency

GEOGRAPHIC LEVELS:
- Nation, State, County, City/Place, Census Tract, Block Group
- Also: Congressional Districts, School Districts, ZIP Code Areas (ZCTA)
- Use "*" for all (e.g., all states, all counties in a state)

COMMON TABLE PREFIXES:
- B01xxx: Age/Sex/Population  - B15xxx: Education      - B19xxx: Income
- B02xxx: Race                - B17xxx: Poverty        - B23xxx: Employment
- B25xxx: Housing             - B08xxx: Commuting

Guidelines:
1. Always specify geography: state required, add county/city if needed
   - "Michigan", "MI", "Wayne County, MI", "Detroit, Michigan"
2. Search before downloading: "Find tables about median income" or "What is table B19013?"
3. Be specific: "median household income" not just "income"
4. For quick facts: "Get quick stats for California"
5. For comparisons: "Compare poverty rates between Michigan, Ohio, and Indiana"
6. Clarify ambiguous places: "Portland, Oregon" not just "Portland"
7. Note that the agent can only download data, it cannot analyze it or run any code on it. Therefore, you must provide precise instructions for which data you want to download. 
8. The more precise your prompt, the more likely you are to get the data you want.
9. If your query is not successful the first time, you should retry it until you get your desired data.

Outputs: 
- CSV files with estimates and optional margins of error (MOE)
- JSON data dictionary saved alongside each CSV (explains all variable codes)

IMPORTANT: If the agent does not download data, you can reprompt it or request explanations.
TIP: You should request bulk downloads as to not exhaust your queries on downloading invididual tables from the ACS agent.
IMPORTANT: You have no data if you don't first request any from the ACS agent! You must request it and confirm download before analyzing anything."""
    # Additional hooks for future specialization (dataset/year) can be added here.


class ACSDataAgent(BaseAgent):
    """
    Thin wrapper that forwards queries to the ACSAgent defined in scripts/tests/acs/acs_agent.py.
    """

    def __init__(self, config: ACSDataAgentConfig, logger: Optional[Any] = None):
        super().__init__(config, logger)
        self.config = config
        self.logger = logger if logger is not None else Logger("acs_data_agent", log_dir=config.log_dir)

        # Resolve to absolute path to ensure files are saved in the correct location
        # regardless of the current working directory when the agent executes
        output_path = Path(config.output_dir).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        self.logger.log_action("acs_output_dir_init", f"ACS agent output directory: {output_path}")
        self._acs_agent = ACSAgent(output_dir=output_path)

    def _run_coro(self, coro):
        """
        Run an async coroutine safely whether or not a loop is already running.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        return asyncio.run(coro)

    def execute(self, query: str, **kwargs) -> str:
        """
        Execute an ACS query. Returns the assistant's Markdown/text response.
        """
        try:
            result = self._run_coro(self._acs_agent.chat(query))
            self.logger.log_response(result, "acs_data_agent_result")
            return result
        except Exception as e:
            err_msg = f"ACSDataAgent error: {str(e)}"
            try:
                self.logger.log_error(err_msg)
            except Exception:
                pass
            raise

    def cleanup(self):
        """No-op hook for symmetry with other agents."""
        return None


