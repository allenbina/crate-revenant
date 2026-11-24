# Crate Revenant

Crate Revenant runs on Windows or another Docker host and connects to Navidrome and Plex. Clients use the API for playlist metadata and explicitly request each original audio file.

## Run with Docker Desktop

Start Docker Desktop with its Linux engine, then run from this directory.

```powershell
docker compose up --build -d
curl.exe --fail http://127.0.0.1:8080/api/v1/health
```

Open `http://127.0.0.1:8080/settings` after startup. The same container serves the settings page and the client API. Enter the Navidrome server URL, username and password, or the Plex server URL and token. **Test connection** checks access to music playlists. **Save settings** applies the settings immediately and stores them in the existing data volume. Leave the password or token blank to keep the existing value.

The health endpoint works before a provider is configured. To start Compose from PowerShell with Navidrome environment defaults, run `./Start-MusicStream.ps1`. To connect from the Scratch Live Mac, publish the API on the Windows laptop's LAN address and use the separate API token if configured. Browser login uses `music-stream` as the username and the same API token as its password.

Saved settings take precedence over environment defaults for the same provider.

## Connect a Spotify account

In the Spotify Developer Dashboard, add `http://127.0.0.1:8087/settings/spotify/callback` to the app's redirect URIs. On the middleware computer, open Settings, save the Spotify app client ID, and select **Connect Spotify** to approve playlist access. The middleware keeps the refresh token in the Docker data volume and shows the account's playlists as a flat list. Spotify catalog results and playlist tracks can be browsed once you connect the account. Spotify file delivery depends on Spotizerr.

Use the **Spotizerr** section on the same Settings page to save its base URL and test API access. For a Cloudflare Access protected endpoint, enter its service token client ID and secret. Enter the Spotizerr application token if the service also requires one. Saved secrets stay in the middleware data volume. Enter any required credentials again when changing the URL.

| Variable | Value |
| --- | --- |
| `NAVIDROME_URL` | Navidrome base URL reachable from the container, for example `http://navidrome.local:4533` |
| `NAVIDROME_USERNAME` | Navidrome login with access to the playlists and permission to download |
| `NAVIDROME_PASSWORD` | Password for that login |
| `PLEX_URL` | Plex Media Server base URL reachable from the container |
| `PLEX_TOKEN` | Plex token from an account with access to music playlists and permission to download |
| `MUSIC_STREAM_API_TOKEN` | Separate bearer token for Music Stream clients. Set this before exposing the API to other devices. |
| `MUSIC_STREAM_BIND_IP` | Defaults to `127.0.0.1`. For another tailnet device, set it to the **Windows laptop's** tailnet IPv4 address. |
| `MUSIC_STREAM_PORT` | Published host port. Defaults to `8080`. |
| `MUSIC_STREAM_OAUTH_PORT` | Loopback-only Spotify authorization port. Defaults to `8087`. Register the matching redirect URI if you change this. |
| `MUSIC_STREAM_CORS_ORIGINS` | Optional comma-separated exact origins of separately hosted browser clients, including scheme and port. |

If Infisical is configured for the laptop, inject the credentials into the Compose process at launch. The example assumes the selected Infisical folder contains the variables above that hold secrets and the other settings are already in your shell.

```powershell
infisical run --projectId=<project-id> --env=<environment> --path=<folder> -- docker compose up --build -d
```

Keep the Compose volume when recreating the container. Its SQLite database stores the mappings for playlist and track IDs. Audio passes through to the client without being written to disk by the API.

## Run directly on Windows

Use the same provider and client settings. Set the Uvicorn host and port explicitly when serving another device.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn music_stream.api:app --host 127.0.0.1 --port 8080
```

## Try the API

[The native Scratch Live companion](clients/scratch-live/README.md) can browse playlists, download tracks and send a completed file to a calibrated Scratch Live deck.

[The desktop companion](clients/mixxx/README.md) can search the same sources and drag a prepared local file onto a Mixxx deck.

Open `http://127.0.0.1:8080/` for the browser frontend. To use it on a different device, open the API's published address on that device. When an API token is configured, use `music-stream` as the browser username and the API token as its password. The browser uses HTTP Basic authentication. Use HTTPS or a private, encrypted network for authenticated browser access.

On the Snow Leopard Mac, browse to a playlist, click **Download** beside a track, and wait for the browser to finish the file. Use Finder to load the completed file into Scratch Live.

Open `http://127.0.0.1:8080/docs` for interactive requests. Use **Authorize** if the API bearer token is configured.

`GET /api/v1/search?q=...` delivers newline-delimited JSON from each configured source as it responds. Each record identifies a source, a result kind (`tracks` or `playlists`), and either `rows` or an `error`. The track rows use the normal opaque IDs, so a client can use the same download endpoint. The `playlists` list on a track contains known memberships from playlists Crate Revenant has opened. Search fetches metadata and keeps playlist summaries cached for up to five minutes.

1. Call `GET /api/v1/sources` and select `navidrome-home`.
2. List the source's playlists and use a returned playlist ID to list its tracks. Playlist entries retain their original order and duplicates.
3. Use the selected track ID with `GET /api/v1/tracks/{track_id}/audio` to download the original. A DJ client should wait for the full download before loading it into a deck.

[OpenSubsonic download endpoint](https://opensubsonic.github.io/docs/endpoints/download/)
