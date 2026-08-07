**Claude Agent SDK: Session Logging Guide**
This guide shows how to log all agent actions (tool calls + transcript) into a per-run session folder. It is framework-agnostic and can be dropped into any Claude Agent SDK project.

**Goals**
1. Create a timestamped session directory.
2. Write a human-readable transcript log.
3. Capture every tool call (inputs + outputs) in JSONL via SDK hooks.

**Overview**
You will add:
- `setup_session()` to create `logs/session_YYYYMMDD_HHMMSS/`
- `TranscriptWriter` to mirror console output into `transcript.txt`
- `ToolTracker` to capture `PreToolUse` and `PostToolUse` events into `tool_calls.jsonl`
- Hook wiring in `ClaudeAgentOptions`

**1) Session + Transcript Helpers**
Create a small module (e.g., `utils/session.py`) to set up the session and transcript.

```python
from datetime import datetime
from pathlib import Path


def setup_session(workspace: Path):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = workspace / "logs" / f"session_{timestamp}"
    session_dir.mkdir(parents=True, exist_ok=True)
    transcript_file = session_dir / "transcript.txt"
    return transcript_file, session_dir


class TranscriptWriter:
    def __init__(self, transcript_file: Path):
        self.file = open(transcript_file, "w", encoding="utf-8")
        self._closed = False

    def write(self, text: str, end: str = "", flush: bool = True):
        if self._closed:
            raise RuntimeError("TranscriptWriter is closed")
        print(text, end=end, flush=flush)
        self.file.write(text + end)
        if flush:
            self.file.flush()

    def write_to_file(self, text: str, flush: bool = True):
        if self._closed:
            raise RuntimeError("TranscriptWriter is closed")
        self.file.write(text)
        if flush:
            self.file.flush()

    def close(self):
        if not self._closed:
            self.file.close()
            self._closed = True
```

**2) Tool Tracking via SDK Hooks**
Create a `ToolTracker` to log all tool calls in JSONL and also write brief lines to the transcript.

```python
import json
from datetime import datetime
from pathlib import Path


class ToolTracker:
    def __init__(self, session_dir: Path, transcript):
        self.session_dir = session_dir
        self.transcript = transcript
        self.tool_log_file = open(session_dir / "tool_calls.jsonl", "w", encoding="utf-8")
        self._current_phase = "initialization"
        self._current_agent = "LEAD-AGENT"
        self._tool_records = {}

    def set_context(self, phase: str, agent_id: str = "LEAD-AGENT"):
        self._current_phase = phase
        self._current_agent = agent_id

    def _log_to_console(self, agent_id: str, tool_name: str, tool_input=None):
        self.transcript.write(f"\n[{agent_id}] -> {tool_name}")
        if tool_input:
            self.transcript.write_to_file(f"    Input: {tool_input}\n")

    def _log_to_jsonl(self, entry):
        self.tool_log_file.write(json.dumps(entry, default=str) + "\n")
        self.tool_log_file.flush()

    async def pre_tool_use_hook(self, hook_input, tool_use_id, context):
        tool_name = hook_input["tool_name"]
        tool_input = hook_input["tool_input"]
        timestamp = datetime.now().isoformat()

        self._tool_records[tool_use_id] = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "timestamp_start": timestamp,
            "phase": self._current_phase,
            "agent": self._current_agent,
        }

        self._log_to_console(self._current_agent, tool_name, tool_input)
        self._log_to_jsonl({
            "event": "tool_call_start",
            "timestamp": timestamp,
            "tool_use_id": tool_use_id,
            "phase": self._current_phase,
            "agent": self._current_agent,
            "tool_name": tool_name,
            "tool_input": tool_input,
        })
        return {"continue_": True}

    async def post_tool_use_hook(self, hook_input, tool_use_id, context):
        tool_response = hook_input.get("tool_response")
        timestamp = datetime.now().isoformat()
        record = self._tool_records.get(tool_use_id, {})
        tool_name = record.get("tool_name", "unknown")

        error = None
        if isinstance(tool_response, dict) and "error" in tool_response:
            error = tool_response["error"]

        self._log_to_jsonl({
            "event": "tool_call_complete",
            "timestamp": timestamp,
            "tool_use_id": tool_use_id,
            "phase": self._current_phase,
            "agent": self._current_agent,
            "tool_name": tool_name,
            "success": error is None,
            "error": error,
            "output_size": len(str(tool_response)) if tool_response else 0,
            "output": tool_response,
        })

        self._tool_records.pop(tool_use_id, None)
        return {"continue_": True}

    def close(self):
        if self.tool_log_file and not self.tool_log_file.closed:
            self.tool_log_file.close()
```

**3) Wire Hooks into the SDK**
Attach the hooks in your orchestrator where you create `ClaudeAgentOptions`.

```python
from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
from pathlib import Path

from utils.session import setup_session, TranscriptWriter
from utils.tool_tracker import ToolTracker


workspace = Path("/path/to/workspace")
transcript_file, session_dir = setup_session(workspace)
transcript = TranscriptWriter(transcript_file)

tracker = ToolTracker(session_dir, transcript)
tracker.set_context("orchestration", "LEAD-AGENT")

hooks = {
    "PreToolUse": [HookMatcher(matcher=None, hooks=[tracker.pre_tool_use_hook])],
    "PostToolUse": [HookMatcher(matcher=None, hooks=[tracker.post_tool_use_hook])],
}

options = ClaudeAgentOptions(
    system_prompt="...",
    allowed_tools=["Task", "Read", "Write", "Bash", "WebSearch", "WebFetch"],
    hooks=hooks,
    # other options...
)
```

**4) Log Assistant Messages to Transcript**
As you stream messages, write text blocks to transcript. This gives you a readable “conversation” log while tool calls are captured by hooks.

```python
async for msg in client.receive_response():
    if type(msg).__name__ == "AssistantMessage":
        for block in msg.content:
            if type(block).__name__ == "TextBlock":
                transcript.write(block.text, end="")
```

**5) Cleanup**
Close logs in a `finally` block.

```python
finally:
    transcript.write(f"\n\nSession logs saved to: {session_dir}\n")
    transcript.close()
    tracker.close()
```

**Expected Output Structure**
```
workspace/
  logs/
    session_YYYYMMDD_HHMMSS/
      transcript.txt
      tool_calls.jsonl
```

**Notes**
- JSONL format is ideal for streaming analysis and later audit/debugging.
- The hook approach captures *all* tool calls, including those from subagents, as long as they share the same SDK session and hooks.
- You can extend `ToolTracker` with `phase` and `agent` context if your system spawns multiple roles.
