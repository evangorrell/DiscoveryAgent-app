# OpenTargets Agent Integration

This document describes the integration of OpenTargets MCP servers into the AIR framework using the Claude Agent SDK.

## Overview

The OpenTargets agent queries and **downloads data** from the OpenTargets Platform for drug discovery research. Query results are saved to CSV/JSON files for downstream analysis by the execution agent.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  DirectorWorkflow                                           │
│    └── agents["opentargets"].execute(query)                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  OpenTargetsAgent (BaseAgent)                               │
│    ├── ClaudeSDKClient (MCP communication)                  │
│    └── OpenTargetsOutputManager (file saving)               │
└─────────────────────────────────────────────────────────────┘
                              │
            SDK spawns servers automatically via stdio
                              │
        ┌─────────┬─────────┬─────────┬─────────┬─────────┐
        ▼         ▼         ▼         ▼         ▼         ▼
   [genetics] [disease] [assoc]  [target] [express] [drug]
   (FastMCP subprocess servers)
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Output Files (CSV/JSON)                                    │
│    └── {output_dir}/YYYY-MM-DD/opentargets_*.{csv,json}     │
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. OpenTargetsAgent (`opentargets_agent.py`)

The main agent class:

- **Inherits from**: `BaseAgent`
- **Uses**: Claude Agent SDK (`ClaudeSDKClient`, `ClaudeAgentOptions`)
- **Transport**: stdio (subprocess-based MCP communication)
- **Output**: Saves query results to CSV/JSON files

### 2. OpenTargetsOutputManager

Manages file outputs for query results:

- Creates standardized directory structure: `{output_dir}/YYYY-MM-DD/`
- Saves data as JSON and CSV files
- Maintains an index file (`opentargets_index.json`) tracking all outputs
- Auto-generates timestamped filenames

### 3. MCP Server Configuration

The agent dynamically builds MCP server configurations:

```python
configs[name] = {
    "command": python_executable,
    "args": ["-I", server_path],
    "env": {
        "PYTHONPATH": f"{pkg_path}/src",
        "OPEN_TARGETS_PATH": data_path,
    }
}
```

## Available MCP Servers

| Server | Description | Key Tools |
|--------|-------------|-----------|
| `genetics` | GWAS and genetic associations | `query_gwas_associations`, `query_l2g_predictions`, `get_credible_sets` |
| `disease` | Disease ontology and phenotypes | `get_disease_info`, `search_diseases_by_name`, `get_disease_hierarchy` |
| `association` | Target-disease associations | `query_associations`, `get_associations_for_disease`, `filter_by_datatype` |
| `target` | Target annotations and druggability | `get_target_info`, `get_target_tractability`, `get_target_safety_profile` |
| `expression` | GTEx tissue expression | `query_expression_by_gene`, `compare_expression_across_tissues` |
| `drug` | Drug information and safety | `search_drugs`, `get_drug_info`, `get_drug_mechanisms` |

## Data Output

When `output_dir` is configured, query results are saved to files:

### Directory Structure
```
{output_dir}/
├── opentargets_index.json              # Index of all outputs
├── opentargets_druggability_PCSK9_14-30-45.json
├── opentargets_druggability_PCSK9_14-30-45.csv
└── query_response_14-30-45.json        # Full response with metadata
```

Files are saved directly to `output_dir` (same pattern as ACS agent) so the execution agent can access them.

### Index File Format
```json
{
  "outputs": [
    {
      "timestamp": "2025-02-04T14:30:45",
      "filepath": "/path/to/file.json",
      "filename": "opentargets_druggability_PCSK9_14-30-45.json",
      "query": "What is the druggability of PCSK9?",
      "format": "json",
      "n_records": 1
    }
  ],
  "created": "2025-02-04T14:00:00"
}
```

## Integration Points

### Agent Registration

In `AIR/tool_agents/agent_directory.py`:
```python
from .specialized.opentargets_agent import OpenTargetsAgent

STRING_TO_AGENT_MAP = {
    ...
    "opentargets_agent": OpenTargetsAgent,
}
```

### CLI Arguments

In `AIR/utils/arg_parsers.py`:
- `--use_opentargets_agent`: Enable the agent
- `--opentargets_model`: Model for agent reasoning
- `--opentargets_servers`: Comma-separated list of servers to enable
- `--opentargets_output_dir`: Directory for saving downloaded data

### Workflow Integration

In `scripts/run_hb_sherlock.py`:
- `set_opentargets_agent_config()`: Creates agent configuration
- Agent added to `agent_config_list` when `--use_opentargets_agent` is set
- In expanding dataset mode, defaults to the expanding data directory

## Data Dependencies

The MCP servers require access to OpenTargets Platform data (version 25_09):
- **Default path**: `/oak/stanford/groups/jamesz/hgzhang/targets/open_target_25_09`
- **Format**: Parquet files
- **Total size**: ~19 GB across all servers

Environment variable: `OPEN_TARGETS_PATH`

## Usage

### Via run_hb_sherlock.py

```bash
# Basic usage with data output
python scripts/run_hb_sherlock.py \
    --analysis_name my_analysis \
    --dataset_name my_dataset \
    --use_opentargets_agent \
    --opentargets_output_dir data/downloads

# With specific servers only
python scripts/run_hb_sherlock.py \
    --analysis_name my_analysis \
    --dataset_name my_dataset \
    --use_opentargets_agent \
    --opentargets_servers "genetics,target,drug"
```

### Direct Testing

```bash
python scripts/testing/test_otagent.py --query "What is the druggability of PCSK9?"
```

### In Director Prompts

The Director invokes the agent using XML tags:
```
<opentargets>
Download genetic evidence linking NOD2 to inflammatory bowel disease
</opentargets>
```

