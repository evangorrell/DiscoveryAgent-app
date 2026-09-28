# Publication review — human decisions pending

Technical hardening does not grant permission to publish or redistribute.
No umbrella project license has been selected by this work.

| Component | Confirmation required before publication |
| --- | --- |
| Umbrella application and manager | Confirm contributors, copyright ownership, institutional/lab obligations, and permission to publish; choose a license only after approval. |
| Imported frontend | Confirm upstream ownership and redistribution terms for the historical import and subsequent contributions. |
| Backend snapshot and local patches | Confirm upstream licensing, attribution, institutional approvals, and rights to distribute the patched snapshot. Preserve its upstream and canonical provenance. |
| Paperclip wheel | Confirm redistribution rights and required notices for the vendored wheel; metadata alone is not a complete license review. |
| Rho template | Review CC BY 4.0 attribution, modification notices, and included assets. |
| PyMuPDF / MuPDF | Review AGPL obligations or obtain applicable commercial licensing before distribution. |
| Native assets and bundled runtimes | Review the picker, Python, uv, Claude SDK executable, native wheels, and all required notices/redistribution conditions. |
| Browser dependencies | Preserve the vendored Marked and DOMPurify license files and review other bundled/CDN assets. |

Separately, final public artifacts need clean-machine installation, macOS-version,
Gatekeeper/quarantine, upgrade, and uninstall acceptance. No signing or
notarization system is introduced in this hardening branch.
