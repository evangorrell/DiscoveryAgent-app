"""Run the Round Workspace chat state JS regression tests under pytest."""

import shutil
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
JS_TEST = REPO / "tests" / "round_workspace_chat_state.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_round_workspace_chat_state_js_unit_tests():
    result = subprocess.run(
        ["node", "--test", str(JS_TEST)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_round_workspace_chat_wires_one_non_replaying_request_path():
    static = REPO / "da_selection_ui" / "static"
    script = (static / "round_workspace.js").read_text(encoding="utf-8")

    assert "fetch('/api/round_chat_jobs?round='" in script
    assert "keepalive:true" in script
    assert "pollRoundChatJob(round,pending)" in script
    assert "resumePendingRoundChatJobs()" in script
    assert "fetch(roundChatJobUrl(pending.jobId,round)" in script
    assert "roundUrl('/api/round_chat'" not in script
    assert "resumeInterruptedChats" not in script
    assert "if(!chat||userMessage.sent)return" in script
    assert "request_id:userMessage.id" in script and "job_id:pending.jobId" in script
    assert "els.input.disabled=false" in script
    assert "ChatState.composerControlState" in script
    assert "button.dataset.action=control.action" in script
    assert "control.icon==='stop'" in script
    assert "ChatState.performComposerAction" in script
    assert "ChatState.composerKeyboardAction" in script
    assert "els.cancel" not in script
    assert "rw-chat-cancel" not in script
    assert "if(state.inFlight.has(round)){cancelRoundChatJob(round);return;}" not in script
    assert "pausedQueueStorageKey" not in script
    assert "persistPausedQueues" not in script
    assert "pausedQueues" not in script
    assert "Queue paused because you interrupted" not in script
    assert "rw-queue-resume" not in script
    assert "syncRoundChatJobState(round,pending,payload.status||'pending')" in script
    assert "payload.next_job_id" in script
    assert "syncRoundChatJobState(round,next,'pending')" in script
    assert "roundChatResetUrl" in script
    assert "state.ignoredJobIds.has(pending.jobId)" in script
    assert "state.inFlight.delete(round);state.chats.delete(round)" in script
    assert "els.reset.addEventListener('click',()=>resetRoundChat(state.activeRound))" in script
    assert "ChatState.persistWithConflict(snapshot,writeDurableChats,readDurableChats)" in script