## How It Works

1. **Query Execution**: Director sends query to OpenTargetsAgent
2. **MCP Communication**: SDK spawns MCP servers, routes tool calls
3. **Data Retrieval**: MCP tools query parquet files, return JSON
4. **Response Processing**: Agent formats results, extracts structured data
5. **File Saving**: OutputManager saves JSON/CSV to configured directory
6. **Result Return**: Agent returns summary with file paths

## Key Design Decisions

1. **stdio transport**: Servers spawn on-demand, no persistent server process needed
2. **Lazy loading**: MCP servers load data lazily on first tool call (fast startup)
3. **Automatic file saving**: Results saved to files for downstream analysis
4. **Index tracking**: All outputs tracked in index file for easy discovery
5. **SDK-managed lifecycle**: Claude Agent SDK handles all subprocess management

## Comparison with ACS Agent

| Feature | ACS Agent | OpenTargets Agent |
|---------|-----------|-------------------|
| Data source | Census Bureau API (remote) | Local parquet files |
| File output | CSV + JSON dictionary | CSV + JSON |
| Output location | Direct to `output_dir` | Direct to `output_dir` |
| Output tracking | Per-file metadata | Central index file |
| Data freshness | Real-time API queries | Pre-computed (v25_09) |
| Execution agent access | Yes (same directory) | Yes (same directory) |

## Alternative: Direct MCP Access in ExecutionAgentCC

In addition to using the OpenTargetsAgent as a separate agent invoked by the Director, you can enable OpenTargets MCP servers **directly within ExecutionAgentCC**. This gives the execution agent direct access to OpenTargets tools during its analysis.

### Architecture (Direct MCP Mode)

```
┌─────────────────────────────────────────────────────────────┐
│  ExecutionAgentCC (Claude Code)                             │
│    ├── Standard tools: Read, Write, Edit, Bash, Glob, Grep  │
│    └── OpenTargets MCP tools (when enabled)                 │
└─────────────────────────────────────────────────────────────┘
                              │
            SDK spawns servers automatically via stdio
                              │
        ┌─────────┬─────────┬─────────┬─────────┬─────────┐
        ▼         ▼         ▼         ▼         ▼         ▼
   [genetics] [disease] [assoc]  [target] [express] [drug]
   (FastMCP subprocess servers, prefixed as opentargets_*)
```

### CLI Arguments for Direct MCP

In `AIR/utils/arg_parsers.py`:
- `--execution_opentargets_mcp`: Enable OpenTargets MCP servers in the execution agent
- `--execution_opentargets_servers`: Comma-separated list of servers to enable
- `--execution_opentargets_mcp_path`: Custom path to OpenTargets MCP package
- `--execution_opentargets_data_path`: Custom path to OpenTargets data directory

### Configuration in ExecutionAgentCCConfig

```python
from AIR.tool_agents.execution_agent_cc import ExecutionAgentCCConfig

config = ExecutionAgentCCConfig(
    # ... other config ...
    opentargets_mcp_enabled=True,
    opentargets_mcp_servers=["genetics", "target", "drug"],  # Optional: subset
    opentargets_mcp_path="/path/to/virtualbiotech_mcp_package",  # Optional
    opentargets_data_path="/path/to/open_targets_data",  # Optional
)
```

### Usage via run_hb_sherlock.py

```bash
# Enable all OpenTargets MCP servers in execution agent
python scripts/run_hb_sherlock.py \
    --analysis_name my_analysis \
    --dataset_name my_dataset \
    --execution_opentargets_mcp

# Enable specific servers only
python scripts/run_hb_sherlock.py \
    --analysis_name my_analysis \
    --dataset_name my_dataset \
    --execution_opentargets_mcp \
    --execution_opentargets_servers "genetics,target,drug"
```

### YAML Configuration

```yaml
# In your config YAML file
execution_opentargets_mcp: true
execution_opentargets_servers: "genetics,target,drug"
# Optional custom paths:
# execution_opentargets_mcp_path: "/custom/path/to/mcp_package"
# execution_opentargets_data_path: "/custom/path/to/data"
```

### Tool Naming in Direct Mode

When MCP servers are added directly to ExecutionAgentCC, tools are accessed with the `mcp__opentargets_{server}__` prefix:
- `mcp__opentargets_genetics__query_gwas_associations`
- `mcp__opentargets_target__get_target_info`
- `mcp__opentargets_drug__search_drugs`

### When to Use Each Mode

| Feature | OpenTargetsAgent | Direct MCP in ExecutionAgentCC |
|---------|------------------|--------------------------------|
| Control | Director decides when to query | Execution agent decides autonomously |
| File saving | Automatic to output_dir | Manual (agent must write files) |
| Context | Separate agent context | Shared with analysis context |
| Use case | Data collection tasks | Integrated analysis tasks |

**Recommendation**: Use OpenTargetsAgent when data collection should be a distinct step controlled by the Director. Use direct MCP mode when the execution agent needs seamless access to OpenTargets data during analysis.

## Troubleshooting

### "Control request timeout: initialize"
- MCP servers failing to start
- Check: Python path, module imports, environment variables
- Verify servers can start: `python -I /path/to/server.py --help`

### Missing module errors
- The virtualbiotech package may be incomplete
- Required: `src/types/agents.py` with `AgentHardLimits` class
- Required env vars: `OPEN_TARGETS_DATA_PATH`, `KNOWLEDGE_BASE_PATH`

### No files saved
- Check that `output_dir` is configured
- Verify the agent returns structured JSON data
- Check logs for file saving errors

### Data access errors
- Verify access to OpenTargets data directory
- Check `OPEN_TARGETS_PATH` environment variable
