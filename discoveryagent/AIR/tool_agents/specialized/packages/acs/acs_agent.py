"""
ACS Agent - AI-powered American Community Survey data assistant.

Uses Claude claude-haiku-4-5-20251101 with tool use to efficiently query and download ACS data.
Unlike traditional multi-step LLM pipelines, this agent uses a single conversation
with tool calling for maximum efficiency.

Usage:
    # Interactive mode
    python acs_agent.py
    
    # Single query mode
    python acs_agent.py --query "What is the population of Michigan?"
    
    # With custom output directory
    python acs_agent.py --output ./data

Environment Variables:
    ANTHROPIC_API_KEY: Required - Your Anthropic API key
    CENSUS_API_KEY: Optional - Census API key for higher rate limits
    ACS_CACHE_PATH: Optional - Path to cache directory
"""

import os
import json
import asyncio
from pathlib import Path
from typing import Optional, Any
from dataclasses import dataclass, field

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table


# =============================================================================
# COST TRACKING
# =============================================================================

# Pricing per million tokens
INPUT_COST_PER_MILLION = 1.00   # $1 per million input tokens
OUTPUT_COST_PER_MILLION = 5.00  # $5 per million output tokens


@dataclass
class UsageTracker:
    """Tracks token usage and calculates cost."""
    input_tokens: int = 0
    output_tokens: int = 0
    api_calls: int = 0
    
    def add_usage(self, input_tokens: int, output_tokens: int):
        """Add usage from an API call."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.api_calls += 1
    
    @property
    def input_cost(self) -> float:
        """Cost for input tokens in dollars."""
        return (self.input_tokens / 1_000_000) * INPUT_COST_PER_MILLION
    
    @property
    def output_cost(self) -> float:
        """Cost for output tokens in dollars."""
        return (self.output_tokens / 1_000_000) * OUTPUT_COST_PER_MILLION
    
    @property
    def total_cost(self) -> float:
        """Total cost in dollars."""
        return self.input_cost + self.output_cost
    
    def reset(self):
        """Reset all counters."""
        self.input_tokens = 0
        self.output_tokens = 0
        self.api_calls = 0
    
    def summary(self) -> str:
        """Return a formatted summary string."""
        return (
            f"API Calls: {self.api_calls} | "
            f"Tokens: {self.input_tokens:,} in / {self.output_tokens:,} out | "
            f"Cost: ${self.total_cost:.4f} "
            f"(${self.input_cost:.4f} in + ${self.output_cost:.4f} out)"
        )
    
    def detailed_summary(self) -> dict:
        """Return detailed usage statistics."""
        return {
            "api_calls": self.api_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "input_cost_usd": round(self.input_cost, 6),
            "output_cost_usd": round(self.output_cost, 6),
            "total_cost_usd": round(self.total_cost, 6),
        }

# Import the acs_access package (local)
from .acs_access import (
    ACSClient,
    ACSConfig,
    ACSDataset,
    GeographyLevel,
)

load_dotenv()

console = Console()

# =============================================================================
# CONFIGURATION
# =============================================================================

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 4096
MAX_TOOL_ROUNDS = 25            # safety cap on tool-call iterations per chat()
COMPACTION_BETA = "compact-2026-01-12"
COMPACTION_TRIGGER_TOKENS = 150_000  # compact when input exceeds this many tokens

# System prompt for the agent
SYSTEM_PROMPT = """You are an expert data analyst assistant specializing in U.S. Census American Community Survey (ACS) data.

Your role is to help users find, understand, and download ACS data. You have access to tools that can:
- Search for relevant ACS tables by topic
- Get detailed information about specific tables
- Download complete tables or specific variables
- Resolve geographic locations (state names, counties, cities) to proper identifiers

## Key Knowledge

**ACS Datasets:**
- ACS 5-year estimates (acs5): Most comprehensive, available for all geographies, 2009-2024
- ACS 1-year estimates (acs1): More current but only for areas with 65,000+ population, 2005-2024
- Subject tables, data profiles, and comparison profiles are also available

