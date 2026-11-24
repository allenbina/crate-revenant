"""Spotify account authorization and playlist metadata."""

import asyncio
import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode

import httpx

from .navidrome import ProviderError


ACCOUNTS = "https://accounts.spotify.com"
CATALOG = "https://api.spotify.com/v1"
PLAYLIST_SCOPES = "playlist-read-private playlist-read-collaborative"


def authorize_url(client_id: str, redirect_uri: str, state: str) -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": PLAYLIST_SCOPES,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    return f"{ACCOUNTS}/authorize?{urlencode(params)}", verifier


class Spotify:
    id = "spotify-home"
    kind = "spotify"
    name = "Spotify"
    capabilities = ["playlists", "search"]

    def __init__(self, client: httpx.AsyncClient, client_id: str, refresh_token: str = "",
                 *, scope: str = "", save_refresh=None):
        self.client = client
        self.client_id = client_id
        self.refresh_token = refresh_token
        self.save_refresh = save_refresh
        self.scope = scope
        self.playlist_cache = None
        self.access_token = ""
        self.expires_at = 0.0
        self.lock = asyncio.Lock()

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> str:
        data = await self.request_token({
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
        })
        refresh = data.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            raise ProviderError(502, "Spotify did not return a refresh token")
        return refresh

    async def request_token(self, fields: dict) -> dict:
        try:
            response = await self.client.post(
                f"{ACCOUNTS}/api/token", data={**fields, "client_id": self.client_id},
            )
        except httpx.RequestError:
            raise ProviderError(502, "Spotify authorization is unreachable") from None
        if response.status_code in (400, 401, 403):
            raise ProviderError(502, "Spotify rejected authorization. Connect Spotify again.") from None
        return metadata(response)

    async def bearer(self) -> str:
        async with self.lock:
            if self.access_token and time.monotonic() < self.expires_at:
                return self.access_token
            data = await self.request_token({"grant_type": "refresh_token", "refresh_token": self.refresh_token})
            token, expires = data.get("access_token"), data.get("expires_in")
            if not isinstance(token, str) or not token or not isinstance(expires, int) or expires <= 0:
                raise ProviderError(502, "Spotify returned an invalid access token")
            refresh = data.get("refresh_token")
            if isinstance(refresh, str) and refresh and refresh != self.refresh_token:
                if self.save_refresh:
                    self.save_refresh(refresh)
                self.refresh_token = refresh
            self.access_token = token
            self.expires_at = time.monotonic() + max(0, expires - 30)
            return token

    async def get(self, path: str, **params) -> dict:
        token = await self.bearer()
        try:
            response = await self.client.get(f"{CATALOG}{path}", params=params,
                                             headers={"Authorization": f"Bearer {token}"})
        except httpx.RequestError:
            raise ProviderError(502, "Spotify is unreachable") from None
        if response.status_code == 401:
            async with self.lock:
                if self.access_token == token:
                    self.expires_at = 0
            raise ProviderError(502, "Spotify rejected the connection")
        if response.status_code in (403, 404):
            raise ProviderError(response.status_code, "Spotify did not grant access to this item")
        return metadata(response)

    async def pages(self, path: str, **params):
        offset = 0
        while True:
            data = await self.get(path, **params, limit=50, offset=offset)
            rows = data.get("items")
            if not isinstance(rows, list):
                raise ProviderError(502, "Spotify returned an invalid playlist page")
            yield object_rows(rows)
            if not rows or not data.get("next"):
                return
            offset += len(rows)

    async def playlists(self, *, cached: bool = False) -> list[dict]:
        if cached and self.playlist_cache and time.monotonic() - self.playlist_cache[0] < 300:
            return self.playlist_cache[1]
        entries = {}
        async for page in self.pages("/me/playlists"):
            for row in page:
                if row.get("id") and row.get("name"):
                    entries.setdefault(row["id"], {
                        "id": row["id"], "name": row["name"],
                        "songCount": (row.get("items") or row.get("tracks") or {}).get("total"),
                    })
        playlists = list(entries.values())
        self.playlist_cache = (time.monotonic(), playlists)
        return playlists

    async def playlist_tracks(self, playlist_id: str) -> list[dict]:
        return [
            normalize(row)
            async for page in self.pages(f"/playlists/{spotify_id(playlist_id)}/items")
            for item in page
            if (row := item.get("item") or item.get("track")) and is_track(row)
        ]

    async def search_tracks(self, query: str, limit: int) -> list[dict]:
        result = []
        offset = 0
        while offset < limit:
            size = min(10, limit - offset)
            data = await self.get("/search", q=query, type="track", limit=size, offset=offset)
            tracks = data.get("tracks") or {}
            result.extend(normalize(row) for row in object_rows(tracks.get("items", [])) if is_track(row))
            if not tracks.get("next"):
                break
            offset += size
        return result

    async def track(self, track_id: str) -> dict:
        row = await self.get(f"/tracks/{spotify_id(track_id)}")
        if not is_track(row):
            raise ProviderError(404, "Spotify did not return a music track")
        return normalize(row)


def object_rows(rows: list) -> list[dict]:
    return [row for row in rows if isinstance(row, dict)]


def metadata(response: httpx.Response) -> dict:
    if response.status_code == 429:
        raise ProviderError(429, "Spotify is rate limiting requests. Try again shortly.")
    if not response.is_success:
        raise ProviderError(502, "Spotify returned an unexpected response")
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    raise ProviderError(502, "Spotify returned invalid metadata")


def spotify_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9]{22}", value):
        raise ProviderError(404, "Invalid Spotify item ID")
    return value


def is_track(row) -> bool:
    return isinstance(row, dict) and row.get("type") == "track" and not row.get("is_local") and bool(
        row.get("id") and row.get("name")
    )


def normalize(row: dict) -> dict:
    duration = row.get("duration_ms")
    return {
        "id": row["id"],
        "title": row["name"],
        "artist": ", ".join(artist["name"] for artist in object_rows(row.get("artists") or []) if artist.get("name")),
        "album": (row.get("album") or {}).get("name"),
        "duration": duration // 1000 if isinstance(duration, int) else None,
        "spotify_url": f"https://open.spotify.com/track/{spotify_id(row['id'])}",
        "audio_available": False,
    }
