import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
import unicodedata
from contextlib import asynccontextmanager, closing
from dataclasses import replace
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote

import httpx
from anyio import CancelScope
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer

from . import browser
from .configuration import Connection, Settings, SpotifySettings, SpotizerrSettings
from .navidrome import Navidrome, ProviderError
from .plex import Plex
from .spotify import Spotify, authorize_url
from .spotizerr import Spotizerr


Provider = Navidrome | Plex | Spotify
PROVIDER_TYPES = {"navidrome": Navidrome, "plex": Plex}
SPOTIFY_CALLBACK = "/settings/spotify/callback"
SPOTIFY_STATE_COOKIE = "crate_spotify_state"
SPOTIFY_AUTH_LIFETIME = 600
AUTH = HTTPBearer(auto_error=False)
BROWSER_AUTH = HTTPBasic(auto_error=False, realm="Music Stream")


class ItemIds:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS items "
                "(id TEXT PRIMARY KEY, source TEXT, kind TEXT, provider_id TEXT, scope TEXT)"
            )
            self.needs_legacy_scope = "scope" not in {column[1] for column in db.execute("PRAGMA table_info(items)")}
            if self.needs_legacy_scope:
                db.execute("ALTER TABLE items ADD COLUMN scope TEXT")
            db.execute(
                "CREATE TABLE IF NOT EXISTS playlist_names "
                "(scope TEXT, playlist_id TEXT, name TEXT, PRIMARY KEY (scope, playlist_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS playlist_members "
                "(scope TEXT, playlist_id TEXT, track_id TEXT, PRIMARY KEY (scope, playlist_id, track_id))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS memberships_by_track ON playlist_members (scope, track_id)"
            )

    def adopt_legacy_navidrome(self, provider: Navidrome):
        if self.needs_legacy_scope:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute(
                    "UPDATE items SET scope = ? WHERE source = ? AND scope IS NULL "
                    "AND (NOT EXISTS (SELECT 1 FROM playlist_names) "
                    "OR EXISTS (SELECT 1 FROM playlist_names WHERE scope = ?))",
                    (provider.scope, provider.id, provider.scope),
                )

    def remember(self, provider: Provider, kind: str, entries: list[dict]) -> list[str]:
        rows = []
        with closing(sqlite3.connect(self.path)) as db, db:
            for item in entries:
                provider_id = str(item["id"])
                key = f"{provider.id}:{kind}:{provider_id}"
                item_id = hashlib.sha256(f"{provider.scope}:{key}".encode()).hexdigest()[:32]
                if provider.id == Navidrome.id:
                    previous = hashlib.sha256(key.encode()).hexdigest()[:32]
                    if db.execute("SELECT 1 FROM items WHERE id = ? AND scope = ?", (previous, provider.scope)).fetchone():
                        item_id = previous
                rows.append((item_id, provider.id, kind, provider_id, provider.scope))
            db.executemany(
                "INSERT OR IGNORE INTO items (id, source, kind, provider_id, scope) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        return [row[0] for row in rows]

    def resolve(self, kind: str, item_id: str) -> tuple[str, str | None, str]:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT source, scope, provider_id FROM items WHERE id = ? AND kind = ?",
                (item_id, kind),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown item")
        return row

    def update_playlist_names(self, scope: str, rows: list[dict]):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM playlist_names WHERE scope = ?", (scope,))
            db.executemany(
                "INSERT INTO playlist_names VALUES (?, ?, ?)",
                ((scope, row["id"], row["name"]) for row in rows),
            )
            db.execute(
                "DELETE FROM playlist_members WHERE scope = ? AND playlist_id NOT IN "
                "(SELECT playlist_id FROM playlist_names WHERE scope = ?)",
                (scope, scope),
            )

    def update_playlist_members(self, scope: str, playlist_id: str, track_ids: list[str]):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM playlist_members WHERE scope = ? AND playlist_id = ?", (scope, playlist_id))
            db.executemany(
                "INSERT OR IGNORE INTO playlist_members VALUES (?, ?, ?)",
                ((scope, playlist_id, track_id) for track_id in track_ids),
            )

    def known_playlists(self, scope: str, track_ids: list[str]) -> dict:
        if not track_ids:
            return {}
        parameters = ",".join("?" for _ in track_ids)
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT m.track_id, p.playlist_id, p.name FROM playlist_members m "
                "JOIN playlist_names p USING (scope, playlist_id) "
                f"WHERE m.scope = ? AND m.track_id IN ({parameters}) ORDER BY p.name, p.playlist_id",
                (scope, *track_ids),
            ).fetchall()
        result = {}
        for track_id, playlist_id, name in rows:
            result.setdefault(track_id, []).append({"id": playlist_id, "name": name})
        return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = Path(os.getenv("MUSIC_STREAM_DB", "data/music-stream.sqlite3"))
    app.state.token = os.getenv("MUSIC_STREAM_API_TOKEN", "")
    app.state.ids = ItemIds(db_path)
    app.state.settings = Settings(db_path.with_name("provider-settings.local.json"))
    app.state.csrf = secrets.token_urlsafe(32)
    app.state.providers = {}
    app.state.spotify_pending = {}
    oauth_port = int(os.getenv("MUSIC_STREAM_OAUTH_PORT", "8087"))
    if not 1 <= oauth_port <= 65535:
        raise RuntimeError("MUSIC_STREAM_OAUTH_PORT must be a valid port.")
    app.state.spotify_origin = f"http://127.0.0.1:{oauth_port}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), trust_env=False) as client:
        app.state.upstream_client = client
        for kind in PROVIDER_TYPES:
            config = app.state.settings.get(kind)
            if not config.url:
                continue
            try:
                provider = make_provider(client, kind, config)
            except ValueError:
                logging.getLogger(__name__).warning("Incomplete or invalid %s provider settings", kind)
                continue
            app.state.providers[provider.id] = provider
            if isinstance(provider, Navidrome):
                app.state.ids.adopt_legacy_navidrome(provider)
        spotify = app.state.settings.get("spotify")
        if spotify.client_id and spotify.refresh_token and spotify.account_scope:
            app.state.providers[Spotify.id] = make_spotify(app.state, spotify)
        yield


