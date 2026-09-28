# Markdown dependencies

Vendored unchanged from npm release archives: Marked 9.1.6 (retains the
existing Marked 9 renderer API) and DOMPurify 3.4.16. Their upstream license
files accompany the distributions. These are dependency licenses, not an
umbrella project license. No CDN availability is needed to sanitize reports.

All parser output must still pass through sanitization. A missing or failing
dependency falls back to escaped content in the application renderer.
