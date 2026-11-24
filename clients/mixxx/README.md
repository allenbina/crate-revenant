# Crate Revenant desktop companion

Run the companion beside Mixxx and enter the URL of the **Crate Revenant middleware**. Use the same Docker instance and Settings page that serve the Scratch Live companion. Enter the optional Crate Revenant API token if the middleware requires one. The companion remembers the URL and asks for the token again when reopened.

## Connect

- **Same machine.** Publish the middleware to loopback and enter `http://127.0.0.1:8080`. To override a saved LAN bind for this launch, run the following from the middleware checkout. The existing Docker volume keeps the saved providers.

  ```powershell
  $env:MUSIC_STREAM_BIND_IP = '127.0.0.1'
  docker compose up -d
  ```

- **Different machine.** Enter the middleware host's reachable LAN or tailnet URL and published port. If the machines are on separate networks, publish the port on the middleware host's tailnet address and set `MUSIC_STREAM_API_TOKEN` before connecting. Use that host's address in the companion. The middleware must also be able to reach Navidrome and Plex.

Provider credentials are entered at `/settings` on the middleware host.

## Load a track

1. Click **Connect**, expand a source to find its playlists, or search across sources.
2. Select a track and click **Prepare file** or double-click its row.
3. When the File column says **Ready**, drag the row onto the desired Mixxx deck waveform.

The companion sends a standard local-file drag. Serato DJ Pro and rekordbox document file drops into their libraries as well. For those apps, drop a ready row into the library or Collection, then load it from there. The companion keeps prepared files in its local application data directory so library references can continue to resolve.

## Run from source

Use Python 3.10 or newer for your OS and architecture. On Windows, start the packaged executable or run from this directory.

```text
python -m venv .venv
```

Run `.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux and macOS to install `-m pip install -r requirements.txt` and start `companion.py`.

Qt 6.12 provides wheels for Windows x64 and ARM64, macOS 14 or newer on Intel and Apple Silicon, and Linux x64 (glibc 2.34+) and ARM64 (glibc 2.39+). A Linux machine running Mixxx in Flatpak must let Mixxx read the companion's downloads directory. Use a build for your own OS and CPU when packaging.

For an in-app library pane, see the [native Mixxx preview](native/README.md). The desktop companion uses standard file drops and can also be used with other DJ applications.
