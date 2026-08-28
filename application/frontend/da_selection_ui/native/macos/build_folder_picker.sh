#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE="$SCRIPT_DIR/DiscoveryAgentFolderPicker.m"
OUTPUT=${1:-"$SCRIPT_DIR/DiscoveryAgentFolderPicker.app/Contents/MacOS/DiscoveryAgentFolderPicker"}

mkdir -p "$(dirname -- "$OUTPUT")"

exec /usr/bin/clang \
    -arch arm64 \
    -mmacosx-version-min=13.0 \
    -fobjc-arc \
    -framework Cocoa \
    "$SOURCE" \
    -o "$OUTPUT"