def make_provider(client: httpx.AsyncClient, kind: str, config: Connection) -> Provider:
    config.validate(kind)
    if kind == "navidrome":
        return Navidrome(client, config.url, config.username, config.secret)
    return Plex(client, config.url, config.secret)


def make_spotify(state, config: SpotifySettings) -> Spotify:
    def save_refresh(refresh: str):
        previous = state.settings.get("spotify")
        if previous.account_scope != config.account_scope:
            return
        try:
            state.settings.save("spotify", replace(previous, refresh_token=refresh))
        except OSError:
            raise ProviderError(500, "Could not save Spotify authorization") from None

    return Spotify(state.upstream_client, config.client_id, config.refresh_token,
                   scope=config.account_scope, save_refresh=save_refresh)


app = FastAPI(title="Crate Revenant API", version="0.1.0", lifespan=lifespan)
origins = [origin.strip() for origin in os.getenv("MUSIC_STREAM_CORS_ORIGINS", "").split(",") if origin.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET"],
        allow_headers=["Authorization"],
        expose_headers=["Content-Disposition", "ETag", "Last-Modified"],
    )


@app.exception_handler(ProviderError)
async def provider_error(request: Request, exc: ProviderError):
    if request.url.path.startswith("/browse/"):
        return browser.page(
            "Music source unavailable",
            f'<p>{escape(exc.message)}</p><p><a href="/">Back to music sources</a></p>',
            status=exc.status,
        )
    return JSONResponse({"detail": exc.message}, status_code=exc.status)


async def authorize(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(AUTH)
):
    expected = request.app.state.token
    if expected and (
        credentials is None or not hmac.compare_digest(credentials.credentials.encode(), expected.encode())
    ):
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})


router = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])


