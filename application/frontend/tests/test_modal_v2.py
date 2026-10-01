from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "da_selection_ui" / "templates"
STATIC = ROOT / "da_selection_ui" / "static"


def test_shared_v2_modal_contract_defines_header_body_footer_and_compact_variant():
    css = (STATIC / "style.css").read_text(encoding="utf-8")

    assert ".v2-modal-head {" in css
    assert "justify-content: center" in css[css.index(".v2-modal-head {") :]
    assert ".v2-modal-body {" in css
    assert "font-size: 1.12rem" in css[css.index(".v2-modal-body {") :]
    assert ".v2-modal-footer {" in css
    assert "border-top: 0" in css[css.index(".v2-modal-footer {") :]
    assert ".v2-modal-compact," in css
    assert "padding: 24px 64px 12px" in css[css.index(".v2-modal-head {") :]
    assert "padding: 12px 32px" in css[css.index(".v2-modal-body {") :]
    assert "padding: 12px 32px 24px" in css[css.index(".v2-modal-footer {") :]
    assert "padding: 28px 38px 10px" in css[css.index(".v2-modal-compact .v2-modal-head") :]
    assert "padding: 10px 38px 26px" in css[css.index(".v2-modal-compact .v2-modal-body") :]
    assert "padding: 16px 38px 18px" in css[css.index(".v2-modal-compact .v2-modal-footer") :]


def test_active_v2_dialogs_use_the_shared_modal_regions():
    for template_name in (
        "projects.html",
        "hypothesis_studio.html",
        "_start_round_overlay.html",
    ):
        html = (TEMPLATES / template_name).read_text(encoding="utf-8")
        assert "v2-modal-head" in html, template_name
        assert "v2-modal-body" in html, template_name
        assert "v2-modal-footer" in html, template_name


def test_round_confirmations_compose_the_shared_compact_modal():
    js = (STATIC / "round_workspace.js").read_text(encoding="utf-8")
    dialog_js = (STATIC / "v2_dialog.js").read_text(encoding="utf-8")

    assert "window.V2Dialog.confirm" in js
    assert "v2-modal-overlay v2-confirm-overlay" in dialog_js
    assert "v2-modal v2-modal-compact" in dialog_js
    assert "v2-modal-head" in dialog_js
    assert "v2-modal-body" in dialog_js
    assert "v2-modal-footer" in dialog_js


def test_compact_confirmation_keeps_copy_aligned_with_the_modal_regions():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    dialog_js = (STATIC / "v2_dialog.js").read_text(encoding="utf-8")

    assert "v2-confirm-modal" in dialog_js
    assert "v2-confirm-copy" in dialog_js
    body_rule = css[
        css.index(".v2-modal-compact .v2-modal-body,"):
        css.index("}", css.index(".v2-modal-compact .v2-modal-body,")) + 1
    ]
    footer_rule = css[
        css.index(".v2-modal-compact .v2-modal-footer,"):
        css.index("}", css.index(".v2-modal-compact .v2-modal-footer,")) + 1
    ]
    assert "padding: 10px 38px 26px" in body_rule
    assert "padding: 16px 38px 18px" in footer_rule
    assert "border-top: 1px solid var(--v2-border-soft, var(--v2-border))" in footer_rule


def test_remaining_confirmations_use_the_shared_dialog_instead_of_browser_prompts():
    audit = (TEMPLATES / "audit_home.html").read_text(encoding="utf-8")
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")

    assert "!confirm(" not in audit
    assert "window.V2Dialog.confirm" in audit
    for template in (base, audit):
        assert "v2_dialog.js" in template


def test_project_relocation_uses_the_shared_v2_confirmation_shell():
    html = (TEMPLATES / "projects.html").read_text(encoding="utf-8")
    javascript = (STATIC / "projects.js").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")

    relocation = html[html.index('class="v2-modal-overlay v2-confirm-overlay v2-relocation-overlay"'):]
    for contract in (
        "v2-modal-overlay v2-confirm-overlay",
        "v2-modal v2-modal-compact v2-confirm-modal",
        "v2-modal-head",
        "v2-modal-body",
        "v2-modal-footer v2-confirm-actions",
        "v2-btn v2-btn-secondary v2-confirm-cancel",
    ):
        assert contract in relocation
    assert "window.confirm" not in javascript
    assert "window.alert" not in javascript
    assert ".v2-confirm-path" in css
    assert "overflow-wrap: anywhere" in css


