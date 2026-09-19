"""Bundled macOS picker provenance and native readiness-contract tests.

These tests validate packaging, process ownership checks, and presentation
ordering. They deliberately do not claim to prove physical mouse or keyboard
delivery; that remains the cold-launch manual acceptance gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


PACKAGED_HELPER_SHA256 = "3a4f915077c5db65578361bf74afba90855adb25e74a55eb4d8f918867f3e25a"
ADD_DATA_PICKER_INSTRUCTION = "Select the data files or folders you want to add."


def native_paths() -> tuple[Path, Path, Path, Path]:
    bundle, executable = app_module._v2_data_library_picker_helper_paths()
    native_root = bundle.parent
    return (
        native_root / "DiscoveryAgentFolderPicker.m",
        bundle,
        bundle / "Contents" / "Info.plist",
        executable,
    )


def macho_load_commands(binary: bytes) -> list[tuple[int, bytes]]:
    """Return little-endian 64-bit Mach-O load commands from an isolated binary."""
    magic, _cpu_type, _cpu_subtype, _file_type, ncmds, sizeofcmds, _flags, _reserved = (
        struct.unpack_from("<IiiIIIII", binary)
    )
    assert magic == 0xFEEDFACF
    offset = 32
    command_end = offset + sizeofcmds
    assert command_end <= len(binary)
    commands = []
    for _index in range(ncmds):
        command, command_size = struct.unpack_from("<II", binary, offset)
        assert command_size >= 8 and offset + command_size <= command_end
        commands.append((command, binary[offset:offset + command_size]))
        offset += command_size
    assert offset == command_end
    return commands


def test_running_module_and_helper_resolve_from_the_same_package_checkout():
    source, bundle, _plist, executable = native_paths()
    package_root = Path(app_module.__file__).resolve().parent

    assert source == package_root / "native" / "macos" / "DiscoveryAgentFolderPicker.m"
    assert bundle.parent.parent.parent == package_root
    assert executable.is_file()


def test_packaged_helper_is_executable_arm64_macho_with_runtime_hash_and_uuid():
    _source, _bundle, _plist, executable = native_paths()
    binary = executable.read_bytes()
    magic, cpu_type = struct.unpack_from("<II", binary)
    uuid_commands = [payload for command, payload in macho_load_commands(binary) if command == 0x1B]

    assert magic == 0xFEEDFACF
    assert cpu_type == 0x0100000C
    assert executable.stat().st_mode & 0o111
    assert app_module._v2_data_library_picker_helper_sha256() == hashlib.sha256(binary).hexdigest()
    assert hashlib.sha256(binary).hexdigest() == PACKAGED_HELPER_SHA256
    assert len(uuid_commands) == 1
    assert len(uuid_commands[0]) == 24
    assert uuid_commands[0][8:] != bytes(16)


def test_native_build_does_not_strip_the_launchservices_required_macho_uuid():
    source, _bundle, _plist, _executable = native_paths()
    build_script = (source.parent / "build_folder_picker.sh").read_text(encoding="utf-8")

    assert "-Wl,-no_uuid" not in build_script
    assert 'OUTPUT=${1:-' in build_script


def test_bundle_has_a_first_class_launchservices_identity():
    _source, _bundle, plist_path, _executable = native_paths()
    with plist_path.open("rb") as stream:
        plist = plistlib.load(stream)

    assert plist["CFBundlePackageType"] == "APPL"
    assert plist["CFBundleIdentifier"] == "org.discoveryagent.folder-picker"
    assert plist["CFBundleExecutable"] == "DiscoveryAgentFolderPicker"
    assert plist["LSArchitecturePriority"] == ["arm64"]
    assert not plist.get("LSUIElement", False)


def test_native_source_requires_real_frontmost_key_and_responder_stability():
    source_path, _bundle, _plist, _executable = native_paths()
    source = source_path.read_text(encoding="utf-8")

    assert "NSApp.isActive && frontmost.processIdentifier == getpid()" in source
    assert "panel.isVisible" in source
    assert "panel.isKeyWindow" in source
    assert "panel.isMainWindow" in source
    assert "panel.firstResponder != nil" in source
    assert "!panel.ignoresMouseEvents" in source
    assert "panel.alphaValue >= 0.99" in source
    assert "stableTurns < 3" in source
    assert "stableTurns < 5" in source
    assert "ReadinessTimeout = 2.0" in source
    assert source.count("AdvanceMainRunLoop();") >= 3


def test_native_panel_displays_the_add_data_instruction_above_its_browser():
    source_path, _bundle, _plist, _executable = native_paths()
    source = source_path.read_text(encoding="utf-8")

    message = f'panel.message = @"{ADD_DATA_PICKER_INSTRUCTION}";'
    assert message in source
    assert source.index(message) < source.index("[panel makeKeyAndOrderFront:nil];")


def test_native_panel_accepts_individual_files_and_directories():
    source_path, _bundle, _plist, _executable = native_paths()
    source = source_path.read_text(encoding="utf-8")

    assert "panel.canChooseDirectories = YES;" in source
    assert "panel.canChooseFiles = YES;" in source
    assert "panel.allowsMultipleSelection = YES;" in source


def test_bundled_instruction_is_isolated_from_project_picker_flows():
    app_source = Path(app_module.__file__).read_text(encoding="utf-8")
    data_run = app_source[
        app_source.index("def _v2_run_data_library_picker()"):
        app_source.index("def _v2_data_library_picker_helper_paths()")
    ]
    project_picker_region = app_source[
        app_source.index("def _v2_native_project_location_picker_command"):
        app_source.index("def _v2_data_library_picker_helper_paths()")
    ]
    recovery_picker = app_source[
        app_source.index("def _v2_native_project_recovery_picker_command"):
        app_source.index("def _v2_project_delete_root")
    ]

    assert "_v2_data_library_picker_command(result_path)" in data_run
    assert app_source.count("_v2_data_library_picker_command(result_path)") == 1
    assert app_source.count("'DiscoveryAgentFolderPicker.app'") == 1
    assert "DiscoveryAgentFolderPicker.app" not in project_picker_region
    assert "DiscoveryAgentFolderPicker.app" not in recovery_picker
    assert ADD_DATA_PICKER_INSTRUCTION not in app_source


def test_panel_is_mouse_enabled_before_reveal_and_only_revealed_after_activation():
    source_path, _bundle, _plist, _executable = native_paths()
    source = source_path.read_text(encoding="utf-8")
    mouse_enabled = source.index("panel.ignoresMouseEvents = NO;")
    opaque = source.index("panel.alphaValue = 1.0;")
    activate = source.index("[NSApp activateIgnoringOtherApps:YES];")
    activation_ready = source.index("if (!WaitForStableApplication())")
    reveal = source.index("[panel makeKeyAndOrderFront:nil];")
    stable = source.index("if (!WaitForStablePanel(panel))")
    modal = source.index("[panel runModal]")

    assert mouse_enabled < activate < activation_ready < reveal < stable < modal
    assert opaque < activate
    assert "ignoresMouseEvents = YES" not in source
    assert "alphaValue = 0" not in source
    assert "orderFrontRegardless" not in source
    assert "NSFloatingWindowLevel" not in source
    assert "sleep(" not in source and "usleep(" not in source


def test_native_helper_source_compiles_in_an_isolated_fixture(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("the Cocoa helper can only be compiled on macOS")
    source, _bundle, _plist, _executable = native_paths()
    output = tmp_path / "DiscoveryAgentFolderPicker"

    result = subprocess.run(
        [
            "/usr/bin/clang",
            "-arch", "arm64",
            "-mmacosx-version-min=13.0",
            "-fobjc-arc",
            "-framework", "Cocoa",
            str(source),
            "-o", str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output.is_file() and output.stat().st_mode & 0o111
    assert any(command == 0x1B for command, _payload in macho_load_commands(output.read_bytes()))


@pytest.mark.skipif(
    os.environ.get("DISCOVERYAGENT_RUN_NATIVE_PICKER_STATE_PROBE") != "1",
    reason="opt-in test briefly presents and auto-cancels a native panel",
)
def test_launchservices_probe_stays_frontmost_key_and_mouse_enabled(tmp_path):
    if sys.platform != "darwin":
        pytest.skip("the native panel state probe requires macOS")
    fixture = Path(__file__).parent / "fixtures" / "native_picker_state_probe.m"
    bundle = tmp_path / "DiscoveryAgentPickerStateProbe.app"
    executable = bundle / "Contents" / "MacOS" / "DiscoveryAgentPickerStateProbe"
    executable.parent.mkdir(parents=True)
    plist_path = bundle / "Contents" / "Info.plist"
    with plist_path.open("wb") as stream:
        plistlib.dump({
            "CFBundleDevelopmentRegion": "en",
            "CFBundleExecutable": executable.name,
            "CFBundleIdentifier": "org.discoveryagent.folder-picker-state-probe",
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleName": "DiscoveryAgent Picker State Probe",
            "CFBundlePackageType": "APPL",
            "LSMinimumSystemVersion": "13.0",
        }, stream)
    compile_result = subprocess.run(
        [
            "/usr/bin/clang", "-arch", "arm64", "-mmacosx-version-min=13.0",
            "-fobjc-arc", "-fblocks", "-framework", "Cocoa",
            str(fixture), "-o", str(executable),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr

    state_path = tmp_path / "states.jsonl"
    run_result = subprocess.run(
        ["/usr/bin/open", "-W", "-n", str(bundle), "--args", str(state_path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert run_result.returncode == 0, run_result.stderr
    states = [json.loads(line) for line in state_path.read_text().splitlines()]
    activation = [state for state in states if state["phase"] == "activation"][-3:]
    interactive = [state for state in states if state["phase"] in {"presentation", "stable", "modal"}]

    assert len(activation) == 3
    assert all(state["appActive"] and state["frontmostMatches"] for state in activation)
    assert all(not state["visible"] and not state["ignoresMouse"] for state in activation)
    assert len([state for state in states if state["phase"] == "presentation"]) >= 5
    assert len([state for state in states if state["phase"] == "stable"]) >= 4
    assert any(state["phase"] == "modal" for state in states)
    assert all(
        state["appActive"]
        and state["ownerActive"]
        and state["frontmostMatches"]
        and state["visible"]
        and state["key"]
        and state["main"]
        and not state["ignoresMouse"]
        and state["alpha"] == 1
        and state["responder"]
        for state in interactive
    )


def test_native_readiness_error_is_not_misreported_as_cancellation():
    with pytest.raises(app_module.data_library.DataLibraryError) as raised:
        app_module._v2_parse_data_library_picker_result(
            '{"status":"error","code":"picker_not_ready","message":"internal"}'
        )

    assert raised.value.code == "native_picker_not_ready"
    assert "ready for input" in str(raised.value)
