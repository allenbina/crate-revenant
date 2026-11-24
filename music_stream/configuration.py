import json
import os
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Connection:
    url: str = ""
    username: str = ""
    secret: str = ""

    def validate(self, kind: str):
        validate_url(self.url)
        if not self.secret or (kind == "navidrome" and not self.username):
            raise ValueError("Enter a username and password." if kind == "navidrome" else "Enter a Plex token.")
        if kind == "plex" and not self.secret.isascii():
            raise ValueError("Enter the Plex token as shown in Plex.")
        if kind == "plex" and any(character.isspace() for character in self.secret):
            raise ValueError("Plex tokens do not contain spaces.")


@dataclass(frozen=True)
class SpotifySettings:
    client_id: str = ""
    refresh_token: str = ""
    account_scope: str = ""


@dataclass(frozen=True)
class SpotizerrSettings:
    url: str = ""
    access_id: str = ""
    access_secret: str = ""
    token: str = ""

    def validate(self):
        validate_url(self.url)
        if bool(self.access_id) != bool(self.access_secret):
            raise ValueError("Enter both Cloudflare Access service token fields.")
        if self.access_id and urlsplit(self.url).scheme != "https":
            raise ValueError("Use an HTTPS URL with a Cloudflare Access service token.")
        for value in (self.access_id, self.access_secret, self.token):
            if any(not 33 <= ord(character) <= 126 for character in value):
                raise ValueError("Credentials must contain printable characters without spaces.")


def validate_url(url: str):
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError:
        raise ValueError("Enter a valid HTTP(S) server URL.") from None
    if (
        parsed.scheme not in ("http", "https") or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment
        or any(character.isspace() for character in url)
    ):
        raise ValueError("Enter an HTTP(S) base URL without credentials or a query.")


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self.defaults = {
            "navidrome": Connection(
                os.getenv("NAVIDROME_URL", "").strip(), os.getenv("NAVIDROME_USERNAME", ""),
                os.getenv("NAVIDROME_PASSWORD", ""),
            ),
            "plex": Connection(os.getenv("PLEX_URL", "").strip(), secret=os.getenv("PLEX_TOKEN", "").strip()),
            "spotify": SpotifySettings(os.getenv("SPOTIFY_CLIENT_ID", "").strip()),
            "spotizerr": SpotizerrSettings(),
        }
        try:
            self.saved = {
                kind: type(self.defaults[kind])(**value)
                for kind, value in json.loads(path.read_text(encoding="utf-8")).items()
                if kind in self.defaults
            }
        except FileNotFoundError:
            self.saved = {}
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("The saved provider settings file is invalid.") from None

    def get(self, kind: str) -> Connection | SpotifySettings | SpotizerrSettings:
        return self.saved.get(kind, self.defaults[kind])

    def save(self, kind: str, config: Connection | SpotifySettings | SpotizerrSettings):
        updated = self.saved | {kind: config}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".provider-settings-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump({key: asdict(value) for key, value in updated.items()}, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.saved = updated
