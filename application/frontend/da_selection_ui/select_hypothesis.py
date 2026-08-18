#!/usr/bin/env python3
"""
Interactive Hypothesis Generation and Selection Tool

This script loads a checkpoint directory, generates hypotheses on-demand using
the HypothesisGenAgent, and provides an interactive interface for Q&A, editing,
and selection. The entire session maintains a continuous conversation with the
agent - no context is ever reset.

Usage:
    python scripts/select_hypothesis.py <checkpoint_dir>
    python scripts/select_hypothesis.py <checkpoint_dir> --output <output_file>

Example:
    python scripts/select_hypothesis.py /path/to/logs/checkpoints/round_0
    python scripts/select_hypothesis.py /path/to/logs/checkpoints/round_0 --output selected.md
"""

import os
import sys
import re
import json
import argparse
import datetime
import signal
import atexit
import hashlib
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple, Union, Dict

# Add AIR to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from AIR.config import HypothesisGenerationConfig, LLMConfig
from AIR.hyp_agents.hypothesis_gen_agent import HypothesisGenAgent
from AIR.utils.pdf_loader import load_pdfs_from_directory, create_pdf_content_blocks
from AIR.hyp_agents.hypothesis_gen_agent import HYPOTHESIS_GENERATION_SYSTEM_PROMPT
from AIR.utils.code_chat import run_code_chat

hyp_system_prompt = HYPOTHESIS_GENERATION_SYSTEM_PROMPT + \
"""
You may be interacting with a human scientist who will ask questions and critique your hypotheses.
You should be helpful and informative, answering the human scientist's questions honestly and openly.
You should integrate any feedback and default to the expert when they provide feedback.

IMPORTANT: You do not have access to the ability to run queries or perform experiments. 
Your job is only to generate hypotheses that will then be tested and validated later.
Focus on formulating hypotheses right now and working with the scientist to refine them.

"""

ROUND_REPORT_SUMMARY_RATIO = 0.5
ROUND_REPORT_SUMMARY_TRIGGER_CHARS = 20000
ROUND_REPORT_SUMMARY_CHUNK_CHARS = 60000
ROUND_REPORT_SUMMARY_MAX_LEN_MULTIPLIER = 1.2
ROUND_REPORT_SUMMARY_SYSTEM_PROMPT = (
    "You are a careful scientific report summarizer. "
    "Summarize only the provided text. Preserve key findings, quantitative results, "
    "decisions, and citations. Keep headings and bullet structure when present. "
    "Do not add new information or speculate."
)


_HYPOTHESIS_SECTION_NAMES = {
    'title': 'title',
    'primary factor': 'primary_factor',
    'interpretation': 'interpretation',
    'hypothesis': 'hypothesis',
    'justification': 'justification',
    'plan': 'plan',
}
_HYPOTHESIS_SECTION_LABEL = (
    r'Title|Primary[ \t]+factor|Interpretation|Hypothesis|Justification|Plan'
)
_EMPHASIZED_HYPOTHESIS_SECTION_RE = re.compile(
    rf'^(?P<marker>\*{{1,3}}|_{{1,3}})[ \t]*'
    rf'(?P<label>{_HYPOTHESIS_SECTION_LABEL})[ \t]*'
    rf'(?::[ \t]*)?(?P=marker)[ \t]*:?[ \t]*(?P<content>.*)$',
    re.IGNORECASE,
)
_PLAIN_HYPOTHESIS_SECTION_RE = re.compile(
    rf'^(?P<label>{_HYPOTHESIS_SECTION_LABEL})[ \t]*:[ \t]*(?P<content>.*)$',
    re.IGNORECASE,
)
_HEADING_HYPOTHESIS_SECTION_RE = re.compile(
    rf'^(?P<label>{_HYPOTHESIS_SECTION_LABEL})(?:[ \t]*:[ \t]*|[ \t]+)?(?P<content>.*)$',
    re.IGNORECASE,
)


def _match_hypothesis_section_line(line: str):
    """Return a tolerant section-label match for one hypothesis document line."""
    candidate = line.strip()
    if not candidate:
        return None

    heading_match = re.match(r'^#{1,6}[ \t]+(.*)$', candidate)
    if heading_match:
        heading_content = heading_match.group(1).strip()
        return (
            _EMPHASIZED_HYPOTHESIS_SECTION_RE.match(heading_content)
            or _HEADING_HYPOTHESIS_SECTION_RE.match(heading_content)
        )

    return (
        _EMPHASIZED_HYPOTHESIS_SECTION_RE.match(candidate)
        or _PLAIN_HYPOTHESIS_SECTION_RE.match(candidate)
    )


def parse_hypothesis_sections_text(
    hypothesis: str | None,
    *,
    fallback_to_full_text: bool = True,
) -> Dict[str, Optional[str]]:
    """Parse the user-editable hypothesis-card document format.

    Section labels may be plain, Markdown-emphasized, or headings. Colons are
    accepted either inside or outside emphasis and are optional for emphasized
    labels, so harmless punctuation edits cannot hide a card's content.

    If no usable Hypothesis section is found, the complete document is returned
    as the hypothesis visibility fallback unless ``fallback_to_full_text`` is
    disabled by a caller that specifically needs structured-only detection.
    """
    text = str(hypothesis or '')
    sections: Dict[str, Optional[str]] = {
        'title': None,
        'interpretation': None,
        'hypothesis': None,
        'justification': None,
        'plan': None,
        'primary_factor': None,
    }
    current_key = None
    current_lines: List[str] = []

    def flush_current_section() -> None:
        nonlocal current_lines
        if current_key is not None:
            content = '\n'.join(current_lines).strip()
            sections[current_key] = content or None
        current_lines = []

    for line in text.splitlines():
        match = _match_hypothesis_section_line(line)
        if match:
            flush_current_section()
            label = re.sub(r'[ \t]+', ' ', match.group('label')).strip().lower()
            current_key = _HYPOTHESIS_SECTION_NAMES[label]
            inline_content = (match.group('content') or '').strip()
            current_lines = [inline_content] if inline_content else []
        elif current_key is not None:
            current_lines.append(line)
    flush_current_section()

    if fallback_to_full_text and text.strip() and not sections['hypothesis']:
        sections['hypothesis'] = text.strip()
    return sections


def preserve_manual_hypothesis_text(
    hypothesis: str | None,
    *,
    title: str | None = None,
) -> str:
    """Keep a direct manual edit verbatim while retaining its existing title.

    The V2 manual editor normally submits only the editable body. If a caller
    does include a Title section, replace only that section with the trusted
    existing title; do not normalize punctuation in the remaining sections.
    """
    text = str(hypothesis or '').strip()
    if not title:
        return text

    lines = text.splitlines()
    title_start = None
    title_end = None
    for index, line in enumerate(lines):
        match = _match_hypothesis_section_line(line)
        if not match:
            continue
        label = re.sub(r'[ \t]+', ' ', match.group('label')).strip().lower()
        if title_start is None and _HYPOTHESIS_SECTION_NAMES[label] == 'title':
            title_start = index
            continue
        if title_start is not None:
            title_end = index
            break

    if title_start is not None:
        end = title_end if title_end is not None else len(lines)
        lines = lines[:title_start] + lines[end:]
        text = '\n'.join(lines).strip()

    parts = [f"**Title**: {title.strip()}"]
    if text:
        parts.append(text)
    return '\n\n'.join(parts)

# Template for the domain-general literature survey query sent to the deep
# research provider.  Must not produce hypotheses directly — the output is used
# as context by HypothesisGenAgent, not as a final deliverable.
_HYPO_LIT_QUERY_TEMPLATE = """\
We are conducting scientific research and are about to generate research hypotheses \
for a dataset. Before hypothesis generation, please provide a focused literature survey \
covering the following topics for this domain:

1. What is already well-established — findings so well-replicated that proposing them \
   as novel would be redundant
2. Contradictory or complicating evidence that nuances the mainstream view
3. Whether key findings have been overgeneralized from a specific organism, disease \
   context, cell type, assay, or intervention and may not apply broadly
4. Underexplored gaps, inconsistencies, or open questions where a novel contribution \
   is feasible
5. Candidate mechanisms, pathways, or interventions worth exploring given the available \
   data
6. Concrete, testable predictions that could be evaluated with observational or \
   computational data (no wet-lab required)
7. Cautious interpretation notes — what the literature does NOT yet confidently support

Domain context:
{domain_context}

Please write a concise, structured literature overview (under 1500 words). Include \
source identifiers, paper titles, or result IDs when available. Do NOT propose final \
hypotheses — this survey will be used by a separate agent to formulate novel, \
dataset-testable hypotheses.
"""

# Prefixes that indicate retrieval failure — these are saved to disk but not
# injected into the hypothesis prompt or chat context.
_HYPO_LIT_ERROR_PREFIXES = ('[RETRIEVAL: ERROR]', '[RETRIEVAL: NO RESULTS]')


def _is_retrieval_failure_markdown(text: Optional[str]) -> bool:
    """True if text is a DeepResearchAgent failure marker rather than usable content.

    Handles both bare `[RETRIEVAL: ERROR]` and the markdown-bold `**[RETRIEVAL: ERROR]**`
    form actually emitted by PaperclipSDKProvider (mirrors canonical hypothesis_gen_agent.py).
    """
    if not text:
        return True
    stripped = text.strip().lstrip("*").strip()
    return stripped.startswith("[RETRIEVAL: ERROR]") or stripped.startswith("[RETRIEVAL: NO RESULTS]")


try:
    from AIR.tool_agents.deep_research import DeepResearchAgent, DeepResearchConfig
except ImportError as _dr_import_err:
    DeepResearchAgent = None   # type: ignore[assignment,misc]
    DeepResearchConfig = None  # type: ignore[assignment,misc]
    print(
        f"ℹ️ DeepResearchAgent not available ({_dr_import_err}); "
        "use_deep_research_for_hypothesis_generation will be a no-op"
    )
    del _dr_import_err


# ---------------------------------------------------------------------------
# Card title contract
#
# A hypothesis card title is a concise scientific *label*, not the full
# hypothesis claim (which always lives in the Hypothesis field). The generation
# prompt asks the model for this shape, but model output can still drift into a
# full sentence, so these pure helpers give both the generation-time
# normalization and the read-time display path one shared, testable definition.
#
# The constraint is WORD-BASED, never character-based: a title is valid when it
# fits the word budget, and a valid title is always displayed in full (no
# character cap, no ellipsis, no truncation). When the model emits an invalid
# title we derive a new title-like phrase; we never clip the original at a
# character or word boundary as the primary mechanism.
#
#   preferred: 10-14 words · hard maximum: 18 words
# ---------------------------------------------------------------------------
CARD_TITLE_PREFERRED_WORDS = 14
CARD_TITLE_MAX_WORDS = 18
HYPOTHESIS_STUDIO_REFERENCE_NOTE = (
    'Card numbers are temporary visible positions after removals or reordering; '
    '“version N” means this hypothesis’s regeneration version, while “round N” means the research round.'
)


def compact_card_title_text(value: Optional[str]) -> str:
    """Collapse whitespace and strip wrapping quotes/backticks from a title."""
    return re.sub(r'\s+', ' ', str(value or '')).strip().strip('"\'`')


def card_title_word_count(value: Optional[str]) -> int:
    """Word count that ignores punctuation-only tokens (hyphenated terms count once)."""
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9/_\-]*", str(value or '')))


def title_needs_card_rewrite(title: Optional[str], hypothesis_text: Optional[str] = None) -> bool:
    """True when a title violates the WORD-BASED card contract and must be re-derived.

    Fires for a missing title, a title over the hard word maximum, a long title
    that reads as a full sentence, or a long title that merely echoes the
    hypothesis claim. Character count is never a trigger: a valid, scientifically
    plausible title that happens to be long in characters but within the word
    budget is left alone and displayed in full.
    """
    title = compact_card_title_text(title)
    if not title:
        return True

    word_count = card_title_word_count(title)
    # A title that merely echoes the hypothesis claim is only a problem when it
    # is also too long to read as a label. A short claim (e.g. a one-line
    # hypothesis used as a fallback scanning title) doubles fine as a card title.
    hypothesis = compact_card_title_text(hypothesis_text)
    if hypothesis and title.lower() == hypothesis.lower() and word_count > CARD_TITLE_PREFERRED_WORDS:
        return True

    if word_count > CARD_TITLE_MAX_WORDS:
        return True
    if word_count > CARD_TITLE_PREFERRED_WORDS and re.search(r'[.!?]\s*$', title):
        return True
    return False


def derive_concise_card_title(title: Optional[str], hypothesis_text: Optional[str] = None) -> str:
    """Derive a title-like noun phrase when the model wrote a sentence as Title.

    Deterministic and side-effect free. The primary mechanism is *semantic*, not
    mechanical: it drops the sentence tail and clausal riders (``that ...``,
    ``via ...``, ``, creating ...``) and strips predicate scaffolding
    (``acts as a``, ``represents a``) so the leading target/mechanism phrase
    survives — e.g. a "W3-type RGCs represent a ... state that can be ..." sentence
    becomes 'W3-type RGCs "regeneration-primed but survival-deficient" state'
    rather than the first N words of the sentence. Only the display label is
    affected; the scientific sections are never touched.

    A final word-budget guard trims to the preferred length purely as a
    last resort for pathological input with no clause structure to cut on; it is
    not how normal titles are produced.
    """
    source = compact_card_title_text(title) or compact_card_title_text(hypothesis_text)
    source = re.sub(r'^\s*(?:\*{0,2})?(?:Title|Hypothesis)(?:\*{0,2})?\s*:\s*', '', source, flags=re.I)
    source = re.split(r'(?<=[.!?])\s+', source, maxsplit=1)[0]
    source = re.split(r'\s+(?:because|while|whereas|whose|which|that|via|using|preventing|distinguished)\b', source, maxsplit=1, flags=re.I)[0]
    source = re.split(r',\s*(?:and|but|while|which|whose|that|\w+ing)\b', source, maxsplit=1, flags=re.I)[0]
    source = re.sub(r'\bacts\s+as\s+an?\b', '', source, flags=re.I)
    source = re.sub(r'\boperates\s+through\s+an?\b', '', source, flags=re.I)
    source = re.sub(r'\brepresents?\s+an?\b', '', source, flags=re.I)
    source = re.sub(r'\bcan\s+be\b.*$', '', source, flags=re.I)
    source = compact_card_title_text(source.strip(' ,;:-'))

    # Last-resort word-budget guard (not the primary mechanism, and never a
    # character cap): only fires when clause-stripping left a phrase still longer
    # than a title should ever be.
    words = re.findall(r'[^\s]+', source)
    if len(words) > CARD_TITLE_PREFERRED_WORDS:
        source = ' '.join(words[:CARD_TITLE_PREFERRED_WORDS]).strip(' ,;:-')
    return source or 'Untitled research direction'


def display_card_title(title: Optional[str], hypothesis_text: Optional[str] = None) -> str:
    """Return a display-safe card label: the model title (in full) when it honors
    the word contract, otherwise a deterministically derived concise label.
    Chooses the label only; the underlying hypothesis/full_text is never mutated,
    and a valid title is returned verbatim (no character truncation)."""
    if title_needs_card_rewrite(title, hypothesis_text):
        return derive_concise_card_title(title, hypothesis_text)
    return compact_card_title_text(title)