**Common Table Prefixes:**
- B01xxx: Age, Sex, Population
- B02xxx: Race
- B03xxx: Hispanic Origin
- B05xxx: Citizenship, Place of Birth
- B06xxx: Place of Birth (detailed)
- B07xxx: Geographic Mobility
- B08xxx: Commuting/Transportation
- B09xxx: Children characteristics
- B11xxx: Household/Family
- B12xxx: Marital Status
- B15xxx: Education
- B17xxx: Poverty
- B19xxx: Income
- B23xxx: Employment Status
- B25xxx: Housing

**Geography Hierarchy:**
- Nation → Region → Division → State → County → Tract → Block Group
- Places (cities) are within states
- Use "*" for all of a geography type (e.g., all states, all counties in a state)

## Guidelines

1. When users ask about data, first search for relevant tables to find the right one
2. Explain what tables/variables mean before downloading
3. For ambiguous locations, ask for clarification (e.g., "Portland" could be Oregon or Maine)
4. Default to 5-year estimates (acs5) and most recent year (2024) unless specified
5. When downloading, confirm the geography and table with the user
6. Present results in a clear, organized way

Always be helpful, accurate, and explain census terminology when needed."""


# =============================================================================
# TOOL DEFINITIONS
# =============================================================================

TOOLS = [
    {
        "name": "search_tables",
        "description": "Search for ACS tables by topic or keyword. Use this to find relevant tables when the user asks about a topic (e.g., 'income', 'education', 'housing'). Returns table IDs and descriptions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query - topic or keywords to search for (e.g., 'median household income', 'educational attainment', 'poverty')"
                },
                "year": {
                    "type": "integer",
                    "description": "ACS year (2009-2024 for 5-year, 2005-2024 for 1-year). Default: 2024",
                    "default": 2024
                },
                "dataset": {
                    "type": "string",
                    "enum": ["acs/acs5", "acs/acs1", "acs/acs5/subject", "acs/acs5/profile"],
                    "description": "ACS dataset type. Default: acs/acs5 (5-year estimates)",
                    "default": "acs/acs5"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of tables to return. Default: 10",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_table_info",
        "description": "Get detailed information about a specific ACS table, including all its variables and their descriptions. Use this to understand what data a table contains before downloading.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_id": {
                    "type": "string",
                    "description": "The table ID (e.g., 'B01001', 'B19013', 'S1501')"
                },
                "year": {
                    "type": "integer",
                    "description": "ACS year. Default: 2024",
                    "default": 2024
                },
                "dataset": {
                    "type": "string",
                    "enum": ["acs/acs5", "acs/acs1", "acs/acs5/subject", "acs/acs5/profile"],
                    "description": "ACS dataset type. Default: acs/acs5",
                    "default": "acs/acs5"
                }
            },
            "required": ["table_id"]
        }
    },
    {
        "name": "download_table",
        "description": "Download a complete ACS table for a specified geography. Saves the data as a CSV file. Use this when the user wants to get actual data for a table.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_id": {
                    "type": "string",
                    "description": "The table ID to download (e.g., 'B01001', 'B19013')"
                },
                "state": {
                    "type": "string",
                    "description": "State name, abbreviation, or FIPS code (e.g., 'Michigan', 'MI', '26'). Use '*' for all states."
                },
                "county": {
                    "type": "string",
                    "description": "Optional: County name (e.g., 'Wayne County'). Use '*' for all counties in the state."
                },
                "place": {
                    "type": "string",
                    "description": "Optional: City/place name (e.g., 'Detroit', 'Los Angeles city')"
                },
                "year": {
                    "type": "integer",
                    "description": "ACS year. Default: 2024",
                    "default": 2024
                },
                "dataset": {
                    "type": "string",
                    "enum": ["acs/acs5", "acs/acs1"],
                    "description": "ACS dataset type. Default: acs/acs5",
                    "default": "acs/acs5"
                },
                "include_moe": {
                    "type": "boolean",
                    "description": "Include margin of error columns. Default: true",
                    "default": True
                }
            },
            "required": ["table_id", "state"]
        }
    },
    {
        "name": "download_variables",
        "description": "Download specific ACS variables (not a full table) for a geography. Use this when the user only needs specific data points rather than a complete table.",
        "input_schema": {
            "type": "object",
            "properties": {
                "variables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of variable names to download (e.g., ['B01003_001E', 'B19013_001E'])"
                },
                "state": {
                    "type": "string",
                    "description": "State name, abbreviation, or FIPS code"
                },
                "county": {
                    "type": "string",
                    "description": "Optional: County name"
                },
                "place": {
                    "type": "string",
                    "description": "Optional: City/place name"
                },
                "year": {
                    "type": "integer",
                    "description": "ACS year. Default: 2024",
                    "default": 2024
                }
            },
            "required": ["variables", "state"]
        }
    },
    {
        "name": "search_variables",
        "description": "Search for specific ACS variables by keyword. Use this to find exact variable codes when you need specific data points.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for variables (e.g., 'total population', 'bachelor degree')"
                },
                "year": {
                    "type": "integer",
                    "description": "ACS year. Default: 2024",
                    "default": 2024
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results. Default: 20",
                    "default": 20
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_quick_stats",
        "description": "Get quick summary statistics for a state (population, income, etc.) without downloading a full table. Good for answering simple questions quickly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "description": "State name or abbreviation"
                },
                "year": {
                    "type": "integer",
                    "description": "ACS year. Default: 2024",
                    "default": 2024
                }
            },
            "required": ["state"]
        }
    },
    {
        "name": "compare_geographies",
        "description": "Compare key statistics between multiple states or geographies. Useful for comparative questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "states": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of state names or abbreviations to compare"
                },
                "metrics": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["population", "median_income", "poverty_rate", "unemployment_rate", "bachelors_degree_rate"]
                    },
                    "description": "Metrics to compare. Default: all available",
                    "default": ["population", "median_income"]
                },
                "year": {
                    "type": "integer",
                    "description": "ACS year. Default: 2024",
                    "default": 2024
                }
            },
            "required": ["states"]
        }
    }
]


# =============================================================================
# TOOL OUTPUT TRUNCATION
# =============================================================================

MAX_TOOL_RESULT_CHARS = 3000  # ~750 tokens

def truncate_tool_result(result: dict, tool_name: str) -> dict:
    """Truncate tool output to prevent context bloat.

    Data tools: keep metadata + small preview, reference file on disk.
    Search/info tools: cap list lengths.
    """
    if tool_name in ("download_table", "download_variables"):
        return _truncate_data_result(result)
    elif tool_name == "compare_geographies":
        return _truncate_comparison(result)
    elif tool_name in ("search_tables", "search_variables"):
        return _truncate_search(result)

    # Generic fallback
    return _enforce_char_limit(result)


def _truncate_data_result(result: dict) -> dict:
    """Truncate download_table / download_variables results."""
    if not result.get("success", False):
        return result

    truncated = {
        "success": True,
        "file_path": result.get("file_path", ""),
        "rows": result.get("rows", "unknown"),
        "columns": result.get("columns", []),
    }

    # Keep a small preview (up to 5 rows)
    for key in ("preview", "data"):
        if key in result and isinstance(result[key], list):
            total = len(result[key])
            truncated["preview"] = result[key][:5]
            truncated["note"] = f"Full data ({total} rows) saved to {truncated['file_path']}. Showing first 5."
            break
    else:
        truncated["note"] = f"Data saved to {truncated['file_path']}"

    # Carry over extra metadata that is small
    for k in ("table_id", "geography"):
        if k in result:
            truncated[k] = result[k]

    return truncated


def _truncate_comparison(result: dict) -> dict:
    """Truncate compare_geographies results."""
    if "data" in result and isinstance(result["data"], list) and len(result["data"]) > 15:
        total = len(result["data"])
        result = dict(result)
        result["data"] = result["data"][:15]
        result["note"] = f"Showing 15 of {total} states."
    return _enforce_char_limit(result)


def _truncate_search(result: dict) -> dict:
    """Cap search result lists to 15 items."""
    for key in ("tables", "variables"):
        if key in result and isinstance(result[key], list) and len(result[key]) > 15:
            total = len(result[key])
            result = dict(result)
            result[key] = result[key][:15]
            result["note"] = f"Showing 15 of {total} results."
    return _enforce_char_limit(result)


def _enforce_char_limit(result: dict) -> dict:
    """Final safety net: truncate serialized output if too large."""
    serialized = json.dumps(result, default=str)
    if len(serialized) <= MAX_TOOL_RESULT_CHARS:
        return result
    return {
        "_truncated_result": serialized[:MAX_TOOL_RESULT_CHARS],
        "_note": f"Result truncated from {len(serialized)} chars to {MAX_TOOL_RESULT_CHARS}.",
    }


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================

class ACSToolExecutor:
    """Executes ACS tools and returns results."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.client: Optional[ACSClient] = None
    
    async def __aenter__(self):
        self.client = ACSClient()
        await self.client._ensure_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.close()
    
    async def execute(self, tool_name: str, tool_input: dict) -> dict:
        """Execute a tool and return the result."""
        try:
            if tool_name == "search_tables":
                return await self._search_tables(**tool_input)
            elif tool_name == "get_table_info":
                return await self._get_table_info(**tool_input)
            elif tool_name == "download_table":
                return await self._download_table(**tool_input)
            elif tool_name == "download_variables":
                return await self._download_variables(**tool_input)
            elif tool_name == "search_variables":
                return await self._search_variables(**tool_input)
            elif tool_name == "get_quick_stats":
                return await self._get_quick_stats(**tool_input)
            elif tool_name == "compare_geographies":
                return await self._compare_geographies(**tool_input)
            else:
                return {"error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            return {"error": str(e)}
    
    async def _search_tables(
        self,
        query: str,
        year: int = 2024,
        dataset: str = "acs/acs5",
        max_results: int = 10
    ) -> dict:
        """Search for tables by topic."""
        tables = await self.client.search_tables(
            query=query,
            year=year,
            dataset=dataset,
            max_results=max_results
        )
        
        return {
            "query": query,
            "year": year,
            "dataset": dataset,
            "count": len(tables),
            "tables": [
                {
                    "table_id": t.table_id,
                    "title": t.title,
                    "universe": t.universe
                }
                for t in tables
            ]
        }
    
    async def _get_table_info(
        self,
        table_id: str,
        year: int = 2024,
        dataset: str = "acs/acs5"
    ) -> dict:
        """Get detailed table information."""
        info = await self.client.get_table_info(
            table_id=table_id,
            year=year,
            dataset=dataset
        )
        
        if not info:
            return {"error": f"Table {table_id} not found"}
        
        # Group variables by type
        estimates = [v for v in info.variables if v.name.endswith("E")]
        
        return {
            "table_id": info.table_id,
            "title": info.title,
            "universe": info.universe,
            "year": year,
            "dataset": dataset,
            "total_variables": len(info.variables),
            "estimate_variables": len(estimates),
            "variables": [
                {
                    "name": v.name,
                    "label": v.label
                }
                for v in estimates[:30]  # Limit to first 30 estimates
            ],
            "truncated": len(estimates) > 30
        }
    
    async def _download_table(
        self,
        table_id: str,
        state: str,
        county: str = None,
        place: str = None,
        year: int = 2024,
        dataset: str = "acs/acs5",
        include_moe: bool = True
    ) -> dict:
        """Download a complete table."""
        geography = {"state": state}
        if county:
            geography["county"] = county
        if place:
            geography["place"] = place
        
        result = await self.client.download_table(
            table_id=table_id,
            geography=geography,
            year=year,
            dataset=dataset,
            output_dir=self.output_dir,
            include_moe=include_moe
        )
        
        if result.success:
            # Read first few rows to show preview
            import pandas as pd
            df = pd.read_csv(result.file_path)
            preview = df.head(5).to_dict(orient='records')
            
            return {
                "success": True,
                "file_path": str(result.file_path),
                "rows": result.rows,
                "columns": result.columns,
                "table_id": table_id,
                "geography": result.metadata.get("geography", ""),
                "preview": preview
            }
        else:
            return {
                "success": False,
                "error": result.error
            }
    
    async def _download_variables(
        self,
        variables: list[str],
        state: str,
        county: str = None,
        place: str = None,
        year: int = 2024
    ) -> dict:
        """Download specific variables."""
        geography = {"state": state}
        if county:
            geography["county"] = county
        if place:
            geography["place"] = place
        
        result = await self.client.download_variables(
            variables=variables,
            geography=geography,
            year=year,
            output_dir=self.output_dir
        )
        
        if result.success:
            import pandas as pd
            df = pd.read_csv(result.file_path)
            preview = df.head(5).to_dict(orient='records')

            return {
                "success": True,
                "file_path": str(result.file_path),
                "rows": len(df),
                "columns": list(df.columns),
                "preview": preview,
                "note": f"Full data ({len(df)} rows) saved to {result.file_path}"
            }
        else:
            return {
                "success": False,
                "error": result.error
            }

    async def _search_variables(
        self,
        query: str,
        year: int = 2024,
        max_results: int = 20
    ) -> dict:
        """Search for specific variables."""
        variables = await self.client.search_variables(
            query=query,
            year=year,
            max_results=max_results
        )
        
        return {
            "query": query,
            "count": len(variables),
            "variables": [
                {
                    "name": v.name,
                    "label": v.label,
                    "concept": v.concept,
                    "table_id": v.table_id
                }
                for v in variables
            ]
        }
    
    async def _get_quick_stats(
        self,
        state: str,
        year: int = 2024
    ) -> dict:
        """Get quick statistics for a state."""
        # Key variables for quick stats
        variables = [
            "B01003_001E",  # Total population
            "B19013_001E",  # Median household income
            "B25001_001E",  # Housing units
            "B23025_002E",  # Labor force
            "B23025_005E",  # Unemployed
            "B17001_002E",  # Below poverty level
            "B17001_001E",  # Poverty universe (total)
            "B15003_022E",  # Bachelor's degree
            "B15003_023E",  # Master's degree
            "B15003_025E",  # Doctorate
            "B15003_001E",  # Education universe (25+)
        ]
        
        result = await self.client.download_variables(
            variables=variables,
            geography={"state": state},
            year=year,
            output_dir=self.output_dir
        )
        
        if not result.success:
            return {"error": result.error}
        
        import pandas as pd
        df = pd.read_csv(result.file_path)
        row = df.iloc[0]
        
        # Calculate derived metrics
        pop = int(row.get("B01003_001E", 0))
        income = int(row.get("B19013_001E", 0))
        housing = int(row.get("B25001_001E", 0))
        labor_force = int(row.get("B23025_002E", 0))
        unemployed = int(row.get("B23025_005E", 0))
        below_poverty = int(row.get("B17001_002E", 0))
        poverty_universe = int(row.get("B17001_001E", 0))
        bachelors = int(row.get("B15003_022E", 0))
        masters = int(row.get("B15003_023E", 0))
        doctorate = int(row.get("B15003_025E", 0))
        edu_universe = int(row.get("B15003_001E", 0))
        
        stats = {
            "state": row.get("NAME", state),
            "year": year,
            "total_population": pop,
            "median_household_income": income,
            "housing_units": housing,
            "labor_force": labor_force,
        }
        
        if labor_force > 0:
            stats["unemployment_rate"] = round(unemployed / labor_force * 100, 1)
        
        if poverty_universe > 0:
            stats["poverty_rate"] = round(below_poverty / poverty_universe * 100, 1)
        
        if edu_universe > 0:
            higher_ed = bachelors + masters + doctorate
            stats["bachelors_or_higher_rate"] = round(higher_ed / edu_universe * 100, 1)
        
        # Clean up temp file
        Path(result.file_path).unlink(missing_ok=True)
        
        return stats
    
    async def _compare_geographies(
        self,
        states: list[str],
        metrics: list[str] = None,
        year: int = 2024
    ) -> dict:
        """Compare statistics across multiple states."""
        if metrics is None:
            metrics = ["population", "median_income", "poverty_rate", "unemployment_rate"]
        
        results = []
        for state in states:
            stats = await self._get_quick_stats(state, year)
            if "error" not in stats:
                results.append(stats)
        
        if not results:
            return {"error": "Could not retrieve data for any of the specified states"}
        
        # Build comparison
        comparison = {
            "year": year,
            "states_compared": len(results),
            "data": results
        }
        
        return comparison


# =============================================================================
# AGENT CLASS
# =============================================================================

class ACSAgent:
    """
    AI-powered ACS data assistant using Claude with tool use.
    """
    
    def __init__(
        self,
        output_dir: Path = None,
        compaction: bool = True,
        compaction_trigger_tokens: int = COMPACTION_TRIGGER_TOKENS,
    ):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")

        self.anthropic = anthropic.Anthropic(api_key=api_key)
        self.output_dir = output_dir or Path("./acs_downloads")
        self.conversation_history = []
        self.usage = UsageTracker()
        self.compaction = compaction
        self.compaction_trigger_tokens = compaction_trigger_tokens

    async def chat(self, user_message: str) -> str:
        """
        Process a user message and return the assistant's response.
        Handles tool calls automatically with context compaction.
        """
        # Add user message to history
        self.conversation_history.append({
            "role": "user",
            "content": user_message
        })

        # Build kwargs shared across every call in this loop
        api_kwargs: dict = dict(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=self.conversation_history,
        )
        if self.compaction:
            api_kwargs["betas"] = [COMPACTION_BETA]
            api_kwargs["context_management"] = {
                "edits": [
                    {
                        "type": "compact_20260112",
                        "trigger": {
                            "type": "input_tokens",
                            "value": self.compaction_trigger_tokens,
                        },
                    }
                ]
            }

        async with ACSToolExecutor(self.output_dir) as executor:
            rounds = 0
            while rounds < MAX_TOOL_ROUNDS:
                rounds += 1

                # Call Claude (via beta endpoint when compaction is on)
                if self.compaction:
                    response = self.anthropic.beta.messages.create(**api_kwargs)
                else:
                    response = self.anthropic.messages.create(**api_kwargs)

                # Track usage
                self.usage.add_usage(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens
                )

                # ----- handle compaction stop reason --------------------
                if response.stop_reason == "compaction":
                    self.conversation_history.append({
                        "role": "assistant",
                        "content": response.content,
                    })
                    console.print("[dim]📦 Context compacted[/dim]")
                    continue

                # ----- handle tool use ----------------------------------
                if response.stop_reason == "tool_use":
                    tool_results = []
                    assistant_content = response.content

                    for block in response.content:
                        if block.type == "tool_use":
                            tool_name = block.name
                            tool_input = block.input
                            tool_id = block.id

                            console.print(f"[dim]🔧 Calling {tool_name}...[/dim]")

                            result = await executor.execute(tool_name, tool_input)
                            result = truncate_tool_result(result, tool_name)

                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": json.dumps(result, default=str)
                            })

                    self.conversation_history.append({
                        "role": "assistant",
                        "content": assistant_content
                    })
                    self.conversation_history.append({
                        "role": "user",
                        "content": tool_results
                    })
                    continue

                # ----- handle end_turn (final answer) -------------------
                if response.stop_reason == "end_turn":
                    text_response = ""
                    for block in response.content:
                        if hasattr(block, "text"):
                            text_response += block.text

                    self.conversation_history.append({
                        "role": "assistant",
                        "content": response.content
                    })
                    return text_response

                # ----- unexpected stop reason ---------------------------
                return f"Unexpected response: {response.stop_reason}"

            # Exhausted MAX_TOOL_ROUNDS
            return (
                "I was unable to complete the request within the maximum number "
                f"of tool-calling rounds ({MAX_TOOL_ROUNDS}). "
                "Please try narrowing your request."
            )
    
    def reset_conversation(self):
        """Clear conversation history."""
        self.conversation_history = []
    
    def get_usage_summary(self) -> str:
        """Get a formatted usage summary."""
        return self.usage.summary()
    
    def get_usage_details(self) -> dict:
        """Get detailed usage statistics."""
        return self.usage.detailed_summary()
    
    def reset_usage(self):
        """Reset usage tracking."""
        self.usage.reset()
    
    async def run_interactive(self):
        """Run an interactive chat session."""
        console.print(Panel(
            "[bold blue]ACS Data Assistant[/bold blue]\n\n"
            "I can help you find and download American Community Survey data.\n"
            "Ask me about demographics, income, education, housing, and more!\n\n"
            "[dim]Commands: 'quit' to exit, 'reset' to clear history, 'cost' for usage, 'help' for examples[/dim]",
            title="Welcome",
            border_style="blue"
        ))
        
        examples = """
[bold]Example queries:[/bold]
• "What is the population of California?"
• "Download the median household income table for Texas"
• "Compare poverty rates between Michigan and Ohio"
• "Find tables about educational attainment"
• "Get housing data for Detroit, Michigan"
• "What's the unemployment rate in Florida?"
"""
        
        while True:
            try:
                user_input = console.input("\n[bold green]You:[/bold green] ").strip()
                
                if not user_input:
                    continue
                
                if user_input.lower() == "quit":
                    # Show final usage stats
                    console.print(f"\n[dim]Session {self.usage.summary()}[/dim]")
                    console.print("[dim]Goodbye![/dim]")
                    break
                
                if user_input.lower() == "reset":
                    self.reset_conversation()
                    console.print("[dim]Conversation reset.[/dim]")
                    continue
                
                if user_input.lower() == "cost":
                    console.print(f"[cyan]{self.usage.summary()}[/cyan]")
                    continue
                
                if user_input.lower() == "help":
                    console.print(examples)
                    continue
                
                # Get response
                console.print()
                response = await self.chat(user_input)
                
                # Display response
                console.print("\n[bold blue]Assistant:[/bold blue]")
                console.print(Markdown(response))
                
                # Show cost for this query
                console.print(f"\n[dim]💰 {self.usage.summary()}[/dim]")
                
            except KeyboardInterrupt:
                console.print("\n[dim]Interrupted. Type 'quit' to exit.[/dim]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")


# =============================================================================
# CLI
# =============================================================================

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="ACS Data Assistant - AI-powered Census data helper"
    )
    parser.add_argument(
        "--query", "-q",
        help="Single query to process (non-interactive mode)"
    )
    parser.add_argument(
        "--output", "-o",
        default="./acs_downloads",
        help="Output directory for downloaded files"
    )
    
    args = parser.parse_args()
    
    output_dir = Path(args.output)
    agent = ACSAgent(output_dir=output_dir)
    
    async def run():
        if args.query:
            # Single query mode
            response = await agent.chat(args.query)
            console.print(Markdown(response))
            console.print(f"\n[dim]💰 {agent.get_usage_summary()}[/dim]")
        else:
            # Interactive mode
            await agent.run_interactive()
    
    asyncio.run(run())


if __name__ == "__main__":
    main()