async def authorize_browser(
    request: Request, credentials: HTTPBasicCredentials | None = Depends(BROWSER_AUTH)
):
    expected = request.app.state.token
    if expected and (
        credentials is None
        or credentials.username != "music-stream"
        or not hmac.compare_digest(credentials.password.encode(), expected.encode())
    ):
        raise HTTPException(
            401, "Music Stream login required", headers={"WWW-Authenticate": 'Basic realm="Music Stream"'}
        )


browse = APIRouter(dependencies=[Depends(authorize_browser)], include_in_schema=False)


@app.get("/api/v1/health")
async def health():
    return {"service": "music-stream", "api_version": "v1", "status": "ok"}


def source(request: Request, source_id: str) -> Provider:
    provider = request.app.state.providers.get(source_id)
    if provider is None:
        raise HTTPException(404, "Unknown source")
    return provider


def describe(provider: Provider) -> dict:
    return {
        "id": provider.id,
        "provider": provider.kind,
        "name": provider.name,
        "capabilities": Spotify.capabilities if isinstance(provider, Spotify) else ["playlists", "original_audio", "search"],
    }


@router.get("/sources")
async def sources(request: Request):
    return [describe(provider) for provider in request.app.state.providers.values()]


@router.get("/sources/{source_id}/playlists")
async def playlists(request: Request, source_id: str):
    return await playlist_rows(request.app.state.ids, source(request, source_id))


async def playlist_rows(ids: ItemIds, provider: Provider, *, cached: bool = False) -> list[dict]:
    entries = await provider.playlists(cached=cached)
    rows = [
        {"id": item_id, "name": entry["name"], "track_count": entry.get("songCount")}
        for item_id, entry in zip(ids.remember(provider, "playlist", entries), entries)
    ]
    ids.update_playlist_names(provider.scope, rows)
    return rows


def track_row(provider: Provider, item_id: str, entry: dict) -> dict:
    path = entry.get("path")
    row = {
        "id": item_id,
        "source_id": provider.id,
        "title": entry["title"],
        "artist": entry.get("artist"),
        "album": entry.get("album"),
        "duration_seconds": entry.get("duration"),
        "format": entry.get("suffix"),
        "size_bytes": entry.get("size"),
        "bpm": entry.get("bpm"),
        "key": entry.get("initialKey"),
        "comment": entry.get("comment"),
        "genre": entry.get("genre"),
        "filename": re.split(r"[/\\]", path)[-1] if path else None,
        "audio_available": entry.get("audio_available", True),
    }
    if entry.get("spotify_url"):
        row["spotify_url"] = entry["spotify_url"]
    return row


@router.get("/sources/{source_id}/playlists/{playlist_id}/tracks")
async def playlist_tracks(request: Request, source_id: str, playlist_id: str):
    provider = source(request, source_id)
    ids = request.app.state.ids
    mapped_source, scope, provider_id = ids.resolve("playlist", playlist_id)
    if mapped_source != provider.id or scope != provider.scope:
        raise HTTPException(404, "Unknown playlist for the configured source")
    entries = await provider.playlist_tracks(provider_id)
    track_ids = ids.remember(provider, "track", entries)
    ids.update_playlist_members(provider.scope, playlist_id, track_ids)
    return [track_row(provider, item_id, entry) for item_id, entry in zip(track_ids, entries)]


