#!/usr/bin/python
"""Package the shortcut probe as a launchable Snow Leopard application."""

import json
import os
import plistlib
import shutil
import sys


def main():
    if len(sys.argv) != 7:
        raise SystemExit("Usage: build_probe.py OUTPUT.app TRACK LEFT_X LEFT_Y RIGHT_X RIGHT_Y")
    bundle = os.path.abspath(sys.argv[1])
    track = os.path.abspath(os.path.expanduser(sys.argv[2]))
    if not bundle.endswith(".app") or os.path.exists(bundle):
        raise SystemExit("Specify a new output path ending in .app.")
    if not os.path.isfile(track):
        raise SystemExit("Choose an existing local audio file.")
    left_x, left_y, right_x, right_y = map(int, sys.argv[3:])
    contents = os.path.join(bundle, "Contents")
    macos = os.path.join(contents, "MacOS")
    resources = os.path.join(contents, "Resources")
    os.makedirs(macos)
    os.mkdir(resources)

    metadata = {
        "CFBundleName": "Music Stream Shortcut Prototype",
        "CFBundleIdentifier": "local.music-stream.shortcut-prototype",
        "CFBundleExecutable": "Launch",
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "10.6",
    }
    info = os.path.join(contents, "Info.plist")
    if hasattr(plistlib, "dumps"):
        with open(info, "wb") as handle:
            handle.write(plistlib.dumps(metadata))
    else:
        plistlib.writePlist(metadata, info)

    launcher = os.path.join(macos, "Launch")
    with open(launcher, "wb") as handle:
        handle.write(b'''#!/bin/sh
resources="$(dirname "$0")/../Resources"
exec /usr/bin/python -u "$resources/shortcut_probe.py" "$resources/config.json" >> "$resources/probe.log" 2>&1
''')
    os.chmod(launcher, 0o700)
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shortcut_probe.py")
    shutil.copyfile(source, os.path.join(resources, "shortcut_probe.py"))
    with open(os.path.join(resources, "config.json"), "w") as handle:
        json.dump({
            "track": track,
            "targets": {"left": [left_x, left_y], "right": [right_x, right_y]},
        }, handle)
    print(bundle)


if __name__ == "__main__":
    main()