def test_start_round_uses_separated_footer_and_right_aligned_actions():
    html = (TEMPLATES / "_start_round_overlay.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    footer = html[html.index('class="v2-modal-footer v2-blocking-actions"') :]

    assert footer.index("v2-blocking-back") < footer.index("v2-start-round-btn")
    assert 'class="v2-btn v2-btn-secondary v2-blocking-back"' in footer
    back_hover = css[css.index(".v2-blocking-back:hover,"):css.index("}", css.index(".v2-blocking-back:hover,"))]
    assert "color: var(--v2-accent)" in back_hover
    assert "text-decoration: none" in back_hover
    assert "text-decoration: underline" not in back_hover
    blocking_box = css[css.index(".v2-modal.v2-blocking-box {") :]
    assert "max-width: min(500px, calc(100vw - 48px))" in blocking_box
    assert "aspect-ratio" not in blocking_box[:200]
    assert "border-radius: 0" not in blocking_box[:200]
    assert ".v2-modal.v2-blocking-box .v2-modal-title" in blocking_box
    assert "margin: 0" in blocking_box
    blocking_body = css[css.index(".v2-blocking-body {"):css.index("}", css.index(".v2-blocking-body {"))]
    assert "margin: 0" in blocking_body
    assert "max-width: none" in blocking_body
    shared_compact = css[css.index(".v2-modal-compact .v2-modal-head,"):css.index(".v2-modal.v2-blocking-box {")]
    assert ".v2-modal.v2-blocking-box .v2-modal-head" in shared_compact
    assert ".v2-modal.v2-blocking-box .v2-modal-body" in shared_compact
    assert ".v2-modal.v2-blocking-box .v2-modal-footer" in shared_compact
    assert "padding: 28px 38px 10px" in shared_compact
    assert "padding: 10px 38px 26px" in shared_compact
    assert "justify-content: flex-end" in shared_compact
    assert "padding: 16px 38px 18px" in shared_compact
    assert "margin-top: 0" in shared_compact
    assert "border-top: 1px solid var(--v2-border-soft, var(--v2-border))" in shared_compact


def test_first_round_overlay_centers_title_left_aligns_body_and_right_aligns_actions():
    studio = (TEMPLATES / "hypothesis_studio.html").read_text(encoding="utf-8")
    workspace = (TEMPLATES / "round_workspace.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    shared_lead = (
        "DiscoveryAgent works best when it begins with an exploratory round "
        "to understand your data. "
    )
    studio_copy = shared_lead + (
        "Once the round is complete, you can use Hypothesis Studio to choose "
        "what DiscoveryAgent investigates next."
    )
    workspace_copy = shared_lead + (
        "Once the round is in progress, you can use "
        "Round Workspace to see what DiscoveryAgent is doing."
    )

    assert studio_copy in studio
    assert workspace_copy in workspace
    assert workspace_copy not in studio
    assert studio_copy not in workspace
    title = css[css.index("\n.v2-modal-title {"):css.index("}", css.index("\n.v2-modal-title {"))]
    body = css[css.index("\n.v2-modal-body {"):css.index("}", css.index("\n.v2-modal-body {"))]
    actions_start = css.index(".v2-modal.v2-blocking-box .v2-modal-footer {")
    actions = css[actions_start:css.index("}", actions_start)]
    assert "text-align: center" in title
    assert "text-align: left" in body
    assert "justify-content: flex-end" in actions


def test_shared_dialog_shells_use_the_warm_v2_surface_and_shared_typography():
    css = (STATIC / "style.css").read_text(encoding="utf-8")

    assert "--v2-surface: #fffdf8" in css
    v2_modal = css[css.index(".v2-modal {"):css.index("}", css.index(".v2-modal {"))]
    legacy_modal = css[css.index(".modal-content {"):css.index("}", css.index(".modal-content {"))]
    legacy_footer = css[css.index(".modal-footer {"):css.index("}", css.index(".modal-footer {"))]
    popup_card = css[css.index(".confirm-overlay-card {"):css.index("}", css.index(".confirm-overlay-card {"))]
    blocking_box = css[css.index(".v2-blocking-box {"):css.index("}", css.index(".v2-blocking-box {"))]
    compact_head_start = css.index(".v2-modal-compact .v2-modal-head,")
    compact_head = css[compact_head_start:css.index("}", compact_head_start)]
    overlay = css[css.index(".v2-modal-overlay {"):css.index("}", css.index(".v2-modal-overlay {"))]
    objective_input = css[css.index("#objective-input {"):css.index("}", css.index("#objective-input {"))]

    for shell in (v2_modal, legacy_modal, legacy_footer, popup_card):
        assert "background: var(--v2-surface, #fffdf8)" in shell
        assert "background: white" not in shell
        assert "background: #fff" not in shell
    assert "background:" not in blocking_box
    assert "justify-content: center" in compact_head
    assert "background: rgba(41, 34, 22, 0.42)" in overlay
    assert "background: var(--v2-bg, var(--background))" in objective_input


def test_every_modal_back_or_cancel_next_to_primary_uses_secondary_button():
    projects = (TEMPLATES / "projects.html").read_text(encoding="utf-8")
    studio = (TEMPLATES / "hypothesis_studio.html").read_text(encoding="utf-8")
    start_round = (TEMPLATES / "_start_round_overlay.html").read_text(encoding="utf-8")
    dialog = (STATIC / "v2_dialog.js").read_text(encoding="utf-8")

    for button_id in ("delete-project-cancel", "np-cancel"):
        tag = projects[projects.rfind("<button", 0, projects.index(f'id="{button_id}"')):projects.index(">", projects.index(f'id="{button_id}"')) + 1]
        assert "v2-btn-secondary" in tag
    assert 'class="v2-btn v2-btn-secondary" id="hs-review-back"' in studio
    assert 'class="v2-btn v2-btn-secondary v2-blocking-back"' in start_round
    assert "make('button', 'v2-btn v2-btn-secondary', config.cancelLabel)" in dialog


def test_hypothesis_review_uses_ready_title_without_final_review_eyebrow():
    studio = (TEMPLATES / "hypothesis_studio.html").read_text(encoding="utf-8")
    css = (STATIC / "hypothesis_studio.css").read_text(encoding="utf-8")
    review = studio[studio.index('id="hs-review"'):studio.index('id="hs-review-back"')]

    assert 'id="hs-review-title">Ready for investigation?</h2>' in review
    assert "Final review" not in review
    assert "v2-modal-eyebrow" not in review
    header = re.search(r"\.hs-review\s*>\s*\.v2-modal-head\s*\{([^}]*)\}", css)
    assert header is not None
    assert re.search(r"\bpadding-bottom\s*:\s*24px\s*;", header.group(1))
    assert re.search(r"\bflex\s*:\s*0\s+0\s+auto\s*;", header.group(1))


def test_hypothesis_review_opens_at_the_top_for_long_content():
    studio = (TEMPLATES / "hypothesis_studio.html").read_text(encoding="utf-8")
    js = (STATIC / "hypothesis_studio.js").read_text(encoding="utf-8")
    open_review = js[js.index("function resetReviewScrollPosition"):
                     js.index("function closeReview")]

    assert 'class="v2-modal hs-review" role="dialog"' in studio
    assert 'aria-labelledby="hs-review-title" tabindex="-1"' in studio
    assert "els.review.scrollTop = 0;" in open_review
    assert "if (dialog) dialog.scrollTop = 0;" in open_review
    assert "if (body) body.scrollTop = 0;" in open_review
    assert "dialog.focus({ preventScroll: true })" in open_review
    assert "requestAnimationFrame(resetReviewScrollPosition);" in open_review
    assert "els.reviewConfirm.focus()" not in open_review


def test_hypothesis_review_uses_one_scientific_body_style_for_both_sections():
    studio = (TEMPLATES / "hypothesis_studio.html").read_text(encoding="utf-8")
    css = (STATIC / "hypothesis_studio.css").read_text(encoding="utf-8")
    js = (STATIC / "hypothesis_studio.js").read_text(encoding="utf-8")
    review = studio[studio.index('id="hs-review"'):studio.index('id="hs-review-back"')]

    assert review.count('class="v2-prose hs-review-scientific-content"') == 2
    assert 'class="v2-prose hs-review-hypothesis"' not in review
    assert 'class="v2-prose hs-review-justification"' not in review
    assert ".hs-review-scientific-content {" in css
    assert "font-family: var(--v2-serif);" in css
    assert "font-size: 1.12rem;" in css
    assert "line-height: 1.62;" in css
    assert "font-weight: 400;" in css
    assert "color: var(--v2-ink);" in css
    assert ".hs-review-hypothesis {" not in css
    assert ".hs-review-justification {" not in css
    # Both content paths retain Markdown rather than flattening emphasis or
    # paragraph structure to textContent.
    assert "els.reviewHypothesis.innerHTML = renderMarkdown(" in js
    assert "els.reviewJustification.innerHTML = renderMarkdown(" in js


def test_post_selection_start_prompt_reuses_delete_project_confirmation_shell():
    projects = (TEMPLATES / "projects.html").read_text(encoding="utf-8")
    studio = (TEMPLATES / "hypothesis_studio.html").read_text(encoding="utf-8")
    delete_id = projects.index('id="delete-project-overlay"')
    start_id = studio.index('id="hs-start-round-overlay"')
    delete_shell = projects[projects.rfind("<div", 0, delete_id):
                            projects.index('<!-- New Project modal -->', delete_id)]
    start_shell = studio[studio.rfind("<div", 0, start_id):
                         studio.index("{% if initial_exploration %}", start_id)]

    for class_name in (
        "v2-modal-overlay v2-confirm-overlay",
        "v2-modal v2-modal-compact v2-confirm-modal",
        "v2-modal-head",
        "v2-modal-title",
        "v2-modal-body",
        "v2-confirm-copy",
        "v2-modal-footer v2-confirm-actions",
        "v2-btn v2-btn-secondary v2-confirm-cancel",
    ):
        assert class_name in delete_shell
        assert class_name in start_shell
    assert "v2-btn v2-btn-primary v2-start-round-btn" in start_shell


def test_hypothesis_manual_edit_buttons_share_project_modal_button_size():
    studio_js = (STATIC / "hypothesis_studio.js").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    studio_css = (STATIC / "hypothesis_studio.css").read_text(encoding="utf-8")

    assert "hs-manual-edit-actions v2-modal-action-group" in studio_js
    assert "'Save Changes'" in studio_js
    shared_rule = css[css.index(".v2-modal-footer .v2-btn,"):
                      css.index("/* Confirmation dialogs", css.index(".v2-modal-footer .v2-btn,"))]
    assert ".v2-modal-action-group .v2-btn" in shared_rule
    for declaration in (
        "min-height: 32px",
        "padding: 7px 13px",
        "border-radius: 7px",
        "font-size: 0.8rem",
        "font-weight: 650",
    ):
        assert declaration in shared_rule

    manual_actions = studio_css[studio_css.index(".hs-manual-edit-actions {"):
                                studio_css.index("}", studio_css.index(".hs-manual-edit-actions {"))]
    assert "align-items: center" in manual_actions
    assert "justify-content: flex-end" in manual_actions
    assert "min-height: 56px" in manual_actions


def test_current_v2_labels_and_round_report_feedback_surface():
    projects_js = (STATIC / "projects.js").read_text(encoding="utf-8")
    audit = (TEMPLATES / "round_workspace.html").read_text(encoding="utf-8")
    legacy_audit = (TEMPLATES / "audit_home.html").read_text(encoding="utf-8")

    assert "np-eyebrow" not in projects_js
    assert "np-eyebrow" not in (TEMPLATES / "projects.html").read_text(encoding="utf-8")
    assert "AUDIT AGENT" in audit
    assert "Guide" not in audit
    assert "Guide" not in legacy_audit
    assert not (TEMPLATES / "report.html").exists()
