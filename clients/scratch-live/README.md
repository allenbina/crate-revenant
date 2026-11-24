# Crate Revenant for Scratch Live

Native Mac client for Scratch Live 2.5 on OS X 10.6.8. It runs with the Mac's bundled Python and PyObjC. The Crate Revenant API runs separately on Windows or another host.

## Build and connect

From this directory on the Mac, run:

```sh
/usr/bin/python build_app.py "$HOME/Desktop/Crate Revenant.app"
open "$HOME/Desktop/Crate Revenant.app"
```

Enter the middleware's local HTTP address. If it uses an API token, enter that token in the companion. The Navidrome login belongs on the middleware. Use a private connection to the API, such as an SSH tunnel. The companion remembers the server address and keeps the API token for the current session only.

**Health** checks only the Crate Revenant API at the entered address. **Connect** opens the music sources.

## Browse and load

- **Tab** switches panes. In the playlist pane, **Right** expands a source or enters the tracks of the selected playlist. **Left** returns to or collapses the source. In the track pane, **Left** returns to the playlists.
- Enter a search and press **Return**. Track results appear on the right with the provider and known playlist memberships. Matching playlist names appear on the left. Press **Right** on a playlist to open it, or click **Clear** to return to the previous playlist.
- Click a column to sort and drag headers to reorder. Right-click a header to show or hide columns or restore playlist order.
- **Download** saves the selected file to `~/Music/Music Stream Downloads`. Scratch Live can load it by dragging the downloaded row straight out of Crate Revenant.
- **Shift+Left** and **Shift+Right** request the selected track. With deck shortcuts enabled, they also send the completed file to the corresponding Scratch Live deck.

Downloads are explicit. The app keeps completed files at stable paths so Scratch Live can find them again. Source size changes get a separate cache entry. Cue points and tag changes made by Scratch Live stay with the existing local copy.

## Calibrate deck shortcuts

Enable **Lock Playing Deck** in Scratch Live's Playback settings. For the tested 1280 × 800 full-screen stacked view, the deck targets were `77,91` and `77,216`. Coordinates are measured from the top-left of the main display. Pass the two points when building the app:

```sh
/usr/bin/python build_app.py --deck-points 77,91,77,216 "$HOME/Desktop/Crate Revenant.app"
```

**Enable deck shortcuts** starts checked on a calibrated install. Clearing it saves your preference. Before sending a standard file drag, the companion checks that Scratch Live is visible at both calibrated points. It relies on Scratch Live's own lock to protect a playing deck. Use manual drag if you switch views or change the screen layout.
