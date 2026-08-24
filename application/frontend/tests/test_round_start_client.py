from pathlib import Path

from da_selection_ui import app as app_module


def test_every_start_surface_uses_the_shared_client():
    root = Path(app_module.__file__).resolve().parent
    templates = root / "templates"
    for name in ("project_home.html", "hypothesis_studio.html", "round_workspace.html", "_start_round_overlay.html"):
        source = (templates / name).read_text(encoding="utf-8")
        assert "round_start.js" in source or "data-start-round" in source

    javascript = (root / "static" / "round_start.js").read_text(encoding="utf-8")
    assert "fetch('/api/rounds/start'" in javascript
    assert "btn.disabled = true" in javascript
    assert "btn.disabled = false" in javascript
    assert "if (!btn || pending) return" in javascript
    assert "setDismissDisabled(btn, true)" in javascript
    assert "setDismissDisabled(btn, false)" in javascript
    assert "window.location.assign(result.redirect_url" in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript


def test_resume_surface_displays_structured_launch_error_in_place():
    root = Path(app_module.__file__).resolve().parent
    javascript = (root / "static" / "round_workspace.js").read_text(encoding="utf-8")
    assert "data.message || data.error" in javascript
    assert "showRoundActionError(button,error)" in javascript
    assert "message.setAttribute('role','alert')" in javascript
    assert "location.href = '/settings" not in javascript


def test_post_selection_research_status_uses_shared_start_action():
    status = app_module._v2_research_status(
        "hypothesis_selected",
        round_number=4,
        studio_url="/projects/example/hypotheses",
        workspace_url="/rounds/?project=example",
    )
    assert status["cta_label"] == "Start Round 4"
    assert status["cta_action"] == "start_round"
    assert status["cta_destination"] is None
    assert status["cta_round"] == 4


def test_studio_post_selection_prompt_composes_delete_modal_and_shared_launcher():
    root = Path(app_module.__file__).resolve().parent
    html = (root / "templates" / "hypothesis_studio.html").read_text(encoding="utf-8")
    javascript = (root / "static" / "hypothesis_studio.js").read_text(encoding="utf-8")
    modal_id = html.index('id="hs-start-round-overlay"')
    modal = html[html.rfind("<div", 0, modal_id):html.index("{% if initial_exploration %}", modal_id)]

    assert 'class="v2-modal-overlay v2-confirm-overlay"' in html
    assert 'class="v2-modal v2-modal-compact v2-confirm-modal"' in modal
    assert 'class="v2-modal-head"' in modal
    assert 'class="v2-modal-body"' in modal
    assert 'class="v2-modal-footer v2-confirm-actions"' in modal
    assert 'id="hs-start-round-not-now" data-start-round-dismiss' in modal
    assert 'id="hs-start-round-confirm" data-start-round' in modal
    assert "fetch('/api/rounds/start'" not in javascript


def test_successful_selection_stays_in_studio_and_only_then_opens_start_prompt():
    root = Path(app_module.__file__).resolve().parent
    javascript = (root / "static" / "hypothesis_studio.js").read_text(encoding="utf-8")
    confirm = javascript[javascript.index("async function confirmReview()"):
                         javascript.index("// ---- shell visibility", javascript.index("async function confirmReview()"))]
    success = confirm[confirm.index("if (resp.ok && data && data.success)"):confirm.index("} else {", confirm.index("if (resp.ok && data && data.success)"))]

    assert "state.events.push({ kind: 'selection'" in success
    assert "render();" in success
    assert "openStartRoundModal(selectedRoundNumber(data));" in success
    assert success.index("state.events.push") < success.index("openStartRoundModal")
    assert "window.location" not in success
    assert "'/projects/'" not in success
    assert "openStartRoundModal" not in confirm[confirm.index("} else {"):]


def test_not_now_is_presentation_only_and_cannot_launch_or_clear_selection():
    root = Path(app_module.__file__).resolve().parent
    javascript = (root / "static" / "hypothesis_studio.js").read_text(encoding="utf-8")
    close = javascript[javascript.index("function closeStartRoundModal()"):
                       javascript.index("async function confirmReview()")]

    assert "els.startRoundOverlay.hidden = true" in close
    assert "state.events" not in close
    assert "fetch(" not in close
    assert "DAStartRound" not in close
    assert "els.startRoundNotNow.addEventListener('click', closeStartRoundModal)" in javascript
