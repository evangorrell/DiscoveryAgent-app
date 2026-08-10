#!/usr/bin/env python3
"""
Interactive Hypothesis Selection Tool

This script helps users select from candidate hypotheses generated during
the exploratory phase of the AIR workflow.

Usage:
    python scripts/show_hypothesis_choice.py <checkpoint_dir>
    python scripts/show_hypothesis_choice.py --output <output_file> <checkpoint_dir>

Example:
    python scripts/show_hypothesis_choice.py /path/to/logs/checkpoints/round_0
    python scripts/show_hypothesis_choice.py --output /tmp/my_choice.md /path/to/logs/checkpoints/round_0
"""

import os
import sys
import re
import argparse
from pathlib import Path
from typing import List, Optional, Tuple, Union


class HypothesisSelector:
    """Interactive tool for selecting from candidate hypotheses."""

    def __init__(self, checkpoint_dir: str, output_file: Optional[str] = None):
        """
        Initialize the hypothesis selector.

        Args:
            checkpoint_dir: Path to checkpoint directory containing next_candidate_hypotheses.md
            output_file: Optional path for output file. If None, saves to checkpoint_dir.
        """
        self.checkpoint_dir = Path(checkpoint_dir)
        self.output_file = output_file
        self.candidates_file = self.checkpoint_dir / "next_candidate_hypotheses.md"
        self.round_report_file = self.checkpoint_dir / "round_report.md"
        self.memory_report_file = self.checkpoint_dir / "memory_report.md"
        self.hypotheses: List[str] = []
        self.report_context: Optional[str] = None
        self.round_report: Optional[str] = None
        self.memory_report: Optional[str] = None

    def validate_checkpoint(self) -> bool:
        """Validate that checkpoint directory exists and has candidates file."""
        if not self.checkpoint_dir.exists():
            print(f"❌ Error: Checkpoint directory not found: {self.checkpoint_dir}")
            return False

        if not self.checkpoint_dir.is_dir():
            print(f"❌ Error: Path is not a directory: {self.checkpoint_dir}")
            return False

        if not self.candidates_file.exists():
            print(f"❌ Error: Candidates file not found: {self.candidates_file}")
            print(f"   Expected: next_candidate_hypotheses.md")
            print(f"\n   Available files in directory:")
            for file in self.checkpoint_dir.iterdir():
                if file.is_file():
                    print(f"   - {file.name}")
            return False

        return True

    def parse_candidates_file(self) -> bool:
        """
        Parse the next_candidate_hypotheses.md file to extract individual hypotheses.

        Returns:
            bool: True if parsing succeeded
        """
        try:
            with open(self.candidates_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Extract the report context (everything before "## Candidate Hypotheses")
            report_match = re.search(
                r'## Context: Report from Round \d+\n\n(.*?)\n\n---\n\n## Candidate Hypotheses',
                content,
                re.DOTALL
            )
            if report_match:
                self.report_context = report_match.group(1).strip()

            # Split by hypothesis sections
            # Pattern: ### Hypothesis N followed by content until next === or end
            hypothesis_pattern = r'### Hypothesis (\d+)\n\n(.*?)(?=\n={80}|### Hypothesis \d+|\Z)'
            matches = re.findall(hypothesis_pattern, content, re.DOTALL)

            if not matches:
                print(f"⚠️  Warning: Could not parse hypotheses from file")
                print(f"   File may have unexpected format")
                return False

            # Store hypotheses (strip leading/trailing whitespace)
            self.hypotheses = [hyp.strip() for _, hyp in matches]

            if not self.hypotheses:
                print(f"❌ Error: No hypotheses found in file")
                return False

            print(f"✓ Found {len(self.hypotheses)} candidate hypotheses")
            return True

        except Exception as e:
            print(f"❌ Error parsing candidates file: {str(e)}")
            return False

    def load_memory_report(self) -> bool:
        """
        Load the memory_report.md file if it exists.

        This file contains the accumulated knowledge from ResultsMemoryManager
        after integrating findings from previous rounds.

        Returns:
            bool: True if memory report was loaded
        """
        if not self.memory_report_file.exists():
            return False

        try:
            with open(self.memory_report_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Extract the report content (everything after "## Accumulated Report")
            report_match = re.search(
                r'## Accumulated Report\n\n(.*)',
                content,
                re.DOTALL
            )

            if report_match:
                self.memory_report = report_match.group(1).strip()
            else:
                # If pattern not found, use entire content
                self.memory_report = content.strip()

            print(f"✓ Loaded memory report ({len(self.memory_report)} characters)")
            return True

        except Exception as e:
            print(f"⚠️  Warning: Could not load memory report: {str(e)}")
            return False

    def load_round_report(self) -> bool:
        """
        Load the round_report.md file if it exists.

        This file contains the report from the current round before it was
        integrated into ResultsMemoryManager.

        Returns:
            bool: True if round report was loaded
        """
        if not self.round_report_file.exists():
            return False

        try:
            with open(self.round_report_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Extract the report content (everything after "## Round Report")
            report_match = re.search(
                r'## Round Report\n\n(.*)',
                content,
                re.DOTALL
            )

            if report_match:
                self.round_report = report_match.group(1).strip()
            else:
                # If pattern not found, use entire content
                self.round_report = content.strip()

            print(f"✓ Loaded round report ({len(self.round_report)} characters)")
            return True

        except Exception as e:
            print(f"⚠️  Warning: Could not load round report: {str(e)}")
            return False

    def display_report_context(self, full: bool = False):
        """
        Display the exploratory round report context.

        Args:
            full: If True, show full report. If False, show preview.
        """
        if not self.report_context:
            return

        print("\n" + "="*80)
        print("EXPLORATORY ROUND CONTEXT")
        print("="*80)

        if full:
            # Show full report
            print(f"\n{self.report_context}")
        else:
            # Show first 500 characters of report
            preview_length = 500
            if len(self.report_context) > preview_length:
                preview = self.report_context[:preview_length] + "..."
                print(f"\n{preview}")
                print(f"\n[Report truncated - showing first {preview_length} characters]")
                print(f"Type 'context-full' to see the complete report")
            else:
                print(f"\n{self.report_context}")

        print("\n" + "="*80)

    def display_memory_report(self, full: bool = False):
        """
        Display the accumulated memory report from ResultsMemoryManager.

        Args:
            full: If True, show full report. If False, show preview.
        """
        if not self.memory_report:
            print("\n⚠️  No memory report available")
            print("   This report is only available if you're resuming from a previous round")
            print("   with the ResultsMemoryManager enabled.")
            return

        print("\n" + "="*80)
        print("ACCUMULATED MEMORY REPORT")
        print("="*80)
        print("\nThis report contains synthesized insights from all previous rounds,")
        print("integrated by the ResultsMemoryManager.\n")
        print("-"*80)

        if full:
            # Show full report
            print(f"\n{self.memory_report}")
        else:
            # Show first 1000 characters of report
            preview_length = 1000
            if len(self.memory_report) > preview_length:
                preview = self.memory_report[:preview_length] + "..."
                print(f"\n{preview}")
                print(f"\n[Report truncated - showing first {preview_length} characters]")
                print(f"Type 'memory-full' to see the complete report")
            else:
                print(f"\n{self.memory_report}")

        print("\n" + "="*80)

    def display_round_report(self, full: bool = False):
        """
        Display the current round's report before memory integration.

        Args:
            full: If True, show full report. If False, show preview.
        """
        if not self.round_report:
            print("\n⚠️  No round report available")
            print("   This report is only available if round_report.md exists in the checkpoint.")
            return

        print("\n" + "="*80)
        print("CURRENT ROUND REPORT (MOST RECENT)")
        print("="*80)
        print("\nThis report contains the findings from this round only,")
        print("before integration with ResultsMemoryManager.\n")
        print("-"*80)

        if full:
            # Show full report
            print(f"\n{self.round_report}")
        else:
            # Show first 1000 characters of report
            preview_length = 1000
            if len(self.round_report) > preview_length:
                preview = self.round_report[:preview_length] + "..."
                print(f"\n{preview}")
                print(f"\n[Report truncated - showing first {preview_length} characters]")
                print(f"Type 'round-full' to see the complete report")
            else:
                print(f"\n{self.round_report}")

        print("\n" + "="*80)

    def parse_hypothesis_sections(self, hypothesis: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Parse hypothesis text into its three sections.

        Args:
            hypothesis: Full hypothesis text

        Returns:
            Tuple of (interpretation, hypothesis_content, justification)
        """
        # Extract Interpretation section
        interp_match = re.search(
            r'\*\*Interpretation\*\*:(.*?)(?=\*\*Hypothesis\*\*:|$)',
            hypothesis,
            re.DOTALL
        )
        interpretation = interp_match.group(1).strip() if interp_match else None

        # Extract Hypothesis section
        hyp_match = re.search(
            r'\*\*Hypothesis\*\*:(.*?)(?=\*\*Justification\*\*:|$)',
            hypothesis,
            re.DOTALL
        )
        hypothesis_content = hyp_match.group(1).strip() if hyp_match else None

        # Extract Justification section
        just_match = re.search(
            r'\*\*Justification\*\*:(.*?)$',
            hypothesis,
            re.DOTALL
        )
        justification = just_match.group(1).strip() if just_match else None

        return interpretation, hypothesis_content, justification

    def display_hypothesis(self, idx: int, hypothesis: str, mode: str = "brief"):
        """
        Display a single hypothesis.

        Args:
            idx: Hypothesis number (1-indexed)
            hypothesis: Hypothesis text
            mode: Display mode - "brief" (hypothesis only), "expanded" (hypothesis + justification),
                  or "full" (all sections)
        """
        print(f"\n{'='*80}")
        print(f"HYPOTHESIS {idx}")
        print(f"{'='*80}\n")

        # Parse sections
        interpretation, hypothesis_content, justification = self.parse_hypothesis_sections(hypothesis)

        if mode == "brief":
            # Show only the hypothesis content
            if hypothesis_content:
                print("**Hypothesis:**")
                print(hypothesis_content)
                print(f"\n[Enter 'view {idx}' to see justification]")
            else:
                # Fallback if parsing fails
                print(hypothesis[:500] + "..." if len(hypothesis) > 500 else hypothesis)
                print(f"\n[Enter 'view {idx}' to see full text]")

        elif mode == "expanded":
            # Show hypothesis + justification
            if hypothesis_content:
                print("**Hypothesis:**")
                print(hypothesis_content)
                print()

            if justification:
                print("**Justification:**")
                print(justification)

            if not hypothesis_content and not justification:
                # Fallback if parsing fails
                print(hypothesis)

        else:  # mode == "full"
            # Show everything
            print(hypothesis)

    def display_all_hypotheses(self, mode: str = "brief"):
        """
        Display all candidate hypotheses.

        Args:
            mode: Display mode - "brief", "expanded", or "full"
        """
        print("\n" + "="*80)
        print(f"CANDIDATE HYPOTHESES ({len(self.hypotheses)} total)")
        print("="*80)

        for i, hyp in enumerate(self.hypotheses, 1):
            self.display_hypothesis(i, hyp, mode=mode)

        print("\n" + "="*80)

    def get_user_selection(self) -> Tuple[Union[int, str], Optional[str]]:
        """
        Prompt user to select a hypothesis and provide optional feedback.

        Returns:
            Tuple[Union[int, str], Optional[str]]: (selected_index_or_custom_hypothesis, feedback)
                First element is either an int (index) or str (custom hypothesis)
        """
        while True:
            print("\n" + "="*80)
            print("SELECT YOUR HYPOTHESIS")
            print("="*80)
            print(f"\nAvailable commands:")
            print(f"  <number>        - Select hypothesis (e.g., '1', '2', '3')")
            print(f"  other           - Enter your own custom hypothesis")
            print(f"  view <number>   - View full text of hypothesis")
            print(f"  view all        - View all hypotheses in full")
            print(f"  context         - Show exploratory round report context (preview)")
            print(f"  context-full    - Show complete exploratory round report")
            if self.round_report:
                print(f"  round           - Show current round's report (preview)")
                print(f"  round-full      - Show complete current round's report")
            if self.memory_report:
                print(f"  memory          - Show accumulated memory report (preview)")
                print(f"  memory-full     - Show complete accumulated memory report")
            print(f"  quit            - Exit without saving")
            print(f"\nEnter your choice: ", end='')

            choice = input().strip().lower()

            # Handle quit
            if choice in ['quit', 'exit', 'q']:
                print("\n❌ Exiting without saving.")
                sys.exit(0)

            # Handle custom hypothesis
            if choice == 'other':
                custom_hypothesis = self.get_custom_hypothesis()
                if custom_hypothesis:
                    # Get optional feedback
                    feedback = self.get_feedback()
                    return custom_hypothesis, feedback
                else:
                    # User cancelled, go back to selection menu
                    continue

            # Handle context view
            if choice == 'context':
                self.display_report_context(full=False)
                continue

            # Handle full context view
            if choice == 'context-full':
                self.display_report_context(full=True)
                continue

            # Handle round report view
            if choice == 'round':
                self.display_round_report(full=False)
                continue

            # Handle full round report view
            if choice == 'round-full':
                self.display_round_report(full=True)
                continue

            # Handle memory view
            if choice == 'memory':
                self.display_memory_report(full=False)
                continue

            # Handle full memory view
            if choice == 'memory-full':
                self.display_memory_report(full=True)
                continue

            # Handle view commands
            if choice.startswith('view'):
                parts = choice.split()
                if len(parts) == 2:
                    if parts[1] == 'all':
                        self.display_all_hypotheses(mode="expanded")
                        continue
                    else:
                        try:
                            view_idx = int(parts[1])
                            if 1 <= view_idx <= len(self.hypotheses):
                                self.display_hypothesis(view_idx, self.hypotheses[view_idx-1], mode="expanded")
                                continue
                            else:
                                print(f"❌ Invalid hypothesis number. Choose 1-{len(self.hypotheses)}")
                                continue
                        except ValueError:
                            print(f"❌ Invalid command. Use 'view <number>' or 'view all'")
                            continue
                print(f"❌ Invalid command. Use 'view <number>' or 'view all'")
                continue

            # Handle selection
            try:
                selection = int(choice)
                if 1 <= selection <= len(self.hypotheses):
                    # Valid selection - show the selected hypothesis
                    print("\n" + "="*80)
                    print(f"YOU SELECTED: HYPOTHESIS {selection}")
                    print("="*80)
                    self.display_hypothesis(selection, self.hypotheses[selection-1], mode="expanded")

                    # Confirm selection
                    print(f"\n" + "="*80)
                    print(f"Confirm selection? (y/n): ", end='')
                    confirm = input().strip().lower()

                    if confirm in ['y', 'yes']:
                        # Get optional feedback
                        feedback = self.get_feedback()
                        return selection, feedback
                    else:
                        print("\n↩️  Selection cancelled. Choose again.")
                        continue
                else:
                    print(f"❌ Invalid number. Choose 1-{len(self.hypotheses)}")
            except ValueError:
                print(f"❌ Invalid input. Enter a number (1-{len(self.hypotheses)}) or a command")

    def get_custom_hypothesis(self) -> Optional[str]:
        """
        Prompt user to enter a custom hypothesis.

        Returns:
            Optional[str]: Custom hypothesis text or None if cancelled
        """
        print("\n" + "="*80)
        print("ENTER CUSTOM HYPOTHESIS")
        print("="*80)
        print("\nYou've chosen to enter your own hypothesis instead of selecting from")
        print("the proposed candidates. Please enter your hypothesis below.")
        print("\nPress Ctrl+D (Unix/Mac) or Ctrl+Z (Windows) when done.")
        print("-"*80 + "\n")

        hypothesis_lines = []
        try:
            while True:
                line = input()
                hypothesis_lines.append(line)
        except EOFError:
            pass  # User finished input

        custom_hypothesis = "\n".join(hypothesis_lines).strip()

        if custom_hypothesis:
            # Show preview
            print("\n" + "="*80)
            print("YOUR CUSTOM HYPOTHESIS")
            print("="*80)
            preview_length = 500
            if len(custom_hypothesis) > preview_length:
                preview = custom_hypothesis[:preview_length] + "..."
                print(f"\n{preview}")
                print(f"\n[Preview - showing first {preview_length} characters]")
            else:
                print(f"\n{custom_hypothesis}")

            # Confirm
            print("\n" + "="*80)
            print(f"Confirm custom hypothesis? (y/n): ", end='')
            confirm = input().strip().lower()

            if confirm in ['y', 'yes']:
                print(f"\n✓ Custom hypothesis accepted ({len(custom_hypothesis)} characters)")
                return custom_hypothesis
            else:
                print("\n↩️  Custom hypothesis cancelled.")
                return None
        else:
            print(f"\n⚠️  No hypothesis entered")
            return None

    def get_feedback(self) -> Optional[str]:
        """
        Prompt user for optional feedback.

        Returns:
            Optional[str]: Feedback text or None
        """
        print("\n" + "="*80)
        print("OPTIONAL FEEDBACK")
        print("="*80)
        print("\nWould you like to add feedback or guidance to refine this hypothesis?")
        print("This will be passed to the research agent along with your selected hypothesis.")
        print("\nOptions:")
        print("  y - Add feedback (opens multi-line input)")
        print("  n - No feedback, proceed with hypothesis as-is")
        print("\nAdd feedback? (y/n): ", end='')

        add_feedback = input().strip().lower()

        if add_feedback in ['y', 'yes']:
            print("\n" + "-"*80)
            print("Enter your feedback below (press Ctrl+D or Ctrl+Z when done):")
            print("Example: 'Focus on peptide binding affinity experiments'")
            print("-"*80 + "\n")

            feedback_lines = []
            try:
                while True:
                    line = input()
                    feedback_lines.append(line)
            except EOFError:
                pass  # User finished input

            feedback = "\n".join(feedback_lines).strip()

            if feedback:
                print(f"\n✓ Feedback added ({len(feedback)} characters)")
                return feedback
            else:
                print(f"\n⚠️  No feedback entered")
                return None
        else:
            print(f"\n✓ No feedback added")
            return None

    def save_selection(self, selection: Union[int, str], feedback: Optional[str]) -> str:
        """
        Save the selected hypothesis and feedback to a file.

        Args:
            selection: Either a 1-indexed hypothesis number (int) or custom hypothesis text (str)
            feedback: Optional feedback text

        Returns:
            str: Path to saved file
        """
        # Get hypothesis text based on selection type
        if isinstance(selection, int):
            selected_hypothesis = self.hypotheses[selection - 1]
        else:
            # Custom hypothesis
            selected_hypothesis = selection

        # Determine output path
        if self.output_file:
            output_path = Path(self.output_file)
        else:
            # Save in checkpoint directory by default
            output_path = self.checkpoint_dir / "selected_hypothesis.md"

        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build content
        content = f"{selected_hypothesis}\n"

        if feedback:
            content += f"\n## Scientist Feedback\n\n{feedback}\n"

        # Save to file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return str(output_path)

    def run(self):
        """Run the interactive hypothesis selection process."""
        print("\n" + "="*80)
        print("🧠 HYPOTHESIS SELECTION TOOL")
        print("="*80)
        print(f"\nCheckpoint directory: {self.checkpoint_dir}")

        # Validate
        if not self.validate_checkpoint():
            sys.exit(1)

        # Parse candidates file
        if not self.parse_candidates_file():
            sys.exit(1)

        # Load round and memory reports if available
        self.load_round_report()
        self.load_memory_report()

        # Display context (preview mode initially)
        self.display_report_context(full=False)

        # Display all hypotheses (brief mode - hypothesis text only)
        self.display_all_hypotheses(mode="brief")

        # Get user selection
        selection, feedback = self.get_user_selection()

        # Save selection
        try:
            output_path = self.save_selection(selection, feedback)

            print("\n" + "="*80)
            print("✅ SELECTION SAVED")
            print("="*80)
            if isinstance(selection, int):
                print(f"\nSelected: Hypothesis {selection}")
            else:
                print(f"\nSelected: Custom hypothesis ({len(selection)} characters)")
            if feedback:
                print(f"Feedback: Yes ({len(feedback)} characters)")
            else:
                print(f"Feedback: None")
            print(f"\nSaved to: {output_path}")
            print("\n" + "="*80 + "\n")

        except Exception as e:
            print(f"\n❌ Error saving selection: {str(e)}")
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Interactive tool for selecting from candidate hypotheses",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Select from latest checkpoint (saves to checkpoint directory)
  python scripts/show_hypothesis_choice.py /path/to/checkpoints/round_0

  # Specify custom output location
  python scripts/show_hypothesis_choice.py --output /tmp/my_selection.md /path/to/checkpoints/round_0

  # Using with test script
  python scripts/show_hypothesis_choice.py \\
    /scratch/users/oqueen/AIR_project/active/protpep_unsup_data/TEST_11-23_*/logs/checkpoints/round_0
        """
    )

    parser.add_argument(
        'checkpoint_dir',
        help='Path to checkpoint directory containing next_candidate_hypotheses.md'
    )

    parser.add_argument(
        '--output', '-o',
        help='Output file path for selected hypothesis (default: <checkpoint_dir>/selected_hypothesis.md)',
        default=None
    )

    args = parser.parse_args()

    # Run selector
    selector = HypothesisSelector(
        checkpoint_dir=args.checkpoint_dir,
        output_file=args.output
    )
    selector.run()


if __name__ == "__main__":
    main()
