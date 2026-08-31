# DiscoveryAgent

DiscoveryAgent (AIR) is an agentic scientific research system. Given a dataset and a research question, it autonomously generates hypotheses, runs multi-step data analyses, and produces structured reports — iterating across multiple rounds with branching exploration strategies. It is built on top of Claude and the Claude Code execution environment.

## Guidance: original implementation and newer features

- The repository includes post-paper features that were not used in the original experiments.
- The original implementation used Perplexity for deep research. Paperclip was added later and is recommended for future work; use Perplexity when reproducing the original provider choice.
- Automatic literature grounding during hypothesis generation was added later and was not used originally. Leave it disabled for paper reproduction; enable the Paperclip-backed option for future literature-grounded studies.
- Reviewer-triggered literature validation is also a post-paper, opt-in feature.
- These settings are checkpointed, so resumed experiments retain their original behavior. See [Paperclip Deep Research Setup](docs/PAPERCLIP_DEEP_RESEARCH_SETUP.md) for configuration details.

## Setup

Requires Python 3.11+. The Claude Agent SDK supplies its own compatible Claude
Code executable for normal DiscoveryAgent workflows; Node.js, npm, and a global
`claude` command are not required.

**1. Create the project environment**

[uv](https://docs.astral.sh/uv/getting-started/installation/) is recommended:

```bash
uv sync --extra interactive
source .venv/bin/activate
python -V
```

`python -V` should report Python 3.11.x.
For most users, this is the recommended install path. It creates the repo-managed `.venv` used for both the main workflows and the interactive CLI.

If you only need the batch workflows and do not plan to use the interactive CLI, you can install without the interactive extra:

```bash
uv sync
source .venv/bin/activate
```

Or with pip:
```bash
pip install -e .
```

For the interactive CLI with pip:

```bash
pip install -e ".[interactive]"
```

If you previously created the environment with the wrong Python version, rebuild it:

```bash
rm -rf .venv
uv sync --extra interactive
source .venv/bin/activate
python -V
```

The base install includes the full Jupyter stack, common data formats (xarray, lxml), the core ML/data science stack (scikit-learn, scipy, matplotlib, seaborn, lightgbm, xgboost, shap, numba, networkx), visualization extras (matplotlib-venn, UpSetPlot, PyWavelets), statistical modeling (statsmodels, pingouin, formulaic, autograd), web/scraping tools (selenium, beautifulsoup4, gql), and PDF tools (PyMuPDF, pypdf).

Optional extras cover heavier or domain-specific dependencies that you can add later by re-running `uv sync`:

| Extra | What it adds |
|---|---|
| `bio` | Bioinformatics / life sciences: scanpy, anndata, biopython, biotite, mdtraj, gseapy, lifelines, h5py, zarr, fair-esm, depmap |
| `torch` | PyTorch ecosystem: torch, torchvision, torch-geometric, transformers, huggingface_hub, datasets, safetensors |
| `tda` | Topological data analysis: gudhi, ripser, persim, kmapper, scikit-tda, umap-learn, pynndescent |
| `langchain` | LangChain integration: langchain, langchain-community, langchain-core |
| `nlp` | NLP utilities: nltk, rouge_score |
| `acs` | Census/ACS specialized agent: aiolimiter, diskcache, redis, ipumspy, rich, tenacity, tqdm |
| `opentargets` | Open Targets specialized agent: pyarrow |
| `astronomy` | Astronomy / astrophysics: astropy, astroquery, pyvo, photutils |
| `aws` | AWS cloud access: boto3 |
| `interactive` | Interactive CLI mode via MCP server: fastmcp |
| `all` | All of the above |

```bash
uv sync --extra bio,torch   # bio + PyTorch
uv sync --extra all         # or: pip install ".[all]"
```

The `interactive` extra is the only extra required for the CLI. The `acs`, `opentargets`, `bio`, `torch`, and `all` extras are optional and only needed for those specialized workflows.

The developer-only direct interactive launcher is separate from the normal
frontend-launched local-round path and may intentionally use a user-supplied
Claude CLI.

**2. Configure environment**

Copy the template and fill in your values:

```bash
cp .env.example .env
```

At minimum, set your LLM API key and data path:

```bash
ANTHROPIC_API_KEY=your_key_here   # or OPENAI_API_KEY, etc.
DATA_PATH=/path/to/your/data
```

For Paperclip-backed literature retrieval, authenticate with `paperclip login` or set:

```bash
PAPERCLIP_API_KEY=your_key_here
```

Set the Perplexity key only when using Perplexity directly or as a Paperclip provider-error fallback:

```bash
PERPLEXITY_API_KEY=your_key_here
```

See [Environment Variables](#environment-variables) for the full list.

**3. Verify**

```bash
python -c "import AIR; print('OK')"
python -c "import claude_agent_sdk; print('Claude Agent SDK OK')"
```

If you plan to use the interactive CLI, prefer launching it with the project environment explicitly:

```bash
./.venv/bin/python -m cli.run_interactive ...
```

## Running

```bash
python scripts/run_hb_sherlock.py \
  --working_directory "/path/to/data" \
  --analysis_name "my_analysis" \
  --num_rounds 1 \
  --num_branches 1
```

Supply `--task_description_file` to provide a research brief; otherwise the dataset's README is used.

Resume or modify a prior run by loading its checkpoint config:

```bash
python scripts/run_hb_sherlock.py \
  --config-file "/path/to/logs/checkpoints/round_0/config.json" \
  --num_rounds 3
```

CLI flags override values from the loaded config.

Outputs and checkpoints are written to `DATA_PATH/LOGS/<dataset>/<analysis_name>_<timestamp>/`.

## Environment Variables

All variables are read from `.env` in the project root (takes precedence over shell environment).

### LLM providers

At least one is required.

| Variable | Provider |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic (Claude) |
| `OPENAI_API_KEY` | OpenAI (GPT) |
| `TOGETHER_API_KEY` | Together AI |
| `OPENROUTER_API_KEY` | OpenRouter |
| `PAPERCLIP_API_KEY` | Paperclip literature retrieval; optional if authenticated with `paperclip login` |
| `PAPERCLIP_BEARER_TOKEN` | Alternative Paperclip bearer-token authentication |
| `PERPLEXITY_API_KEY` | Perplexity deep research, or fallback when Paperclip has a provider error |

### Other

| Variable | Description | Required? |
|---|---|---|
| `DATA_PATH` | Root directory for datasets and logs | Yes |
| `SEMANTIC_SCHOLAR_API_KEY` | Academic search MCP — unauthenticated access is rate-limited | No |
| `CENSUS_API_KEY` | Required for the `acs` extra | Only with `acs` |
| `CLAUDE_CODE_CLI_PATH` | Absolute executable path for an intentional developer/HPC Claude CLI override; normal execution uses the SDK bundle | No |
