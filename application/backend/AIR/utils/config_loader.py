import os
from argparse import ArgumentParser
from typing import Any, Dict, Iterable, Optional, Set, Tuple

import yaml

# Legacy YAML key aliases for backward compatibility.
# Maps old/alternative YAML keys to the canonical argparse dest names.
# New arguments do NOT need to be added here - they are auto-discovered from argparse.
LEGACY_YAML_ALIASES: Dict[str, str] = {
    # Legacy key that maps to the new canonical name
    'expanding_data_directory': 'expanding_dataset_dir',
}


AGENT_BEHAVIOR_VERSION_KEY = "agent_behavior_version"
CURRENT_AGENT_BEHAVIOR_VERSION = 2
LEGACY_AGENT_BEHAVIOR_VERSION = 1

# These settings were introduced together in the V2 local-release work.  Their
# absence from a checkpoint therefore means "this run predates the behavior",
# not "adopt whatever the current defaults happen to be".
RESUME_BEHAVIOR_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "legacy": {
        "deep_research_provider": "perplexity",
        "use_deep_research_for_hypothesis_generation": False,
        "enable_deep_research_in_review": False,
    },
    "current": {
        "deep_research_provider": "paperclip",
        "use_deep_research_for_hypothesis_generation": False,
        "enable_deep_research_in_review": False,
    },
}
RESUME_BEHAVIOR_KEYS = tuple(RESUME_BEHAVIOR_DEFAULTS["current"])


def _has_resume_behavior_value(mapping: Dict[str, Any], key: str) -> bool:
    if key not in mapping or mapping[key] is None:
        return False
    if key == "deep_research_provider" and not str(mapping[key]).strip():
        return False
    return True


def merge_checkpoint_behavior_context(
    checkpoint_state: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill missing behavior fields from an adjacent round configuration.

    Early V2 query checkpoints did not persist every behavior-changing field,
    while their round ``config.json`` files did.  This merge is deliberately
    narrow: query-checkpoint values remain authoritative, and only absent
    behavior/version fields are copied from the surrounding round config.
    """
    merged = dict(checkpoint_state or {})
    context = dict(context or {})
    if isinstance(context.get("config"), dict):
        context = context["config"]
    context_flags = context.get("workflow_config_flags")
    if not isinstance(context_flags, dict):
        context_flags = {}

    for key in (AGENT_BEHAVIOR_VERSION_KEY, *RESUME_BEHAVIOR_KEYS):
        if _has_resume_behavior_value(merged, key):
            continue
        if _has_resume_behavior_value(context, key):
            merged[key] = context[key]
        elif _has_resume_behavior_value(context_flags, key):
            merged[key] = context_flags[key]

    return merged


def resolve_checkpoint_behavior(updates: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Resolve versioned agent behavior for a checkpoint/config resume.

    Fresh sessions do not call this function and continue to use the parser and
    dataclass defaults.  A checkpoint is considered current when it explicitly
    declares the current behavior version or contains the complete V2 behavior
    field set.  Older and partially migrated checkpoints retain legacy behavior
    for missing fields.  Explicit persisted values are always preserved.

    Returns a normalized shallow copy and whether the input was classified as a
    legacy behavior profile. Explicit values can still differ from its defaults.
    """
    normalized = dict(updates or {})
    workflow_flags = normalized.get("workflow_config_flags")
    if not isinstance(workflow_flags, dict):
        workflow_flags = {}

    version = normalized.get(AGENT_BEHAVIOR_VERSION_KEY)
    try:
        if isinstance(version, bool):
            raise ValueError
        if isinstance(version, float) and not version.is_integer():
            raise ValueError
        version_number = int(version) if version is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid agent behavior version {version!r}; expected an integer"
        ) from exc
    if version_number is not None and version_number < LEGACY_AGENT_BEHAVIOR_VERSION:
        raise ValueError(
            f"Invalid agent behavior version {version_number}; "
            f"expected {LEGACY_AGENT_BEHAVIOR_VERSION} or greater"
        )
    if (
        version_number is not None
        and version_number > CURRENT_AGENT_BEHAVIOR_VERSION
    ):
        raise ValueError(
            f"Unsupported agent behavior version {version_number}; "
            f"this release supports up to {CURRENT_AGENT_BEHAVIOR_VERSION}"
        )

    # PR #3 query checkpoints written before behavior versioning persisted the
    # provider and reviewer setting, but omitted hypothesis grounding even
    # though it was enabled by default. Preserve the behavior those checkpoints
    # actually ran with instead of misclassifying them as pre-PR legacy state.
    is_pre_versioned_pr_checkpoint = (
        version_number is None
        and _has_resume_behavior_value(normalized, "deep_research_provider")
        and _has_resume_behavior_value(normalized, "enable_deep_research_in_review")
        and not _has_resume_behavior_value(
            normalized, "use_deep_research_for_hypothesis_generation"
        )
    )
    if is_pre_versioned_pr_checkpoint:
        normalized["use_deep_research_for_hypothesis_generation"] = True

    available_keys = {
        key for key in RESUME_BEHAVIOR_KEYS
        if _has_resume_behavior_value(normalized, key)
        or _has_resume_behavior_value(workflow_flags, key)
    }
    is_legacy = (
        version_number is None
        and not is_pre_versioned_pr_checkpoint
        and len(available_keys) < len(RESUME_BEHAVIOR_KEYS)
    ) or (
        version_number is not None
        and version_number < CURRENT_AGENT_BEHAVIOR_VERSION
    )
    defaults = RESUME_BEHAVIOR_DEFAULTS["legacy" if is_legacy else "current"]

    for key, default in defaults.items():
        if _has_resume_behavior_value(normalized, key):
            continue
        if _has_resume_behavior_value(workflow_flags, key):
            normalized[key] = workflow_flags[key]
        else:
            normalized[key] = default

    normalized[AGENT_BEHAVIOR_VERSION_KEY] = (
        LEGACY_AGENT_BEHAVIOR_VERSION
        if is_legacy
        else CURRENT_AGENT_BEHAVIOR_VERSION
    )
    return normalized, is_legacy


