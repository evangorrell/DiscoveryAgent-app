"""
Sandbox enforcement hook for Claude Code SDK.

Restricts the execution agent to operate within a designated sandbox directory.
Write/Edit operations are confined to the sandbox root. Read/Glob/Grep operations
are allowed within the sandbox AND a configurable set of additional read paths.
Bash commands are checked on a best-effort basis for common shell patterns.

Symlinks inside the sandbox that resolve to external targets are permitted:
the check passes if EITHER the literal absolute path OR the resolved real path
falls within an allowed root. This lets symlinked data directories work
transparently.
"""

import os
import re
import shlex
from typing import List, Optional, Any

from claude_agent_sdk import HookMatcher


# Commands that destructively modify targets (all non-flag args are targets)
_DESTRUCTIVE_ALL_ARGS = {"rm", "rmdir", "mkdir", "touch", "chmod", "chown", "chgrp"}

# Commands where only the LAST non-flag arg is the write destination
_COPY_LIKE_COMMANDS = {"cp", "mv", "ln", "install"}

# Patterns for in-place edit flags
_INPLACE_EDIT_PATTERN = re.compile(r"\b(sed|perl|awk)\b.*\s-i\b")

# Patterns for output redirection
_REDIRECT_PATTERN = re.compile(r"(>{1,2})\s*(\S+)")

# Pattern for tee command writing to a file
_TEE_PATTERN = re.compile(r"\btee\b(?:\s+-a)?\s+(\S+)")

# Read-oriented commands whose arguments are source paths
_READ_COMMANDS = {"cat", "head", "tail", "less", "more", "wc", "file", "stat", "md5sum", "sha256sum"}


def _make_absolute(path: str, cwd: Optional[str] = None) -> str:
    """Return the absolute (but NOT realpath-resolved) form of *path*."""
    if not os.path.isabs(path):
        base = cwd or os.getcwd()
        path = os.path.join(base, path)
    return os.path.normpath(path)


def _is_within(path: str, allowed_roots: List[str], cwd: Optional[str] = None) -> bool:
    """Check whether *path* falls under any of *allowed_roots*.

    The check passes if EITHER:
      1. The literal (non-resolved) absolute path is inside an allowed root, OR
      2. The ``os.path.realpath()``-resolved path is inside an allowed root.

    This dual check lets symlinks that live inside the sandbox (literal path
    passes) transparently point to external targets, while also allowing direct
    references to those external targets when they appear in *allowed_roots*.
    """
    abs_path = _make_absolute(path, cwd)
    real_path = os.path.realpath(abs_path)

    for root in allowed_roots:
        root_slash = root + "/"
        # Check literal path
        if abs_path == root or abs_path.startswith(root_slash):
            return True
        # Check resolved path
        if real_path == root or real_path.startswith(root_slash):
            return True

    return False


def _check_bash_write(command: str, sandbox_root: str, cwd: Optional[str] = None) -> Optional[str]:
    """Check a Bash command for write operations outside the sandbox.

    Returns a denial reason if a violation is found, or None if allowed.
    """
    # Claude Code often emits relative write targets like "outputs" or "src/analysis".
    # If the hook call does not provide the tool cwd, resolve those relative paths
    # against the sandbox root rather than the parent Python process cwd.
    cwd = cwd or sandbox_root
    write_roots = [sandbox_root]
    segments = re.split(r"\s*(?:&&|\|\||;)\s*", command)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        # 1. Output redirections (> or >>)
        for redir_match in _REDIRECT_PATTERN.finditer(segment):
            target_file = redir_match.group(2).strip("'\"")
            if not _is_within(target_file, write_roots, cwd):
                return f"Blocked: redirect to path outside sandbox: {target_file}"

        # 2. tee writing to file
        for tee_match in _TEE_PATTERN.finditer(segment):
            target_file = tee_match.group(1).strip("'\"")
            if not _is_within(target_file, write_roots, cwd):
                return f"Blocked: 'tee' writing outside sandbox: {target_file}"

        # 3. In-place edits (sed -i, perl -i)
        if _INPLACE_EDIT_PATTERN.search(segment):
            try:
                tokens = shlex.split(segment)
            except ValueError:
                tokens = segment.split()
            for token in tokens:
                if token.startswith("-"):
                    continue
                if "/" in token or os.path.sep in token:
                    if not _is_within(token, write_roots, cwd):
                        return f"Blocked: in-place edit outside sandbox: {token}"

        # 4. Destructive / copy-like commands
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()

        if not tokens:
            continue

        cmd_name = os.path.basename(tokens[0])

        if cmd_name in _DESTRUCTIVE_ALL_ARGS:
            for token in tokens[1:]:
                if token.startswith("-"):
                    continue
                if not _is_within(token, write_roots, cwd):
                    return f"Blocked: '{cmd_name}' targeting path outside sandbox: {token}"

        elif cmd_name in _COPY_LIKE_COMMANDS:
            non_flag_args = [t for t in tokens[1:] if not t.startswith("-")]
            if non_flag_args:
                dest = non_flag_args[-1]
                if not _is_within(dest, write_roots, cwd):
                    return f"Blocked: '{cmd_name}' writing outside sandbox: {dest}"

    return None


