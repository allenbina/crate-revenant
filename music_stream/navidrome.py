import hashlib
import secrets
import time

import httpx


class ProviderError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


class Navidrome:
    id = "navidrome-home"
    name = "Home Navidrome"
    kind = "navidrome"

    def __init__(self, client: httpx.AsyncClient, url: str, username: str, password: str):
        self.client = client
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.scope = hashlib.sha256(f"{self.url}\0{username}".encode()).hexdigest()
        self.playlist_cache: tuple[float, list[dict]] | None = None

    def request(self, method: str, **params: str) -> httpx.Request:
        salt = secrets.token_hex(16)
        # Subsonic's salted-token protocol specifies MD5.
        token = hashlib.md5(f"{self.password}{salt}".encode(), usedforsecurity=False).hexdigest()
        return self.client.build_request(
            "GET",
            f"{self.url}/rest/{method}.view",
            params=dict(u=self.username, s=salt, t=token, v="1.16.1", c="music-stream", f="json", **params),
            headers={"Accept-Encoding": "identity"},
        )

    async def send(self, method: str, *, stream: bool = False, **params: str) -> httpx.Response:
        try:
            response = await self.client.send(self.request(method, **params), stream=stream)
        except httpx.TimeoutException:
            raise ProviderError(504, "Navidrome timed out") from None
        except httpx.RequestError:
            raise ProviderError(502, "Navidrome is unreachable") from None
        if response.status_code != 200:
            await response.aclose()
            if response.status_code == 404:
                raise ProviderError(404, "Item was not found on Navidrome")
            raise ProviderError(502, "Navidrome returned an unexpected HTTP status")
        return response

    async def metadata(self, method: str, **params: str) -> dict:
        response = await self.send(method, **params)
        return self.read_metadata(response)

    @staticmethod
    def read_metadata(response: httpx.Response) -> dict:
        try:
            payload = response.json()["subsonic-response"]
            if payload["status"] == "ok":
                return payload
            code = payload.get("error", {}).get("code")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ProviderError(502, "Navidrome returned invalid metadata") from None
        if code == 70:
            raise ProviderError(404, "Item was not found on Navidrome")
        if code == 40:
            raise ProviderError(502, "Navidrome rejected the configured username or password")
        if code == 50:
            raise ProviderError(502, "The Navidrome account is not permitted to perform this operation")
        if code in (41, 42, 43, 44):
            raise ProviderError(502, "Navidrome rejected the backend authentication method")
        raise ProviderError(502, "Navidrome could not complete the request")

    async def playlists(self, *, cached: bool = False) -> list[dict]:
        if cached and self.playlist_cache is not None:
            updated, entries = self.playlist_cache
            if time.monotonic() - updated < 300:
                return entries
        payload = await self.metadata("getPlaylists")
        entries = payload.get("playlists", {}).get("playlist", [])
        self.playlist_cache = (time.monotonic(), entries)
        return entries

    async def search_tracks(self, query: str, limit: int) -> list[dict]:
        payload = await self.metadata(
            "search3", query=query, songCount=str(limit), artistCount="0", albumCount="0"
        )
        return payload.get("searchResult3", {}).get("song", [])

    async def playlist_tracks(self, playlist_id: str) -> list[dict]:
        payload = await self.metadata("getPlaylist", id=playlist_id)
        return payload["playlist"].get("entry", [])

    async def track(self, track_id: str) -> dict:
        payload = await self.metadata("getSong", id=track_id)
        return payload["song"]

    async def download(self, track_id: str, _entry: dict) -> httpx.Response:
        response = await self.send("download", stream=True, id=track_id)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type.startswith("audio/") or content_type in (
            "", "application/octet-stream", "application/ogg", "application/x-flac",
        ):
            return response
        try:
            if content_type == "application/json":
                await response.aread()
                self.read_metadata(response)
            raise ProviderError(502, "Navidrome did not return an audio file")
        finally:
            await response.aclose()