@router.get("/search", response_class=StreamingResponse)
async def search(request: Request, q: str = Query(min_length=1, max_length=200), limit: int = Query(30, ge=1, le=100)):
    query = q.strip()
    if not query:
        raise HTTPException(422, "Enter a search term")
    ids = request.app.state.ids
    configured = tuple(request.app.state.providers.values())

    async def search_tracks(adapter: Provider):
        entries = await adapter.search_tracks(query, limit)
        track_ids = ids.remember(adapter, "track", entries)
        membership = ids.known_playlists(adapter.scope, track_ids)
        return [
            track_row(adapter, item_id, entry) | {"playlists": membership.get(item_id, [])}
            for item_id, entry in zip(track_ids, entries)
        ]

    async def search_playlists(adapter: Provider):
        terms = query.casefold().split()
        rows = await playlist_rows(ids, adapter, cached=True)
        return [row for row in rows if all(term in row["name"].casefold() for term in terms)][:limit]

    async def packet(adapter, kind, operation):
        event = {"source": describe(adapter), "kind": kind}
        try:
            async with asyncio.timeout(12):
                event["rows"] = await operation(adapter)
        except TimeoutError:
            event["error"] = f"{adapter.name} timed out"
        except ProviderError as error:
            event["error"] = error.message
        except (TypeError, ValueError, KeyError, AttributeError):
            event["error"] = f"{adapter.name} returned invalid search metadata"
        return json.dumps(event, ensure_ascii=False) + "\n"

    async def events():
        tasks = [
            asyncio.create_task(packet(adapter, kind, operation))
            for adapter in configured
            for kind, operation in (("tracks", search_tracks), ("playlists", search_playlists))
        ]
        try:
            for result in asyncio.as_completed(tasks):
                yield await result
        finally:
            for task in tasks:
                task.cancel()
            with CancelScope(shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)

    return StreamingResponse(
        events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"}
    )


@router.get("/tracks/{track_id}")
async def track_metadata(request: Request, track_id: str):
    provider, provider_id = track_source(request, track_id)
    entry = await provider.track(provider_id)
    return track_row(provider, track_id, entry)


def track_source(request: Request, track_id: str) -> tuple[Provider, str]:
    source_id, scope, provider_id = request.app.state.ids.resolve("track", track_id)
    provider = source(request, source_id)
    if scope != provider.scope:
        raise HTTPException(404, "Unknown track for the configured source")
    return provider, provider_id


@router.get("/tracks/{track_id}/audio", response_class=StreamingResponse)
async def track_audio(request: Request, track_id: str):
    provider, provider_id = track_source(request, track_id)
    if isinstance(provider, Spotify):
        raise HTTPException(409, "Connect Spotizerr for Spotify audio.")
    entry = await provider.track(provider_id)
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", entry.get("title") or "track").strip(" .")[:120] or "track"
    suffix = entry.get("suffix")
    extension = f".{suffix}" if suffix and re.fullmatch(r"[A-Za-z0-9]{1,12}", suffix) else ""
    filename = title + extension
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    ascii_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", ascii_title).strip(" .") or "track"
    upstream = await provider.download(provider_id, entry)
    headers = {
        name: upstream.headers[name]
        for name in ("content-length", "content-type", "etag", "last-modified")
        if name in upstream.headers
    }
    headers.setdefault("content-type", "application/octet-stream")
    if "content-length" not in headers and isinstance(entry.get("size"), int) and entry["size"] > 0:
        headers["content-length"] = str(entry["size"])
    headers["content-disposition"] = (
        f"""attachment; filename="{ascii_title}{extension}"; filename*=UTF-8''{quote(filename, safe='')}"""
    )
    headers["cache-control"] = "no-store"
    headers["x-content-type-options"] = "nosniff"

    async def audio():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        except httpx.RequestError:
            # Abort the response without logging upstream URLs with auth parameters.
            logging.getLogger(__name__).warning("%s audio transfer interrupted", provider.name)
            raise RuntimeError("Provider audio transfer interrupted") from None
        finally:
            with CancelScope(shield=True):
                await upstream.aclose()

    return StreamingResponse(audio(), headers=headers)


@browse.get("/", response_class=HTMLResponse)
async def browser_sources(request: Request):
    return browser.source_list(await sources(request))


@browse.get("/settings", response_class=HTMLResponse)
async def provider_settings(request: Request):
    return render_settings(request.app.state)


def render_settings(state, **kwargs):
    return browser.settings_page(state.settings, state.csrf, spotify_origin=state.spotify_origin, **kwargs)


async def parse_settings(request: Request):
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Expected a settings form")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 8192:
            raise HTTPException(413, "Settings form is too large")
        body.extend(chunk)
    try:
        form = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=6, errors="strict")
    except ValueError:
        raise HTTPException(400, "Invalid settings form") from None
    state = request.app.state
    if not hmac.compare_digest(form.get("csrf", [""])[0].encode(), state.csrf.encode()):
        raise HTTPException(403, "Reload the settings page and try again")
    return form


