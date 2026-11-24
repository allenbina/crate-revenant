import hashlib
import re
import time
from urllib.parse import unquote, urlsplit

import httpx

from .navidrome import ProviderError


class Plex:
    id = "plex-home"
    name = "Home Plex"
    kind = "plex"

    def __init__(self, client: httpx.AsyncClient, url: str, token: str):
        self.client = client
        self.url = url.rstrip("/")
        self.token = token
        self.scope = hashlib.sha256(f"plex\0{self.url}\0{token}".encode()).hexdigest()
        self.playlist_cache: tuple[float, list[dict]] | None = None

    async def send(self, path: str, *, stream: bool = False, **params) -> httpx.Response:
        request = self.client.build_request(
            "GET", self.url + path, params=params,
            headers={
                "Accept": "*/*" if stream else "application/json",
                "Accept-Encoding": "identity",
                "X-Plex-Token": self.token,
                "X-Plex-Product": "Crate Revenant",
                "X-Plex-Client-Identifier": "crate-revenant-" + self.scope[:16],
            },
        )
        try:
            response = await self.client.send(request, stream=stream)
        except httpx.TimeoutException:
            raise ProviderError(504, "Plex timed out") from None
        except httpx.RequestError:
            raise ProviderError(502, "Plex is unreachable") from None
        if response.status_code != 200:
            await response.aclose()
            if response.status_code in (401, 403):
                raise ProviderError(502, "Plex rejected the token or denied access")
            if response.status_code == 404:
                raise ProviderError(404, "Item was not found on Plex")
            raise ProviderError(502, "Plex returned an unexpected HTTP status")
        return response

    async def metadata(self, path: str, **params) -> dict:
        response = await self.send(path, **params)
        try:
            container = response.json()["MediaContainer"]
            if not isinstance(container, dict):
                raise ValueError
            return container
        except (ValueError, KeyError, TypeError):
            raise ProviderError(502, "Plex returned invalid metadata") from None

    async def playlists(self, *, cached: bool = False) -> list[dict]:
        if cached and self.playlist_cache is not None:
            updated, entries = self.playlist_cache
            if time.monotonic() - updated < 300:
                return entries
        payload = await self.metadata("/playlists", playlistType="audio", type=15)
        result = [
            {"id": str(row["ratingKey"]), "name": row["title"], "songCount": row.get("leafCount")}
            for row in payload.get("Metadata", [])
            if row.get("playlistType") == "audio" and row.get("ratingKey") and row.get("title")
        ]
        self.playlist_cache = (time.monotonic(), result)
        return result

    async def playlist_tracks(self, playlist_id: str) -> list[dict]:
        payload = await self.metadata(f"/playlists/{plex_id(playlist_id)}/items", type=10)
        return [normalize(row) for row in tracks(payload.get("Metadata", []))]

    async def search_tracks(self, query: str, limit: int) -> list[dict]:
        try:
            payload = await self.metadata("/hubs/search", query=query, limit=limit)
        except ProviderError as error:
            if error.status == 404:
                return []
            raise
        matches = {}
        for hub in payload.get("Hub", []):
            for row in tracks(hub.get("Metadata", [])):
                matches.setdefault(str(row["ratingKey"]), normalize(row))
        return list(matches.values())[:limit]

    async def track(self, track_id: str) -> dict:
        payload = await self.metadata(f"/library/metadata/{plex_id(track_id)}")
        match = next(tracks(payload.get("Metadata", [])), None)
        if match is None:
            raise ProviderError(404, "Track was not found on Plex")
        return normalize(match)

    async def download(self, _track_id: str, entry: dict) -> httpx.Response:
        path = entry.get("part")
        if not path or not safe_part(path):
            raise ProviderError(404, "Plex did not provide an original audio file for this track")
        response = await self.send(path, stream=True, download=1)
        media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if media_type.startswith("audio/") or media_type in ("", "application/octet-stream", "application/ogg"):
            return response
        await response.aclose()
        raise ProviderError(502, "Plex did not return an audio file")


def plex_id(value: str) -> str:
    if not re.fullmatch(r"[0-9]+", value):
        raise ProviderError(404, "Unknown Plex item")
    return value


def tracks(entries: list[dict]):
    return (
        row for row in entries if row.get("type") == "track" and row.get("ratingKey") and row.get("title")
    )


def safe_part(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        return False
    path = unquote(parsed.path)
    return (
        not (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment)
        and path.startswith("/library/parts/")
        and re.fullmatch(r"/library/parts/[0-9]+(?:/[0-9]+)?/[^/\\\x00-\x1f]+", path) is not None
        and not any(segment in (".", "..") for segment in path.split("/"))
    )


def normalize(row: dict) -> dict:
    part, container = {}, None
    for media in row.get("Media", []):
        parts = media.get("Part", [])
        if len(parts) == 1 and parts[0].get("key") and safe_part(parts[0]["key"]):
            part, container = parts[0], media.get("container")
            break
    duration = row.get("duration")
    filename = part.get("file")
    suffix = re.split(r"[/\\]", filename)[-1].rpartition(".")[2] if filename else None
    return {
        "id": str(row["ratingKey"]),
        "title": row["title"],
        "artist": row.get("originalTitle") or row.get("grandparentTitle"),
        "album": row.get("parentTitle"),
        "duration": duration // 1000 if isinstance(duration, int) else None,
        "suffix": suffix or part.get("container") or container,
        "size": part.get("size"),
        "path": filename,
        "part": part.get("key"),
        "audio_available": bool(part) or "Media" not in row,
    }
