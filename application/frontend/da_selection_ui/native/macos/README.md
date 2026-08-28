# macOS folder-picker helper

DiscoveryAgent's Data Library calls this first-class Cocoa application through
LaunchServices. The executable is intentionally checked in because the
frontend wheel is an immutable release input and cannot assume that Xcode or a
compiler is installed on the user's Mac.

- Supported architecture: Apple Silicon (`arm64`)
- Minimum deployment target: macOS 13.0
- Source: `DiscoveryAgentFolderPicker.m`
- Build command: `./build_folder_picker.sh [optional-output-path]`
- Packaged executable SHA-256:
  `80edd7a18f0fd952a81686df5519b91ecc992d113698c157e9de6c64022c7a71`

The build script accepts an optional output path so CI and reviewers can
compile into a temporary directory without overwriting the packaged binary.
After intentionally rebuilding the packaged executable, verify all of the
following before updating the checksum above:

```sh
file DiscoveryAgentFolderPicker.app/Contents/MacOS/DiscoveryAgentFolderPicker
shasum -a 256 DiscoveryAgentFolderPicker.app/Contents/MacOS/DiscoveryAgentFolderPicker
python -m pytest tests/test_native_data_picker.py -p no:cacheprovider
```

The binary must remain executable, arm64 Mach-O, carry a nonzero Mach-O UUID,
and match the app bundle's `CFBundleExecutable`, architecture, and macOS
deployment declarations. The build consumes only the tracked Objective-C
source and the system Cocoa framework; it embeds no credentials, network
content, or machine-specific paths.