class InteractiveHypothesisGenerator:
    """Interactive tool for generating and selecting hypotheses with conversational agent."""

    def __init__(
        self,
        checkpoint_dir: str,
        code_dir: Optional[str] = None,
        output_file: Optional[str] = None,
        hypothesis_model: Optional[str] = None,
        register_signal_handlers: bool = True,
        include_background_literature: bool = False,
        use_legacy_hyp_gen: bool = False,
        num_hypotheses: Optional[int] = None,
        generate_plan: Optional[bool] = None,
        readme_path: Optional[str] = None,
        enable_citations: bool = False,
        cache_documents: bool = False,
        research_paper_path: Optional[str] = None,
    ):
        """
        Initialize the interactive hypothesis generator.

        Args:
            checkpoint_dir: Path to checkpoint directory containing config.json and reports
            code_dir: Optional path to code directory (containing data/README.txt); overrides
                working_directory from checkpoint config for loading dataset description
            output_file: Optional path for output file. If None, saves to checkpoint_dir.
            hypothesis_model: Optional model name override for hypothesis generation
            register_signal_handlers: Whether to register signal handlers (set False for web/threaded contexts)
            include_background_literature: Whether to load deep_research_background.md and feed it to the hypothesis generator
            generate_plan: Whether to ask the hypothesis generator to include a plan section
            readme_path: Optional explicit path to the dataset README; overrides auto-detected path
            research_paper_path: Optional explicit research paper path; relative paths are
                resolved against checkpoint/log roots
        """
        self.checkpoint_dir = Path(checkpoint_dir)
        self.code_dir = Path(code_dir) if code_dir else None
        self.output_file = output_file
        self.hypothesis_model_name = hypothesis_model
        self.include_background_literature = include_background_literature
        self.use_legacy_hyp_gen = use_legacy_hyp_gen
        self.num_hypotheses_override = num_hypotheses
        self.generate_plan_override = generate_plan
        self.dataset_readme_path = Path(readme_path) if readme_path else None
        self.configured_research_paper_path = research_paper_path
        self.enable_citations = enable_citations
        self.cache_documents = cache_documents
        # For web debugging: when True, print prompts sent to the hypothesis agent
        self.debug_print_prompts = False

        # State
        self.hypotheses: List[str] = []  # Source of truth for generated hypotheses
        self.qa_history: List[Dict] = []  # For display only: [{'Q': ..., 'A': ...}, ...]
        self.report_chat_agents: Dict[str, HypothesisGenAgent] = {}
        self.report_chat_context_hashes: Dict[str, str] = {}
        self.report_qa_history: Dict[str, List[Dict]] = {}
        self.report_code_chat_conversations: Dict[str, 'CodeChatConversation'] = {}
        self.audit_only_mode = False

        # Will be loaded from checkpoint
        self.config: dict = None
        self.hypothesis_config: HypothesisGenerationConfig = None
        self.dataset_description: str = None
        self.task_description: str = None
        self.round_report: Optional[str] = None
        self.memory_report: Optional[str] = None
        self.round_report_summary: Optional[str] = None
        self.memory_report_summary: Optional[str] = None
        self.deep_research_background: Optional[str] = None
        self.background_pdf_blocks = None
        self.research_paper_path: Optional[Path] = None
        self.citation_documents = []

        # Agent (maintains conversation via self.messages)
        self.hypothesis_gen_agent: HypothesisGenAgent = None
        self._hypothesis_chat_context_fingerprint: Optional[str] = None
        # V2 per-hypothesis refinement threads, keyed by a STABLE thread id (not
        # the display index): thread_id -> message list. The thread id follows a
        # hypothesis across revisions and index shifts (removal), so display
        # index is used only for presentation and can never mis-attach a thread.
        # Each thread starts from the generation-time V1 baseline snapshot and
        # then accumulates only that hypothesis's own refinement conversation.
        self.hypothesis_chat_threads: Dict[str, List[dict]] = {}
        # Stable per-candidate thread ids, parallel to self.hypotheses (1-based).
        self.hypothesis_thread_ids: List[str] = []
        self._active_thread_key: Optional[str] = None
        # Per-generator lock making the whole save/install/V1-call/persist/restore
        # swap on the ONE shared HypothesisGenAgent.messages atomic, independent
        # of any external (per-session) serialization. Reentrant so a generator
        # is never at risk of self-deadlock.
        self._thread_swap_lock = threading.RLock()
        # The exact V1 post-generation baseline, captured ONCE when a set is
        # generated, then cloned into every thread. Preserved so a thread first
        # opened after another hypothesis was revised still receives the
        # generation-time baseline, never a rebuild from mutated hypotheses.
        self._refinement_baseline_snapshot: Optional[str] = None
        self.logger = None  # Minimal logger for file-only logging

        # Session logging
        self.session_start_time = datetime.datetime.now()
        self.session_log_dir: Optional[Path] = None
        self.interaction_log: List[Dict] = []  # Log of all user interactions
        self.final_selection: Optional[Dict] = None  # Final selection info
        self.last_answer_citations: List[Dict] = []
        self.last_generation_citations: List[Dict] = []
        self.last_report_answer_citations: List[Dict] = []

        # Deep-research / hypothesis literature config (read from config.json in load_checkpoint)
        self.use_deep_research_for_hypothesis_generation: bool = False
        self.deep_research_provider: str = 'paperclip'
        self.hypothesis_literature_context: Optional[str] = None  # in-memory cache

        # Register cleanup handlers (only in main thread contexts like CLI)
        if register_signal_handlers:
            atexit.register(self._cleanup)
            signal.signal(signal.SIGINT, self._handle_interrupt)
            signal.signal(signal.SIGTERM, self._handle_interrupt)

    def _handle_interrupt(self, signum, frame):
        """Handle Ctrl+C and other termination signals."""
        print("\n\n⚠️  Interrupt received. Saving session log before exit...")
        self._cleanup()
        sys.exit(1)

    def _cleanup(self):
        """Cleanup handler - saves session log."""
        try:
            self.save_session_log()
        except Exception as e:
            print(f"⚠️  Warning: Error saving session log: {str(e)}")

    def log_interaction(self, action_type: str, details: Dict):
        """
        Log a user interaction.

        Args:
            action_type: Type of action (e.g., "view", "edit", "regenerate", "question", "select")
            details: Dictionary with action-specific details
        """
        self.interaction_log.append({
            'timestamp': datetime.datetime.now().isoformat(),
            'action_type': action_type,
            'details': details
        })

    def save_session_log(self):
        """Save complete session log to JSON file."""
        if not self.session_log_dir:
            # Create session log directory
            self.session_log_dir = self.checkpoint_dir / "interactive_hyp_gen"
            self.session_log_dir.mkdir(parents=True, exist_ok=True)

        # Generate session filename with timestamp
        session_file = self.session_log_dir / f"session_{self.session_start_time.strftime('%Y%m%d_%H%M%S')}.json"

        # Build complete session log
        session_data = {
            'session_metadata': {
                'start_time': self.session_start_time.isoformat(),
                'end_time': datetime.datetime.now().isoformat(),
                'checkpoint_dir': str(self.checkpoint_dir),
                'output_file': str(self.output_file) if self.output_file else None,
            },
            'configuration': {
                'dataset_description': self.dataset_description,
                'task_description': self.task_description,
                'hypothesis_model': self.hypothesis_config.llm_config.model_name if self.hypothesis_config else None,
                'n_candidate_hypotheses': len(self.hypotheses),
            },
            'generated_hypotheses': self.hypotheses,
            'agent_conversation': [],
            'user_interactions': self.interaction_log,
            'qa_history': self.qa_history,
            'final_selection': self.final_selection,
        }

        # Extract agent conversation messages if agent exists
        if self.hypothesis_gen_agent and hasattr(self.hypothesis_gen_agent, 'messages'):
            session_data['agent_conversation'] = self.hypothesis_gen_agent.messages

        # Save to JSON
        try:
            with open(session_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
            print(f"\n💾 Session log saved to: {session_file}")
            return str(session_file)
        except Exception as e:
            print(f"\n❌ Error saving session log: {str(e)}")
            # Never redirect project conversation history into the process CWD:
            # for a managed project that could be the disposable source checkout.
            return None

    def validate_checkpoint(self) -> bool:
        """Validate that checkpoint directory exists and has required files."""
        if not self.checkpoint_dir.exists():
            print(f"❌ Error: Checkpoint directory not found: {self.checkpoint_dir}")
            return False

        if not self.checkpoint_dir.is_dir():
            print(f"❌ Error: Path is not a directory: {self.checkpoint_dir}")
            return False

        config_file = self.checkpoint_dir / "config.json"
        if not config_file.exists():
            print(f"❌ Error: config.json not found in {self.checkpoint_dir}")
            return False

        return True

    def load_checkpoint(self) -> bool:
        """Load configuration and reports from checkpoint directory."""
        try:
            # Load config.json
            config_file = self.checkpoint_dir / "config.json"
            with open(config_file, 'r') as f:
                checkpoint_data = json.load(f)

            print(f"✓ Loaded config from {config_file}")

            # The config is nested under "config" key
            self.config = checkpoint_data.get('config', checkpoint_data)

            # Load dataset_description from working_directory/data/README.txt
            # This matches the pattern in run_hb_sherlock.py
            working_dir_config = self.config.get('working_directory')
            working_dir = self.code_dir if self.code_dir else (Path(working_dir_config) if working_dir_config else None)

            # If code_dir wasn't provided, but the checkpoint config has a usable working_directory,
            # adopt it so code chat can mount the repository locally.
            if self.code_dir is None and working_dir_config:
                try:
                    candidate = Path(working_dir_config).expanduser()
                    if candidate.exists() and candidate.is_dir():
                        self.code_dir = candidate
                        working_dir = candidate
                        print(f"✓ Using working_directory for code_dir: {candidate}")
                except Exception:
                    pass
            data_description_path = None

            # Respect explicit README path when provided (relative paths resolve against code_dir/working_directory)
            if self.dataset_readme_path:
                explicit_path = self.dataset_readme_path
                if not explicit_path.is_absolute():
                    base_dir = self.code_dir or (Path(working_dir_config) if working_dir_config else None)
                    if base_dir:
                        explicit_path = Path(base_dir) / explicit_path
                explicit_path = explicit_path.resolve()

                if explicit_path.exists():
                    data_description_path = explicit_path
                else:
                    print(f"⚠️ Warning: Provided README path does not exist: {explicit_path}")

            # Fallback to auto-detected README under working_directory/data
            if data_description_path is None and working_dir:
                candidate_path = Path(working_dir) / "data" / "README.txt"
                if candidate_path.exists():
                    data_description_path = candidate_path
                else:
                    print(f"⚠️ Warning: Dataset description not found at {candidate_path}")
            elif data_description_path is None:
                print(f"⚠️ Warning: No working_directory in config and no README path provided")

            # Load dataset description if we have a path
            if data_description_path:
                try:
                    with open(data_description_path, 'r') as f:
                        self.dataset_description = f.read()

                    # Replace {datapath} placeholder with actual path
                    data_path = str(Path(working_dir) / "data") if working_dir else str(data_description_path.parent)
                    self.dataset_description = self.dataset_description.replace("{datapath}", data_path)

                    if self.dataset_readme_path:
                        source_label = "provided_readme_path"
                    else:
                        source_label = "code_dir" if self.code_dir else "working_directory"

                    print(f"✓ Loaded dataset description from {data_description_path} ({len(self.dataset_description)} characters, source={source_label})")
                except Exception as e:
                    print(f"⚠️ Warning: Could not read dataset description from {data_description_path}: {e}")
                    self.dataset_description = None
            else:
                self.dataset_description = None

            # Fallback: Use dataset_name as minimal description
            if not self.dataset_description:
                dataset_name = self.config.get('dataset_name', 'unknown')
                self.dataset_description = f"Dataset: {dataset_name}"
                print(f"   Using dataset name as fallback: {dataset_name}")

            # Load task_description from file if path provided
            task_desc_file = self.config.get('task_description_file')
            if task_desc_file and Path(task_desc_file).exists():
                with open(task_desc_file, 'r') as f:
                    self.task_description = f.read().strip()
                print(f"✓ Loaded task description ({len(self.task_description)} characters)")
            else:
                self.task_description = self.config.get('task_description', '')

            # Build LLMConfig from flat config
            if self.hypothesis_model_name is None:
                hypothesis_model = self.config.get('hypothesis_model', 'claude-sonnet-4-5-20250929')
                self.hypothesis_model_name = hypothesis_model
            else:
                hypothesis_model = self.hypothesis_model_name

            llm_config = LLMConfig(
                model_name=hypothesis_model,
                temperature=self.config.get('temperature', 0.2),
                max_tokens=self.config.get('max_tokens', 8192)
            )

            # Determine candidate hypothesis count (CLI override takes precedence)
            n_candidate_hypotheses = self.config.get('n_candidate_hypotheses', 3)
            if self.num_hypotheses_override:
                n_candidate_hypotheses = self.num_hypotheses_override

            # Build HypothesisGenerationConfig
            generate_plan_value = (
                self.generate_plan_override
                if self.generate_plan_override is not None
                else self.config.get('generate_plan', False)
            )
            self.hypothesis_config = HypothesisGenerationConfig(
                llm_config=llm_config,
                log_dir=self.config.get('log_dir', 'logs'),
                max_tokens=self.config.get('max_tokens', 8192),
                system_prompt=hyp_system_prompt,
                human_selection_mode=self.config.get('human_selection', False),
                n_candidate_hypotheses=n_candidate_hypotheses,
                selected_hypothesis_file=self.config.get('selected_hypothesis_file'),
                no_auto_hypothesis=False,
                generate_plan=generate_plan_value,
                enable_citations=self.enable_citations,
                cache_documents=self.cache_documents,
            )

            print(f"✓ Loaded hypothesis config (model: {self.hypothesis_config.llm_config.model_name})")

            # Apply defaults for deep-research keys absent from config.json.
            # self.config is the raw config dict (loaded once above), so any
            # keys present in the file are already here; old checkpoints that
            # predate these keys get the preferred defaults rather than
            # silent no-ops.
            _DR_DEFAULTS = {
                'use_deep_research_for_hypothesis_generation': True,
                'deep_research_provider': 'paperclip',
                'enable_deep_research_in_review': True,
                # How often review/optimization feedback runs: 2 = every two completed query steps.
                'review_optimization_steps': 2,
                # True = hold review until an explicit hypothesis phase; do not run
                # review during the exploratory phase.
                'hold_reviews_in_exploratory_phase': True,
            }
            for _k, _v in _DR_DEFAULTS.items():
                if _k not in self.config:
                    self.config[_k] = _v

            # Read deep-research feature flags from checkpoint config
            self.use_deep_research_for_hypothesis_generation = bool(
                self.config.get('use_deep_research_for_hypothesis_generation', True)
            )
            raw_provider = self.config.get('deep_research_provider', 'paperclip')
            self.deep_research_provider = (str(raw_provider).lower().strip() or 'paperclip')
            print(
                f"✓ Deep-research effective config: {{"
                f"'deep_research_provider': {self.deep_research_provider!r}, "
                f"'use_deep_research_for_hypothesis_generation': {self.use_deep_research_for_hypothesis_generation}, "
                f"'enable_deep_research_in_review': {self.config.get('enable_deep_research_in_review')}, "
                f"'review_optimization_steps': {self.config.get('review_optimization_steps')}, "
                f"'hold_reviews_in_exploratory_phase': {self.config.get('hold_reviews_in_exploratory_phase')}"
                f"}}"
            )
            if self.use_deep_research_for_hypothesis_generation:
                print(
                    f"ℹ️ use_deep_research_for_hypothesis_generation=True "
                    f"provider={self.deep_research_provider}"
                )

            # Load round_report.md if exists
            round_report_file = self.checkpoint_dir / "round_report.md"
            if round_report_file.exists():
                with open(round_report_file, 'r') as f:
                    content = f.read()
                # Extract report content after "## Round Report" if present
                match = re.search(r'## Round Report\n\n(.*)', content, re.DOTALL)
                self.round_report = match.group(1).strip() if match else content.strip()
                print(f"✓ Loaded round report ({len(self.round_report)} characters)")

            # Load memory_report.md if exists
            memory_report_file = self.checkpoint_dir / "memory_report.md"
            if memory_report_file.exists():
                with open(memory_report_file, 'r') as f:
                    content = f.read()
                # Extract report content after "## Accumulated Report" if present
                match = re.search(r'## Accumulated Report\n\n(.*)', content, re.DOTALL)
                self.memory_report = match.group(1).strip() if match else content.strip()
                print(f"✓ Loaded memory report ({len(self.memory_report)} characters)")

            # Load memory_report_summary.md if exists
            memory_summary_file = self.checkpoint_dir / "memory_report_summary.md"
            if memory_summary_file.exists():
                with open(memory_summary_file, 'r') as f:
                    self.memory_report_summary = f.read().strip()
                print(f"✓ Loaded memory report summary ({len(self.memory_report_summary)} characters)")

            # Load round_report_summary.md if exists
            round_summary_file = self.checkpoint_dir / "round_report_summary.md"
            if round_summary_file.exists():
                with open(round_summary_file, 'r') as f:
                    self.round_report_summary = f.read().strip()
                print(f"✓ Loaded round report summary ({len(self.round_report_summary)} characters)")

            # Load deep_research_background.md if exists
            dr_background_file = self.checkpoint_dir / "deep_research_background.md"
            if dr_background_file.exists():
                try:
                    with open(dr_background_file, 'r') as f:
                        self.deep_research_background = f.read().strip()
                    print(f"✓ Loaded deep research background ({len(self.deep_research_background)} characters)")
                except Exception as e:
                    print(f"⚠️ Warning: Could not read deep research background: {e}")

            # Detect research_paper.md (rendered separately in the web UI)
            research_paper_file = self.checkpoint_dir / "research_paper.md"
            if research_paper_file.exists():
                self.research_paper_path = research_paper_file
                print(f"✓ Found research_paper.md ({research_paper_file.stat().st_size} bytes)")

            # Prefer the latest research_paper_* directory when present.
            latest_research_paper = self._find_latest_research_paper()
            if latest_research_paper is not None:
                self.research_paper_path = latest_research_paper

            configured_research_paper = self._resolve_configured_research_paper()
            if configured_research_paper is not None:
                self.research_paper_path = configured_research_paper
                try:
                    print(f"✓ Found configured research_paper.md ({configured_research_paper.stat().st_size} bytes)")
                except Exception:
                    print("✓ Found configured research_paper.md")

            # Load background PDFs if specified
            background_path = self.config.get('background_path')
            if background_path and Path(background_path).exists():
                try:
                    pdf_documents = load_pdfs_from_directory(background_path)
                    if pdf_documents:
                        self.background_pdf_blocks = create_pdf_content_blocks(pdf_documents)
                        total_size_mb = sum(doc['size_bytes'] for doc in pdf_documents) / (1024 * 1024)
                        print(f"✓ Loaded {len(pdf_documents)} background PDF(s) ({total_size_mb:.1f}MB)")
                except Exception as e:
                    print(f"⚠️ Warning: Could not load background PDFs: {str(e)}")

            # Build citation documents when enabled
            if self.enable_citations:
                self._build_citation_documents()

            return True

        except Exception as e:
            print(f"❌ Error loading checkpoint: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def initialize_agent(self):
        """Initialize HypothesisGenAgent with checkpoint configuration."""
        try:
            # Create a minimal file-only logger for this interactive script
            # We don't want the AIR Logger's console output duplicating our UI
            log_dir = str(self.checkpoint_dir / "interactive_generation_logs")
            os.makedirs(log_dir, exist_ok=True)

            # Create a simple logger that only writes to file, no console output
            import logging
            from logging.handlers import RotatingFileHandler

            file_logger = logging.getLogger("interactive_hyp_gen")
            file_logger.setLevel(logging.INFO)

            # Clear any existing handlers
            if file_logger.handlers:
                for h in list(file_logger.handlers):
                    file_logger.removeHandler(h)

            # Add file handler only (no console handler)
            log_file = os.path.join(log_dir, "interactive_session.log")
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=50 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8"
            )
            formatter = logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            file_handler.setFormatter(formatter)
            file_logger.addHandler(file_handler)

            # Create a minimal logger wrapper compatible with AIR's Logger interface
            class MinimalLogger:
                def __init__(self, logger):
                    self._logger = logger
                    self.analysis_name = "interactive_hyp_gen"

                def log_action(self, action, details):
                    self._logger.info(f"[ACTION] {action}: {details}")

                def log_error(self, error):
                    self._logger.error(f"[ERROR] {error}")

                def log_prompt(self, role, prompt_text, prompt_name=""):
                    self._logger.info(f"[PROMPT] {role} {prompt_name}")

                def log_response(self, response_text, source="model"):
                    self._logger.info(f"[RESPONSE] {source}")

            self.logger = MinimalLogger(file_logger)

            # Extract multi-hypothesis parameters
            n_hypotheses = getattr(self.hypothesis_config, 'n_candidate_hypotheses', 3)
            judge_llm_config = getattr(self.hypothesis_config, 'judge_llm_config', None)

            # Resolve background report path from checkpoint_dir/deep_research_background.md
            background_report_path = None
            if self.include_background_literature:
                candidate_background_path = (self.checkpoint_dir / "../../deep_research_background.md").resolve()
                if not candidate_background_path.exists():
                    print(f"ℹ️ include_background_literature set but no deep_research_background.md found under {self.checkpoint_dir}")
                else:
                    background_report_path = candidate_background_path
                    print(f"ℹ️ Using background report for literature context: {background_report_path}")
            else:
                print("ℹ️ Background literature disabled (use --include_background_literature to enable)")

            # Initialize HypothesisGenAgent
            self.hypothesis_gen_agent = HypothesisGenAgent(
                config=self.hypothesis_config,
                background_pdf_blocks=self.background_pdf_blocks,
                background_report_path=str(background_report_path) if background_report_path else None,
                logger=self.logger,
                n_hypotheses=n_hypotheses,
                judge_llm_config=judge_llm_config,
                citation_documents=self.citation_documents,
            )

            print(f"✓ Initialized HypothesisGenAgent")

        except Exception as e:
            print(f"❌ Error initializing agent: {str(e)}")
            raise

    def _build_combined_background(self) -> str:
        """Combine round_report and memory_report into single background context."""
        combined = ""

        if self.deep_research_background:
            combined += self.deep_research_background.strip() + "\n\n"

        if self.memory_report:
            combined += self.memory_report.strip() + "\n\n"

        if self.round_report:
            combined += self.round_report.strip()

        return combined if combined else "No prior exploration data available."

    def _load_research_paper_text(self) -> Optional[str]:
        """Load the latest research paper text if available."""
        if not self.research_paper_path:
            return None
        try:
            if not self.research_paper_path.exists():
                return None
            return self.research_paper_path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception as exc:
            print(f"⚠️ Warning: Could not read research paper: {exc}")
            return None

    def _split_text_into_chunks(self, text: str, chunk_limit: int) -> List[str]:
        """Split text into roughly sized chunks, preferring paragraph boundaries."""
        if not text:
            return []
        if len(text) <= chunk_limit:
            return [text]

        paragraphs = text.split("\n\n")
        chunks: List[str] = []
        current: List[str] = []
        current_len = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(para) > chunk_limit:
                if current:
                    chunks.append("\n\n".join(current))
                    current = []
                    current_len = 0
                for i in range(0, len(para), chunk_limit):
                    chunks.append(para[i:i + chunk_limit])
                continue
            if current_len + len(para) + 2 > chunk_limit and current:
                chunks.append("\n\n".join(current))
                current = [para]
                current_len = len(para)
            else:
                current.append(para)
                current_len += len(para) + 2

        if current:
            chunks.append("\n\n".join(current))

        return chunks if chunks else [text]

    def _summarize_text_chunk(
        self,
        chunk: str,
        label: str,
        chunk_idx: int,
        total_chunks: int,
        target_ratio: float,
    ) -> Optional[str]:
        """Summarize a single chunk with the LLM."""
        if not self.hypothesis_gen_agent:
            return None
        chunk = chunk.strip()
        if not chunk:
            return None

        target_chars = max(200, int(len(chunk) * target_ratio))
        chunk_label = f"{label} chunk {chunk_idx}/{total_chunks}" if total_chunks > 1 else label
        user_prompt = (
            f"Summarize the following {chunk_label} to about {int(target_ratio * 100)}% of its original length.\n"
            f"Original length: {len(chunk)} characters. Target length: about {target_chars} characters.\n"
            "Keep section order and preserve headings/bullets/citations when present.\n"
            "Return only the summary.\n\n"
            f"--- BEGIN {chunk_label.upper()} ---\n"
            f"{chunk}\n"
            f"--- END {chunk_label.upper()} ---"
        )

        return self.hypothesis_gen_agent.call_llm_once(
            system_prompt=ROUND_REPORT_SUMMARY_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=self.hypothesis_config.max_tokens if self.hypothesis_config else None,
            temperature=0.0,
        )

    def _summarize_text_with_llm(
        self,
        text: str,
        label: str,
        target_ratio: float = ROUND_REPORT_SUMMARY_RATIO,
    ) -> Optional[str]:
        """Summarize long text via chunking, then optional consolidation."""
        if not text or not self.hypothesis_gen_agent:
            return None

        chunks = self._split_text_into_chunks(text, ROUND_REPORT_SUMMARY_CHUNK_CHARS)
        summaries: List[str] = []
        total_chunks = len(chunks)

        for idx, chunk in enumerate(chunks, 1):
            summary = self._summarize_text_chunk(chunk, label, idx, total_chunks, target_ratio)
            if summary:
                summaries.append(summary.strip())

        combined = "\n\n".join(s for s in summaries if s).strip()
        if not combined:
            return None

        target_chars = max(200, int(len(text) * target_ratio))
        if len(combined) > int(target_chars * ROUND_REPORT_SUMMARY_MAX_LEN_MULTIPLIER):
            combined = self._summarize_text_chunk(
                combined,
                f"{label} (combined)",
                1,
                1,
                target_ratio,
            ) or combined

        return combined.strip()

    def _ensure_round_report_summary(self) -> Optional[str]:
        """Generate a shorter round report summary when the full report is too long."""
        if not self.round_report:
            return None

        if self.round_report_summary and len(self.round_report_summary) <= len(self.round_report) * 0.6:
            return self.round_report_summary

        if len(self.round_report) < ROUND_REPORT_SUMMARY_TRIGGER_CHARS:
            return self.round_report_summary

        if not self.hypothesis_gen_agent:
            return self.round_report_summary

        try:
            if self.logger:
                self.logger.log_action(
                    "round_report_summarize_start",
                    f"Summarizing round report ({len(self.round_report)} chars)"
                )
            summary = self._summarize_text_with_llm(self.round_report, "Round Report")
            if summary:
                self.round_report_summary = summary
                if self.enable_citations:
                    self._build_citation_documents()
                if self.logger:
                    self.logger.log_action(
                        "round_report_summarize_complete",
                        f"Summary length: {len(summary)} chars"
                    )
            return summary
        except Exception as e:
            if self.logger:
                self.logger.log_error(f"Round report summarization failed: {str(e)}")
            return self.round_report_summary

    def _get_round_report_for_generation(self) -> Optional[str]:
        """Return a generation-safe round report (summary when needed)."""
        summary = self._ensure_round_report_summary()
        return summary if summary else self.round_report

    def _build_combined_background_for_generation(self) -> str:
        """Combine reports for model input, using summarized round report when needed."""
        combined = ""

        if self.deep_research_background:
            combined += self.deep_research_background.strip() + "\n\n"

        if self.memory_report:
            combined += self.memory_report.strip() + "\n\n"

        round_report_for_generation = self._get_round_report_for_generation()
        if round_report_for_generation:
            combined += round_report_for_generation.strip()

        return combined if combined else "No prior exploration data available."

    def _build_hypothesis_chat_context(self) -> str:
        """Canonical grounding packet for post-generation hypothesis chat.

        This is intentionally narrower than generation context: Memory is the
        continuity layer, so we do not inject round_report.md separately here.
        """
        memory = (self.memory_report or '').strip() or "No memory_report.md is available yet."
        context_setup = f"""DiscoveryAgent already has the following project context for this Hypothesis Studio conversation. Do not ask the scientist to provide it again.

## Research Objective
{self.task_description if self.task_description else 'Not specified'}

## Project Context / README
{self.dataset_description if self.dataset_description else 'Not specified'}

## Memory Report
{memory}

## Active Generated Hypotheses
"""

        if self.hypotheses:
            for hyp in self.hypotheses:
                context_setup += f"\n### Generated hypothesis\n{hyp}\n"
            context_setup += (
                "\nUse these active hypotheses when answering follow-up questions. "
                "If the scientist asks to generate again, do not silently mutate state in chat; "
                "explain that they can use the explicit Generate action or ask what should change."
            )
        else:
            context_setup += "\nNo hypotheses are currently generated for this session."

        return context_setup

    def _focused_hypothesis_title(self, hypothesis_text: str) -> str:
        """Return the current card title without exposing its mutable slot index."""
        try:
            sections = self.parse_hypothesis_sections(hypothesis_text or '')
        except Exception:
            sections = {}
        title = sections.get('title') if isinstance(sections, dict) else None
        statement = sections.get('hypothesis') if isinstance(sections, dict) else None
        return display_card_title(title, statement or hypothesis_text)

    @staticmethod
    def _sanitize_refinement_baseline_labels(value: str) -> str:
        """Remove legacy numbered headings from persisted refinement baselines."""
        return re.sub(
            r'(?m)^###\s+Hypothesis\s+\d+\s*$',
            '### Generated hypothesis',
            str(value or ''),
        )

    def _hypothesis_chat_context_fingerprint_for_current_state(self) -> str:
        payload = {
            'task_description': self.task_description or '',
            'dataset_description': self.dataset_description or '',
            'memory_report': self.memory_report or '',
            'hypotheses': list(self.hypotheses or []),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

    def _hypothesis_chat_has_context_seed(self) -> bool:
        marker = "## Active Generated Hypotheses"
        for msg in getattr(self.hypothesis_gen_agent, 'messages', []) or []:
            content = msg.get('content') if isinstance(msg, dict) else None
            if isinstance(content, str) and marker in content:
                return True
            if isinstance(content, list):
                for block in content:
                    text = block.get('text') if isinstance(block, dict) else None
                    if isinstance(text, str) and marker in text:
                        return True
        return False

    def ensure_hypothesis_chat_context(self, reason: str = "pre_chat") -> None:
        """Ensure post-generation chat has project grounding after refresh.

        Does not mutate hypotheses. It only seeds HypothesisGenAgent.messages
        when missing or stale relative to the active hypotheses/project context.
        """
        if not self.hypothesis_gen_agent:
            return
        # A per-hypothesis thread is active: its baseline seed was set at thread
        # creation and must never be reset/reseeded mid-thread.
        if getattr(self, '_active_thread_key', None) is not None:
            return
        expected = self._hypothesis_chat_context_fingerprint_for_current_state()
        if self._hypothesis_chat_has_context_seed() and self._hypothesis_chat_context_fingerprint == expected:
            return
        self.reset_hypothesis_chat_context(reason=reason)

    def reset_hypothesis_chat_context(self, reason: str = "manual_reset") -> None:
        """
        Re-seed the hypothesis chat with full baseline context after a reset.
        """
        if not self.hypothesis_gen_agent:
            return

        self.hypothesis_gen_agent.reset_conversation()

        lit_context = getattr(self, 'hypothesis_literature_context', None)

        context_setup = self._build_hypothesis_chat_context()

        if lit_context:
            context_setup += (
                "<literature_context>\n"
                + lit_context
                + "\n</literature_context>\n\n"
                "Use the literature context above only for novelty checking, "
                "contradiction checking, and grounding. Do not treat it as primary "
                "evidence. Responses and hypothesis suggestions must still be "
                "testable with the available dataset.\n\n"
            )

        if getattr(self, "audit_only_mode", False):
            research_paper_text = self._load_research_paper_text()
            if research_paper_text:
                context_setup += f"""## Research Paper
{research_paper_text}

"""

        self.hypothesis_gen_agent.add_user_message(context_setup)
        self.hypothesis_gen_agent.add_assistant_message(
            "Understood. I will answer follow-up questions using the Research Objective, "
            "Project Context, Memory Report, and active generated hypotheses already provided."
        )
        self._hypothesis_chat_context_fingerprint = self._hypothesis_chat_context_fingerprint_for_current_state()
        self.log_interaction('hypothesis_chat_context_reset', {
            'reason': reason,
            'hypothesis_count': len(self.hypotheses),
        })

    # =========================================================================
    # V2 per-hypothesis refinement threads
    # -------------------------------------------------------------------------
    # Each hypothesis gets its own independent conversation with the SAME
    # HypothesisGenAgent (same prompts, same API paths). A thread's first
    # message is the exact V1 post-generation baseline seed (Dataset
    # Description + Task Description + combined Background Report + optional
    # literature context + the generated set), reproduced verbatim from the V1
    # reset_hypothesis_chat_context. The thread then accumulates only that
    # hypothesis's own ask/edit exchanges. Threads are swapped in and out of
    # hypothesis_gen_agent.messages around the existing V1 ask/edit/regenerate
    # methods, so those methods stay byte-identical in behavior.
    # =========================================================================

    def _build_v1_refinement_baseline(self) -> str:
        """The exact V1 post-generation chat seed (verbatim V1 structure)."""
        combined_background = self._build_combined_background_for_generation()
        lit_context = getattr(self, 'hypothesis_literature_context', None)

        context_setup = f"""I have generated {len(self.hypotheses)} candidate hypotheses for this research task.

## Dataset Description
{self.dataset_description}

## Task Description
{self.task_description if self.task_description else 'Not specified'}

## Background Report
{combined_background}

"""

        if lit_context:
            context_setup += (
                "<literature_context>\n"
                + lit_context
                + "\n</literature_context>\n\n"
                "Use the literature context above only for novelty checking, "
                "contradiction checking, and grounding. Do not treat it as primary "
                "evidence. Responses and hypothesis suggestions must still be "
                "testable with the available dataset.\n\n"
            )

        if getattr(self, "audit_only_mode", False):
            research_paper_text = self._load_research_paper_text()
            if research_paper_text:
                context_setup += f"""## Research Paper
{research_paper_text}

"""

        if self.hypotheses:
            context_setup += """## Generated Hypotheses

"""
            for hyp in self.hypotheses:
                context_setup += f"### Generated hypothesis\n{hyp}\n\n"
            context_setup += (
                "I am now ready to answer questions about these hypotheses, the background research, "
                "or help you refine any of them. You can ask me to explain my reasoning, compare "
                "hypotheses, or suggest modifications."
            )
        else:
            context_setup += (
                "## Generated Hypotheses\n\n"
                "No hypotheses are currently generated for this session.\n\n"
                "I can still answer questions about the dataset and report context."
            )

        return context_setup

    def capture_refinement_baseline(self) -> str:
        """Snapshot the V1 post-generation baseline for the CURRENT set. Called
        once when a set is generated, so every thread — including one first
        opened much later — clones the same generation-time grounding rather
        than a rebuild from since-mutated hypotheses."""
        self._refinement_baseline_snapshot = self._build_v1_refinement_baseline()
        return self._refinement_baseline_snapshot

    def _refinement_baseline(self) -> str:
        """The generation-time baseline snapshot, or a best-effort rebuild when
        no snapshot exists (e.g. legacy data restored without one)."""
        snapshot = getattr(self, '_refinement_baseline_snapshot', None)
        if snapshot:
            return self._sanitize_refinement_baseline_labels(snapshot)
        return self._sanitize_refinement_baseline_labels(self._build_v1_refinement_baseline())

    def ensure_hypothesis_thread(self, thread_key: str, restored_messages: Optional[List[dict]] = None) -> List[dict]:
        """Return the refinement thread for a stable thread id, creating it from
        the generation-time V1 baseline (plus optional durable-history messages)
        when missing."""
        thread = self.hypothesis_chat_threads.get(thread_key)
        if thread is not None:
            return thread
        thread = []
        if self.hypothesis_gen_agent and getattr(self.hypothesis_gen_agent.llm_config, 'api_provider', None) != 'anthropic':
            thread.append({"role": "system", "content": self.hypothesis_gen_agent.system_prompt})
        thread.append({"role": "assistant", "content": self._refinement_baseline()})
        for msg in restored_messages or []:
            role = msg.get('role')
            content = msg.get('content')
            if role in ('user', 'assistant') and isinstance(content, str) and content.strip():
                thread.append({"role": role, "content": content})
        self.hypothesis_chat_threads[thread_key] = thread
        self.log_interaction('hypothesis_thread_created', {
            'thread_key': thread_key,
            'restored_messages': len(restored_messages or []),
        })
        return thread

    def _get_thread_swap_lock(self) -> "threading.RLock":
        """Return the per-generator swap lock, creating it if a generator was
        built via __new__ (e.g. a test double) without running __init__."""
        lock = getattr(self, '_thread_swap_lock', None)
        if lock is None:
            lock = threading.RLock()
            self._thread_swap_lock = lock
        return lock

    @contextmanager
    def use_hypothesis_thread(self, thread_key: str, restored_messages: Optional[List[dict]] = None):
        """Swap one hypothesis's thread (by stable thread id) into
        hypothesis_gen_agent.messages for the duration of one existing V1
        ask/edit/regenerate call, then save it back.

        The ENTIRE save/install/call/persist/restore sequence runs under this
        generator's own _thread_swap_lock, so overlapping requests can never
        interleave message state on the one shared agent — this holds even if a
        caller does not also hold the per-session operation lock (which remains
        the outer serialization for whole-request work).

        While a thread is active, ensure_hypothesis_chat_context is a no-op so
        the shared-context reseeding cannot wipe or contaminate the thread.
        """
        if not self.hypothesis_gen_agent:
            yield None
            return
        with self._get_thread_swap_lock():
            thread = self.ensure_hypothesis_thread(thread_key, restored_messages=restored_messages)
            saved_messages = self.hypothesis_gen_agent.messages
            saved_active = self._active_thread_key
            self.hypothesis_gen_agent.messages = thread
            self._active_thread_key = thread_key
            try:
                yield thread
            finally:
                # The V1 methods append in place, but guard against reassignment.
                self.hypothesis_chat_threads[thread_key] = self.hypothesis_gen_agent.messages
                self.hypothesis_gen_agent.messages = saved_messages
                self._active_thread_key = saved_active

    def clear_hypothesis_thread(self, thread_key: str) -> None:
        """Drop one hypothesis's refinement thread by stable thread id."""
        if self.hypothesis_chat_threads.pop(thread_key, None) is not None:
            self.log_interaction('hypothesis_thread_cleared', {'thread_key': thread_key})

    def clear_all_hypothesis_threads(self) -> None:
        if self.hypothesis_chat_threads:
            self.hypothesis_chat_threads = {}
            self.log_interaction('hypothesis_threads_cleared', {})

    def _build_citation_documents(self) -> List[dict]:
        """
        Construct Anthropic document blocks with citations enabled for the sources
        used to generate hypotheses and answer questions.
        """
        if not self.enable_citations:
            return []

        documents: List[dict] = []

        def add_text_document(title: str, data: Optional[str], context: Optional[str] = None):
            if not data:
                return
            doc = {
                "type": "document",
                "source": {
                    "type": "text",
                    "media_type": "text/plain",
                    "data": data,
                },
                "title": title,
                "citations": {"enabled": True},
            }
            if context:
                doc["context"] = context
            if self.cache_documents:
                doc["cache_control"] = {"type": "ephemeral"}
            documents.append(doc)

        # Core text sources
        add_text_document("Dataset Description", self.dataset_description)
        add_text_document("Task Description", self.task_description)
        round_report_source = self.round_report_summary or self.round_report
        add_text_document("Round Report", round_report_source)
        add_text_document("Memory Report", self.memory_report)
        add_text_document("Background Literature", self.deep_research_background)

        # Research paper (if available)
        if self.research_paper_path and self.research_paper_path.exists():
            try:
                add_text_document("Research Paper", self.research_paper_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Background PDFs that were already prepared as document blocks
        if self.background_pdf_blocks:
            for block in self.background_pdf_blocks:
                if isinstance(block, dict) and block.get("type") == "document":
                    block = block.copy()
                    block.setdefault("citations", {"enabled": True})
                    if self.cache_documents:
                        block.setdefault("cache_control", {"type": "ephemeral"})
                    documents.append(block)
                elif isinstance(block, dict):
                    documents.append(block)

        self.citation_documents = documents
        if self.hypothesis_gen_agent:
            self.hypothesis_gen_agent.citation_documents = documents
        return documents

    def _conversation_has_citation_documents(self) -> bool:
        """Check whether citation documents are already present in the conversation."""
        if not self.hypothesis_gen_agent:
            return False
        for message in self.hypothesis_gen_agent.messages:
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "document":
                        return True
        return False

    def _candidate_research_paper_roots(self) -> List[Path]:
        """Return roots that may contain research_paper directories."""
        if not self.checkpoint_dir:
            return []
        roots = []
        try:
            base = self.checkpoint_dir
            roots.append(base)
            if base.name.startswith("round_"):
                roots.append(base.parent)
                roots.append(base.parent.parent)
            else:
                roots.append(base.parent)
        except Exception:
            roots = [self.checkpoint_dir]

        seen_roots = set()
        unique_roots = []
        for root in roots:
            if not root:
                continue
            try:
                root_key = str(root.resolve())
            except Exception:
                root_key = str(root)
            if root_key in seen_roots:
                continue
            seen_roots.add(root_key)
            unique_roots.append(root)
        return unique_roots

    def _resolve_configured_research_paper(self) -> Optional[Path]:
        """Return the configured research_paper.md path, if present on disk."""
        raw_path = (self.configured_research_paper_path or "").strip().strip("/")
        if not raw_path:
            return None

        requested = Path(raw_path).expanduser()
        candidates = [requested] if requested.is_absolute() else [
            root / requested for root in self._candidate_research_paper_roots()
        ]
        for candidate in candidates:
            try:
                candidate = candidate.resolve()
            except Exception:
                pass
            if candidate.is_file() and candidate.suffix.lower() != ".pdf":
                return candidate
            if candidate.is_file() and candidate.suffix.lower() == ".pdf":
                paper_file = candidate.with_name("research_paper.md")
                if paper_file.exists():
                    return paper_file
            paper_file = candidate / "research_paper.md"
            if paper_file.exists():
                return paper_file
        return None

    def _find_latest_research_paper(self) -> Optional[Path]:
        """Return path to latest research_paper.md inside research_paper_* directory."""
        if not self.checkpoint_dir:
            return None

        pattern = re.compile(r"^research_paper_(\d{8})_(\d{6})$")
        latest_stamp = None
        latest_path = None
        try:
            for root in self._candidate_research_paper_roots():
                if not root or not root.exists() or not root.is_dir():
                    continue
                for entry in root.iterdir():
                    if not entry.is_dir():
                        continue
                    match = pattern.match(entry.name)
                    if not match:
                        continue
                    stamp = f"{match.group(1)}_{match.group(2)}"
                    candidate = entry / "research_paper.md"
                    if not candidate.exists():
                        continue
                    if latest_stamp is None or stamp > latest_stamp:
                        latest_stamp = stamp
                        latest_path = candidate
        except Exception as exc:
            print(f"⚠️ Warning: Could not scan for research papers: {exc}")
            return None

        if latest_path is not None:
            try:
                print(f"✓ Found latest research_paper.md ({latest_path.stat().st_size} bytes)")
            except Exception:
                print("✓ Found latest research_paper.md")
        return latest_path

    def _get_citation_documents_for_message(self) -> Optional[List[dict]]:
        """
        Only attach citation documents once at the top of the conversation.
        """
        if not self.enable_citations:
            return None
        if not self.hypothesis_gen_agent or self.hypothesis_gen_agent.llm_config.api_provider != 'anthropic':
            return None
        if not self.citation_documents:
            return None
        if self._conversation_has_citation_documents():
            return None
        return self.citation_documents

    @staticmethod
    def _compact_card_title_text(value: Optional[str]) -> str:
        return compact_card_title_text(value)

    @staticmethod
    def _card_title_word_count(value: Optional[str]) -> int:
        return card_title_word_count(value)

    def _title_needs_card_rewrite(self, title: Optional[str], hypothesis_text: Optional[str] = None) -> bool:
        return title_needs_card_rewrite(title, hypothesis_text)

    def _derive_concise_card_title(self, title: Optional[str], hypothesis_text: Optional[str] = None) -> str:
        return derive_concise_card_title(title, hypothesis_text)

    def _normalize_hypothesis_card_title(self, hypothesis: str) -> str:
        sections = self.parse_hypothesis_sections(hypothesis)
        if not any(sections.values()):
            return hypothesis.strip()

        if self._title_needs_card_rewrite(sections.get('title'), sections.get('hypothesis')):
            sections['title'] = self._derive_concise_card_title(
                sections.get('title'),
                sections.get('hypothesis'),
            )

        parts = []
        if sections.get('title'):
            parts.append(f"**Title**: {sections['title']}")
        if sections.get('primary_factor'):
            parts.append(f"**Primary factor**: {sections['primary_factor']}")
        if sections.get('interpretation'):
            parts.append(f"**Interpretation**: {sections['interpretation']}")
        if sections.get('hypothesis'):
            parts.append(f"**Hypothesis**: {sections['hypothesis']}")
        if sections.get('justification'):
            parts.append(f"**Justification**: {sections['justification']}")
        if sections.get('plan'):
            parts.append(f"**Plan**: {sections['plan']}")

        normalized = "\n\n".join(parts).strip()
        return normalized if normalized else hypothesis.strip()

    def generate_candidates(
        self,
        n: int,
        use_legacy: bool = False,
        guidance: Optional[str] = None,
    ) -> List[str]:
        """
        Generate N candidate hypotheses using the agent.

        This follows the pattern from director_workflow.py:787-864.
        The agent's conversation context (self.messages) is maintained throughout.
        """
        print(f"\n{'='*80}")
        print(f"🧠 Generating {n} candidate hypotheses...")
        print(f"{'='*80}\n")

        # Update agent's n_hypotheses parameter temporarily
        original_n_hypotheses = self.hypothesis_gen_agent.n_hypotheses
        self.hypothesis_gen_agent.n_hypotheses = n

        try:
            combined_background = self._build_combined_background_for_generation()
            if guidance and guidance.strip():
                combined_background = (
                    f"{combined_background}\n\n"
                    "Current Hypothesis-Cycle Guidance (from the scientist, verbatim):\n"
                    f"{guidance}"
                )

            # Inject hypothesis literature context when deep research is enabled.
            # Uses the existing background_literature_content attribute so no AIR
            # method signature change is needed.
            lit_context = self.get_hypothesis_literature_context()
            if lit_context:
                self.hypothesis_gen_agent.background_literature_content = lit_context
                print(
                    f"ℹ️ Injecting hypothesis literature context "
                    f"({len(lit_context)} chars) into generation"
                )

            if use_legacy:
                candidate_hypotheses = self._generate_candidates_legacy(n, combined_background)
            else:
                # Use agent's multi-hypothesis generation with a format override (Title/Hypothesis/Justification[/Plan])
                plan_section = ""
                if getattr(self.hypothesis_config, "generate_plan", False):
                    plan_section = "\n**Plan**: <numbered plan with clear, actionable steps>"

                format_override = f"""
### Hypothesis 1
**Title**: <concise scientific title; preferred 10-14 words, hard maximum 18 words; title-like and specific enough to compare hypotheses, NOT the full hypothesis sentence or every mechanistic detail>
**Hypothesis**: <complete testable scientific claim; this can be longer and detailed>
**Justification**: <2-3 paragraphs grounded in the dataset, background report, and literature>{plan_section}

### Hypothesis 2
**Title**: <concise scientific title; preferred 10-14 words, hard maximum 18 words; title-like and specific enough to compare hypotheses, NOT the full hypothesis sentence or every mechanistic detail>
**Hypothesis**: <complete testable scientific claim; this can be longer and detailed>
**Justification**: <2-3 paragraphs grounded in the dataset, background report, and literature>{plan_section}

(Continue this pattern until you have produced exactly {{n_hypotheses}} hypotheses.

Expected title style — a good **Title** is concise, title-like, specific enough to compare hypotheses, and is NOT a full sentence with every mechanistic detail nor the full hypothesis claim. Good titles: "AlphaONS-Enriched Resilience Signature as a Regeneration-Permissive State"; "Intervention-Rescued Cytoskeletal Module as an mTOR-Dependent Execution Program"; "MAPT/Tau Suppression as a Brake on Microtubule-Based Axon Extension". A title like "W3-type RGCs represent a 'regeneration-primed but survival-deficient' state that can be computationally decomposed to identify the specific survival bottleneck..." is a full claim and belongs in **Hypothesis**, not the card title.)
"""

                self.hypothesis_gen_agent.reset_conversation()
                _ = self.hypothesis_gen_agent._generate_multiple_hypotheses_with_judge(
                    dataset_description=self.dataset_description,
                    task_description=self.task_description,
                    background_report=combined_background,
                    format_prompt_override=format_override,
                    use_judge=False,
                )

                # Parse hypotheses from the latest assistant response
                response = self.hypothesis_gen_agent.messages[-1]["content"] if self.hypothesis_gen_agent.messages else ""
                candidate_hypotheses = self._parse_multiple_hypotheses(response)

                # Fallback: if parsing failed, keep the raw response as a single hypothesis
                if not candidate_hypotheses and response:
                    candidate_hypotheses = [response.strip()]

            # Deduplicate while preserving order
            seen = set()
            deduped = []
            for hyp in candidate_hypotheses:
                key = hyp.strip()
                if key and key not in seen:
                    seen.add(key)
                    deduped.append(hyp)

            if len(deduped) != len(candidate_hypotheses):
                print(f"⚠️ Removed {len(candidate_hypotheses) - len(deduped)} duplicate hypothesis/es from model output")

            # Sanitize to remove any trailing extraneous model text, then enforce
            # card-sized titles before the generated set is persisted.
            sanitized = [self._sanitize_hypothesis_text(h) for h in deduped]
            sanitized = [self._normalize_hypothesis_card_title(h) for h in sanitized]

            self.hypotheses = sanitized
            self.last_generation_citations = (
                self.hypothesis_gen_agent.last_response_citations
                if (self.enable_citations and self.hypothesis_gen_agent.llm_config.api_provider == 'anthropic')
                else []
            )

            if len(self.hypotheses) != n:
                print(f"⚠️ Expected {n} hypotheses but parsed {len(self.hypotheses)}")

            print(f"\n✓ Generated {len(self.hypotheses)} candidate hypotheses")

            # Re-seed chat context so follow-up chat and reset flows start from a consistent baseline.
            self.reset_hypothesis_chat_context(reason='post_generation')

            # A full new set starts fresh: drop the previous set's threads and
            # capture the exact generation-time V1 baseline ONCE, so every thread
            # for this set (whenever first opened) clones the same grounding.
            self.clear_all_hypothesis_threads()
            self.capture_refinement_baseline()

            return candidate_hypotheses

        finally:
            # Restore original n_hypotheses
            self.hypothesis_gen_agent.n_hypotheses = original_n_hypotheses

    def persist_generated_hypotheses(self, *, raise_on_error: bool = False) -> None:
        """
        Durably save the current candidate hypotheses (pre-selection) to the
        checkpoint directory so they can be restored after a session restart.

        Writes <checkpoint_dir>/generated_hypotheses.json and .md using a
        write-temp-then-rename pattern so readers never see a partial file.
        Best-effort: failures are logged but never raised, since this should
        never block a generate/edit/regenerate request from succeeding.
        """
        try:
            if not self.hypotheses:
                return

            state_ids = getattr(self, 'hypothesis_state_ids', None)
            model_name = None
            if self.hypothesis_config and self.hypothesis_config.llm_config:
                model_name = self.hypothesis_config.llm_config.model_name
            if not model_name:
                model_name = self.hypothesis_model_name

            generated_at = datetime.datetime.now().isoformat()

            thread_ids = getattr(self, 'hypothesis_thread_ids', None)
            json_payload = {
                'hypotheses': list(self.hypotheses),
                'hypothesis_state_ids': list(state_ids) if state_ids else None,
                # Stable per-candidate thread ids + the generation-time baseline
                # snapshot, so per-hypothesis refinement threads restore to the
                # correct hypothesis (immune to index shifts) and from the same
                # grounding after a worker restart.
                'hypothesis_thread_ids': list(thread_ids) if thread_ids else None,
                'refinement_baseline': getattr(self, '_refinement_baseline_snapshot', None),
                'generated_at': generated_at,
                'model': model_name,
                'n_candidate_hypotheses': len(self.hypotheses),
            }

            md_parts = [f"# Generated Hypotheses\n\nGenerated at: {generated_at}\n"]
            for i, hyp in enumerate(self.hypotheses, 1):
                md_parts.append(f"\n## Hypothesis {i}\n\n{hyp}\n")
            md_content = "".join(md_parts)

            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            self._atomic_write(self.checkpoint_dir / "generated_hypotheses.json", json.dumps(json_payload, indent=2, ensure_ascii=False))
            self._atomic_write(self.checkpoint_dir / "generated_hypotheses.md", md_content)
            print(f"Saved regenerated hypotheses ({len(self.hypotheses)} hypotheses)")
        except Exception as e:
            print(f"⚠️ Warning: Failed to persist generated hypotheses: {e}")
            if raise_on_error:
                raise

    def clear_persisted_generated_hypotheses(self) -> None:
        """Delete generated_hypotheses.md and .json from the checkpoint directory.

        Called at the start of a full regeneration so stale files cannot be
        restored if the new generation fails or the session is restarted.
        Best-effort: failures are logged but never raised.
        """
        for fname in ("generated_hypotheses.json", "generated_hypotheses.md"):
            try:
                p = self.checkpoint_dir / fname
                if p.exists():
                    p.unlink()
                    print(f"Removed {fname}")
            except Exception as e:
                print(f"⚠️ Warning: Failed to remove {fname}: {e}")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        """Write content to path via a temp file + rename so readers never see a partial write."""
        tmp_path = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp_path, path)

    def parse_hypothesis_sections(self, hypothesis: str) -> Dict[str, Optional[str]]:
        """
        Parse hypothesis text into its sections.

        Returns:
            Dict with keys: 'title', 'interpretation', 'hypothesis', 'justification', 'plan', 'primary_factor'
        """
        return parse_hypothesis_sections_text(hypothesis)

    def _sanitize_hypothesis_text(self, hypothesis: str, title_override: Optional[str] = None) -> str:
        """
        Reconstruct a hypothesis from parsed sections to drop any trailing
        extraneous text the model may have appended after the structured output.

        title_override, when not None, replaces the parsed title unconditionally.
        Used by manual-edit save, which must preserve a version's existing title
        rather than deriving one from the edited body.
        """
        sections = parse_hypothesis_sections_text(
            hypothesis,
            fallback_to_full_text=False,
        )
        parsed_anything = any(sections.values())
        if title_override is not None:
            sections['title'] = title_override
        # If the body had no structured sections at all, fall back to the
        # stripped original (there is nothing to attach title_override to).
        if not parsed_anything:
            return hypothesis.strip()

        parts = []
        if sections.get('title'):
            parts.append(f"**Title**: {sections['title']}")
        if sections.get('primary_factor'):
            parts.append(f"**Primary factor**: {sections['primary_factor']}")
        if sections.get('interpretation'):
            parts.append(f"**Interpretation**: {sections['interpretation']}")
        if sections.get('hypothesis'):
            parts.append(f"**Hypothesis**: {sections['hypothesis']}")
        if sections.get('justification'):
            parts.append(f"**Justification**: {sections['justification']}")
        if sections.get('plan'):
            # Plan is intentionally placed after justification
            parts.append(f"**Plan**: {sections['plan']}")

        sanitized = "\n\n".join(parts).strip()
        return sanitized if sanitized else hypothesis.strip()

    def display_hypothesis(self, idx: int, hypothesis: str, mode: str = "brief"):
        """
        Display a single hypothesis.

        Args:
            idx: Hypothesis number (1-indexed)
            hypothesis: Hypothesis text
            mode: Display mode - "brief", "expanded", or "full"
        """
        print(f"\n{'='*80}")
        print(f"HYPOTHESIS {idx}")
        print(f"{'='*80}\n")

        sections = self.parse_hypothesis_sections(hypothesis)

        if mode == "brief":
            # Show only the hypothesis content
            if sections['hypothesis']:
                print("**Hypothesis:**")
                print(sections['hypothesis'])
                print(f"\n[Enter 'view {idx}' to see justification]")
            else:
                # Fallback if parsing fails
                preview = hypothesis[:500] + "..." if len(hypothesis) > 500 else hypothesis
                print(preview)
                print(f"\n[Enter 'view {idx}' to see full text]")

        elif mode == "expanded":
            # Show hypothesis + justification
            if sections['hypothesis']:
                print("**Hypothesis:**")
                print(sections['hypothesis'])
                print()

            if sections['justification']:
                print("**Justification:**")
                print(sections['justification'])

            if not sections['hypothesis'] and not sections['justification']:
                # Fallback if parsing fails
                print(hypothesis)

        else:  # mode == "full"
            # Show everything
            print(hypothesis)

    def display_all_hypotheses(self, mode: str = "brief"):
        """Display all candidate hypotheses."""
        print("\n" + "="*80)
        print(f"CANDIDATE HYPOTHESES ({len(self.hypotheses)} total)")
        print("="*80)

        for i, hyp in enumerate(self.hypotheses, 1):
            self.display_hypothesis(i, hyp, mode=mode)

        print("\n" + "="*80)

    def display_background_report(self, full: bool = False):
        """Display the combined background report."""
        combined = self._build_combined_background()

        if not combined or combined == "No prior exploration data available.":
            print("\n⚠️ No background report available")
            return

        print("\n" + "="*80)
        print("BACKGROUND REPORT")
        print("="*80)

        if full:
            print(f"\n{combined}")
        else:
            preview_length = 1000
            if len(combined) > preview_length:
                preview = combined[:preview_length] + "..."
                print(f"\n{preview}")
                print(f"\n[Report truncated - showing first {preview_length} characters]")
                print(f"Type 'context-full' to see the complete report")
            else:
                print(f"\n{combined}")

        print("\n" + "="*80)

    def display_round_report(self, full: bool = False):
        """
        Display the current round's report (most recent findings).

        Args:
            full: If True, show full report. If False, show preview.
        """
        if not self.round_report:
            print("\n⚠️ No round report available")
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
                print(f"Type 'latest_context-full' to see the complete report")
            else:
                print(f"\n{self.round_report}")

        print("\n" + "="*80)

    def ask_agent_question(self, question: str, hypothesis_index: Optional[int] = None,
                           version_reference_context: Optional[str] = None) -> str:
        """
        Ask the agent a question. Conversation context is maintained via agent's self.messages.

        CRITICAL: This continues the existing conversation - NO reset.
        """

        if not self.hypothesis_gen_agent:
            return "The LLM chat agent is not available yet. Please generate hypotheses first before asking questions."

        self.ensure_hypothesis_chat_context(reason='pre_chat')

        # Log interaction
        self.log_interaction('question', {
            'question': question,
            'hypothesis_index': hypothesis_index
        })

        wrapped_question = question
        if hypothesis_index is not None:
            try:
                idx = int(hypothesis_index)
            except (TypeError, ValueError):
                idx = None

            if idx and 1 <= idx <= len(self.hypotheses):
                hypothesis_text = self.hypotheses[idx - 1]
                hypothesis_title = self._focused_hypothesis_title(hypothesis_text)
                version_block = (
                    f"\nStored version context requested by the user:\n{version_reference_context}\n"
                    if version_reference_context else ''
                )
                wrapped_question = f"""The user is asking about this hypothesis, titled “{hypothesis_title}”.

This hypothesis:
{hypothesis_text}

{HYPOTHESIS_STUDIO_REFERENCE_NOTE}
{version_block}

User question:
{question}
"""

        # Add user question to conversation (attach citation documents only once)
        documents = self._get_citation_documents_for_message()
        if documents:
            self.hypothesis_gen_agent.add_user_message_with_documents(wrapped_question, documents=documents)
        else:
            self.hypothesis_gen_agent.add_user_message(wrapped_question)

        try:
            # Get response using agent's API call
            api_params = {
                "model": self.hypothesis_gen_agent.llm_config.model_name,
                "max_tokens": self.hypothesis_config.max_tokens,
                "system": self.hypothesis_gen_agent.system_prompt,
                "messages": self.hypothesis_gen_agent.messages,  # Uses maintained conversation
            }

            track_citations = (
                self.enable_citations
                and self.hypothesis_gen_agent.llm_config.api_provider == 'anthropic'
            )
            documents_for_tracking = self.citation_documents if track_citations else None
            response = self.hypothesis_gen_agent._call_api_with_retry(
                api_params,
                track_citations=track_citations,
                documents=documents_for_tracking,
            )

            # Add response to conversation
            self.hypothesis_gen_agent.add_assistant_message(response)

            # Track for display history (not for context - that's in agent.messages)
            self.qa_history.append({
                'Q': question,
                'A': response,
                'hypothesis_index': hypothesis_index
            })
            self.last_answer_citations = self.hypothesis_gen_agent.last_response_citations if track_citations else []

            # Log response
            self.log_interaction('question_response', {'response': response[:500] + '...' if len(response) > 500 else response})

            return response

        except Exception as e:
            error_msg = f"Error in Q&A: {str(e)}"
            print(f"❌ {error_msg}")
            self.log_interaction('question_error', {'error': str(e)})
            return None

    def _build_report_chat_system_prompt(self, report_label: str) -> str:
        """Create a focused system prompt for report-only chat."""
        return (
            "You are an analyst helping a researcher understand a report. "
            f"Answer questions using ONLY the provided {report_label} content. "
            "If the answer is not in the report, say you do not know based on the report. "
            "Do not speculate or invent details."
        )

    def _get_report_chat_agent(self, report_key: str, report_label: str, report_text: str) -> HypothesisGenAgent:
        """Return a cached report chat agent, resetting if the report content has changed."""
        if not report_text:
            return None

        report_hash = hashlib.sha256(report_text.encode('utf-8')).hexdigest()
        existing_hash = self.report_chat_context_hashes.get(report_key)
        agent = self.report_chat_agents.get(report_key)

        if agent and existing_hash == report_hash:
            return agent

        report_system_prompt = self._build_report_chat_system_prompt(report_label)
        report_config = HypothesisGenerationConfig(
            llm_config=self.hypothesis_config.llm_config,
            log_dir=self.hypothesis_config.log_dir,
            max_tokens=self.hypothesis_config.max_tokens,
            system_prompt=report_system_prompt,
            human_selection_mode=False,
            n_candidate_hypotheses=1,
            selected_hypothesis_file=None,
            no_auto_hypothesis=True,
            generate_plan=False,
            enable_citations=False,
            cache_documents=self.cache_documents,
        )

        agent = HypothesisGenAgent(
            config=report_config,
            background_pdf_blocks=None,
            background_report_path=None,
            logger=self.logger,
            n_hypotheses=1,
            judge_llm_config=None,
            helper_agents_prompt=None,
            citation_documents=[],
        )

        agent.reset_conversation()
        context_setup = (
            f"I will answer questions about the report below.\n\n"
            f"## {report_label}\n"
            f"{report_text}\n\n"
            "I am ready to answer questions about this report. "
            "If the report does not contain the answer, I will say so."
        )
        agent.add_assistant_message(context_setup)

        self.report_chat_agents[report_key] = agent
        self.report_chat_context_hashes[report_key] = report_hash
        self.report_qa_history[report_key] = []
        return agent

    def ask_code_question(self, question: str) -> str:
        """
        Ask Claude Code about the codebase (read-only). Requires self.code_dir.
        """
        if not self.code_dir:
            return "Code chat is unavailable because no code_dir was provided."

        self.log_interaction('code_chat', {'question': question})

        print("\n🤖 Claude Code (read-only) is thinking...\n")
        code_chat_model = (
            self.config.get('code_chat_model')
            or 'claude-sonnet-4-6'
        )
        answer = run_code_chat(
            question=question,
            code_dir=self.code_dir,
            model=code_chat_model
        )

        self.log_interaction('code_chat_answer', {'answer': answer})
        return answer

    def interactive_qa_session(self):
        """Enter conversational Q&A mode. Maintains conversation context throughout."""
        print("\n" + "="*80)
        print("Q&A MODE")
        print("="*80)
        print("\nYou can now ask questions about the hypotheses, dataset, or task.")
        print("The agent has full context of everything discussed so far.")
        print("="*80 + "\n")

        while True:
            # Show commands on every iteration
            print("-"*80)
            print("Commands:")
            print("  - Type your question and press Enter")
            print("  - Type 'back', 'done', or leave empty to return to main menu")
            print("-"*80)
            print("\nYour question: ", end='')

            question = input().strip()

            # Exit Q&A mode
            if question.lower() in ['back', 'done', ''] or not question:
                print("\nExiting Q&A mode.\n")
                break

            # Ask question (continues conversation via agent.messages)
            response = self.ask_agent_question(question)

            if response:
                # Display response
                print("\n" + "-"*80)
                print("Agent Response:")
                print("-"*80)
                print(response)
                print("-"*80 + "\n")

    def display_qa_history(self):
        """Display the Q&A conversation history."""
        if not self.qa_history:
            print("\n⚠️ No Q&A history yet")
            return

        print("\n" + "="*80)
        print("Q&A CONVERSATION HISTORY")
        print("="*80)

        for i, exchange in enumerate(self.qa_history, 1):
            print(f"\n[Question {i}]")
            print(exchange['Q'])
            print(f"\n[Answer {i}]")
            print(exchange['A'])
            print("-"*80)

    def regenerate_hypothesis(self, idx: int, feedback: Optional[str] = None) -> Optional[str]:
        """
        Regenerate a specific hypothesis with user feedback.

        Args:
            idx: Hypothesis index (1-based)
            feedback: Optional feedback text. If None, will prompt for input (interactive mode).

        CRITICAL: Continues conversation - NO reset. Uses agent's self.messages.
        """
        # Log the action
        self.log_interaction('regenerate_start', {'hypothesis_index': idx})

        # Get feedback from parameter or prompt
        if feedback is None:
            print("\nWhat would you like to change about this hypothesis?")
            print("Enter your feedback: ", end='')
            feedback = input().strip()

        if not feedback:
            print("\n⚠️ No feedback provided. Regeneration cancelled.")
            self.log_interaction('regenerate_cancelled', {'hypothesis_index': idx})
            return None

        # Log the feedback
        self.log_interaction('regenerate_feedback', {'hypothesis_index': idx, 'feedback': feedback})

        # Continue the conversation - NO RESET
        hypothesis_title = self._focused_hypothesis_title(self.hypotheses[idx - 1])
        regenerate_prompt = f"""Please rewrite this hypothesis, titled “{hypothesis_title}”, integrating the following feedback from a human expert:

{feedback}

{HYPOTHESIS_STUDIO_REFERENCE_NOTE}

Generate a complete revised hypothesis with **Title**:, **Hypothesis**:, and **Justification**: sections. The title should be a concise scientific title (preferred 10-14 words, hard maximum 18 words), not a full hypothesis sentence or justification.
"""

        documents = self._get_citation_documents_for_message()
        if documents:
            self.hypothesis_gen_agent.add_user_message_with_documents(regenerate_prompt, documents=documents)
        else:
            self.hypothesis_gen_agent.add_user_message(regenerate_prompt)

        try:
            api_params = {
                "model": self.hypothesis_gen_agent.llm_config.model_name,
                "max_tokens": self.hypothesis_config.max_tokens,
                "system": self.hypothesis_gen_agent.system_prompt,
                "messages": self.hypothesis_gen_agent.messages,
            }

            track_citations = (
                self.enable_citations
                and self.hypothesis_gen_agent.llm_config.api_provider == 'anthropic'
            )
            documents = self.citation_documents if track_citations else None
            new_hypothesis = self.hypothesis_gen_agent._call_api_with_retry(
                api_params,
                track_citations=track_citations,
                documents=documents,
            )
            self.hypothesis_gen_agent.add_assistant_message(new_hypothesis)

            # Update the list
            self.hypotheses[idx - 1] = new_hypothesis

            print(f"\n✓ This hypothesis was regenerated ({len(new_hypothesis)} characters)")

            # Log success
            self.log_interaction('regenerate_success', {
                'hypothesis_index': idx,
                'new_hypothesis': new_hypothesis[:500] + '...' if len(new_hypothesis) > 500 else new_hypothesis
            })

            return new_hypothesis

        except Exception as e:
            print(f"❌ Error regenerating hypothesis: {str(e)}")
            self.log_interaction('regenerate_error', {'hypothesis_index': idx, 'error': str(e)})
            return None

    def _parse_multiple_hypotheses(self, response: str) -> List[str]:
        """Parse multiple hypotheses from agent response."""
        # Primary pattern: ### Hypothesis N ... (until next heading)
        heading_pattern = r'### Hypothesis \d+\s*\n+(.*?)(?=\n### Hypothesis \d+|\Z)'
        matches = re.findall(heading_pattern, response, re.DOTALL)

        if matches:
            return [hyp.strip() for hyp in matches if hyp.strip()]

        # Fallback: "Hypothesis N:" style headings
        alt_pattern = r'Hypothesis \d+[:\-]\s*\n+(.*?)(?=\nHypothesis \d+[:\-]|\Z)'
        alt_matches = re.findall(alt_pattern, response, re.DOTALL)
        if alt_matches:
            return [hyp.strip() for hyp in alt_matches if hyp.strip()]

        # Final fallback: split on numbered list
        numbered_split = re.split(r'\n\d+\.\s+', response)
        if len(numbered_split) > 1:
            return [chunk.strip() for chunk in numbered_split[1:] if chunk.strip()]

        return []

    # =========================================================================
    # Deep-research / hypothesis literature context helpers
    # =========================================================================

    def _hypothesis_literature_cache_path(self) -> Path:
        return self.checkpoint_dir / "hypothesis_literature_context.md"

    def _hypothesis_literature_audit_path(self) -> Path:
        return self.checkpoint_dir / "hypothesis_literature_context_audit.json"

    def _cached_literature_is_failure(self, content: str) -> bool:
        """
        Decide whether the cached literature context is a recorded failure.
        Prefers the structured is_error flag from the audit JSON written
        alongside the cache; falls back to markdown error-marker sniffing for
        caches written before the audit file existed.
        """
        audit_path = self._hypothesis_literature_audit_path()
        try:
            if audit_path.exists():
                audit = json.loads(audit_path.read_text(encoding='utf-8'))
                if isinstance(audit, dict) and isinstance(audit.get('is_error'), bool):
                    return audit['is_error']
        except Exception:
            pass
        return _is_retrieval_failure_markdown(content)

    def get_hypothesis_literature_context(self) -> Optional[str]:
        """Return cached or freshly retrieved literature context for hypothesis generation.

        Checks the feature flag, returns in-memory cache, then disk cache, and only
        calls the remote provider as a last resort.  Never raises; returns None on
        any failure so callers can proceed with baseline behavior.
        """
        if not getattr(self, 'use_deep_research_for_hypothesis_generation', False):
            return None

        # In-memory cache (set during this session).
        cached = getattr(self, 'hypothesis_literature_context', None)
        if cached is not None:
            return cached

        # Disk cache from a previous session or previous generation in this session.
        cache_path = self._hypothesis_literature_cache_path()
        if cache_path.exists():
            try:
                content = cache_path.read_text(encoding='utf-8').strip()
                if not self._cached_literature_is_failure(content):
                    print(
                        f"✓ Loaded hypothesis literature context from cache "
                        f"({len(content)} chars)"
                    )
                    self.hypothesis_literature_context = content
                    return content
                print("ℹ️ Cached hypothesis literature context is a recorded failure, skipping")
                # Don't retry on a failed cache entry — return None so generation continues.
                return None
            except Exception as exc:
                print(f"⚠️ Warning: Could not read hypothesis literature context cache: {exc}")

        # Live retrieval via configured provider.
        print(
            f"ℹ️ Retrieving hypothesis literature context via "
            f"provider={self.deep_research_provider}..."
        )
        result, is_error = self._call_deep_research_provider()

        # Persist the result (including failures) to avoid repeated live calls.
        if result:
            audit = {
                'provider': self.deep_research_provider,
                'retrieved_at': datetime.datetime.now().isoformat(),
                'result_chars': len(result),
                'is_error': is_error,
            }
            try:
                self._atomic_write(cache_path, result)
                self._atomic_write(
                    self._hypothesis_literature_audit_path(),
                    json.dumps(audit, indent=2, ensure_ascii=False),
                )
            except Exception as exc:
                print(f"⚠️ Warning: Could not save hypothesis literature context: {exc}")

            if not is_error:
                self.hypothesis_literature_context = result
                return result

            print(
                "ℹ️ Deep research returned error/no-results marker, "
                "proceeding without literature context"
            )

        return None

    def _build_literature_query_prompt(self) -> str:
        """Build a domain-general literature survey query from session context."""
        task_ctx = (self.task_description or '').strip()
        dataset_ctx = (self.dataset_description or '').strip()

        # Cap dataset description to avoid over-long queries.
        if len(dataset_ctx) > 800:
            dataset_ctx = dataset_ctx[:800].rsplit('\n', 1)[0] + '\n...'

        domain_parts: List[str] = []
        if task_ctx:
            domain_parts.append(f"Task: {task_ctx}")
        if dataset_ctx:
            domain_parts.append(f"Dataset context:\n{dataset_ctx}")

        domain_context = (
            '\n\n'.join(domain_parts)
            or 'Scientific research task (no further details available).'
        )
        return _HYPO_LIT_QUERY_TEMPLATE.format(domain_context=domain_context.strip())

    def _call_deep_research_provider(self) -> tuple[Optional[str], bool]:
        """
        Retrieve a literature novelty context via the canonical DeepResearchAgent.

        Delegates to discoveryagent/AIR/tool_agents/deep_research.py (imported at
        module level via the air_bootstrap path extension), which handles all
        provider logic (Paperclip SDK, Perplexity REST, error handling).
        Mirrors director_workflow.py:_generate_hypothesis_literature_context().

        Injection note: the returned context is injected via
        HypothesisGenAgent.background_literature_content before
        _generate_multiple_hypotheses_with_judge() is called. The local AIR copy
        (DA_selection_ui/AIR/hyp_agents/hypothesis_gen_agent.py) does not have a
        literature_context= parameter; attribute mutation is the supported path.

        Never raises. Returns (markdown, is_failure): is_failure comes from the
        agent's structured last_result_status when available, falling back to
        error-marker sniffing for older agents; (None, True) when the agent is
        unavailable or raises.
        """
        if DeepResearchAgent is None or DeepResearchConfig is None:
            print("ℹ️ DeepResearchAgent not available; skipping literature retrieval")
            return None, True

        query = self._build_literature_query_prompt()
        searches_dir = self.checkpoint_dir / "searches" / "hypothesis_lit"
        try:
            searches_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            # llm_config is consumed only by the perplexity/openai path; the
            # paperclip path ignores it.  LLMConfig here is the local AIR's
            # class (already imported at module top), whose attributes are
            # compatible with what DeepResearchAgent reads at runtime.
            llm_config = LLMConfig(
                model_name="sonar-deep-research",
                api_provider="perplexity",
            )
            config = DeepResearchConfig(
                llm_config=llm_config,
                log_dir=str(self.checkpoint_dir),
                deep_research_provider=self.deep_research_provider,
            )
            # logger=None → DeepResearchAgent creates Logger("deep_research", ...)
            # using the local AIR's Logger (resolved via AIR.utils.logger).
            agent = DeepResearchAgent(config=config)
            markdown = agent.execute(query, searches_dir=str(searches_dir))
            status = getattr(agent, 'last_result_status', None)
            if status is not None:
                return markdown, status != 'ok'
            # Agent without structured status — fall back to marker sniffing.
            return markdown, _is_retrieval_failure_markdown(markdown)
        except Exception as exc:
            print(f"⚠️ DeepResearchAgent.execute() failed: {exc}")
            return None, True

    def _generate_candidates_legacy(self, n: int, combined_background: str) -> List[str]:
        """
        Legacy path: generate hypotheses one at a time (multiple model calls).
        Conversation is reset between generations to avoid cross-contamination.
        """
        candidates: List[str] = []
        for i in range(n):
            print(f"🧠 (Legacy) Generating hypothesis {i + 1}/{n}...")
            self.hypothesis_gen_agent.reset_conversation()
            hyp_text = self.hypothesis_gen_agent._generate_single_hypothesis(
                dataset_description=self.dataset_description,
                task_description=self.task_description,
                background_report=combined_background,
            )
            if hyp_text:
                candidates.append(hyp_text.strip())
        return candidates

    def regenerate_all_hypotheses(self) -> Optional[List[str]]:
        """
        Regenerate all hypotheses with user feedback.

        CRITICAL: Continues conversation - NO reset. Uses agent's self.messages.
        """
        print(f"\nWhat would you like to change about the hypotheses overall?")
        print("Enter your feedback: ", end='')

        feedback = input().strip()

        if not feedback:
            print("\n⚠️ No feedback provided. Regeneration cancelled.")
            return None

        # Confirm action
        print("\n" + "="*80)
        print(f"This will regenerate all {len(self.hypotheses)} hypotheses.")
        print(f"Continue? (y/n): ", end='')
        confirm = input().strip().lower()

        if confirm not in ['y', 'yes']:
            print("\n↩️ Regeneration cancelled.")
            return None

        # Continue the conversation - NO RESET
        regenerate_all_prompt = f"""Please regenerate all {len(self.hypotheses)} hypotheses integrating the following feedback from a human expert:

{feedback}

Generate {len(self.hypotheses)} complete hypotheses, each with **Title**:, **Hypothesis**:, and **Justification**: sections. Each title should be a concise scientific title (preferred 10-14 words, hard maximum 18 words), not a full hypothesis sentence or justification.
Format them as:

### Hypothesis 1
**Title**: ...
**Hypothesis**: ...
**Justification**: ...

### Hypothesis 2
**Title**: ...
**Hypothesis**: ...
**Justification**: ...

(etc.)
"""

        documents = self._get_citation_documents_for_message()
        if documents:
            self.hypothesis_gen_agent.add_user_message_with_documents(regenerate_all_prompt, documents=documents)
        else:
            self.hypothesis_gen_agent.add_user_message(regenerate_all_prompt)

        try:
            api_params = {
                "model": self.hypothesis_gen_agent.llm_config.model_name,
                "max_tokens": self.hypothesis_config.max_tokens,
                "system": self.hypothesis_gen_agent.system_prompt,
                "messages": self.hypothesis_gen_agent.messages,
            }

            track_citations = (
                self.enable_citations
                and self.hypothesis_gen_agent.llm_config.api_provider == 'anthropic'
            )
            documents = self.citation_documents if track_citations else None
            response = self.hypothesis_gen_agent._call_api_with_retry(
                api_params,
                track_citations=track_citations,
                documents=documents,
            )
            self.hypothesis_gen_agent.add_assistant_message(response)

            # Parse the response to extract individual hypotheses
            new_hypotheses = self._parse_multiple_hypotheses(response)

            if len(new_hypotheses) == len(self.hypotheses):
                self.hypotheses = new_hypotheses
                print(f"\n✓ All {len(new_hypotheses)} hypotheses regenerated")
                return new_hypotheses
            else:
                print(f"\n⚠️ Expected {len(self.hypotheses)} hypotheses but got {len(new_hypotheses)}")
                print("Keeping original hypotheses. Please try again.")
                return None

        except Exception as e:
            print(f"❌ Error regenerating hypotheses: {str(e)}")
            return None

    def edit_hypothesis(self, idx: int, feedback: Optional[str] = None) -> Optional[str]:
        """
        Edit a specific hypothesis with user feedback.

        Args:
            idx: Hypothesis index (1-based)
            feedback: Optional feedback text. If None, will prompt for input (interactive mode).

        CRITICAL: Continues conversation - NO reset. Uses agent's self.messages.
        """
        # Log the action
        self.log_interaction('edit_start', {'hypothesis_index': idx})

        # Get feedback from parameter or prompt
        if feedback is None:
            print("\nWhat would you like to change about this hypothesis?")
            print("Enter your feedback: ", end='')
            feedback = input().strip()

        if not feedback:
            print("\n⚠️ No feedback provided. Edit cancelled.")
            self.log_interaction('edit_cancelled', {'hypothesis_index': idx})
            return None

        # Log the feedback
        self.log_interaction('edit_feedback', {'hypothesis_index': idx, 'feedback': feedback})

        # Continue the conversation - NO RESET
        hypothesis_title = self._focused_hypothesis_title(self.hypotheses[idx - 1])
        edit_prompt = f"""Please rewrite this hypothesis, titled “{hypothesis_title}”, integrating the following feedback from a human expert:

{feedback}

{HYPOTHESIS_STUDIO_REFERENCE_NOTE}

Maintain the structure with **Title**:, **Hypothesis**:, and **Justification**: sections. Keep the title as a concise scientific title (preferred 10-14 words, hard maximum 18 words), not a full hypothesis sentence or justification.
Provide the complete revised hypothesis below:
"""

        documents = self._get_citation_documents_for_message()
        if documents:
            self.hypothesis_gen_agent.add_user_message_with_documents(edit_prompt, documents=documents)
        else:
            self.hypothesis_gen_agent.add_user_message(edit_prompt)

        try:
            api_params = {
                "model": self.hypothesis_gen_agent.llm_config.model_name,
                "max_tokens": self.hypothesis_config.max_tokens,
                "system": self.hypothesis_gen_agent.system_prompt,
                "messages": self.hypothesis_gen_agent.messages,
            }

            track_citations = (
                self.enable_citations
                and self.hypothesis_gen_agent.llm_config.api_provider == 'anthropic'
            )
            documents = self.citation_documents if track_citations else None
            edited_hypothesis = self.hypothesis_gen_agent._call_api_with_retry(
                api_params,
                track_citations=track_citations,
                documents=documents,
            )
            self.hypothesis_gen_agent.add_assistant_message(edited_hypothesis)

            # Update the list
            self.hypotheses[idx - 1] = edited_hypothesis

            print(f"\n✓ This hypothesis was edited ({len(edited_hypothesis)} characters)")

            # Log success
            self.log_interaction('edit_success', {
                'hypothesis_index': idx,
                'edited_hypothesis': edited_hypothesis[:500] + '...' if len(edited_hypothesis) > 500 else edited_hypothesis
            })

            return edited_hypothesis

        except Exception as e:
            print(f"❌ Error editing hypothesis: {str(e)}")
            self.log_interaction('edit_error', {'hypothesis_index': idx, 'error': str(e)})
            return None

    def get_custom_hypothesis(self) -> Optional[str]:
        """Prompt user to enter a custom hypothesis."""
        print("\n" + "="*80)
        print("ENTER CUSTOM HYPOTHESIS")
        print("="*80)
        print("\nYou've chosen to enter your own hypothesis instead of selecting from")
        print("the proposed candidates.")
        print("\nEnter your custom hypothesis: ", end='')

        custom_hypothesis = input().strip()

        if custom_hypothesis:
            # Confirm
            print("\n" + "="*80)
            print(f"Confirm custom hypothesis? (y/n): ", end='')
            confirm = input().strip().lower()

            if confirm in ['y', 'yes']:
                print(f"\n✓ Custom hypothesis accepted ({len(custom_hypothesis)} characters)")
                return custom_hypothesis
            else:
                print("\n↩️ Custom hypothesis cancelled.")
                return None
        else:
            print(f"\n⚠️ No hypothesis entered")
            return None

    def get_feedback(self) -> Union[None, str, Tuple[str, str]]:
        """
        Prompt user for optional feedback with confirmation.

        Returns:
            None: No feedback provided
            str: Feedback text if confirmed for final selection
            Tuple[str, 'REVISE']: (feedback, 'REVISE') if user wants to apply and continue exploring
        """
        print("\n" + "="*80)
        print("OPTIONAL FEEDBACK")
        print("="*80)
        print("\nWould you like to add feedback or guidance to refine this hypothesis?")
        print("If yes, the agent will revise the hypothesis to incorporate your feedback.")
        print("\nOptions:")
        print("  y - Add feedback (agent will revise hypothesis)")
        print("  n - No feedback, proceed with hypothesis as-is")
        print("\nAdd feedback? (y/n): ", end='')

        add_feedback = input().strip().lower()

        if add_feedback in ['y', 'yes']:
            print("\nEnter your feedback: ", end='')
            feedback = input().strip()

            if feedback:
                print(f"\n✓ Feedback entered ({len(feedback)} characters)")

                # Confirm feedback
                print("\n" + "="*80)
                print("Are you satisfied with this feedback?")
                print("  y - Yes, proceed with this selection and feedback")
                print("  n - No, apply feedback to hypothesis and return to selection menu")
                print("\nYour choice (y/n): ", end='')
                confirm = input().strip().lower()

                if confirm in ['y', 'yes']:
                    return feedback
                else:
                    # User wants to apply feedback and continue exploring
                    return (feedback, 'REVISE')
            else:
                print(f"\n⚠️ No feedback entered")
                return None
        else:
            print(f"\n✓ No feedback added")
            return None

    def apply_scientist_feedback(self, hypothesis: str, feedback: str) -> str:
        """
        Incorporate scientist feedback into hypothesis by revising it once.

        CRITICAL: Continues conversation - NO reset. Uses agent's self.messages.
        """
        revision_prompt = f"""A scientist has selected a hypothesis and provided feedback. Please revise the hypothesis to incorporate this feedback:

Selected Hypothesis:
{hypothesis}

Scientist Feedback:
{feedback}

Please provide a revised hypothesis that:
1. Maintains the **Title**:, **Hypothesis**:, and **Justification**: structure
2. Integrates the scientist's suggestions into the hypothesis and justification
3. Reflects the feedback naturally within the scientific narrative

Provide the complete revised hypothesis:
"""

        documents = self._get_citation_documents_for_message()
        if documents:
            self.hypothesis_gen_agent.add_user_message_with_documents(revision_prompt, documents=documents)
        else:
            self.hypothesis_gen_agent.add_user_message(revision_prompt)

        try:
            api_params = {
                "model": self.hypothesis_gen_agent.llm_config.model_name,
                "max_tokens": self.hypothesis_config.max_tokens,
                "system": self.hypothesis_gen_agent.system_prompt,
                "messages": self.hypothesis_gen_agent.messages,
            }

            track_citations = (
                self.enable_citations
                and self.hypothesis_gen_agent.llm_config.api_provider == 'anthropic'
            )
            documents_for_tracking = self.citation_documents if track_citations else None
            revised_hypothesis = self.hypothesis_gen_agent._call_api_with_retry(
                api_params,
                track_citations=track_citations,
                documents=documents_for_tracking,
            )
            self.hypothesis_gen_agent.add_assistant_message(revised_hypothesis)

            return revised_hypothesis

        except Exception as e:
            print(f"❌ Error applying feedback: {str(e)}")
            return hypothesis  # Return original if error

    def refine_hypothesis_with_feedback(
        self,
        hypothesis: str,
        feedback: str,
        idx: Optional[int] = None
    ) -> str:
        """
        Enter an interactive loop to refine a hypothesis with iterative feedback.

        Shows the updated hypothesis after each revision and asks the user to
        either accept it or provide additional feedback for further refinement.
        """
        current_hypothesis = hypothesis
        current_feedback = feedback
        revision_round = 1

        while True:
            revised_hypothesis = self.apply_scientist_feedback(current_hypothesis, current_feedback)
            current_hypothesis = revised_hypothesis

            # Display updated hypothesis
            print("\n" + "="*80)
            header = f"UPDATED HYPOTHESIS {idx} (Revision {revision_round})" if idx else f"UPDATED HYPOTHESIS (Revision {revision_round})"
            print(header)
            print("="*80 + "\n")

            sections = self.parse_hypothesis_sections(revised_hypothesis)
            if sections['hypothesis']:
                print("**Hypothesis:**")
                print(sections['hypothesis'])
                print()
            if sections['justification']:
                print("**Justification:**")
                print(sections['justification'])
            if not sections['hypothesis'] and not sections['justification']:
                print(revised_hypothesis)

            # Ask user whether to accept or refine again
            while True:
                print("\nOptions:")
                print("  y - Accept this hypothesis")
                print("  f - Provide more feedback to refine further")
                print("Your choice (y/f): ", end='')
                decision = input().strip().lower()

                if decision in ['y', 'yes']:
                    return revised_hypothesis
                if decision in ['f', 'feedback', 'refine']:
                    print("\nEnter additional feedback: ", end='')
                    new_feedback = input().strip()
                    if not new_feedback:
                        print("\n⚠️ No new feedback entered. Keeping current revision.")
                        return revised_hypothesis
                    current_feedback = new_feedback
                    revision_round += 1
                    break  # Run another revision with new feedback

                print("\n⚠️ Invalid choice. Please enter 'y' to accept or 'f' to refine.")

    def get_user_selection(self) -> Tuple[Union[int, str], Optional[str]]:
        """
        Main interactive loop for hypothesis selection.

        Returns:
            Tuple[Union[int, str], Optional[str]]: (selected_index_or_custom_hypothesis, feedback)
        """
        while True:
            print("\n" + "="*80)
            print("SELECT YOUR HYPOTHESIS")
            print("="*80)
            print(f"\nAvailable commands:")
            print(f"  <number>              - Select hypothesis (e.g., '1', '2', '3')")
            print(f"  other                 - Enter your own custom hypothesis")
            print(f"  view <number>         - View hypothesis + justification")
            print(f"  view all              - View all hypotheses")
            print(f"  ask                   - Enter Q&A mode (continuous conversation)")
            if self.code_dir:
                print(f"  code_chat             - Ask Claude Code about the codebase (read-only)")
            print(f"  edit <number>         - Edit hypothesis with your feedback")
            print(f"  regenerate <number>   - Regenerate hypothesis with your feedback")
            print(f"  regenerate all        - Regenerate all hypotheses with your feedback")
            print(f"  history               - Show Q&A conversation history")
            print(f"  context               - Show background report (preview)")
            print(f"  context-full          - Show complete background report")
            if self.round_report:
                print(f"  latest_context        - Show most recent round's report (preview)")
                print(f"  latest_context-full   - Show complete most recent round's report")
            print(f"  quit                  - Exit without saving")
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
                    feedback_result = self.get_feedback()

                    # Check if user wants to apply feedback and continue exploring
                    if isinstance(feedback_result, tuple) and feedback_result[1] == 'REVISE':
                        actual_feedback = feedback_result[0]
                        print("\n🔄 Applying feedback to refine your custom hypothesis...")

                        revised_hypothesis = self.refine_hypothesis_with_feedback(
                            custom_hypothesis,
                            actual_feedback
                        )
                        # Add revised custom hypothesis as a new candidate
                        self.hypotheses.append(revised_hypothesis)
                        print(f"\n✓ Your custom hypothesis has been refined and added as Hypothesis {len(self.hypotheses)}")
                        print("Returning to selection menu...")
                        self.log_interaction('custom_feedback_applied_continue', {
                            'feedback': actual_feedback
                        })
                        continue

                    return custom_hypothesis, feedback_result
                else:
                    continue

            # Handle context view
            if choice == 'context':
                self.display_background_report(full=False)
                continue

            if choice == 'context-full':
                self.display_background_report(full=True)
                continue

            # Handle latest_context view (most recent round's report)
            if choice == 'latest_context':
                self.display_round_report(full=False)
                continue

            if choice == 'latest_context-full':
                self.display_round_report(full=True)
                continue

            # Handle Q&A mode
            if choice in ['ask', 'question']:
                self.interactive_qa_session()
                continue

            # Handle history
            if choice == 'history':
                self.display_qa_history()
                continue

            # Handle view commands
            if choice.startswith('view'):
                parts = choice.split()
                if len(parts) == 2:
                    if parts[1] == 'all':
                        self.log_interaction('view_all', {})
                        self.display_all_hypotheses(mode="brief")
                        continue
                    else:
                        try:
                            view_idx = int(parts[1])
                            if 1 <= view_idx <= len(self.hypotheses):
                                self.log_interaction('view', {'hypothesis_index': view_idx})
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

            # Handle edit commands
            if choice.startswith('edit'):
                parts = choice.split()
                if len(parts) == 2:
                    try:
                        edit_idx = int(parts[1])
                        if 1 <= edit_idx <= len(self.hypotheses):
                            result = self.edit_hypothesis(edit_idx)
                            if result:
                                self.display_hypothesis(edit_idx, self.hypotheses[edit_idx-1], mode="expanded")
                            continue
                        else:
                            print(f"❌ Invalid hypothesis number. Choose 1-{len(self.hypotheses)}")
                            continue
                    except ValueError:
                        print(f"❌ Invalid command. Use 'edit <number>'")
                        continue
                print(f"❌ Invalid command. Use 'edit <number>'")
                continue

            # Handle regenerate commands
            if choice.startswith('regenerate'):
                parts = choice.split()
                if len(parts) == 2:
                    if parts[1] == 'all':
                        result = self.regenerate_all_hypotheses()
                        if result:
                            self.display_all_hypotheses(mode="brief")
                        continue
                    else:
                        try:
                            regen_idx = int(parts[1])
                            if 1 <= regen_idx <= len(self.hypotheses):
                                result = self.regenerate_hypothesis(regen_idx)
                                if result:
                                    self.display_hypothesis(regen_idx, self.hypotheses[regen_idx-1], mode="expanded")
                                continue
                            else:
                                print(f"❌ Invalid hypothesis number. Choose 1-{len(self.hypotheses)}")
                                continue
                        except ValueError:
                            print(f"❌ Invalid command. Use 'regenerate <number>' or 'regenerate all'")
                            continue
                print(f"❌ Invalid command. Use 'regenerate <number>' or 'regenerate all'")
                continue

            # Handle code chat (only when code_dir is provided)
            if choice == 'code_chat':
                if not self.code_dir:
                    print("\n⚠️ Code chat is unavailable because no code_dir was provided.")
                    continue

                print("\nEnter your codebase question: ", end='')
                code_question = input().strip()
                if not code_question:
                    print("⚠️ No question entered.")
                    continue

                answer = self.ask_code_question(code_question)
                print(f"\n{answer}\n")
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
                        # Log selection
                        self.log_interaction('hypothesis_selected', {'hypothesis_index': selection})

                        # Get optional feedback
                        feedback_result = self.get_feedback()

                        # Check if user wants to apply feedback and continue exploring
                        if isinstance(feedback_result, tuple) and feedback_result[1] == 'REVISE':
                            actual_feedback = feedback_result[0]
                            # User wants to revise - apply the feedback and return to selection loop
                            print("\n🔄 Applying feedback to refine the hypothesis...")

                            revised_hypothesis = self.refine_hypothesis_with_feedback(
                                self.hypotheses[selection - 1],
                                actual_feedback,
                                idx=selection
                            )
                            # Update the hypothesis in the list
                            self.hypotheses[selection - 1] = revised_hypothesis
                            print(f"\n✓ Hypothesis {selection} has been refined with your feedback")
                            print("Returning to selection menu...")
                            self.log_interaction('feedback_applied_continue', {
                                'hypothesis_index': selection,
                                'feedback': actual_feedback
                            })

                            # Continue the loop (don't return)
                            continue
                        elif feedback_result:
                            self.log_interaction('feedback_provided', {'feedback': feedback_result})

                        return selection, feedback_result
                    else:
                        print("\n↩️ Selection cancelled. Choose again.")
                        self.log_interaction('selection_cancelled', {'hypothesis_index': selection})
                        continue
                else:
                    print(f"❌ Invalid number. Choose 1-{len(self.hypotheses)}")
            except ValueError:
                print(f"❌ Invalid input. Enter a number (1-{len(self.hypotheses)}) or a command")

    def save_agent_guidance(self, guidance: str) -> str:
        """
        Persist scientist guidance for the next round so it can be reused verbatim.
        """
        guidance_path = self.checkpoint_dir / "next_round_instructions.md"
        guidance_path.parent.mkdir(parents=True, exist_ok=True)
        guidance_path.write_text(guidance.strip() + "\n", encoding="utf-8")
        return str(guidance_path)

    def save_selection(
        self,
        selection: Union[int, str],
        feedback: Optional[str],
        agent_guidance: Optional[str] = None,
        username: Optional[str] = None,
    ) -> str:
        """
        Save the selected hypothesis to file in format expected by director_workflow.

        Output format: Only Hypothesis and Justification sections (no Interpretation).
        If feedback provided, incorporate it by revising the hypothesis (not appending).
        """
        # Get hypothesis text based on selection type
        if isinstance(selection, int):
            hypothesis_text = self.hypotheses[selection - 1]
        else:
            # Custom hypothesis
            hypothesis_text = selection

        # If feedback provided, incorporate it into the hypothesis
        if feedback:
            print("\n🔄 Incorporating your feedback into the hypothesis...")
            hypothesis_text = self.refine_hypothesis_with_feedback(
                hypothesis_text,
                feedback,
                idx=selection if isinstance(selection, int) else None
            )
            print("✓ Feedback incorporated")

        # Parse to extract only Hypothesis and Justification sections
        sections = self.parse_hypothesis_sections(hypothesis_text)

        # Build output with these sections
        output_content = ""

        if sections['hypothesis']:
            output_content += f"**Hypothesis**: {sections['hypothesis']}\n\n"

        if sections['justification']:
            output_content += f"**Justification**: {sections['justification']}\n"

        if sections.get('plan'):
            output_content += f"\n**Plan**: {sections['plan']}\n"

        if feedback:
            output_content += "\n**Feedback from expert scientist**: "
            output_content += feedback.strip() + "\n"

        if agent_guidance:
            output_content += "\n**Overall Research Feedback**: "
            output_content += agent_guidance.strip() + "\n"

        # If parsing failed, use the whole text as fallback
        if not output_content.strip():
            output_content = hypothesis_text

        # Determine output path
        if self.output_file:
            output_path = Path(self.output_file)
        else:
            # Save in checkpoint directory by default
            output_path = self.checkpoint_dir / "selected_hypothesis.md"

        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Save to file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output_content)

        safe_username = None
        if isinstance(username, str):
            safe_username = re.sub(r'[^a-zA-Z0-9._-]+', '-', username.strip().lower())
            safe_username = safe_username.strip('-') or None
        if not safe_username:
            safe_username = 'anonymous'
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        timestamped_path = output_path.with_name(
            f"selected_hypothesis_{timestamp}_{safe_username}.md"
        )
        with open(timestamped_path, 'w', encoding='utf-8') as f:
            f.write(output_content)

        guidance_path = None
        if agent_guidance:
            guidance_path = self.save_agent_guidance(agent_guidance)

        # Store final selection info for session log
        self.final_selection = {
            'selection_type': 'index' if isinstance(selection, int) else 'custom',
            'selection_value': selection,
            'feedback_provided': feedback is not None,
            'feedback': feedback,
            'agent_guidance': agent_guidance,
            'agent_guidance_path': guidance_path,
            'final_hypothesis': output_content,
            'output_path': str(output_path),
            'timestamped_output_path': str(timestamped_path),
        }

        return str(output_path)

    def save_selection_raw(
        self,
        output_content: str,
        agent_guidance: Optional[str] = None,
        username: Optional[str] = None,
    ) -> str:
        """
        Save pre-rendered hypothesis content exactly as provided.
        """
        if agent_guidance:
            output_content = (
                output_content.rstrip()
                + "\n\n**Overall Research Feedback**: "
                + agent_guidance.strip()
                + "\n"
            )
        if self.output_file:
            output_path = Path(self.output_file)
        else:
            output_path = self.checkpoint_dir / "selected_hypothesis.md"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output_content)

        safe_username = None
        if isinstance(username, str):
            safe_username = re.sub(r'[^a-zA-Z0-9._-]+', '-', username.strip().lower())
            safe_username = safe_username.strip('-') or None
        if not safe_username:
            safe_username = 'anonymous'
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        timestamped_path = output_path.with_name(
            f"selected_hypothesis_{timestamp}_{safe_username}.md"
        )
        with open(timestamped_path, 'w', encoding='utf-8') as f:
            f.write(output_content)

        guidance_path = None
        if agent_guidance:
            guidance_path = self.save_agent_guidance(agent_guidance)

        self.final_selection = {
            'selection_type': 'raw',
            'selection_value': None,
            'feedback_provided': False,
            'feedback': None,
            'agent_guidance': agent_guidance,
            'agent_guidance_path': guidance_path,
            'final_hypothesis': output_content,
            'output_path': str(output_path),
            'timestamped_output_path': str(timestamped_path),
        }

        return str(output_path)

    def run(self):
        """Run the interactive hypothesis generation and selection process."""
        print("\n" + "="*80)
        print("🧠 INTERACTIVE HYPOTHESIS GENERATION TOOL")
        print("="*80)
        print(f"\nCheckpoint directory: {self.checkpoint_dir}")

        # Validate
        if not self.validate_checkpoint():
            sys.exit(1)

        # Load checkpoint
        if not self.load_checkpoint():
            sys.exit(1)

        # Initialize agent
        self.initialize_agent()

        # Generate hypotheses
        n_candidates = getattr(self.hypothesis_config, 'n_candidate_hypotheses', 3)
        if self.use_legacy_hyp_gen:
            print("⚙️  Using legacy hypothesis generation (one model call per hypothesis).")
        self.generate_candidates(n_candidates, use_legacy=self.use_legacy_hyp_gen)

        if not self.hypotheses:
            print("❌ No hypotheses were generated. Exiting.")
            sys.exit(1)

        # Display all hypotheses (brief mode)
        self.display_all_hypotheses(mode="brief")

        # Get user selection (interactive loop)
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
                print(f"Feedback: Incorporated into hypothesis")
            else:
                print(f"Feedback: None")
            print(f"\nSaved to: {output_path}")
            print("\n" + "="*80 + "\n")

        except Exception as e:
            print(f"\n❌ Error saving selection: {str(e)}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Interactive tool for generating and selecting hypotheses",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate and select from checkpoint
  python scripts/select_hypothesis.py /path/to/checkpoints/round_0

  # Specify custom output location
  python scripts/select_hypothesis.py /path/to/checkpoints/round_0 --output /tmp/my_selection.md

The script will:
1. Load configuration and reports from the checkpoint directory
2. Generate N candidate hypotheses using HypothesisGenAgent
3. Provide an interactive interface for Q&A, editing, and selection
4. Maintain continuous conversation context throughout the session
5. Save the selected hypothesis in a format compatible with director_workflow
        """
    )

    parser.add_argument(
        'checkpoint_dir',
        help='Path to checkpoint directory containing config.json and reports'
    )

    parser.add_argument(
        '--code_dir',
        help='Path to code directory containing data/README.txt (overrides working_directory from checkpoint config)',
        default=None,
    )

    parser.add_argument(
        '--output', '-o',
        help='Output file path for selected hypothesis (default: <checkpoint_dir>/selected_hypothesis.md)',
        default=None
    )

    parser.add_argument(
        "--hypothesis_model",
        help="Model for the hypothesis generation agent",
        default=None,
    )

    parser.add_argument(
        "--legacy_hyp_gen",
        action="store_true",
        help="Use legacy hypothesis generation (one model call per hypothesis) instead of single-shot batch generation",
    )

    parser.add_argument(
        "--num_hypotheses",
        type=int,
        default=None,
        help="Number of hypotheses to generate (overrides checkpoint config)",
    )

    parser.add_argument(
        "--include_background_literature",
        action="store_true",
        help="Include deep_research_background.md as background literature for the hypothesis generator",
    )

    parser.add_argument(
        "--generate_plan",
        action="store_true",
        help="Ask the hypothesis generator to include a plan section for each hypothesis",
    )
    parser.add_argument(
        "--enable_citations",
        action="store_true",
        help="Enable Claude citations by sending background documents as citable sources (Anthropic only)",
    )
    parser.add_argument(
        "--cache_documents",
        action="store_true",
        help="Enable prompt caching for document blocks when citations are on",
    )

    parser.add_argument(
        "--readme-path",
        dest="readme_path",
        type=str,
        default=None,
        help="Explicit path to dataset README (overrides auto-detected data/README.txt)",
    )

    args = parser.parse_args()

    # Run generator
    generator = InteractiveHypothesisGenerator(
        checkpoint_dir=args.checkpoint_dir,
        code_dir=args.code_dir,
        output_file=args.output,
        hypothesis_model=args.hypothesis_model,
        include_background_literature=args.include_background_literature,
        use_legacy_hyp_gen=args.legacy_hyp_gen,
        num_hypotheses=args.num_hypotheses,
        generate_plan=args.generate_plan,
        readme_path=args.readme_path,
        enable_citations=args.enable_citations,
        cache_documents=args.cache_documents,
    )
    generator.run()


if __name__ == "__main__":
    main()
