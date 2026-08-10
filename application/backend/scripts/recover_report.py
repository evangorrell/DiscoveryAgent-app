#!/usr/bin/env python3
"""
Recover Reports from HypothesisBrancher Checkpoints

This script recovers round-level reports from HypothesisBrancher checkpoint structure
when reports don't exist (e.g., when director agent ran out of context).

By default, it generates BOTH round_report.md and memory_report.md if they don't exist.
Use --omit-round-report or --omit-memory-report to skip generating specific reports.

It works by:
1. Loading the latest checkpoint from the round's query checkpoints
2. Extracting the director agent's conversation history and cumulative report
3. Prompting the LLM to generate a round report (same as director_workflow.py does)
4. Using ResultsMemoryManager to generate accumulated memory report

Usage:
    python scripts/recover_report.py <project_prefix> [OPTIONS]

Examples:
    # List all available rounds
    python scripts/recover_report.py dwarf/jean/test_run --list

    # Recover missing reports from a specific round (generates both by default)
    python scripts/recover_report.py dwarf/jean/test_run --round 1

    # Recover missing reports from latest round
    python scripts/recover_report.py dwarf/jean/test_run --latest

    # Recover missing reports from all rounds
    python scripts/recover_report.py dwarf/jean/test_run --all

    # Only generate round report (skip memory report)
    python scripts/recover_report.py dwarf/jean/test_run --round 1 --omit-memory-report

    # Only generate memory report (skip round report)
    python scripts/recover_report.py dwarf/jean/test_run --round 1 --omit-round-report
"""

import argparse
import json
import os
import sys
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# Add parent directory to path to import AIR modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from AIR.config import LLMConfig, HypothesisGenerationConfig
from AIR.prompts.results_memory import CONTINUATION_REPORT_PROMPT
from AIR.memory.results_memory import ResultsMemoryManager
import anthropic
import openai

DATA_PATH = os.getenv("DATA_PATH", "/scratch/users/oqueen/AIR_project/active")


