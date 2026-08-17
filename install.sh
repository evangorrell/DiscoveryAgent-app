#!/bin/sh

set -eu
umask 077

RELEASE_TAG="v0.1.0"
RELEASE_ID="dev-5a87ee0d0270-d547cedabc0d"
ARTIFACT_NAME="discoveryagent-${RELEASE_ID}-macos-arm64.tar.gz"
PUBLIC_ARTIFACT_URL="https://github.com/evangorrell/DiscoveryAgent-app/releases/download/${RELEASE_TAG}/${ARTIFACT_NAME}"
PUBLIC_ARTIFACT_SHA256="fdbfe2bd7af2230c75052148c5ebfb48a15a252d6c262df42f4022d647fbe579"
UV_URL="https://releases.astral.sh/github/uv/releases/download/0.11.17/uv-aarch64-apple-darwin.tar.gz"
UV_SHA256="2a162f6b90ff3691a2f9cae1622e066a3ce592e110f66670cdcc841324b28226"

fail() {
    printf '%s\n' "DiscoveryAgent installation failed: $*" >&2
    exit 1
}

# Homebrew-style step narration. Every phase that can take more than a moment
# announces itself before it starts, so the installer is never silent while it
# downloads or builds.
step() {
    printf '==> %s\n' "$*"
}

for utility in curl tar shasum mktemp uname chmod rm mkdir; do
    command -v "$utility" >/dev/null 2>&1 || fail "required macOS utility is missing: $utility"
done

platform=${DISCOVERYAGENT_INTERNAL_PLATFORM:-$(uname -s)}
architecture=${DISCOVERYAGENT_INTERNAL_ARCHITECTURE:-$(uname -m)}
if [ "$platform" != "Darwin" ] || [ "$architecture" != "arm64" ]; then
    fail "DiscoveryAgent currently supports Apple Silicon macOS."
fi

case $0 in
    */*) script_directory=${0%/*}; [ -n "$script_directory" ] || script_directory=/ ;;
    *) script_directory=. ;;
esac
script_directory=$(CDPATH= cd "$script_directory" && pwd) || fail "could not locate install.sh"
local_artifact="$script_directory/$ARTIFACT_NAME"

artifact_url=${DISCOVERYAGENT_INTERNAL_ARTIFACT_URL:-$PUBLIC_ARTIFACT_URL}
artifact_sha256=${DISCOVERYAGENT_INTERNAL_ARTIFACT_SHA256:-$PUBLIC_ARTIFACT_SHA256}
uv_url=${DISCOVERYAGENT_INTERNAL_UV_URL:-$UV_URL}
uv_sha256=${DISCOVERYAGENT_INTERNAL_UV_SHA256:-$UV_SHA256}

temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/discoveryagent-installer.XXXXXX") || fail "could not create a secure temporary directory"
cleanup() {
    rm -rf "$temporary_root"
}
trap cleanup EXIT HUP INT TERM
chmod 700 "$temporary_root" || fail "could not secure the temporary directory"

verify_sha256() {
    expected=$1
    file=$2
    actual=$(shasum -a 256 "$file") || fail "could not calculate a download checksum"
    actual=${actual%% *}
    [ "$actual" = "$expected" ] || fail "download integrity verification failed"
}

validate_archive_paths() {
    archive=$1
    required_prefix=$2
    listing=$(tar -tzf "$archive") || fail "downloaded archive is corrupt"
    [ -n "$listing" ] || fail "downloaded archive is empty"
    printf '%s\n' "$listing" | while IFS= read -r entry; do
        case "$entry" in
            /*|../*|*/../*|*/..|*\\*) fail "downloaded archive contains an unsafe path" ;;
        esac
        case "$entry" in
            "$required_prefix"|"$required_prefix"/*) ;;
            *) fail "downloaded archive has an unexpected layout" ;;
        esac
    done
    verbose_listing=$(tar -tvzf "$archive") || fail "downloaded archive is corrupt"
    printf '%s\n' "$verbose_listing" | while IFS= read -r line; do
        mode=${line%% *}
        case "$mode" in
            l*)
                target=${line##* -> }
                [ "$target" != "$line" ] || fail "downloaded archive has malformed symlink metadata"
                case "$target" in
                    /*|../*|*/../*|*/..|*\\*) fail "downloaded archive contains an unsafe symlink" ;;
                esac
                ;;
            h*) fail "downloaded archive contains an unsupported hard link" ;;
            b*|c*|p*|s*) fail "downloaded archive contains an unsupported special file" ;;
        esac
    done
}

