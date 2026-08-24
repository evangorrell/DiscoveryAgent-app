#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE="$SCRIPT_DIR/DiscoveryAgentFolderPicker.m"
OUTPUT="$SCRIPT_DIR/DiscoveryAgentFolderPicker.app/Contents/MacOS/DiscoveryAgentFolderPicker"

exec /usr/bin/clang \
    -arch arm64 \
    -mmacosx-version-min=13.0 \
    -fobjc-arc \
    -framework Cocoa \
    "$SOURCE" \
    -o "$OUTPUT"