@browse.post("/settings/spotify", response_class=HTMLResponse)
async def update_spotify(request: Request):
    form = await parse_settings(request)
    state = request.app.state
    client_id = form.get("client_id", [""])[0].strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}", client_id):
        return render_settings(state, kind="spotify", notice="Enter a Spotify app client ID.", status=422)
    previous = state.settings.get("spotify")
    if client_id != previous.client_id:
        try:
            state.settings.save("spotify", SpotifySettings(client_id))
        except OSError:
            return render_settings(state, kind="spotify", notice="Could not write to the settings volume.", status=500)
        state.providers.pop(Spotify.id, None)
        state.spotify_pending.clear()
    return render_settings(state, kind="spotify", notice="Client ID saved. Connect Spotify in this browser.")


@browse.post("/settings/spotizerr", response_class=HTMLResponse)
async def update_spotizerr(request: Request):
    form = await parse_settings(request)
    state = request.app.state
    url = form.get("url", [""])[0].strip().rstrip("/")
    saved = state.settings.get("spotizerr")
    prior = saved if saved.url == url else SpotizerrSettings()
    access_id = form.get("access_id", [""])[0].strip()
    access_secret = form.get("access_secret", [""])[0].strip()
    if access_id == prior.access_id or not access_id:
        access_secret = access_secret or prior.access_secret
    config = SpotizerrSettings(
        url=url,
        access_id=access_id or prior.access_id,
        access_secret=access_secret,
        token=form.get("token", [""])[0].strip() or prior.token,
    )

    def page(message: str, status: int = 200):
        return render_settings(state, kind="spotizerr", draft=config, notice=message, status=status)

    try:
        config.validate()
    except ValueError as error:
        return page(str(error), 422)
    action = form.get("action", [""])[0]
    if action == "test":
        client = Spotizerr(state.upstream_client, config.url, config.token,
                           access_id=config.access_id, access_secret=config.access_secret)
        try:
            async with asyncio.timeout(12):
                await client.test_connection()
        except TimeoutError:
            return page("Spotizerr timed out.", 504)
        except ProviderError as error:
            return page(error.message, error.status)
        return page("Connection works. Spotizerr accepted the API request.")
    if action != "save":
        return page("Choose Save settings or Test connection.", 400)
    try:
        state.settings.save("spotizerr", config)
    except OSError:
        return page("Could not write to the settings volume.", 500)
    return page("Settings saved.")


