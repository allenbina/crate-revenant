#!/usr/bin/python
"""Build a Snow Leopard .app bundle using the system Python and PyObjC."""

import json
import optparse
import os
import plistlib
import shutil


def main():
    parser = optparse.OptionParser("usage: %prog [options] OUTPUT.app")
    parser.add_option("--server", default="http://127.0.0.1:8080")
    parser.add_option("--deck-points", help="Calibrate left x,y and right x,y as four comma-separated integers.")
    parser.add_option("--connect", action="store_true", default=False, help="Connect on launch.")
    options, args = parser.parse_args()
    if len(args) != 1 or not args[0].endswith(".app") or os.path.exists(args[0]):
        parser.error("Specify a new output path ending in .app.")

    config = {"server": options.server, "connect_on_launch": options.connect}
    if options.deck_points:
        try:
            left_x, left_y, right_x, right_y = map(int, options.deck_points.split(","))
        except ValueError:
            parser.error("Enter four deck coordinates, for example 77,91,77,216.")
        if (left_x, left_y) == (right_x, right_y) or min(left_x, left_y, right_x, right_y) < 0:
            parser.error("The two deck targets must be different points on the main display.")
        config["targets"] = {"left": [left_x, left_y], "right": [right_x, right_y]}

    bundle = os.path.abspath(args[0])
    contents = os.path.join(bundle, "Contents")
    macos = os.path.join(contents, "MacOS")
    resources = os.path.join(contents, "Resources")
    os.makedirs(macos)
    os.mkdir(resources)
    metadata = {
        "CFBundleName": "Crate Revenant",
        "CFBundleDisplayName": "Crate Revenant",
        "CFBundleIdentifier": "local.music-stream.scratch-live",
        "CFBundleExecutable": "Launch",
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "10.6",
    }
    info = os.path.join(contents, "Info.plist")
    if hasattr(plistlib, "dumps"):
        with open(info, "wb") as output:
            output.write(plistlib.dumps(metadata))
    else:
        plistlib.writePlist(metadata, info)

    launcher = os.path.join(macos, "Launch")
    with open(launcher, "wb") as output:
        output.write(b'''#!/bin/sh
resources="$(dirname "$0")/../Resources"
exec /usr/bin/python -u "$resources/companion.py" "$resources/config.json" >> "$resources/companion.log" 2>&1
''')
    os.chmod(launcher, 0o700)
    here = os.path.dirname(os.path.abspath(__file__))
    probe = os.path.normpath(os.path.join(here, "..", "..", "prototypes", "scratch-live-shortcuts", "shortcut_probe.py"))
    for source in (os.path.join(here, "companion.py"), os.path.join(here, "music_api.py"), probe):
        shutil.copyfile(source, os.path.join(resources, os.path.basename(source)))
    with open(os.path.join(resources, "config.json"), "w") as output:
        json.dump(config, output)
    print(bundle)


if __name__ == "__main__":
    main()
