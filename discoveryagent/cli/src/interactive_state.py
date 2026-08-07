"""
Interactive session state management.

Tracks tool calls, manages resumable snapshots, and accumulates the research
report for the interactive CLI mode where a human scientist replaces the
DirectorAgent.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .session_store import (
    SNAPSHOT_PREFIX,
    extract_snapshot_id,
    get_queries_root,
    get_report_path,
    get_snapshots_root,
    get_state_root,
)


class InteractiveState:
    """Manages in-memory interactive state plus session-root snapshot storage."""

    def __init__(
        self,
        log_dir: str,
        dataset_description: str = "",
        task_description: Optional[str] = None,
        memory_manager=None,
        logger=None,
        resume_from: Optional[str] = None,
        session_dir: Optional[str] = None,
        session_metadata_path: Optional[str] = None,
    ):
        self.log_dir = Path(log_dir).expanduser().resolve()
        self.dataset_description = dataset_description
        self.task_description = task_description
        self.memory_manager = memory_manager
        self.logger = logger
        self.report_text: str = ""

        self.session_dir = self._resolve_session_dir(
            session_dir=session_dir,
            session_metadata_path=session_metadata_path,
        )
        self.state_root = (
            get_state_root(self.session_dir)
            if self.session_dir is not None
            else (self.log_dir / "checkpoints" / "interactive")
        )
        self.snapshots_root = (
            get_snapshots_root(self.session_dir)
            if self.session_dir is not None
            else self.state_root
        )
        self.queries_root = (
            get_queries_root(self.session_dir)
            if self.session_dir is not None
            else self.state_root
        )
        self.report_path = (
            get_report_path(self.session_dir)
            if self.session_dir is not None
            else (self.state_root / "memory_report.md")
        )
        self.latest_link = self.state_root / "latest"

        self.state_root.mkdir(parents=True, exist_ok=True)
        self.snapshots_root.mkdir(parents=True, exist_ok=True)
        self.queries_root.mkdir(parents=True, exist_ok=True)

        # State
        self.query_count: int = 0
        self.tool_call_history: List[Dict[str, Any]] = []
        self.results: List[Dict[str, Any]] = []
        self.images: Dict[str, str] = {}
        self.review_history: List[Dict[str, Any]] = []
        self.current_snapshot_id: Optional[str] = None
        self.current_snapshot_path: Optional[Path] = None
        self._snapshot_sequence: int = self._discover_latest_snapshot_sequence()

        if resume_from:
            self.load_checkpoint(resume_from)
        else:
            self._materialize_report_view()

    @property
    def accumulated_report(self) -> str:
        if self.memory_manager is not None:
            return self.memory_manager.get_report() or ""
        return self.report_text

    @property
    def report_file_path(self) -> Path:
        return self.report_path

    def record_tool_call(
        self,
        tool_name: str,
        query: str,
        result: str,
        error: bool = False,
        duration_seconds: float = 0.0,
    ) -> str:
        """Record a tool call and persist a resumable snapshot."""
        timestamp = datetime.datetime.now().isoformat()
        query_num = self.query_count
        self.query_count += 1

        record = {
            "query_num": query_num,
            "timestamp": timestamp,
            "tool_name": tool_name,
            "query_raw": query,
            "result": result,
            "error": error,
            "duration_seconds": duration_seconds,
        }
        self.results.append(record)
        self.tool_call_history.append(record)

        query_dir = self.get_query_dir(query_num)
        query_dir.mkdir(parents=True, exist_ok=True)
        query_markdown = self._write_query_markdown(
            query_num=query_num,
            tool_name=tool_name,
            query=query,
            result=result,
            error=error,
            timestamp=timestamp,
            query_dir=query_dir,
        )

        checkpoint_dir = self.save_checkpoint(
            query_num,
            trigger="auto_tool",
            tool_record=record,
            query_dir=query_dir,
            query_markdown_path=query_markdown,
        )
        return checkpoint_dir

    def update_report(self, new_content: str) -> str:
        """Accumulate findings into the research report or manual notes buffer."""
        if self.memory_manager is None:
            new_content = (new_content or "").strip()
            if not new_content:
                return self.report_text
            if self.report_text:
                self.report_text = f"{self.report_text}\n\n{new_content}"
            else:
                self.report_text = new_content
            report = self.report_text
        else:
            report = self.memory_manager.update_report(new_content)

        self._materialize_report_view(report)
        if self.logger:
            self.logger.log_action("memory_report_updated", f"Report updated ({len(report)} chars)")
        return report

    def get_report(self) -> str:
        """Return the current accumulated research report."""
        return self.accumulated_report

    def save_checkpoint(
        self,
        query_num: int,
        *,
        trigger: str = "manual",
        tool_record: Optional[Dict[str, Any]] = None,
        query_dir: Optional[Path] = None,
        query_markdown_path: Optional[Path] = None,
    ) -> str:
        """Persist a snapshot and return its directory path."""
        if self.session_dir is None:
            return self._save_legacy_checkpoint(query_num)

        snapshot_id = self._allocate_snapshot_id()
        snapshot_dir = self.snapshots_root / snapshot_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        checkpoint_data = self._create_checkpoint_data(
            query_num=query_num,
            snapshot_id=snapshot_id,
            trigger=trigger,
            tool_record=tool_record,
            query_dir=query_dir,
            query_markdown_path=query_markdown_path,
        )

        snapshot_path = snapshot_dir / "snapshot.json"
        checkpoint_path = snapshot_dir / "checkpoint.json"
        for path in (snapshot_path, checkpoint_path):
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(checkpoint_data, handle, indent=2, ensure_ascii=False, default=str)

        if tool_record is not None:
            summary_md_path = snapshot_dir / "summary.md"
            summary_md_path.write_text(
                self._build_query_markdown(
                    query_num=tool_record["query_num"],
                    tool_name=tool_record["tool_name"],
                    query=tool_record["query_raw"],
                    result=tool_record["result"],
                    error=tool_record["error"],
                    timestamp=tool_record["timestamp"],
                ),
                encoding="utf-8",
            )

        self._update_latest_link(snapshot_dir)
        self.current_snapshot_id = snapshot_id
        self.current_snapshot_path = snapshot_dir

        if self.logger:
            self.logger.log_action(
                "checkpoint_saved",
                f"Interactive snapshot saved to {snapshot_path}",
            )

        return str(snapshot_dir)

    def load_checkpoint(self, checkpoint_dir: str) -> bool:
        """Restore state from a previous interactive snapshot or legacy checkpoint."""
        checkpoint_path = self._resolve_checkpoint_path(checkpoint_dir)
        if checkpoint_path is None or not checkpoint_path.exists():
            return False

        try:
            with open(checkpoint_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)

            snapshot_state = data.get("state", {})
            workflow_state = data.get("workflow_state", {})

            next_query_num = snapshot_state.get("next_query_num")
            if next_query_num is None:
                next_query_num = workflow_state.get("query_num", 0) + 1

            results = snapshot_state.get("tool_history")
            if results is None:
                results = workflow_state.get("results", [])

            report = snapshot_state.get("report")
            if report is None:
                report = workflow_state.get("cumulative_report") or workflow_state.get("report", "")

            images = snapshot_state.get("images")
            if images is None:
                images = workflow_state.get("images", {})

            review_history = snapshot_state.get("review_history")
            if review_history is None:
                review_history = workflow_state.get("review_history", [])

            self.query_count = int(next_query_num or 0)
            self.results = list(results or [])
            self.images = dict(images or {})
            self.review_history = list(review_history or [])
            self.tool_call_history = self.results.copy()

            if report:
                if self.memory_manager is not None:
                    self.memory_manager.current_report = report
                else:
                    self.report_text = report
            else:
                self.report_text = ""

            self.current_snapshot_id = data.get("snapshot_id") or extract_snapshot_id(checkpoint_path)
            self.current_snapshot_path = checkpoint_path.parent if checkpoint_path.parent != checkpoint_path else None
            self._snapshot_sequence = max(
                self._snapshot_sequence,
                self._sequence_from_snapshot_id(self.current_snapshot_id),
            )
            self._materialize_report_view()

            if self.logger:
                self.logger.log_action(
                    "checkpoint_loaded",
                    f"Restored interactive state from {checkpoint_path} (query_count={self.query_count})",
                )
            return True
        except Exception as exc:
            if self.logger:
                self.logger.log_error(f"Failed to load checkpoint: {exc}")
            return False

    def get_statistics(self) -> Dict[str, Any]:
        """Return session statistics."""
        stats = {
            "query_count": self.query_count,
            "tool_calls": len(self.tool_call_history),
            "report_length": len(self.accumulated_report),
            "has_report": bool(self.accumulated_report),
        }
        if self.memory_manager is not None:
            mem_stats = self.memory_manager.get_statistics()
            stats.update(mem_stats)
        return stats

    def get_query_dir(self, query_num: int) -> Path:
        return self.queries_root / f"query_{query_num:03d}"

    def get_checkpoint_dir(self, query_num: int) -> Path:
        """Return the per-query artifact directory for a given query number."""
        return self.get_query_dir(query_num)

    # -- Private helpers --

    def _resolve_session_dir(
        self,
        *,
        session_dir: Optional[str],
        session_metadata_path: Optional[str],
    ) -> Optional[Path]:
        if session_dir:
            return Path(session_dir).expanduser().resolve()
        if session_metadata_path:
            return Path(session_metadata_path).expanduser().resolve().parent
        return None

    def _create_checkpoint_data(
        self,
        *,
        query_num: int,
        snapshot_id: Optional[str],
        trigger: str,
        tool_record: Optional[Dict[str, Any]],
        query_dir: Optional[Path],
        query_markdown_path: Optional[Path],
    ) -> Dict[str, Any]:
        workflow_state = {
            "dataset_description": self.dataset_description,
            "task_description": self.task_description,
            "query_num": query_num,
            "results": self.results,
            "report": self.accumulated_report,
            "cumulative_report": self.accumulated_report,
            "images": self.images,
            "review_history": self.review_history,
        }

        state_payload = {
            "next_query_num": self.query_count,
            "tool_history": self.results,
            "report": self.accumulated_report,
            "images": self.images,
            "review_history": self.review_history,
        }

        artifacts = {
            "report_path": self._relativize(self.report_path),
        }
        if query_dir is not None:
            artifacts["query_dir"] = self._relativize(query_dir)
        if query_markdown_path is not None:
            artifacts["query_markdown"] = self._relativize(query_markdown_path)

        summary = {
            "trigger": trigger,
            "query_num": query_num,
            "tool_name": tool_record.get("tool_name") if tool_record else None,
            "query_preview": (tool_record.get("query_raw", "")[:200] if tool_record else ""),
            "success": (not tool_record.get("error", False)) if tool_record else True,
            "duration_seconds": tool_record.get("duration_seconds") if tool_record else None,
        }

        checkpoint = {
            "checkpoint_version": "3.0",
            "timestamp": datetime.datetime.now().isoformat(),
            "query_num": query_num,
            "mode": "interactive",
            "snapshot_id": snapshot_id,
            "parent_snapshot_id": self.current_snapshot_id,
            "trigger": trigger,
            "state": state_payload,
            "summary": summary,
            "artifacts": artifacts,
            "workflow_state": workflow_state,
            "agents": {},
            "validation": {
                "state_hashes": {},
                "checkpoint_hash": "",
            },
        }

        checkpoint_json = json.dumps(checkpoint, sort_keys=True, default=str)
        checkpoint["validation"]["checkpoint_hash"] = hashlib.sha256(
            checkpoint_json.encode()
        ).hexdigest()
        return checkpoint

    def _write_query_markdown(
        self,
        *,
        query_num: int,
        tool_name: str,
        query: str,
        result: str,
        error: bool,
        timestamp: str,
        query_dir: Path,
    ) -> Path:
        markdown = self._build_query_markdown(
            query_num=query_num,
            tool_name=tool_name,
            query=query,
            result=result,
            error=error,
            timestamp=timestamp,
        )
        md_path = query_dir / f"query_{query_num:03d}.md"
        md_path.write_text(markdown, encoding="utf-8")
        return md_path

    def _build_query_markdown(
        self,
        *,
        query_num: int,
        tool_name: str,
        query: str,
        result: str,
        error: bool,
        timestamp: str,
    ) -> str:
        markdown = f"# Query {query_num:03d}\n\n"
        markdown += f"**Timestamp:** {timestamp}\n"
        markdown += f"**Tool:** {tool_name}\n"
        markdown += f"**Status:** {'ERROR' if error else 'OK'}\n\n"
        markdown += f"## Query\n\n{query}\n\n"
        markdown += f"## Result\n\n{result}\n"
        return markdown

    def _save_legacy_checkpoint(self, query_num: int) -> str:
        checkpoint_dir = self.state_root / f"query_{query_num:03d}"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_data = self._create_checkpoint_data(
            query_num=query_num,
            snapshot_id=None,
            trigger="legacy",
            tool_record=None,
            query_dir=checkpoint_dir,
            query_markdown_path=None,
        )
        checkpoint_path = checkpoint_dir / "checkpoint.json"
        with open(checkpoint_path, "w", encoding="utf-8") as handle:
            json.dump(checkpoint_data, handle, indent=2, ensure_ascii=False, default=str)
        self._update_latest_link(checkpoint_dir)
        return str(checkpoint_dir)

    def _relativize(self, path: Path) -> str:
        path = path.expanduser().resolve()
        if self.session_dir is not None:
            try:
                return str(path.relative_to(self.session_dir))
            except ValueError:
                pass
        try:
            return str(path.relative_to(self.state_root))
        except ValueError:
            return str(path)

    def _resolve_checkpoint_path(self, checkpoint_dir: str | Path) -> Optional[Path]:
        candidate = Path(checkpoint_dir).expanduser().resolve()
        if candidate.is_dir():
            for filename in ("snapshot.json", "checkpoint.json"):
                candidate_file = candidate / filename
                if candidate_file.exists():
                    return candidate_file
            return None
        if candidate.exists():
            return candidate
        return None

    def _discover_latest_snapshot_sequence(self) -> int:
        latest = 0
        if not self.snapshots_root.exists():
            return latest
        for entry in self.snapshots_root.iterdir():
            if not entry.is_dir() or not entry.name.startswith(SNAPSHOT_PREFIX):
                continue
            latest = max(latest, self._sequence_from_snapshot_id(entry.name))
        return latest

    def _allocate_snapshot_id(self) -> str:
        self._snapshot_sequence = self._discover_latest_snapshot_sequence() + 1
        return f"{SNAPSHOT_PREFIX}{self._snapshot_sequence:06d}"

    def _sequence_from_snapshot_id(self, snapshot_id: Optional[str]) -> int:
        if not snapshot_id or not snapshot_id.startswith(SNAPSHOT_PREFIX):
            return 0
        suffix = snapshot_id[len(SNAPSHOT_PREFIX):]
        return int(suffix) if suffix.isdigit() else 0

    def _update_latest_link(self, target_dir: Path) -> None:
        try:
            if self.latest_link.is_symlink() or self.latest_link.exists():
                self.latest_link.unlink()
            self.latest_link.symlink_to(target_dir.relative_to(self.state_root))
        except OSError:
            return

    def _materialize_report_view(self, report: Optional[str] = None) -> None:
        report = self.accumulated_report if report is None else report
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(report or "", encoding="utf-8")