def _check_bash_read(command: str, read_roots: List[str], cwd: Optional[str] = None) -> Optional[str]:
    """Check a Bash command for read operations outside allowed roots.

    Returns a denial reason if a violation is found, or None if allowed.
    """
    segments = re.split(r"\s*(?:&&|\|\||;)\s*", command)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        # 1. Block cd outside allowed paths
        cd_match = re.match(r"^cd\s+(.+)$", segment)
        if cd_match:
            target = cd_match.group(1).strip().strip("'\"")
            if not _is_within(target, read_roots, cwd):
                return f"Blocked: 'cd' to path outside sandbox: {target}"

        # 2. Read-oriented commands
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()

        if not tokens:
            continue

        cmd_name = os.path.basename(tokens[0])
        if cmd_name in _READ_COMMANDS:
            for token in tokens[1:]:
                if token.startswith("-"):
                    continue
                # Only check tokens that look like paths
                if "/" in token or os.path.sep in token:
                    if not _is_within(token, read_roots, cwd):
                        return f"Blocked: '{cmd_name}' reading outside sandbox: {token}"

    return None


def create_sandbox_enforcement_hook(
    sandbox_root: str,
    allowed_read_paths: Optional[List[str]] = None,
    logger: Any = None,
) -> List[HookMatcher]:
    """Factory that returns PreToolUse HookMatchers enforcing sandbox boundaries.

    Args:
        sandbox_root: Absolute path to the sandbox directory. All writes are
            confined here. Reads are also confined here plus *allowed_read_paths*.
        allowed_read_paths: Optional list of additional absolute directory paths
            that the agent may read from (but not write to).
        logger: Optional logger with ``log_action`` / ``log_error`` methods.

    Returns:
        List of 6 ``HookMatcher`` objects (Write, Edit, Read, Glob, Grep, Bash).
    """
    # Normalize roots — canonical form without trailing slash
    sandbox_root = os.path.realpath(sandbox_root).rstrip("/")
    read_extras = [os.path.realpath(p).rstrip("/") for p in (allowed_read_paths or [])]
    write_roots = [sandbox_root]
    read_roots = [sandbox_root] + read_extras

    # ------------------------------------------------------------------
    # Write / Edit hook — hard block outside sandbox
    # ------------------------------------------------------------------
    async def write_edit_hook(input_data, tool_use_id, context) -> dict:
        tool_input = input_data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")

        if not file_path:
            return {"permissionDecision": "allow"}

        if not _is_within(file_path, write_roots):
            reason = f"Sandbox enforcement: write denied outside sandbox: {file_path}"
            if logger:
                logger.log_action("sandbox_write_deny", reason)
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }

        return {"permissionDecision": "allow"}

    # ------------------------------------------------------------------
    # Read hook — hard block outside sandbox + allowed read paths
    # ------------------------------------------------------------------
    async def read_hook(input_data, tool_use_id, context) -> dict:
        tool_input = input_data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")

        if not file_path:
            return {"permissionDecision": "allow"}

        if not _is_within(file_path, read_roots):
            reason = f"Sandbox enforcement: read denied outside sandbox: {file_path}"
            if logger:
                logger.log_action("sandbox_read_deny", reason)
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }

        return {"permissionDecision": "allow"}

    # ------------------------------------------------------------------
    # Glob / Grep hook — hard block searches outside sandbox + allowed reads
    # ------------------------------------------------------------------
    async def search_hook(input_data, tool_use_id, context) -> dict:
        tool_input = input_data.get("tool_input", {})
        search_path = tool_input.get("path", "")

        # Empty path means cwd, which is set to sandbox root — allow
        if not search_path:
            return {"permissionDecision": "allow"}

        if not _is_within(search_path, read_roots):
            reason = f"Sandbox enforcement: search denied outside sandbox: {search_path}"
            if logger:
                logger.log_action("sandbox_search_deny", reason)
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }

        return {"permissionDecision": "allow"}

    # ------------------------------------------------------------------
    # Bash hook — best-effort: check writes AND reads
    # ------------------------------------------------------------------
    async def bash_hook(input_data, tool_use_id, context) -> dict:
        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        if not command:
            return {"permissionDecision": "allow"}

        cwd = tool_input.get("cwd", None)

        # Check writes first (stricter — sandbox only)
        write_violation = _check_bash_write(command, sandbox_root, cwd)
        if write_violation:
            if logger:
                logger.log_action("sandbox_bash_write_deny", write_violation)
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Sandbox enforcement: {write_violation}",
            }

        # Check reads (sandbox + allowed read paths)
        read_violation = _check_bash_read(command, read_roots, cwd)
        if read_violation:
            if logger:
                logger.log_action("sandbox_bash_read_deny", read_violation)
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Sandbox enforcement: {read_violation}",
            }

        return {"permissionDecision": "allow"}

    return [
        HookMatcher(matcher="Write", hooks=[write_edit_hook]),
        HookMatcher(matcher="Edit", hooks=[write_edit_hook]),
        HookMatcher(matcher="Read", hooks=[read_hook]),
        HookMatcher(matcher="Glob", hooks=[search_hook]),
        HookMatcher(matcher="Grep", hooks=[search_hook]),
        HookMatcher(matcher="Bash", hooks=[bash_hook]),
    ]