def convert_compaction_blocks(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert compaction content blocks to text blocks in messages.

    Compaction blocks (type='compaction') are produced by the Anthropic compaction API beta
    and contain summarized conversation context. They are not accepted by the standard
    messages API, so we convert them to text blocks to preserve the information.
    """
    converted = []
    for msg in messages:
        content = msg.get('content')
        if isinstance(content, list):
            new_content = []
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'compaction':
                    # Convert compaction block to text block, preserving the summary
                    new_content.append({'type': 'text', 'text': block.get('content', '')})
                else:
                    new_content.append(block)
            converted.append({**msg, 'content': new_content})
        else:
            converted.append(msg)
    return converted


def find_all_rounds(log_dir: str) -> List[int]:
    """
    Find all available rounds in the checkpoint directory.

    Args:
        log_dir: Base logs directory

    Returns:
        List of round numbers, sorted ascending
    """
    checkpoint_base = Path(log_dir) / "checkpoints"
    if not checkpoint_base.exists():
        return []

    rounds = []
    for item in checkpoint_base.iterdir():
        if item.is_dir() and item.name.startswith('round_'):
            try:
                round_num = int(item.name.split('_')[1])
                rounds.append(round_num)
            except (ValueError, IndexError):
                continue

    return sorted(rounds)


def find_latest_checkpoint_in_round(log_dir: str, round_num: int) -> Optional[Path]:
    """
    Find the latest (highest query number) checkpoint in a round.

    Args:
        log_dir: Base logs directory
        round_num: Round number

    Returns:
        Path to the latest checkpoint.json file, or None if not found
    """
    checkpoint_base = Path(log_dir) / "checkpoints"
    round_dir = checkpoint_base / f"round_{round_num}"

    if not round_dir.exists():
        return None

    # Find all query directories
    query_dirs = []
    for item in round_dir.iterdir():
        if item.is_dir() and item.name.startswith('query_'):
            try:
                query_num = int(item.name.split('_')[1])
                checkpoint_file = item / "checkpoint.json"
                if checkpoint_file.exists():
                    query_dirs.append((query_num, checkpoint_file))
            except (ValueError, IndexError):
                continue

    if not query_dirs:
        return None

    # Return the checkpoint with the highest query number
    query_dirs.sort(key=lambda x: x[0], reverse=True)
    return query_dirs[0][1]


def load_checkpoint(checkpoint_path: Path) -> Dict[str, Any]:
    """
    Load checkpoint data from a checkpoint.json file.

    Args:
        checkpoint_path: Path to checkpoint.json

    Returns:
        Dictionary containing checkpoint data
    """
    with open(checkpoint_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_report_from_checkpoint(
    checkpoint_data: Dict[str, Any],
    model_name: Optional[str] = None,
    api_provider: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    verbose: bool = True
) -> Optional[str]:
    """
    Generate a final report by calling the LLM with the director agent's conversation history.

    This replicates what director_workflow.py does in its generate_final_report method.
    Uses the model configuration from the checkpoint if not explicitly provided.

    Args:
        checkpoint_data: Checkpoint data loaded from checkpoint.json
        model_name: LLM model name to use (defaults to checkpoint config)
        api_provider: API provider (defaults to checkpoint config)
        max_tokens: Maximum tokens for the response (defaults to checkpoint config)
        temperature: Temperature for the LLM (defaults to checkpoint config)
        verbose: Print status messages

    Returns:
        Generated report string, or None if generation fails
    """
    # Extract director agent state
    agents = checkpoint_data.get('agents', {})
    director_agent = agents.get('director_agent', {})
    director_state = director_agent.get('state', {})

    # Get conversation history
    messages = director_state.get('messages', [])
    system_prompt = director_state.get('system_prompt', '')

    # Extract model config from checkpoint
    llm_config = director_state.get('llm_config', {})
    config_data = director_state.get('config_data', {})

    # Use checkpoint config as defaults, allow command-line overrides
    model_name = model_name or llm_config.get('model_name', 'claude-sonnet-4-5-20250929')
    api_provider = api_provider or llm_config.get('api_provider', 'anthropic')
    max_tokens = max_tokens or config_data.get('max_tokens', 16000)
    temperature = temperature if temperature is not None else llm_config.get('temperature', 0.2)

    if not messages:
        if verbose:
            print("⚠️  No messages found in director agent state")
        return None

    # Get cumulative report from workflow state
    workflow_state = checkpoint_data.get('workflow_state', {})
    cumulative_report = workflow_state.get('cumulative_report', '')

    if not cumulative_report:
        if verbose:
            print("⚠️  No cumulative report found in workflow state")
        # Try to reconstruct from report field
        cumulative_report = workflow_state.get('report', '')
        if not cumulative_report:
            if verbose:
                print("⚠️  No report content available")
            return None

    if verbose:
        print(f"📝 Generating report from checkpoint...")
        print(f"   Director messages: {len(messages)}")
        print(f"   Cumulative report: {len(cumulative_report)} characters")
        print(f"   Using model: {model_name} (from checkpoint config)")
        print(f"   Temperature: {temperature}")
        print(f"   Max tokens: {max_tokens}")

    # Build the final report prompt (same as director_agent.generate_final_report)
    final_report_prompt = CONTINUATION_REPORT_PROMPT + f"\n\nHere is the cumulative report from all of your queries and analyses:\n\n{cumulative_report}"

    # Try with full conversation history first
    # If it's too long, fall back to just the cumulative report without conversation history
    conversation = convert_compaction_blocks(messages)
    conversation.append({"role": "user", "content": final_report_prompt})

    try:
        if api_provider == 'anthropic':
            # Use Anthropic API with long-context beta (1M token context)
            api_key = os.environ.get('ANTHROPIC_API_KEY')
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set")

            client = anthropic.Anthropic(api_key=api_key)

            if verbose:
                print(f"   Using long-context beta (context-1m-2025-08-07)")

            response = client.beta.messages.create(
                model=model_name,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=conversation,
                betas=["context-1m-2025-08-07"],
            )

            # Extract text from response
            report_text = ""
            for block in response.content:
                if hasattr(block, 'text'):
                    report_text += block.text

            if verbose:
                print(f"✅ Report generated ({len(report_text)} characters)")

            return report_text

        elif api_provider == 'openai':
            # Use OpenAI API
            api_key = os.environ.get('OPENAI_API_KEY')
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")

            client = openai.OpenAI(api_key=api_key)

            # OpenAI includes system prompt in messages
            openai_messages = [{"role": "system", "content": system_prompt}] + conversation

            response = client.chat.completions.create(
                model=model_name,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=openai_messages
            )

            report_text = response.choices[0].message.content

            if verbose:
                print(f"✅ Report generated ({len(report_text)} characters)")

            return report_text

        else:
            raise ValueError(f"Unsupported API provider: {api_provider}")

    except Exception as e:
        # Check if it's a context length error (works for both Anthropic and OpenAI)
        error_msg = str(e)
        is_context_error = any(keyword in error_msg.lower() for keyword in [
            "prompt is too long",
            "maximum",
            "context_length_exceeded",
            "tokens",
            "too many tokens"
        ])

        if is_context_error:
            if verbose:
                print(f"⚠️  Context too long with full conversation history, retrying without it...")

            # Retry with just the cumulative report (no conversation history),
            # still using long-context beta for Anthropic
            try:
                if api_provider == 'anthropic':
                    api_key = os.environ.get('ANTHROPIC_API_KEY')
                    client = anthropic.Anthropic(api_key=api_key)

                    minimal_messages = [{"role": "user", "content": final_report_prompt}]

                    response = client.beta.messages.create(
                        model=model_name,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system_prompt,
                        messages=minimal_messages,
                        betas=["context-1m-2025-08-07"],
                    )

                    report_text = ""
                    for block in response.content:
                        if hasattr(block, 'text'):
                            report_text += block.text

                    if verbose:
                        print(f"✅ Report generated without full context ({len(report_text)} characters)")

                    return report_text

                elif api_provider == 'openai':
                    api_key = os.environ.get('OPENAI_API_KEY')
                    client = openai.OpenAI(api_key=api_key)

                    minimal_messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": final_report_prompt}
                    ]

                    response = client.chat.completions.create(
                        model=model_name,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        messages=minimal_messages
                    )

                    report_text = response.choices[0].message.content

                    if verbose:
                        print(f"✅ Report generated without full context ({len(report_text)} characters)")

                    return report_text

            except Exception as retry_e:
                if verbose:
                    print(f"❌ Error generating report (retry): {retry_e}")
                import traceback
                traceback.print_exc()
                return None
        else:
            # Other error, just fail
            if verbose:
                print(f"❌ Error generating report: {e}")
            import traceback
            traceback.print_exc()
            return None


def list_available_rounds(log_dir: str, verbose: bool = True) -> List[int]:
    """
    List all available rounds with their report and checkpoint status.

    Args:
        log_dir: Base logs directory
        verbose: Print status information

    Returns:
        List of round numbers
    """
    rounds = find_all_rounds(log_dir)

    if not rounds:
        if verbose:
            print(f"No rounds found in {log_dir}/checkpoints")
        return []

    if verbose:
        print(f"\n📋 Available rounds in {log_dir}/checkpoints:\n")
        print(f"{'Round':<8} {'Round Report':<15} {'Memory Report':<15} {'Has Checkpoint':<20}")
        print("-" * 60)

        for round_num in rounds:
            # Check for existing round_report.md
            checkpoint_base = Path(log_dir) / "checkpoints"
            round_dir = checkpoint_base / f"round_{round_num}"
            report_path = round_dir / "round_report.md"
            has_report = "✓" if report_path.exists() else "✗"

            # Check for memory_report.md
            memory_report_path = round_dir / "memory_report.md"
            has_memory_report = "✓" if memory_report_path.exists() else "✗"

            # Check for checkpoint files
            latest_checkpoint = find_latest_checkpoint_in_round(log_dir, round_num)
            has_checkpoint = "✓" if latest_checkpoint else "✗"

            print(f"{round_num:<8} {has_report:<15} {has_memory_report:<15} {has_checkpoint:<20}")

        print()

    return rounds


def recover_round_report(
    log_dir: str,
    round_num: int,
    output_path: Optional[str] = None,
    model_name: Optional[str] = None,
    api_provider: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    omit_round_report: bool = False,
    omit_memory_report: bool = False,
    memory_llm_model: Optional[str] = None,
    use_ace_memory: Optional[bool] = None,
    verbose: bool = True
) -> Optional[str]:
    """
    Recover reports from a single round.

    By default, generates both round_report.md and memory_report.md if they don't exist.

    This will:
    1. Check if round_report.md already exists (skip if --omit-round-report)
    2. If not, find the latest checkpoint and generate it
    3. Check if memory_report.md already exists (skip if --omit-memory-report)
    4. If not, generate it using ResultsMemoryManager

    Args:
        log_dir: Base logs directory
        round_num: Round number to recover
        output_path: Optional custom output path for round report
        model_name: LLM model to use for round report (defaults to checkpoint config)
        api_provider: API provider (defaults to checkpoint config)
        max_tokens: Maximum tokens for LLM response (defaults to checkpoint config)
        temperature: Temperature for LLM (defaults to checkpoint config)
        omit_round_report: Skip round report generation
        omit_memory_report: Skip memory report generation
        memory_llm_model: Model for memory integration (defaults to checkpoint config)
        use_ace_memory: Use ACE (3-agent) mode for memory (defaults to checkpoint config)
        verbose: Print status messages

    Returns:
        Path to saved round report, or None if nothing was generated
    """
    if verbose:
        print(f"🔍 Recovering reports from round {round_num}...")

    checkpoint_base = Path(log_dir) / "checkpoints"
    round_dir = checkpoint_base / f"round_{round_num}"
    round_report_path = round_dir / "round_report.md"
    memory_report_path = round_dir / "memory_report.md"

    # Determine what needs to be generated
    need_round_report = not round_report_path.exists() and not omit_round_report
    need_memory_report = not memory_report_path.exists() and not omit_memory_report

    # Check current status
    if verbose:
        if round_report_path.exists():
            print(f"   ✓ Round report exists: {round_report_path}")
        elif omit_round_report:
            print(f"   ⊘ Round report: skipped (--omit-round-report)")
        else:
            print(f"   ✗ Round report missing, will generate")

        if memory_report_path.exists():
            print(f"   ✓ Memory report exists: {memory_report_path}")
        elif omit_memory_report:
            print(f"   ⊘ Memory report: skipped (--omit-memory-report)")
        else:
            print(f"   ✗ Memory report missing, will generate")

    # If nothing to do, return early
    if not need_round_report and not need_memory_report:
        if verbose:
            print(f"   Nothing to generate for round {round_num}")
        return str(round_report_path) if round_report_path.exists() else None

    # We need to generate something - load the checkpoint
    if verbose:
        print(f"   Looking for checkpoints in round {round_num}...")

    latest_checkpoint = find_latest_checkpoint_in_round(log_dir, round_num)

    if latest_checkpoint is None:
        if verbose:
            print(f"❌ No checkpoints found in round {round_num}")
        return None

    if verbose:
        print(f"   Found checkpoint: {latest_checkpoint}")

    # Load checkpoint
    try:
        checkpoint_data = load_checkpoint(latest_checkpoint)
    except Exception as e:
        if verbose:
            print(f"❌ Error loading checkpoint: {e}")
        return None

    # Variable to hold round report content (needed for memory report)
    report_content = None
    final_round_report_path = None

    # Generate round report if needed
    if need_round_report:
        report_content = generate_report_from_checkpoint(
            checkpoint_data=checkpoint_data,
            model_name=model_name,
            api_provider=api_provider,
            max_tokens=max_tokens,
            temperature=temperature,
            verbose=verbose
        )

        if report_content is None:
            if verbose:
                print(f"❌ Failed to generate round report")
            # Can still try memory report if round report exists
        else:
            # Format the report with metadata
            formatted_report = f"""# Round {round_num} Report

**Timestamp:** {datetime.datetime.now().isoformat()}

**Round:** {round_num}

---

## Description

This report contains the findings from Round {round_num}, recovered from checkpoint.
Checkpoint used: {latest_checkpoint}

---

## Round Report

{report_content}
"""

            # Determine output path
            save_path = output_path if output_path else round_dir / "round_report.md"
            if output_path:
                save_path = Path(output_path)
                save_path.parent.mkdir(parents=True, exist_ok=True)

            # Save the round report
            try:
                with open(save_path, 'w', encoding='utf-8') as f:
                    f.write(formatted_report)

                if verbose:
                    print(f"💾 Round report saved to: {save_path}")
                    print(f"   Size: {len(formatted_report)} characters")

                final_round_report_path = str(save_path)

            except Exception as e:
                if verbose:
                    print(f"❌ Error saving round report: {e}")

    # Load existing round report content if we didn't just generate it
    if report_content is None and round_report_path.exists():
        try:
            with open(round_report_path, 'r', encoding='utf-8') as f:
                existing_content = f.read()

            # Extract just the report section (skip metadata)
            import re
            match = re.search(r'## Round Report\s*\n\s*\n(.*)', existing_content, re.DOTALL)
            if match:
                report_content = match.group(1).strip()
            else:
                report_content = existing_content  # Fall back to full content

            if verbose:
                print(f"   Loaded existing round report ({len(report_content)} characters)")

        except Exception as e:
            if verbose:
                print(f"⚠️  Could not read existing round report: {e}")

    # Generate memory report if needed
    if need_memory_report:
        if report_content is None:
            if verbose:
                print(f"❌ Cannot generate memory report: no round report content available")
        else:
            memory_result = generate_memory_report(
                log_dir=log_dir,
                round_num=round_num,
                round_report_content=report_content,
                output_path=None,  # Save to default location
                memory_llm_model=memory_llm_model,
                use_ace_memory=use_ace_memory,
                verbose=verbose
            )

            if not memory_result and verbose:
                print(f"⚠️  Memory report generation failed")

    return final_round_report_path if final_round_report_path else (str(round_report_path) if round_report_path.exists() else None)


def generate_memory_report(
    log_dir: str,
    round_num: int,
    round_report_content: str,
    output_path: Optional[str] = None,
    memory_llm_model: Optional[str] = None,
    use_ace_memory: Optional[bool] = None,
    verbose: bool = True
) -> Optional[str]:
    """
    Generate a memory report by integrating the round report with previous accumulated knowledge.

    This uses ResultsMemoryManager to accumulate knowledge across rounds, just like
    hypothesis_brancher.py does.

    Args:
        log_dir: Base logs directory
        round_num: Round number to generate memory report for
        round_report_content: The round report content to integrate
        output_path: Optional custom output path
        memory_llm_model: Model to use for memory integration (defaults to checkpoint config)
        use_ace_memory: Whether to use ACE (3-agent) mode (defaults to checkpoint config)
        verbose: Print status messages

    Returns:
        Path to saved memory report, or None if generation failed
    """
    if verbose:
        print(f"\n💾 Generating memory report for round {round_num}...")

    checkpoint_base = Path(log_dir) / "checkpoints"

    # Load config from round's config.json to get memory settings
    round_config_path = checkpoint_base / f"round_{round_num}" / "config.json"

    if round_config_path.exists():
        try:
            with open(round_config_path, 'r', encoding='utf-8') as f:
                round_config = json.load(f)
                config = round_config.get('config', {})

                # Extract memory settings from config
                if memory_llm_model is None:
                    memory_llm_model = config.get('memory_llm_model', 'claude-sonnet-4-5-20250929')

                if use_ace_memory is None:
                    use_simple_memory = config.get('use_simple_memory', False)
                    use_ace_memory = not use_simple_memory

                if verbose:
                    print(f"   Loaded config from: {round_config_path}")
                    print(f"   Memory model: {memory_llm_model}")
                    print(f"   Memory mode: {'ACE (3-agent)' if use_ace_memory else 'Simple (1-agent)'}")
        except Exception as e:
            if verbose:
                print(f"   ⚠️  Could not load round config, using defaults: {e}")
            if memory_llm_model is None:
                memory_llm_model = 'claude-sonnet-4-5-20250929'
            if use_ace_memory is None:
                use_ace_memory = True
    else:
        if verbose:
            print(f"   ⚠️  No config.json found at {round_config_path}, using defaults")
        if memory_llm_model is None:
            memory_llm_model = 'claude-sonnet-4-5-20250929'
        if use_ace_memory is None:
            use_ace_memory = True

    # Initialize ResultsMemoryManager
    try:
        # Determine API provider from model name
        api_provider = 'anthropic' if 'claude' in memory_llm_model.lower() else 'openai'

        # Set up API key
        if api_provider == 'anthropic':
            api_key = os.environ.get('ANTHROPIC_API_KEY')
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable not set")
        else:
            api_key = os.environ.get('OPENAI_API_KEY')
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")

        # Create LLM config for memory
        memory_llm_config = LLMConfig(
            model_name=memory_llm_model,
            max_tokens=18000,
            api_key=api_key,
            api_provider=api_provider
        )

        # Initialize memory manager
        results_memory = ResultsMemoryManager(
            llm_config=memory_llm_config,
            use_ace_approach=use_ace_memory
        )

        if verbose:
            print(f"   ✓ Initialized ResultsMemoryManager")

    except Exception as e:
        if verbose:
            print(f"❌ Error initializing ResultsMemoryManager: {e}")
        import traceback
        traceback.print_exc()
        return None

    # Load previous memory report if it exists (from previous rounds)
    if round_num > 0:
        # Check previous round's memory report
        prev_round_dir = checkpoint_base / f"round_{round_num - 1}"
        prev_memory_report_path = prev_round_dir / "memory_report.md"

        if prev_memory_report_path.exists():
            try:
                with open(prev_memory_report_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Extract just the accumulated report section (skip metadata)
                # The file format is: # Round X Memory Report\n\n... metadata ...\n\n## Accumulated Report\n\n{content}
                import re
                match = re.search(r'## Accumulated Report\s*\n\s*\n(.*)', content, re.DOTALL)
                if match:
                    prev_report = match.group(1).strip()
                else:
                    prev_report = content  # Fall back to full content

                results_memory.current_report = prev_report

                if verbose:
                    print(f"   ✓ Loaded previous memory report from round {round_num - 1} ({len(prev_report)} characters)")
            except Exception as e:
                if verbose:
                    print(f"   ⚠️  Could not load previous memory report: {e}")

    # Update memory with current round's report
    try:
        if verbose:
            print(f"   Integrating round {round_num} report into memory...")

        accumulated_report = results_memory.update_report(
            new_report=round_report_content,
            verbose=verbose
        )

        if verbose:
            print(f"   ✓ Memory updated (accumulated: {len(accumulated_report)} characters)")

    except Exception as e:
        if verbose:
            print(f"❌ Error updating memory: {e}")
        import traceback
        traceback.print_exc()
        return None

    # Format the memory report with metadata
    formatted_report = f"""# Round {round_num} Memory Report

**Timestamp:** {datetime.datetime.now().isoformat()}

**Round:** {round_num}

**Memory Mode:** {'ACE (3-agent)' if use_ace_memory else 'Simple (1-agent)'}

**Memory Model:** {memory_llm_model}

---

## Description

This report contains the accumulated knowledge through Round {round_num},
integrated by the ResultsMemoryManager using {'the ACE (Atomic Context Extension) approach with verification' if use_ace_memory else 'a simple integration approach'}.

---

## Accumulated Report

{accumulated_report}
"""

    # Determine output path
    if output_path is None:
        # Save to the round directory as memory_report.md
        round_dir = checkpoint_base / f"round_{round_num}"
        output_path = round_dir / "memory_report.md"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    # Save the report
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(formatted_report)

        if verbose:
            print(f"💾 Memory report saved to: {output_path}")
            print(f"   Size: {len(formatted_report)} characters")

            # Print memory statistics
            stats = results_memory.get_statistics()
            if stats.get('total_cost', 0) > 0:
                print(f"   Total cost: ${stats['total_cost']:.4f}")

        return str(output_path)

    except Exception as e:
        if verbose:
            print(f"❌ Error saving memory report: {e}")
        return None


def recover_all_rounds(
    log_dir: str,
    model_name: Optional[str] = None,
    api_provider: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    omit_round_report: bool = False,
    omit_memory_report: bool = False,
    memory_llm_model: Optional[str] = None,
    use_ace_memory: Optional[bool] = None,
    verbose: bool = True
) -> int:
    """
    Recover reports from all rounds.

    By default, generates both round_report.md and memory_report.md for each round
    if they don't exist.

    Args:
        log_dir: Base logs directory
        model_name: LLM model to use (defaults to checkpoint config)
        api_provider: API provider (defaults to checkpoint config)
        max_tokens: Maximum tokens for LLM response (defaults to checkpoint config)
        temperature: Temperature for LLM (defaults to checkpoint config)
        omit_round_report: Skip round report generation
        omit_memory_report: Skip memory report generation
        memory_llm_model: Model for memory integration (defaults to checkpoint config)
        use_ace_memory: Use ACE mode for memory (defaults to checkpoint config)
        verbose: Print status messages

    Returns:
        Number of rounds successfully processed
    """
    rounds = find_all_rounds(log_dir)

    if not rounds:
        if verbose:
            print(f"❌ No rounds found in {log_dir}/checkpoints")
        return 0

    if verbose:
        print(f"🔍 Recovering reports from {len(rounds)} rounds...")
        print()

    recovered_count = 0
    for round_num in rounds:
        if verbose:
            print(f"\n{'='*60}")
            print(f"Round {round_num}")
            print(f"{'='*60}")

        result = recover_round_report(
            log_dir=log_dir,
            round_num=round_num,
            output_path=None,  # Save to default location
            model_name=model_name,
            api_provider=api_provider,
            max_tokens=max_tokens,
            temperature=temperature,
            omit_round_report=omit_round_report,
            omit_memory_report=omit_memory_report,
            memory_llm_model=memory_llm_model,
            use_ace_memory=use_ace_memory,
            verbose=verbose
        )

        if result:
            recovered_count += 1

    if verbose:
        print(f"\n{'='*60}")
        print(f"✅ Recovered {recovered_count}/{len(rounds)} reports")
        print(f"{'='*60}")

    return recovered_count


def main():
    parser = argparse.ArgumentParser(
        description="Recover round-level reports from HypothesisBrancher checkpoints",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all available rounds and their report status
  python scripts/recover_report.py --project-prefix dwarf/jean/test_run --list

  # Recover missing reports from a specific round (generates both by default)
  python scripts/recover_report.py --project-prefix dwarf/jean/test_run --round 1

  # Recover missing reports from latest round
  python scripts/recover_report.py --project-prefix dwarf/jean/test_run --latest

  # Recover missing reports from all rounds
  python scripts/recover_report.py --project-prefix dwarf/jean/test_run --all

  # Only generate round report (skip memory report)
  python scripts/recover_report.py --project-prefix dwarf/jean/test_run --round 1 --omit-memory-report

  # Only generate memory report (skip round report)
  python scripts/recover_report.py --project-prefix dwarf/jean/test_run --round 1 --omit-round-report

  # Use simple memory mode instead of ACE (3-agent) mode
  python scripts/recover_report.py --project-prefix dwarf/jean/test_run --round 1 --simple-memory

Note: By default, the script generates BOTH round_report.md and memory_report.md
if they don't exist. It uses configuration from the checkpoint to ensure report
generation matches the original experiment settings.
        """
    )

    parser.add_argument(
        '--project-prefix',
        required=True,
        help='Project prefix under LOGS/, e.g. dataset_name/run_dir_name'
    )

    parser.add_argument(
        '--list', '-l',
        action='store_true',
        help='List all available rounds with their status'
    )

    parser.add_argument(
        '--round', '-r',
        type=int,
        help='Recover report from a specific round number'
    )

    parser.add_argument(
        '--latest',
        action='store_true',
        help='Recover report from the latest round'
    )

    parser.add_argument(
        '--all', '-a',
        action='store_true',
        help='Recover reports from all rounds'
    )

    parser.add_argument(
        '--output', '-o',
        help='Output path for recovered report (default: save to round directory as round_report.md)'
    )

    parser.add_argument(
        '--model', '-m',
        help='LLM model to use for report generation (default: use checkpoint config)'
    )

    parser.add_argument(
        '--provider', '-p',
        choices=['anthropic', 'openai'],
        help='API provider to use (default: use checkpoint config)'
    )

    parser.add_argument(
        '--max-tokens',
        type=int,
        help='Maximum tokens for LLM response (default: use checkpoint config)'
    )

    parser.add_argument(
        '--temperature',
        type=float,
        help='Temperature for LLM (default: use checkpoint config)'
    )

    parser.add_argument(
        '--omit-round-report',
        action='store_true',
        help='Skip round report generation (only generate memory report if missing)'
    )

    parser.add_argument(
        '--omit-memory-report',
        action='store_true',
        help='Skip memory report generation (only generate round report if missing)'
    )

    parser.add_argument(
        '--memory-model',
        help='LLM model for memory integration (default: use checkpoint config)'
    )

    parser.add_argument(
        '--simple-memory',
        action='store_true',
        help='Use simple (1-agent) memory mode instead of ACE (3-agent) mode'
    )

    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress status messages'
    )

    args = parser.parse_args()

    verbose = not args.quiet
    log_dir = os.path.join(DATA_PATH, "LOGS", args.project_prefix)

    if not os.path.isdir(log_dir):
        print(f"❌ Log directory not found: {log_dir}")
        sys.exit(1)

    # Handle --list
    if args.list:
        list_available_rounds(log_dir, verbose=verbose)
        return

    # Validate that at least one action is specified
    if not (args.round is not None or args.latest or args.all):
        parser.error("Must specify one of: --list, --round, --latest, or --all")
        return

    # Handle --all
    if args.all:
        use_ace_memory = None if not args.simple_memory else False  # None = use checkpoint config

        recovered_count = recover_all_rounds(
            log_dir=log_dir,
            model_name=args.model,
            api_provider=args.provider,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            omit_round_report=args.omit_round_report,
            omit_memory_report=args.omit_memory_report,
            memory_llm_model=args.memory_model,
            use_ace_memory=use_ace_memory,
            verbose=verbose
        )
        sys.exit(0 if recovered_count > 0 else 1)

    # Determine round number
    round_num = None
    if args.round is not None:
        round_num = args.round
    elif args.latest:
        rounds = find_all_rounds(log_dir)
        if not rounds:
            print(f"❌ No rounds found in {log_dir}/checkpoints")
            sys.exit(1)
        round_num = max(rounds)
        if verbose:
            print(f"Latest round: {round_num}")

    # Recover single round
    if round_num is not None:
        use_ace_memory = None if not args.simple_memory else False  # None = use checkpoint config

        result = recover_round_report(
            log_dir=log_dir,
            round_num=round_num,
            output_path=args.output,
            model_name=args.model,
            api_provider=args.provider,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            omit_round_report=args.omit_round_report,
            omit_memory_report=args.omit_memory_report,
            memory_llm_model=args.memory_model,
            use_ace_memory=use_ace_memory,
            verbose=verbose
        )
        if result:
            if not verbose:
                print(result)
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
