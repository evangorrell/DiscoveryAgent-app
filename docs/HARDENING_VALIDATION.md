# Release-hardening validation

Baseline: `c8305208a829ab120dab8d58f41f86e908f47361`. This is technical
validation, not publication approval or a claim that paid model calls were tested.

## Runtime dependency changes

Locks were regenerated with uv and checked under Python 3.11.11. Targeted patched
packages retain existing architecture; compatible supporting packages were resolved
with them. Together 1.x caps Pillow below 12, so Together 2.x is needed for patched
Pillow. Its actual chat API was exercised with an offline HTTP transport. Starlette
1.x is already used by the frontend SDK graph and satisfies backend MCP constraints.
Cryptography uses calendar-like major versions; no application crypto API changed.

| Package | Old | New | Reason |
| --- | --- | --- | --- |
| Flask (frontend) | 3.0.0 | 3.1.3 | Security patch |
| Markdown (frontend) | 3.6 | 3.11 | Security patch |
| bleach (frontend) | absent | 6.4.0 | Server-side fail-closed sanitizer |
| python-dotenv (frontend) | 1.0.1 | 1.2.3 | Compatible current runtime |
| aiohttp | 3.11.11 | 3.14.3 | HTTP security patches |
| urllib3 | 2.2.3 | 2.8.0 | HTTP security patches |
| requests | 2.32.3 | 2.34.2 | HTTP security patches |
| Pillow | 11.2.1 | 12.3.0 | Image decoder security patches |
| lxml | 6.0.2 | 6.1.3 | Parser security patches |
| pypdf | 6.9.1 | 6.19.0 | PDF parser security patches |
| nbconvert | 7.17.0 | 7.17.1 | Notebook conversion security patch |
| Mistune | 3.2.0 | 3.3.4 | Markdown security patches |
| idna | 3.10 | 3.20 | Domain parsing security patches |
| soupsieve | 2.8.3 | 2.10 | Parser security patches |
| Pygments | 2.19.2 | 2.21.0 | Lexer security patches |
| authlib | 1.6.9 | 1.8.0 | Authentication security patches |
| bleach (backend) | 6.3.0 | 6.4.0 | Sanitizer security patches |
| click | 8.2.0 | 8.5.0 | CLI security patches |
| cryptography (frontend) | 49.0.0 | 50.0.1 | Security patches |
| cryptography (backend) | 45.0.7 / 46.0.0 | 50.0.1 | Security patches |
| filelock | 3.18.0 | 3.32.7 | Filesystem security patches |
| fonttools | 4.54.1 | 4.66.0 | Font handling security patches |
| jupyter-server | 2.17.0 | 2.21.1 | Notebook server security patches |
| jupyterlab | 4.5.6 | 4.6.4 | Notebook UI security patches |
| PyJWT | 2.10.1 | 2.15.0 | Token handling security patches |
| python-multipart | 0.0.21 | 0.0.32 | Form parsing security patches |
| setuptools | 82.0.1 | 84.0.0 | Build tooling security patches |
| Starlette (backend) | 0.52.1 | 1.7.0 | ASGI security patches |
| Together | 1.5.8 | 2.37.0 | Remove vulnerable Pillow constraint; offline API regression |
| Tornado | 6.5.2 | 6.5.10 | HTTP/server security patches |
| pytest (backend dev extra) | 9.0.2 | 9.1.1 | Straightforward test-only patch |

Supporting resolutions: aiohappyeyeballs 2.4.4→2.7.1, aiosignal 1.3.2→1.4.0,
AnyIO frontend 4.14.1→4.15.1/backend 4.10.0→4.14.2, typing-extensions frontend
4.15.0→4.16.0, cffi adds 2.1.1 on applicable interpreter branches. New resolver
requirements include joserfc, jupyter-builder, detect-agent and types-pyyaml.
Frontend's new optional test extra supplies pytest and nbformat, not production
dependencies. LightGBM remains locked only for its optional extra (also in `all`).

## Remaining dependency advisories

OSV scan: **390 unique locked package/version pairs** queried. **13 pairs flagged,
all optional-only** for Apple Silicon / CPython 3.11.11; **zero default-runtime,
dev-only, or non-target-only flagged pairs**. This is a point-in-time package scan,
not proof that all application behavior is secure. Duplicate OSV/GHSA/PYSEC records
are not counted as separate vulnerable package/version pairs.

