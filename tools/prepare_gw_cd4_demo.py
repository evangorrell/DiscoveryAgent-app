#!/usr/bin/env python3
"""Prepare a private, repeatable Round 13 Studio using the archived real trace.

Run with the frontend environment (or through tools/gw_cd4_demo.sh).
Only the demo copy is changed; reset archives previous Studio interactions.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "application/frontend"))

from da_selection_ui.project_import import inspect_project_metadata, recognized_layout
from da_selection_ui.project_storage import new_project_id, write_project_metadata


def prepare(source: Path, data_root: Path, reset: bool = False) -> Path:
    source, data_root = source.resolve(), data_root.resolve()
    if source == data_root or source in data_root.parents or data_root in source.parents:
        raise ValueError("Demo storage must be separate from the source trace")
    root = data_root / "projects/gw-cd4-demo"
    marker = data_root / "gw_cd4_demo.json"
    fresh = not root.exists()
    if not fresh and not marker.is_file():
        raise ValueError(f"Refusing to modify an unrecognized existing directory: {root}")
    source_layout = recognized_layout(source)
    if not source_layout.get("checkpoint_dir"):
        raise ValueError("Source is not a recognized DiscoveryAgent trace")
    if not (Path(source_layout["checkpoint_dir"]).parent / "round_12/round_report.md").is_file():
        raise ValueError("The source must contain the completed Round 12 report")
    if fresh:
        root.parent.mkdir(parents=True, exist_ok=True)
        print(f"Copying historical trace to {root}", flush=True)
        # APFS clones are independent writable files without duplicating 5 GB.
        if sys.platform == "darwin":
            subprocess.run(["cp", "-cR", str(source), str(root)], check=True)
        else:
            shutil.copytree(source, root, symlinks=True)
        marker.write_text(json.dumps({"source": str(source), "project_root": str(root)}, indent=2))
    else:
        manifest = json.loads(marker.read_text())
        if manifest != {"source": str(source), "project_root": str(root)}:
            raise ValueError("Existing demo belongs to a different source or location")

    layout = recognized_layout(root)
    rounds = Path(layout["checkpoint_dir"]).parent
    current = rounds / "round_12"
    if fresh or reset:
        if (rounds / "round_13").exists():
            raise ValueError("Round 13 execution has started; use a fresh --data-root for another demo")
        archive = data_root / "studio-backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        # Move only this cycle's Studio outputs; preserve all research reports,
        # checkpoint queries, code, and earlier hypothesis history.
        names = {
            "generated_hypotheses.json", "generated_hypotheses.md",
            "hypothesis_studio_history.json", "selected_hypothesis.md",
            "agent_guidance.md", "overall_research_guidance.md", "next_round_instructions.md",
            "interactive_hyp_gen", "interactive_generation_logs",
        }
        for path in current.iterdir():
            if path.name in names or path.name.startswith("selected_hypothesis_"):
                archive.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(archive / path.name))
        # Old exports sometimes retain only summaries. Without the canonical
        # filename the app mistakes historical queries for a running round.
        for completed in rounds.glob("round_*"):
            report, summary = completed / "round_report.md", completed / "round_report_summary.md"
            if not report.exists() and summary.is_file():
                shutil.copy2(summary, report)
            elif not report.exists() and completed.name.removeprefix("round_").isdigit() and int(completed.name.removeprefix("round_")) < 12:
                report.write_text(
                    f"# {completed.name.replace('_', ' ').title()} — archived report unavailable\n\n"
                    "Demo compatibility marker: this historical round precedes the completed "
                    "Round 12 checkpoint. Its standalone report was not included in the export. "
                    "Consult memory_report.md and the retained query checkpoints for evidence.\n"
                )
        # Adapt machine-local paths and disable the optional external literature
        # lookup for this screenshot workflow. Generation still uses the real
        # model, dataset context, accumulated memory and completed round report.
        config_path = current / "config.json"
        payload = json.loads(config_path.read_text())
        config = payload.get("config", payload)
        config["working_directory"] = str(layout["code_dir"])
        config["use_deep_research_for_hypothesis_generation"] = False
        config["generate_plan"] = True
        config_path.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"Studio reset to initial guidance; previous outputs archived in {archive}")

    registry_path = data_root / "projects.json"
    if fresh:
        metadata = inspect_project_metadata(root)
        now = datetime.now(timezone.utc).isoformat()
        project_id = new_project_id()
        write_project_metadata(root, project_id, "gw_cd4")
        record = {
            "id": project_id, "url_slug": "gw-cd4-demo", "display_name": "gw_cd4",
            "task_description": metadata["task_description"],
            "dataset_context": metadata["dataset_context"],
            "project_root": str(root), "project_parent_dir": str(root.parent),
            **{key: str(layout[key]) for key in ("code_dir", "data_dir", "checkpoint_dir", "readme_path")},
            "external_data": False, "data_sources": [], "imported_in_place": True,
            "reasoning_model": "claude-opus-4-6", "execution_model": "claude-opus-4-6",
            "query_count": 12, "deep_research": False, "web_search": False,
            "created_at": now, "updated_at": now, "last_opened_at": now,
        }
        existing = json.loads(registry_path.read_text()) if registry_path.exists() else {"projects": []}
        existing["projects"].append(record)
        registry_path.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"Demo data: {data_root}")
    return data_root


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=REPOSITORY.parent / "manuscript/results/gw_cd4/trace")
    parser.add_argument("--data-root", type=Path, default=Path.home() / "DiscoveryAgent-gw-cd4-interactive-demo")
    parser.add_argument("--reset", action="store_true", help="Archive Round 13 Studio outputs and reopen initial guidance; stop the demo server first")
    args = parser.parse_args()
    prepare(args.source, args.data_root, args.reset)
