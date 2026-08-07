"""
Config serialization helpers for passing configuration to the MCP server subprocess.

Serializes agent config dataclasses to/from JSON, handling nested LLMConfig
objects and stripping API keys (they're read from env vars at runtime).
"""

import json
from dataclasses import asdict, is_dataclass, fields
from typing import Dict, Any


# Keys that contain secrets and should not be serialized
_SECRET_KEYS = {"api_key", "aws_access_key_id", "aws_secret_access_key", "aws_session_token"}


def serialize_agent_config(config) -> dict:
    """Serialize a dataclass config to a JSON-safe dict.

    Strips API keys (they are loaded from env vars at runtime).
    """
    if config is None:
        return None
    if is_dataclass(config):
        d = asdict(config)
    elif isinstance(config, dict):
        d = config
    else:
        return str(config)

    # Recursively strip secret keys
    return _strip_secrets(d)


def _strip_secrets(obj):
    if isinstance(obj, dict):
        return {
            k: _strip_secrets(v)
            for k, v in obj.items()
            if k not in _SECRET_KEYS
        }
    if isinstance(obj, (list, tuple)):
        return [_strip_secrets(v) for v in obj]
    return obj


def deserialize_agent_config(cls, data: dict):
    """Reconstruct a dataclass config from a dict.

    Handles nested dataclass fields by inspecting type annotations.
    """
    if data is None:
        return None
    if not is_dataclass(cls):
        raise TypeError(f"{cls} is not a dataclass")

    # Build kwargs, recursively deserializing nested dataclass fields
    kwargs = {}
    field_map = {f.name: f for f in fields(cls)}

    for key, value in data.items():
        if key in field_map and value is not None:
            field_type = field_map[key].type
            # Resolve string type annotations
            if isinstance(field_type, str):
                # Can't resolve forward references here, just pass through
                kwargs[key] = value
            elif is_dataclass(field_type) and isinstance(value, dict):
                kwargs[key] = deserialize_agent_config(field_type, value)
            else:
                kwargs[key] = value
        elif key in field_map:
            kwargs[key] = value

    return cls(**kwargs)


def serialize_config(config_dict: Dict[str, Any]) -> str:
    """Serialize full interactive config to JSON string."""
    serialized = {}
    for key, value in config_dict.items():
        if is_dataclass(value):
            serialized[key] = {
                "__dataclass__": f"{type(value).__module__}.{type(value).__name__}",
                "data": serialize_agent_config(value),
            }
        elif isinstance(value, dict):
            serialized[key] = _strip_secrets(value)
        else:
            try:
                json.dumps(value)
                serialized[key] = value
            except (TypeError, ValueError):
                serialized[key] = str(value)

    return json.dumps(serialized, indent=2, default=str)


def deserialize_config(json_path: str) -> Dict[str, Any]:
    """Load config from JSON file. Dataclass reconstruction is left to the caller."""
    with open(json_path, "r") as f:
        return json.load(f)