@browse.get("/settings/spotify/connect")
async def connect_spotify(request: Request):
    state = request.app.state
    if f"{request.url.scheme}://{request.url.netloc}" != state.spotify_origin:
        return RedirectResponse(state.spotify_origin + request.url.path, headers={"Cache-Control": "no-store"})
    config = state.settings.get("spotify")
    if not config.client_id:
        return render_settings(state, kind="spotify", notice="Save the Spotify app client ID first.", status=422)
    now = time.monotonic()
    state.spotify_pending = {
        key: pending for key, pending in state.spotify_pending.items() if pending[2] > now
    }
    if len(state.spotify_pending) >= 32:
        raise HTTPException(429, "Too many pending Spotify connections. Try again shortly.")
    nonce = secrets.token_urlsafe(32)
    url, verifier = authorize_url(config.client_id, state.spotify_origin + SPOTIFY_CALLBACK, nonce)
    state.spotify_pending[nonce] = (config.client_id, verifier, now + SPOTIFY_AUTH_LIFETIME)
    response = RedirectResponse(url, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.set_cookie(SPOTIFY_STATE_COOKIE, nonce, max_age=SPOTIFY_AUTH_LIFETIME,
                        httponly=True, samesite="lax", path=SPOTIFY_CALLBACK)
    return response


@browse.get(SPOTIFY_CALLBACK, response_class=HTMLResponse)
async def spotify_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    runtime = request.app.state
    if f"{request.url.scheme}://{request.url.netloc}" != runtime.spotify_origin:
        raise HTTPException(400, "Open the Spotify connection from the Windows middleware host")
    cookie = request.cookies.get(SPOTIFY_STATE_COOKIE, "")
    if not state or not hmac.compare_digest(state.encode(), cookie.encode()):
        raise HTTPException(403, "Spotify connection expired. Start again from Settings.")
    pending = runtime.spotify_pending.pop(state, None)
    config = runtime.settings.get("spotify")
    if not pending or pending[0] != config.client_id or pending[2] <= time.monotonic():
        raise HTTPException(403, "Spotify connection expired. Start again from Settings.")
    if error or not code:
        return render_settings(runtime, kind="spotify", notice="Spotify access was not granted.", status=400)
    client = Spotify(runtime.upstream_client, config.client_id)
    try:
        refresh = await client.exchange(code, pending[1], runtime.spotify_origin + SPOTIFY_CALLBACK)
        if runtime.settings.get("spotify").client_id != config.client_id:
            raise HTTPException(409, "Spotify configuration changed. Start again from Settings.")
        saved = replace(config, refresh_token=refresh, account_scope=secrets.token_hex(16))
        runtime.settings.save("spotify", saved)
    except ProviderError as exc:
        return render_settings(runtime, kind="spotify", notice=exc.message, status=exc.status)
    except OSError:
        return render_settings(runtime, kind="spotify", notice="Could not save Spotify authorization.", status=500)
    runtime.providers[Spotify.id] = make_spotify(runtime, saved)
    response = RedirectResponse("/settings", status_code=303,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.delete_cookie(SPOTIFY_STATE_COOKIE, path=SPOTIFY_CALLBACK)
    return response


@browse.post("/settings/{kind}", response_class=HTMLResponse)
async def update_provider_settings(request: Request, kind: str):
    if kind not in PROVIDER_TYPES:
        raise HTTPException(404, "Unknown provider")
    form = await parse_settings(request)
    state = request.app.state

    prior = state.settings.get(kind)
    action = form.get("action", [""])[0]
    secret = form.get("secret", [""])[0]
    config = Connection(
        url=form.get("url", [""])[0].strip().rstrip("/"),
        username=form.get("username", [""])[0].strip() if kind == "navidrome" else "",
        secret=(secret.strip() if kind == "plex" else secret) or prior.secret,
    )

    def page(message, status=200):
        return render_settings(state, kind=kind, draft=config, notice=message, status=status)

    if action not in ("test", "save"):
        return page("Choose Save settings or Test connection.", 400)
    try:
        provider = make_provider(state.upstream_client, kind, config)
    except ValueError as error:
        return page(str(error), 422)
    if action == "test":
        try:
            async with asyncio.timeout(12):
                rows = await provider.playlists()
        except TimeoutError:
            return page(f"{provider.name} timed out.", 504)
        except ProviderError as error:
            return page(error.message, error.status)
        return page(f"Connection works. Found {len(rows)} music playlist{'s' if len(rows) != 1 else ''}.")

    try:
        state.settings.save(kind, config)
    except OSError:
        logging.getLogger(__name__).exception("Could not save provider settings")
        return page("Could not write to the settings volume.", 500)
    state.providers[provider.id] = provider
    return page("Settings saved.")


@browse.get("/browse/{source_id}", response_class=HTMLResponse)
async def browser_playlists(request: Request, source_id: str):
    return browser.playlist_list(source_id, await playlists(request, source_id))


@browse.get("/browse/{source_id}/playlists/{playlist_id}", response_class=HTMLResponse)
async def browser_tracks(request: Request, source_id: str, playlist_id: str):
    return browser.track_list(source_id, await playlist_tracks(request, source_id, playlist_id))


@browse.get("/browse/tracks/{track_id}/download", response_class=StreamingResponse)
async def browser_download(request: Request, track_id: str):
    return await track_audio(request, track_id)


app.include_router(router)
app.include_router(browse)