| Optional package | Locked version | Applicability / remaining risk |
| --- | --- | --- |
| biopython | 1.86 | Optional biology toolbox; XML/Entrez handling |
| datasets | 4.0.0 | Optional dataset/model tooling; untrusted input handling |
| diskcache | 5.6.3 | Optional ACS tooling; untrusted pickle/cache deserialization |
| fastmcp | 2.14.7 | Optional interactive MCP tooling; CLI/integration/auth issues; patched line requires major upgrade |
| langchain | 0.3.28 | Optional language/model tooling; untrusted prompt/manifests |
| langchain-core | 0.3.83 | Optional tooling; SSRF/serialization issues |
| langchain-text-splitters | 0.3.11 | Optional tooling; URL/redirect SSRF |
| langsmith | 0.7.20 | Optional tooling; untrusted prompt/manifests |
| msgpack | 1.1.2 | Optional tooling; parser reuse after failure |
| nltk | 3.9.3 | Optional NLP; numerous corpus/parser/path handling advisories |
| pyarrow | 18.1.0 | Optional tooling; untrusted IPC parsing |
| torch | 2.10.0 | Optional model tooling; JIT/memory handling |
| transformers | 5.3.0 | Optional model tooling; unsafe model-loading paths |

These extras are not installed by the production manager. Do not treat `--all-extras`
or unreviewed optional features as covered by default-install acceptance. Review or
upgrade the relevant extra before exposing it to untrusted inputs. Wholesale
LangChain/FastMCP/model-stack migrations are deliberately outside this pass.

## Evidence and limits

| Suite | Passed | Failed | Skipped/Deselected | Notes |
| --- | ---: | ---: | ---: | --- |
| Frontend JavaScript | 392 | 0 | 0 | Complete Node suite |
| Frontend Python | 1270 | 0 | 2 | Full suite; legitimate OS interaction skips |
| Backend Python | 172 | 0 | 4 deselected | Only paid/live SDK cases excluded |
| Manager/installer/updater/build | 202 | 0 | 0 | Pinned Python 3.11.11 |
| Release builder (also included above) | 17 | 0 | 0 | Independent pinned-Python run |
| Final focused frontend security | 69 | 0 | 0 | After small renderer cache-key/source cleanup |
| Lock validation | 2 | 0 | 0 | Frontend and backend `uv lock --check` |
| Syntax | 369 files | 0 | 0 | 286 Python, 63 JavaScript, 20 shell |
| Fresh default managed environments | 2 | 0 | 0 | Actual manager sync, non-editable installs and import/resource validation |

Fresh backend validation imports NumPy, Pydantic Core, Claude SDK, Paperclip,
PyMuPDF, pypdf, nbformat, nbclient and jupyter_client. Neither optional LightGBM
nor paid-provider calls are part of default-runtime validation.

Native audit inspected **396 Mach-O files** in the fresh default environments.
The highest declared minimum is macOS **15.0**, in bundled Pandoc and debugpy's
attachment library. Python/uv target 11 and the bundled Claude executable targets
13. The enforced default-bundle minimum is therefore 15, not the picker's 13.

Central request tests and live isolated-server probes verify foreign Host, foreign
Origin and cross-site form requests fail with 403 while the registry is unchanged;
same-origin local requests succeed. GET is covered because legacy read routes can
activate/reconcile project state. Headerless local CLI access remains supported.

The browser-verification skill was used against real Chrome and a separate temporary
data root. Welcome/Create Project rendered without console errors. The actual
vendored Marked/DOMPurify libraries sanitized malicious HTML and unsafe links; missing
or throwing parser/sanitizer cases remained escaped and safe. No paid provider calls
were made. Unit regressions cover opaque IDs, persistence and wrong-project rejection,
terminal SDK errors, history rollback and retries.

The two legitimate frontend skips are the opt-in foreground native-panel probe and
a case-sensitive-path test on this case-insensitive filesystem. Exact paid backend
exclusions and the production build/upgrade procedure are listed in
[RELEASE_HARDENING.md](RELEASE_HARDENING.md).

Final artifact acceptance remains: actual macOS 15 clean-machine installation,
Gatekeeper/quarantine behavior, and user-approved live-provider checks. Publication
and redistribution decisions remain in [PUBLICATION_REVIEW.md](PUBLICATION_REVIEW.md).