printf '%s\n\n' "Installing DiscoveryAgent ${RELEASE_TAG} for macOS ${architecture}."

if [ -f "$local_artifact" ]; then
    step "Using the local release archive ${ARTIFACT_NAME}"
    artifact=$local_artifact
else
    step "Downloading the DiscoveryAgent release (${ARTIFACT_NAME})"
    artifact="$temporary_root/$ARTIFACT_NAME"
    curl -fsSL "$artifact_url" -o "$artifact" || fail "could not download the DiscoveryAgent release"
fi
step "Verifying the release archive"
verify_sha256 "$artifact_sha256" "$artifact"
validate_archive_paths "$artifact" "discoveryagent-install"
step "Extracting the release archive"
payload_root="$temporary_root/payload"
mkdir "$payload_root"
tar -xzf "$artifact" -C "$payload_root" || fail "could not extract the DiscoveryAgent release"
payload_root="$payload_root/discoveryagent-install"
[ -f "$payload_root/tools/install_local_release.py" ] || fail "release is missing its installer engine"

step "Downloading uv 0.11.17"
uv_archive="$temporary_root/uv-aarch64-apple-darwin.tar.gz"
curl -fsSL "$uv_url" -o "$uv_archive" || fail "could not download the DiscoveryAgent runtime"
step "Verifying uv 0.11.17"
verify_sha256 "$uv_sha256" "$uv_archive"
validate_archive_paths "$uv_archive" "uv-aarch64-apple-darwin"
uv_root="$temporary_root/uv"
mkdir "$uv_root"
tar -xzf "$uv_archive" -C "$uv_root" || fail "could not extract the DiscoveryAgent runtime"
uv="$uv_root/uv-aarch64-apple-darwin/uv"
[ -x "$uv" ] || fail "the verified runtime archive does not contain uv"
uv_version=$("$uv" --version) || fail "the verified uv runtime could not execute"
case "$uv_version" in
    "uv 0.11.17"*) ;;
    *) fail "the runtime archive contains an unexpected uv version" ;;
esac

step "Downloading Python 3.11.11 (this can take a minute)"
python_root="$temporary_root/python"
TMPDIR="$temporary_root" UV_PYTHON_INSTALL_DIR="$python_root" UV_CACHE_DIR="$temporary_root/uv-cache" \
    UV_NO_MODIFY_PATH=1 UV_NO_PROGRESS=1 \
    "$uv" python install 3.11.11 \
    --install-dir "$python_root" --no-bin --no-registry --managed-python \
    >/dev/null 2>&1 || fail "could not install the DiscoveryAgent Python runtime"
python="$python_root/cpython-3.11.11-macos-aarch64-none/bin/python3.11"
[ -x "$python" ] || fail "the pinned DiscoveryAgent Python runtime is missing"

# The installer engine narrates its own steps on stdout from here.
unset PYTHONPATH PYTHONHOME VIRTUAL_ENV UV_PROJECT_ENVIRONMENT CLAUDE_CODE_CLI_PATH NODE_PATH || true
TMPDIR="$temporary_root" PYTHONNOUSERSITE=1 "$python" "$payload_root/tools/install_local_release.py" \
    --bootstrap-uv "$uv" \
    --bootstrap-python-root "$python_root" \
    --bootstrap-python "$python" \
    || fail "the DiscoveryAgent application could not be installed"

printf '\n%s\n' "DiscoveryAgent installed successfully."
printf '%s\n' 'Open a new terminal, or run: export PATH="$HOME/.local/bin:$PATH"'
printf '%s\n' 'Then run: discoveryagent'
