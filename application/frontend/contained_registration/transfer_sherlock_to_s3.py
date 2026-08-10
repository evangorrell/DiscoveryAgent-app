import argparse
import os
import subprocess
import shutil
from pathlib import Path
import fnmatch

try:
    import boto3  # type: ignore
except ImportError as exc:
    raise RuntimeError(
        "boto3 is required to transfer data to S3. Install the AWS SDK for Python."
    ) from exc
from dotenv import load_dotenv  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=REPO_ROOT / ".env")

DATA_PATH = os.getenv("DATA_PATH")
if DATA_PATH is None:
    raise RuntimeError("DATA_PATH must be set to transfer Sherlock data to S3.")

DEFAULT_MAX_UPLOAD_SIZE_MB = 750
DEFAULT_MAX_UPLOAD_SIZE_BYTES = DEFAULT_MAX_UPLOAD_SIZE_MB * 1024 * 1024


def _get_s3_client():
    """Create an S3 client using env vars when provided."""
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    session_token = os.getenv("AWS_SESSION_TOKEN")

    if access_key and secret_key:
        return boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
        )

    return boto3.client("s3", region_name=region)


def _join_prefix(*parts: str | None) -> str:
    cleaned = [p.strip("/") for p in parts if p]
    return "/".join(p for p in cleaned if p)

def _format_size_mb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _format_limit_mb(max_bytes: int) -> str:
    return f"{max_bytes / (1024 * 1024):.0f} MB"


def _should_include(rel_path: str, filters: list[tuple[str, bool]]) -> bool:
    include = True
    for pattern, should_include in filters:
        if fnmatch.fnmatch(rel_path, pattern):
            include = should_include
    return include


def _collect_oversized_files(
    dir_path: Path,
    *,
    max_bytes: int,
    exclude_patterns: list[str] | None = None,
    include_patterns: list[str] | None = None,
    follow_symlinks: bool = False,
) -> list[tuple[Path, int]]:
    filters: list[tuple[str, bool]] = []
    if exclude_patterns:
        filters.extend((pattern, False) for pattern in exclude_patterns)
    if include_patterns:
        filters.extend((pattern, True) for pattern in include_patterns)

    oversized: list[tuple[Path, int]] = []
    for root, _, files in os.walk(dir_path, followlinks=follow_symlinks):
        for filename in files:
            file_path = Path(root) / filename
            rel_path = file_path.relative_to(dir_path).as_posix()
            if filters and not _should_include(rel_path, filters):
                continue
            if file_path.is_symlink() and not follow_symlinks:
                size_bytes = file_path.lstat().st_size
            else:
                size_bytes = file_path.stat().st_size
            if size_bytes > max_bytes:
                oversized.append((file_path, size_bytes))
    return oversized


def _write_omitted_notes(
    notes_path: Path,
    *,
    base_dir: Path,
    max_bytes: int,
    omitted: list[tuple[Path, int]],
) -> None:
    if not omitted:
        return

    lines = [
        "# S3 Upload Notes",
        "",
        "This file records local files that were omitted from the S3 upload because they exceeded the size limit.",
        f"Configured size limit: {_format_limit_mb(max_bytes)}.",
        "",
        "Omitted files (not uploaded):",
    ]
    for index, (path, size_bytes) in enumerate(omitted, start=1):
        rel_path = path.relative_to(base_dir).as_posix()
        lines.append(
            f"{index}. path: {rel_path}; size: {_format_size_mb(size_bytes)}; reason: size exceeds limit."
        )

    notes_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _enforce_size_limit_for_file(path: Path, *, max_bytes: int) -> None:
    size_bytes = path.stat().st_size
    if size_bytes > max_bytes:
        raise RuntimeError(
            f"Refusing to upload {path} because it is {_format_size_mb(size_bytes)}, "
            f"which exceeds the {_format_limit_mb(max_bytes)} limit."
        )


def _sync_directory_to_s3(
    dir_path: Path,
    bucket_name: str,
    s3_prefix: str,
    exclude_patterns: list[str] | None = None,
    include_patterns: list[str] | None = None,
    force_exclude_patterns: list[str] | None = None,
    follow_symlinks: bool = False,
) -> None:
    """Upload a directory using aws s3 sync."""
    if shutil.which("aws") is None:
        raise RuntimeError(
            "AWS CLI is required for directory syncs. Install `aws` or upload files individually."
        )
    destination = f"s3://{bucket_name}"
    if s3_prefix:
        destination = f"{destination}/{s3_prefix}"

    command = ["aws", "s3", "sync", str(dir_path), destination]
    if follow_symlinks:
        command.append("--follow-symlinks")
    if exclude_patterns:
        for pattern in exclude_patterns:
            command.extend(["--exclude", pattern])
    if include_patterns:
        for pattern in include_patterns:
            command.extend(["--include", pattern])
    if force_exclude_patterns:
        for pattern in force_exclude_patterns:
            command.extend(["--exclude", pattern])

    subprocess.run(command, check=True)