def explicit_cli_destinations(
    parser: ArgumentParser,
    argv: Iterable[str],
) -> Set[str]:
    """Return argparse destinations explicitly present in ``argv``.

    Comparing parsed values with defaults misses explicit options whose chosen
    value equals the default (for example ``--deep_research_provider paperclip``
    or ``--enable_deep_research_in_review``).  Resume configuration needs the
    distinction so explicit CLI choices can override legacy checkpoint values.
    """
    explicit: Set[str] = set()
    option_actions = parser._option_string_actions
    for token in argv:
        if token == "--":
            break
        option = token.split("=", 1)[0]
        action = option_actions.get(option)
        if action is not None and action.dest != "help":
            explicit.add(action.dest)
    return explicit


def build_arg_mapping_from_parser(parser: ArgumentParser) -> Dict[str, str]:
    """
    Build a YAML key -> argparse dest mapping from an ArgumentParser.

    This auto-discovers all arguments from the parser, so new arguments
    only need to be added to arg_parsers.py - no manual mapping required.

    The mapping includes:
    1. All argparse dest names (identity mapping)
    2. All option strings (--foo-bar -> foo_bar)
    3. Legacy aliases from LEGACY_YAML_ALIASES

    Args:
        parser: The ArgumentParser to extract arguments from

    Returns:
        Dict mapping YAML keys to argparse dest names
    """
    mapping = {}

    for action in parser._actions:
        if action.dest == 'help':
            continue

        dest = action.dest

        # Add identity mapping for the dest name
        mapping[dest] = dest

        # Add mappings for all option strings (e.g., --foo-bar -> foo_bar)
        for opt in action.option_strings:
            # Strip leading dashes and convert to underscore
            key = opt.lstrip('-').replace('-', '_')
            mapping[key] = dest

    # Add legacy aliases
    mapping.update(LEGACY_YAML_ALIASES)

    return mapping


def load_yaml_config(yaml_path: Optional[str]) -> Dict[str, Any]:
    """Load YAML configuration from the given path. Returns {} on failure."""
    if not yaml_path:
        return {}
    if not os.path.exists(yaml_path):
        print(f"⚠️  YAML config file not found: {yaml_path}")
        return {}
    try:
        with open(yaml_path, 'r') as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            print(f"⚠️  YAML config at {yaml_path} is not a mapping/object; ignoring.")
            return {}
        return data
    except Exception as e:
        print(f"⚠️  Failed to load YAML config from {yaml_path}: {e}")
        return {}


def apply_updates_with_cli_priority(
    args,
    updates: Dict[str, Any],
    cli_override_keys: Set[str],
    parser: Optional[ArgumentParser] = None,
    mapping: Optional[Dict[str, str]] = None,
    source_label: str = "config",
):
    """
    Apply values from a mapping (YAML or checkpoint config) onto argparse args.

    CLI overrides are respected: if a key was provided via CLI, it will not be
    overwritten. Later calls can override earlier non-CLI values (e.g., YAML can
    override checkpoint config).

    Args:
        args: The argparse Namespace to update
        updates: Dict of config values to apply
        cli_override_keys: Set of keys that were explicitly set via CLI (these won't be overwritten)
        parser: Optional ArgumentParser to auto-build the mapping from.
                If provided, mapping is built automatically from parser's arguments.
        mapping: Optional explicit mapping dict. If neither parser nor mapping provided,
                 uses simple key normalization (replace - with _).
        source_label: Label for logging (e.g., "YAML config", "checkpoint")

    Returns:
        Updated args Namespace
    """
    # Build mapping from parser if provided, otherwise use explicit mapping or simple normalization
    if parser is not None:
        effective_mapping = build_arg_mapping_from_parser(parser)
    elif mapping is not None:
        effective_mapping = mapping
    else:
        # Fallback: simple key normalization, no explicit mapping
        effective_mapping = {}

    applied_count = 0
    skipped_count = 0

    for raw_key, value in updates.items():
        # Normalize the key: check mapping first, then just normalize dashes to underscores
        target_key = effective_mapping.get(raw_key, raw_key).replace("-", "_")

        if target_key in cli_override_keys:
            skipped_count += 1
            continue
        if not hasattr(args, target_key):
            skipped_count += 1
            continue
        if value is None:
            skipped_count += 1
            continue

        setattr(args, target_key, value)
        applied_count += 1

    print(f"✓ Applied {applied_count} values from {source_label}")
    if skipped_count > 0:
        print(f"   - Skipped {skipped_count} values (CLI override, missing arg, or None)")

    return args


def snapshot_args_to_yaml(args, output_path: str):
    """Persist resolved argparse Namespace to YAML for future reuse."""
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        # Underscore-prefixed values are process-local coordination state, not
        # reproducible user configuration (for example resume authority flags).
        serializable = {
            key: value
            for key, value in vars(args).items()
            if not key.startswith("_")
        }
        with open(output_path, 'w') as f:
            yaml.safe_dump(serializable, f, sort_keys=True)
        print(f"✓ Saved resolved arguments to YAML: {output_path}")
    except Exception as e:
        print(f"⚠️  Could not write args YAML to {output_path}: {e}")
