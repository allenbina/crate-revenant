# Scratch Live deck shortcut prototype

This one-track AppKit prototype tests keyboard handoff to Scratch Live 2.5.0.11 on OS X 10.6.8. It uses the Mac's bundled Python and PyObjC.

On the test Mac, Scratch Live was using its full-screen 1280 × 800 stacked two-deck view. Both decks were idle. Adjust the two deck coordinates if the view or screen size changes. The points are measured from the top-left of the main display.

```sh
/usr/bin/python build_probe.py "$HOME/Desktop/Music Stream Shortcut Prototype.app" \
  "$HOME/Downloads/example-track.mp3" 77 91 77 216
open "$HOME/Desktop/Music Stream Shortcut Prototype.app"
```

Put focus in the prototype window. Press **Shift+Left** or **Shift+Right** to send the selected local file to that deck. The prototype starts a standard AppKit file drag using a synthesized pointer gesture. Keep the target area visible and use idle decks.

The prototype checks which app is at the target coordinate. A full companion needs reliable deck targeting across Scratch Live views and a check that the selected deck is safe to load.