def transfer_file_to_s3(
    file_path: str,
    s3_key: str | None = None,
    project_prefix: str | None = None,
    exclude_dir_patterns: list[str] | None = None,
    include_dir_patterns: list[str] | None = None,
    follow_symlinks: bool = False,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_SIZE_BYTES,
    notes_path: Path | None = None,
) -> str:
    """
    Upload a single file to the S3 bucket defined in .env.

    Required env:
      - S3_BUCKET_NAME
    Optional env:
      - S3_PREFIX (prefix/folder within the bucket)
      - AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
      - AWS_REGION or AWS_DEFAULT_REGION
    """
    bucket_name = os.getenv("S3_BUCKET_NAME")
    if not bucket_name:
        raise RuntimeError("S3_BUCKET_NAME must be set to upload a file.")

    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    prefix = _join_prefix(os.getenv("S3_PREFIX"), project_prefix)

    if path.is_dir():
        directory_prefix = s3_key if s3_key is not None else prefix
        oversized = _collect_oversized_files(
            path,
            max_bytes=max_upload_bytes,
            exclude_patterns=exclude_dir_patterns,
            include_patterns=include_dir_patterns,
            follow_symlinks=follow_symlinks,
        )
        if oversized:
            oversized.sort(key=lambda item: item[1], reverse=True)
            if notes_path is not None:
                _write_omitted_notes(
                    notes_path,
                    base_dir=path,
                    max_bytes=max_upload_bytes,
                    omitted=oversized,
                )
            oversized_patterns = [
                file_path.relative_to(path).as_posix() for file_path, _ in oversized
            ]
        else:
            oversized_patterns = None

        _sync_directory_to_s3(
            path,
            bucket_name,
            directory_prefix,
            exclude_patterns=exclude_dir_patterns,
            include_patterns=include_dir_patterns,
            force_exclude_patterns=oversized_patterns,
            follow_symlinks=follow_symlinks,
        )
        return directory_prefix

    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    _enforce_size_limit_for_file(path, max_bytes=max_upload_bytes)

    if s3_key is None:
        s3_key = path.name
        if prefix:
            s3_key = f"{prefix}/{s3_key}"

    s3 = _get_s3_client()
    s3.upload_file(str(path), bucket_name, s3_key)
    return s3_key


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload a single file to an S3 bucket using .env settings."
    )
    parser.add_argument(
        "--file_path", type=str, help="Path to the local file or directory to upload."
    )
    parser.add_argument(
        "--project_prefix",
        nargs="?",
        default=None,
        type=str,
        help="Prefix for the S3 object key. Should be <dataset_name>/<project_prefix>. Will automatically link to data directory.",
    )
    parser.add_argument(
        "--key",
        dest="s3_key",
        default=None,
        help="Optional S3 object key override.",
    )
    parser.add_argument(
        "--size-limit",
        dest="size_limit_mb",
        type=int,
        default=DEFAULT_MAX_UPLOAD_SIZE_MB,
        help="Maximum allowed file size in MB before upload is blocked (default: 750).",
    )
    args = parser.parse_args()
    max_upload_bytes = args.size_limit_mb * 1024 * 1024

    if args.project_prefix is not None:
        # Need to iterate over both code and checkpoint directories:
        checkpoint_path = os.path.join(DATA_PATH, "LOGS", args.project_prefix)
        code_path = os.path.join(DATA_PATH, args.project_prefix)

        checkpoint_s3_key = transfer_file_to_s3(
            checkpoint_path,
            _join_prefix(args.project_prefix, "checkpoint"),
            max_upload_bytes=max_upload_bytes,
        )
        code_s3_key = transfer_file_to_s3(
            code_path,
            _join_prefix(args.project_prefix, "code"),
            exclude_dir_patterns=["data/**"],
            include_dir_patterns=["data/README.txt"],
            follow_symlinks=True,
            max_upload_bytes=max_upload_bytes,
            notes_path=Path(code_path) / "S3_NOTES.md",
        )

        print(f"Uploaded checkpoint data to s3://{os.getenv('S3_BUCKET_NAME')}/{checkpoint_s3_key}")
        print(f"Uploaded code data to s3://{os.getenv('S3_BUCKET_NAME')}/{code_s3_key}")

    else:   
        assert args.file_path is not None, "file_path is required when project_prefix is not provided"
        s3_key = transfer_file_to_s3(
            args.file_path,
            args.s3_key,
            args.project_prefix,
            max_upload_bytes=max_upload_bytes,
        )
        bucket_name = os.getenv("S3_BUCKET_NAME")
        print(f"Uploaded to s3://{bucket_name}/{s3_key}")


if __name__ == "__main__":
    main()
