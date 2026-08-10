#!/usr/bin/env python3
"""
Lightweight CLI to chat with Claude Code over a codebase (read-only).
Uses the shared run_code_chat helper; no hypothesis-generation plumbing.
"""

import argparse
import sys
from pathlib import Path

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from AIR.utils.code_chat import run_code_chat  # noqa: E402


def run_once(code_dir: Path, model: str, question: str, max_turns: int) -> str:
    return run_code_chat(
        question=question,
        code_dir=code_dir,
        model=model,
        max_turns=max_turns,
    )


def repl(code_dir: Path, model: str, max_turns: int):
    print(f"\nClaude Code chat (read-only) mounted at: {code_dir}")
    print("Type your question and press Enter. Type 'exit' or 'quit' to leave.\n")
    while True:
        try:
            q = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return
        if q.lower() in ("exit", "quit"):
            print("Goodbye.")
            return
        if not q:
            continue
        print("Code Assistant is thinking...\n")
        answer = run_once(code_dir, model, q, max_turns)
        print(answer)
        print()


def main():
    parser = argparse.ArgumentParser(description="Standalone Claude Code chat CLI (read-only).")
    parser.add_argument("--code_dir", required=True, help="Path to the codebase root for Claude Code.")
    parser.add_argument("--model", default=None, help="Claude model to use (optional; uses helper default if None).")
    parser.add_argument("--question", "-q", help="Single question to ask; if omitted, enters interactive mode.")
    parser.add_argument("--max_turns", type=int, default=6, help="Max turns for the Claude Code session.")
    args = parser.parse_args()

    code_dir = Path(args.code_dir).expanduser().resolve()
    if not code_dir.exists() or not code_dir.is_dir():
        print(f"Error: code_dir not found or not a directory: {code_dir}", file=sys.stderr)
        sys.exit(2)

    if args.question:
        print("Code Assistant is thinking...\n")
        answer = run_once(code_dir, args.model, args.question.strip(), args.max_turns)
        print(answer)
    else:
        repl(code_dir, args.model, args.max_turns)


if __name__ == "__main__":
    main()


