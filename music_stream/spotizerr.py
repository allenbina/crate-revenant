"""Queue single-track jobs in Spotizerr and locate their completed files."""

import asyncio
import re
from pathlib import Path, PurePosixPath

import httpx

from .navidrome import ProviderError
from .spotify import spotify_id


class Spotizerr:
    def __init__(self, client: httpx.AsyncClient, url: str, token: str = "",
                 *, access_id: str = "", access_secret: str = ""):
        self.client = client
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        if access_id and access_secret:
            self.headers.update({"CF-Access-Client-Id": access_id, "CF-Access-Client-Secret": access_secret})

    async def get(self, route: str) -> dict:
        try:
            response = await self.client.get(f"{self.url}/api/{route}", headers=self.headers, follow_redirects=False)
        except httpx.TimeoutException:
            raise ProviderError(504, "Spotizerr timed out") from None
        except httpx.RequestError:
            raise ProviderError(502, "Spotizerr is unreachable") from None
        if response.is_redirect or response.status_code in (401, 403):
            raise ProviderError(502, "Spotizerr requires access credentials or rejected the credentials provided.")
        if response.status_code == 404:
            raise ProviderError(404, "Spotizerr did not find this job") from None
        if response.status_code == 429:
            raise ProviderError(429, "Spotizerr is rate limiting requests")
        if not response.is_success:
            raise ProviderError(502, "Spotizerr returned an unexpected response")
        try:
            data = response.json()
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
        raise ProviderError(502, "Spotizerr returned an invalid job response")

    async def test_connection(self):
        data = await self.get("auth/status")
        if not isinstance(data.get("auth_enabled"), bool):
            raise ProviderError(502, "This URL did not return Spotizerr's authentication status.")
        if data["auth_enabled"] and data.get("authenticated") is not True:
            raise ProviderError(502, "Spotizerr requires a valid application token.")

    async def queue_track(self, track_id: str) -> str:
        data = await self.get(f"track/download/{spotify_id(track_id)}")
        return job_id(data.get("task_id"))

    async def completed_path(self, task_id: str, *, timeout: float = 240, interval: float = 2) -> str:
        route = f"history/{job_id(task_id)}"
        try:
            async with asyncio.timeout(timeout):
                while True:
                    try:
                        record = await self.get(route)
                    except ProviderError as error:
                        if error.status != 404:
                            raise
                    else:
                        status = record.get("status")
                        details = record.get("metadata") or {}
                        outcome = details.get("status_info") or {}
                        path = record.get("file_path") or outcome.get("final_path")
                        if status in ("completed", "skipped") and isinstance(path, str) and path:
                            return path
                        if status in ("failed", "cancelled", "canceled", "skipped"):
                            raise ProviderError(502, "Spotizerr did not produce a file for this track")
                    await asyncio.sleep(interval)
        except TimeoutError:
            raise ProviderError(504, "Spotizerr did not finish this track in time") from None


def job_id(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ProviderError(502, "Spotizerr returned an invalid job ID")
    return value


def mounted_download(server_path: str, server_root: str, mount: Path) -> Path:
    remote = PurePosixPath(server_path)
    root = PurePosixPath(server_root)
    try:
        relative = remote.relative_to(root)
        if not root.is_absolute() or not remote.is_absolute() or any(
            part in (".", "..") or "\\" in part or "\x00" in part for part in relative.parts
        ):
            raise ValueError
        boundary = mount.resolve(strict=True)
        path = boundary.joinpath(*relative.parts).resolve(strict=True)
        if path == boundary or not path.is_relative_to(boundary) or not path.is_file():
            raise ValueError
    except (OSError, RuntimeError, ValueError):
        raise ProviderError(502, "The completed Spotizerr file is unavailable in the shared downloads folder") from None
    return path
