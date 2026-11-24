"""Crate Revenant HTTP and local audio cache for the Mixxx companion."""

import hashlib
import json
import re
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class ClientError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class MusicClient:
    def __init__(self, url, token=""):
        try:
            parsed = urlsplit(url.strip())
            parsed.port
        except ValueError:
            raise ClientError("Enter a valid Crate Revenant server URL.") from None
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(character.isspace() for character in url)):
            raise ClientError("Enter a server URL, such as http://127.0.0.1:8080.")
        self.url = parsed.geturl().rstrip("/")
        self.token = token
        self.namespace = hashlib.sha256(self.url.encode()).hexdigest()[:16]
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def open(self, path, timeout=15):
        headers = {"Authorization": "Bearer " + self.token} if self.token else {}
        try:
            return self.opener.open(Request(self.url + "/api/v1" + path, headers=headers), timeout=timeout)
        except HTTPError as error:
            with error:
                if error.code in (401, 403):
                    raise ClientError("Check the Crate Revenant API token.") from None
                try:
                    detail = json.load(error).get("detail")
                except (ValueError, AttributeError):
                    detail = None
                message = detail if isinstance(detail, str) else f"Crate Revenant returned HTTP {error.code}."
                raise ClientError(message) from None
        except (URLError, OSError, ValueError):
            raise ClientError("Could not reach the Crate Revenant server.") from None

    def read(self, path):
        with self.open(path) as response:
            try:
                return json.load(response)
            except ValueError:
                raise ClientError("Crate Revenant returned invalid metadata.") from None

    def health(self):
        response = self.read("/health")
        if response.get("service") != "music-stream" or response.get("status") != "ok":
            raise ClientError("This address did not return Crate Revenant's health status.")

    def playlists(self, source_id):
        return self.read(f"/sources/{quote(source_id, safe='')}/playlists")

    def tracks(self, source_id, playlist_id):
        return self.read(f"/sources/{quote(source_id, safe='')}/playlists/{quote(playlist_id, safe='')}/tracks")

    def search(self, query, cancelled):
        with self.open("/search?" + urlencode({"q": query})) as response:
            for line in response:
                if cancelled.is_set():
                    return
                if line.strip():
                    yield json.loads(line)

    def download(self, track, root):
        identity = hashlib.sha256(track["id"].encode()).hexdigest()[:24]
        folder = Path(root) / self.namespace / identity
        folder.mkdir(parents=True, exist_ok=True)
        existing = next((path for path in folder.iterdir()
                         if path.suffix != ".part" and path.is_file() and path.stat().st_size), None)
        if existing:
            return existing
        route = f"/tracks/{quote(track['id'], safe='')}/audio"
        temporary = None
        try:
            with self.open(route, timeout=60) as response:
                filename = response.headers.get_filename() or track.get("filename") or ""
                suffix = Path(filename).suffix.lstrip(".") or track.get("format") or ""
                if not re.fullmatch(r"[A-Za-z0-9]{1,12}", suffix):
                    raise ClientError("The server did not identify a file format Mixxx can open.")
                title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", track["title"]).strip(" .")[:90].rstrip(" .")
                if not title or re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", title):
                    title = "Track " + title
                destination = folder / (title + "." + suffix.lower())
                size = 0
                with tempfile.NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as output:
                    temporary = Path(output.name)
                    while chunk := response.read(1024 * 256):
                        output.write(chunk)
                        size += len(chunk)
                length = response.headers.get("Content-Length")
                if not size or (length and int(length) != size):
                    raise ClientError("The audio download was incomplete.")
            if not destination.exists():
                try:
                    temporary.replace(destination)
                except PermissionError:
                    if not destination.is_file():
                        raise
            return destination
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
