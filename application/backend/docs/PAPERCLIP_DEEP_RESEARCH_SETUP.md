# Paperclip Deep Research Setup

## What Paperclip does

Paperclip is the literature-retrieval backend used by DiscoveryAgent’s `deep_research` helper.

It is used when the agent needs biomedical literature context.

Paperclip does **not** replace `web_search` or `single_search`. Those still use their existing search tools.

The active deep research provider is controlled by:

```yaml
deep_research_provider: paperclip
```

Paperclip is the default provider. To use the older Perplexity path directly, set:

```yaml
deep_research_provider: perplexity
```

## Install the Paperclip SDK

Paperclip is the default deep-research provider and is installed with the
normal DiscoveryAgent backend dependencies:

```bash
uv sync
```

GXL distributes `gxl-paperclip` from its official wheel URL rather than the
PyPI package index. The repository vendors the official wheel under its
canonical versioned filename; `pyproject.toml` declares it and `uv.lock` pins
its checksum, so no separate manual install is required.

## Verify the install

Run:

```bash
.venv/bin/python - <<'PY'
from gxl_paperclip import PaperclipClient, APIKeyAuth
import gxl_paperclip

print("gxl_paperclip:", getattr(gxl_paperclip, "__version__", "unknown"))
print("Paperclip SDK import OK")
PY
```

Expected output should include:

```text
Paperclip SDK import OK
```

## Authentication

DiscoveryAgent uses:

```python
PaperclipClient.from_env()
```

That means Paperclip looks for credentials automatically.

It checks:

1. `PAPERCLIP_BEARER_TOKEN`
2. `PAPERCLIP_API_KEY`
3. saved credentials from `paperclip login`

For local interactive use, `paperclip login` is enough, but the code is configured to work with an api key too.

## What happens if Paperclip fails?

If `deep_research_provider: paperclip` is selected and Paperclip fails because of an import, auth, network, or provider error, DiscoveryAgent can fall back to Perplexity if this is set:

```bash
PERPLEXITY_API_KEY="..."
```

Fallback order:

```text
Paperclip
→ Perplexity, if PERPLEXITY_API_KEY is available
→ safe retrieval-error markdown
```

A true “no relevant papers found” result from Paperclip does **not** trigger Perplexity fallback. Fallback is only for provider errors.

## Running with Paperclip

Use this in config:

```yaml
deep_research_provider: paperclip
```

To disable Paperclip and use Perplexity directly:

```yaml
deep_research_provider: perplexity
```

## Optional auth check

Before a live run, check that credentials are available:

```bash
test -n "$PAPERCLIP_API_KEY" && echo "PAPERCLIP_API_KEY is set" || echo "PAPERCLIP_API_KEY is NOT set"
test -f ~/.paperclip/credentials.json && echo "paperclip login credentials found" || echo "no saved credentials"
```

Either an API key or saved login credentials can work.
