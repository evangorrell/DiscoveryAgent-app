"""Project-scoped import, validation, listing, and removal for V2 skills."""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import threading
import unicodedata
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO


MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILES = 500
MAX_MEMBER_BYTES = 10 * 1024 * 1024
MAX_SKILL_MD_BYTES = 1_000_000
MAX_PATH_LENGTH = 240
_SKILL_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ROUND_SKILLS_SNAPSHOT_DIRECTORY = ".execution_skills"
_SKILL_MUTATION_LOCK = threading.RLock()


class SkillImportError(ValueError):
    """Expected, user-facing skill package validation error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SkillMetadata:
    skill_id: str
    name: str
    description: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.skill_id,
            "name": self.name,
            "description": self.description,
        }


@contextmanager
def skill_mutation_guard():
    """Serialize installed-skill mutations with immutable round capture."""
    with _SKILL_MUTATION_LOCK:
        yield


def skills_root(project: dict) -> Path:
    project_root_raw = project.get("project_root") if isinstance(project, dict) else None
    code_dir = project.get("code_dir") if isinstance(project, dict) else None
    if not project_root_raw or not code_dir:
        raise SkillImportError("invalid_project", "This project cannot store custom skills.")

    project_root = Path(project_root_raw).expanduser()
    code_root = Path(code_dir).expanduser()
    if not project_root.is_absolute() or not code_root.is_absolute():
        raise SkillImportError("invalid_project", "This project's skill storage is unsafe.")
    if project_root.is_symlink() or not project_root.is_dir():
        raise SkillImportError("invalid_project", "This project's local directory is unavailable.")
    if code_root.is_symlink() or not code_root.is_dir():
        raise SkillImportError("invalid_project", "This project's code directory is unavailable.")
    if os.path.abspath(code_root) != os.path.abspath(project_root / "code"):
        raise SkillImportError("invalid_project", "This project's code directory is not trusted.")

    try:
        trusted_project_root = project_root.resolve(strict=True)
        trusted_code_root = code_root.resolve(strict=True)
        trusted_code_root.relative_to(trusted_project_root)
    except (OSError, ValueError) as exc:
        raise SkillImportError("invalid_project", "This project's skill storage is unsafe.") from exc

    root = code_root / ".claude" / "skills"
    try:
        for directory in (code_root / ".claude", root):
            if os.path.lexists(directory):
                if directory.is_symlink() or not directory.is_dir():
                    raise SkillImportError(
                        "invalid_project", "This project's skill storage is unsafe."
                    )
                directory.resolve(strict=True).relative_to(trusted_code_root)
    except OSError as exc:
        raise SkillImportError("invalid_project", "This project's skill storage is unavailable.") from exc
    except ValueError as exc:
        raise SkillImportError("invalid_project", "This project's skill storage is unsafe.") from exc
    return root


def _parse_frontmatter(skill_md: Path) -> tuple[str, str]:
    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SkillImportError("invalid_skill", "SKILL.md must be readable UTF-8 text.") from exc
    match = re.search(r"^\s*---\s*\n(.*?)\n---\s*", content, flags=re.DOTALL)
    if not match:
        raise SkillImportError(
            "missing_metadata",
            "SKILL.md must include name and description metadata.",
        )
    parsed: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in {"name", "description"} and re.fullmatch(
            r"[|>](?:[1-9][+-]?|[+-][1-9]?)?(?:\s+#.*)?", value
        ):
            raise SkillImportError(
                "invalid_metadata",
                "Skill name and description must use simple single-line frontmatter values.",
            )
        parsed[key] = value.strip('"').strip("'")
    name = parsed.get("name", "").strip()
    description = parsed.get("description", "").strip()
    if not name or not description:
        raise SkillImportError(
            "missing_metadata",
            "SKILL.md must include a non-empty name and description.",
        )
    if len(name) > 120 or len(description) > 1000:
        raise SkillImportError("invalid_metadata", "Skill name or description is too long.")
    return name, description


def _safe_skill_id(name: str) -> str:
    if any(character in name for character in ("/", "\\", "\x00")) or name.strip() in {".", ".."}:
        raise SkillImportError("invalid_metadata", "The skill name contains unsafe characters.")
    normalized = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not normalized:
        raise SkillImportError("invalid_metadata", "The skill name cannot form a safe identifier.")
    skill_id = normalized[:80].rstrip("-")
    if not _SKILL_ID_RE.fullmatch(skill_id):
        raise SkillImportError("invalid_metadata", "The skill name cannot form a safe identifier.")
    return skill_id


def metadata_from_directory(skill_dir: Path) -> SkillMetadata:
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir() or skill_dir.is_symlink():
        raise SkillImportError("invalid_skill", "The installed skill directory is invalid.")
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file() or skill_md.is_symlink():
        raise SkillImportError("missing_skill_md", "The package must contain a root-level SKILL.md file.")
    name, description = _parse_frontmatter(skill_md)
    return SkillMetadata(skill_dir.name, name, description)


def list_project_skills(project: dict) -> list[dict[str, str]]:
    root = skills_root(project)
    if not root.is_dir():
        return []
    skills = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name.lower())
        for child in children:
            if not _SKILL_ID_RE.fullmatch(child.name):
                continue
            try:
                skills.append(metadata_from_directory(child).as_dict())
            except SkillImportError:
                continue
    except OSError as exc:
        raise SkillImportError(
            "invalid_project", "This project's skill storage is unavailable."
        ) from exc
    return skills


def has_project_skills(project: dict) -> bool:
    return bool(list_project_skills(project))


def _validate_member(info: zipfile.ZipInfo) -> PurePosixPath:
    raw = info.filename.replace("\\", "/")
    if not raw or "\x00" in raw or len(raw) > MAX_PATH_LENGTH:
        raise SkillImportError("unsafe_archive", "The archive contains an unsafe file path.")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise SkillImportError("unsafe_archive", "The archive contains an absolute path.")
    if any(part in ("", ".", "..") for part in path.parts):
        raise SkillImportError("unsafe_archive", "The archive contains an unsafe file path.")

    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode):
        raise SkillImportError("unsafe_archive", "Skill packages may not contain symbolic links.")
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise SkillImportError("unsafe_archive", "The archive contains an unsupported filesystem entry.")
    if (kind == stat.S_IFDIR) != info.is_dir() and kind != 0:
        raise SkillImportError("unsafe_archive", "The archive contains an invalid filesystem entry.")
    if info.file_size > MAX_MEMBER_BYTES:
        raise SkillImportError("archive_too_large", "A file in the skill package is too large.")
    return path


def _canonical_member_path(path: PurePosixPath) -> tuple[str, ...]:
    """Model the case/normalization-insensitive release filesystem."""
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in path.parts)


def _is_finder_metadata(path: PurePosixPath) -> bool:
    return path.parts[0] == "__MACOSX" or any(part.startswith("._") for part in path.parts)


def _inspect_archive(archive: zipfile.ZipFile) -> tuple[list[zipfile.ZipInfo], str | None]:
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_FILES:
        raise SkillImportError("archive_too_large", "The skill package contains too many entries.")
    files = [info for info in members if not info.is_dir()]
    if sum(info.file_size for info in files) > MAX_UNCOMPRESSED_BYTES:
        raise SkillImportError("archive_too_large", "The expanded skill package is too large.")

    paths = [_validate_member(info) for info in members]
    canonical_paths = [_canonical_member_path(path) for path in paths]
    if len(set(canonical_paths)) != len(canonical_paths):
        raise SkillImportError("invalid_skill", "The ZIP contains conflicting package paths.")
    canonical_files = {
        canonical for canonical, info in zip(canonical_paths, members) if not info.is_dir()
    }
    if any(
        canonical[:index] in canonical_files
        for canonical in canonical_paths
        for index in range(1, len(canonical))
    ):
        raise SkillImportError("invalid_skill", "The ZIP contains conflicting package paths.")

    for path, info in zip(paths, members):
        if not info.is_dir() and path.name == "SKILL.md" and info.file_size > MAX_SKILL_MD_BYTES:
            raise SkillImportError(
                "archive_too_large", "SKILL.md exceeds Claude's 1,000,000-byte limit."
            )

    package_members = [
        info for path, info in zip(paths, members) if not _is_finder_metadata(path)
    ]
    package_paths = [
        path for path in paths if not _is_finder_metadata(path)
    ]
    file_paths = [
        path for path, info in zip(package_paths, package_members) if not info.is_dir()
    ]
    if not file_paths:
        raise SkillImportError("invalid_skill", "The skill package is empty.")
    root_skill = PurePosixPath("SKILL.md") in file_paths
    top_level = {path.parts[0] for path in file_paths}
    if root_skill:
        nested_skill_files = [path for path in file_paths if path.name == "SKILL.md" and len(path.parts) > 1]
        if nested_skill_files:
            raise SkillImportError("multiple_skills", "The ZIP must contain exactly one skill.")
        return package_members, None

    candidates = [path for path in file_paths if path.name == "SKILL.md" and len(path.parts) == 2]
    if len(candidates) != 1 or len(top_level) != 1:
        code = "missing_skill_md" if not candidates else "multiple_skills"
        message = (
            "The package must contain a root-level SKILL.md file."
            if code == "missing_skill_md"
            else "The ZIP must contain exactly one skill."
        )
        raise SkillImportError(code, message)
    wrapper = candidates[0].parts[0]
    if any(path.parts[0] != wrapper for path in package_paths):
        raise SkillImportError("multiple_skills", "The ZIP must contain exactly one skill.")
    if any(path.name == "SKILL.md" and path != candidates[0] for path in file_paths):
        raise SkillImportError("multiple_skills", "The ZIP must contain exactly one skill.")
    return package_members, wrapper


def _extract_archive(archive: zipfile.ZipFile, members: list[zipfile.ZipInfo], destination: Path) -> None:
    destination_resolved = destination.resolve()
    for info in members:
        relative = _validate_member(info)
        target = destination.joinpath(*relative.parts)
        target_resolved = target.resolve()
        if not target_resolved.is_relative_to(destination_resolved):
            raise SkillImportError("unsafe_archive", "The archive contains an unsafe file path.")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info, "r") as source, target.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        executable_bits = (info.external_attr >> 16) & 0o111
        if executable_bits:
            target.chmod((target.stat().st_mode & 0o666) | executable_bits)


def _save_upload(upload: BinaryIO, destination: Path) -> None:
    total = 0
    with destination.open("xb") as output:
        while True:
            chunk = upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise SkillImportError("archive_too_large", "The selected skill file is too large.")
            output.write(chunk)


def _skill_upload_kind(filename: str) -> str:
    basename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if basename == "SKILL.md":
        return "standalone"
    suffix = Path(basename).suffix.lower()
    if suffix == ".zip":
        return "zip"
    if suffix == ".skill":
        return "skill"
    raise SkillImportError(
        "unsupported_skill_file",
        "Choose a .skill, .zip, or SKILL.md file.",
    )


def _looks_like_zip(upload_path: Path) -> bool:
    try:
        with upload_path.open("rb") as source:
            signature = source.read(4)
    except OSError as exc:
        raise SkillImportError("invalid_skill", "The selected skill file could not be read.") from exc
    return signature in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}


def _standalone_package(upload_path: Path, temp_root: Path) -> Path:
    try:
        size = upload_path.stat().st_size
        payload = upload_path.read_bytes()
    except OSError as exc:
        raise SkillImportError("invalid_skill", "The selected skill file could not be read.") from exc
    if size > MAX_SKILL_MD_BYTES:
        raise SkillImportError(
            "archive_too_large", "SKILL.md exceeds Claude's 1,000,000-byte limit."
        )
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillImportError("invalid_skill", "SKILL.md must be readable UTF-8 text.") from exc
    if not content.strip():
        raise SkillImportError("invalid_skill", "SKILL.md cannot be empty.")
    if any(
        unicodedata.category(character) == "Cc" and character not in "\t\n\r"
        for character in content
    ):
        raise SkillImportError("invalid_skill", "SKILL.md must be plain UTF-8 text.")

    package_root = temp_root / "standalone"
    package_root.mkdir()
    skill_md = package_root / "SKILL.md"
    os.replace(upload_path, skill_md)
    skill_md.chmod(0o644)
    return package_root


def _archive_package(upload_path: Path, temp_root: Path) -> Path:
    extract_root = temp_root / "extracted"
    extract_root.mkdir()
    try:
        with zipfile.ZipFile(upload_path, "r") as archive:
            members, wrapper = _inspect_archive(archive)
            _extract_archive(archive, members, extract_root)
    except zipfile.BadZipFile as exc:
        raise SkillImportError("invalid_zip", "The selected file is not a valid ZIP package.") from exc
    except (NotImplementedError, RuntimeError) as exc:
        raise SkillImportError("invalid_zip", "The ZIP package uses an unsupported format.") from exc
    return extract_root / wrapper if wrapper else extract_root


@contextmanager
def _validated_skill_package(upload, filename: str):
    upload_kind = _skill_upload_kind(filename)
    with tempfile.TemporaryDirectory(prefix="discoveryagent-skill-") as temp_name:
        temp_root = Path(temp_name)
        upload_path = temp_root / "upload"
        _save_upload(upload, upload_path)
        is_archive = zipfile.is_zipfile(upload_path)

        if upload_kind == "zip":
            if not is_archive:
                raise SkillImportError("invalid_zip", "The selected file is not a valid ZIP package.")
            package_root = _archive_package(upload_path, temp_root)
        elif upload_kind == "skill" and is_archive:
            package_root = _archive_package(upload_path, temp_root)
        else:
            if is_archive or _looks_like_zip(upload_path):
                message = (
                    "A standalone SKILL.md file must be plain UTF-8 text."
                    if upload_kind == "standalone"
                    else "The .skill file looks like a damaged ZIP package."
                )
                raise SkillImportError("invalid_skill", message)
            package_root = _standalone_package(upload_path, temp_root)

        name, description = _parse_frontmatter(package_root / "SKILL.md")
        skill_id = _safe_skill_id(name)
        yield package_root, SkillMetadata(skill_id, name, description)


def validate_project_skill(project: dict, upload, filename: str) -> dict[str, str]:
    root = skills_root(project)
    with _validated_skill_package(upload, filename) as (_package_root, metadata):
        if (root / metadata.skill_id).exists():
            raise SkillImportError(
                "duplicate_skill", f'A skill named "{metadata.name}" is already imported.'
            )
        return metadata.as_dict()


def import_project_skill(project: dict, upload, filename: str) -> dict[str, str]:
    with skill_mutation_guard():
        root = skills_root(project)
        with _validated_skill_package(upload, filename) as (package_root, metadata):
            name = metadata.name
            skill_id = metadata.skill_id
            final_dir = root / skill_id
            if os.path.lexists(final_dir):
                if final_dir.is_symlink():
                    raise SkillImportError("invalid_project", "This project's skill storage is unsafe.")
                raise SkillImportError("duplicate_skill", f'A skill named "{name}" is already imported.')
            root.mkdir(parents=True, exist_ok=True)
            if skills_root(project) != root:
                raise SkillImportError("invalid_project", "This project's skill storage is unsafe.")
            staging = root / f".import-{uuid.uuid4().hex}"
            try:
                shutil.copytree(package_root, staging, symlinks=False)
                os.replace(staging, final_dir)
            except FileExistsError as exc:
                raise SkillImportError("duplicate_skill", f'A skill named "{name}" is already imported.') from exc
            finally:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
            return metadata.as_dict()


def remove_project_skill(project: dict, skill_id: str) -> dict[str, str]:
    with skill_mutation_guard():
        if not isinstance(skill_id, str) or not _SKILL_ID_RE.fullmatch(skill_id):
            raise SkillImportError("invalid_skill_id", "The selected skill identifier is invalid.")
        root = skills_root(project)
        target = root / skill_id
        if not target.is_dir() or target.is_symlink():
            raise SkillImportError("skill_not_found", "That skill is no longer available.")
        try:
            if not target.resolve().is_relative_to(root.resolve()):
                raise SkillImportError("invalid_skill_id", "The selected skill identifier is invalid.")
        except OSError as exc:
            raise SkillImportError("skill_not_found", "That skill is no longer available.") from exc
        metadata = metadata_from_directory(target).as_dict()
        tombstone = root / f".remove-{uuid.uuid4().hex}"
        os.replace(target, tombstone)
        shutil.rmtree(tombstone)
        return metadata


def _snapshot_has_skills(snapshot: Path) -> bool:
    try:
        for child in snapshot.iterdir():
            if not _SKILL_ID_RE.fullmatch(child.name):
                continue
            try:
                metadata_from_directory(child)
            except SkillImportError:
                continue
            return True
    except OSError as exc:
        raise SkillImportError(
            "invalid_project", "This round's captured skill storage is unavailable."
        ) from exc
    return False


def _skill_tree_is_safe(skill_dir: Path) -> bool:
    """Reject manually introduced links or special files before snapshotting."""
    try:
        for current, directories, filenames in os.walk(skill_dir, followlinks=False):
            current_path = Path(current)
            for name in [*directories, *filenames]:
                entry = current_path / name
                mode = entry.lstat().st_mode
                if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    return False
    except OSError:
        return False
    return True


def capture_round_skills(project: dict, round_dir: Path) -> tuple[Path, bool]:
    """Atomically capture the exact installed skill set for one round.

    The snapshot directory itself is the durable capture marker, including for
    an empty skill set. Once it exists, resume/restart paths reuse it rather
    than consulting the project's live Skills directory.
    """
    target_round = Path(round_dir).expanduser().absolute()
    snapshot = target_round / ROUND_SKILLS_SNAPSHOT_DIRECTORY
    with skill_mutation_guard():
        if os.path.lexists(snapshot):
            if snapshot.is_symlink() or not snapshot.is_dir():
                raise SkillImportError(
                    "invalid_project", "This round's captured skill storage is unsafe."
                )
            return snapshot, _snapshot_has_skills(snapshot)

        root = skills_root(project)
        target_round.mkdir(parents=True, exist_ok=True)
        staging = target_round / f".{ROUND_SKILLS_SNAPSHOT_DIRECTORY.lstrip('.')}.tmp-{uuid.uuid4().hex}"
        copied = False
        try:
            staging.mkdir(mode=0o700)
            if root.is_dir():
                for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
                    if not _SKILL_ID_RE.fullmatch(child.name):
                        continue
                    try:
                        metadata_from_directory(child)
                    except SkillImportError:
                        continue
                    if not _skill_tree_is_safe(child):
                        continue
                    shutil.copytree(child, staging / child.name, symlinks=False)
                    copied = True
            os.replace(staging, snapshot)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        return snapshot, copied
