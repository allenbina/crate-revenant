"""Music Stream requests and an on-disk cache for the Snow Leopard client."""

import errno
import json
import os
import re
import tempfile
import urllib2
from urllib import urlencode
from urlparse import urlsplit


class ApiError(Exception):
    pass


class NoRedirect(urllib2.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def segment(value):
    if not re.match(r"^[A-Za-z0-9_-]+\Z", value):
        raise ApiError("Unexpected identifier from Crate Revenant.")
    return value


class MusicApi(object):
    def __init__(self, url, token):
        parsed = urlsplit(url.strip())
        if (parsed.scheme != "http" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ApiError("Enter the Crate Revenant HTTP address, for example http://crate-revenant.local:8080.")
        self.base = parsed.geturl().rstrip("/")
        self.token = token
        self.opener = urllib2.build_opener(urllib2.ProxyHandler({}), NoRedirect())

    def request(self, path):
        headers = {"Authorization": "Bearer " + self.token} if self.token else {}
        request = urllib2.Request(self.base + "/api/v1" + path, headers=headers)
        try:
            return self.opener.open(request, timeout=20)
        except urllib2.HTTPError as error:
            try:
                status = error.code
                if status == 401:
                    raise ApiError("Crate Revenant rejected the API token.")
                if status == 404:
                    raise ApiError("This item is no longer available.")
                if status in (502, 504):
                    raise ApiError("Crate Revenant could not reach or authenticate to the music source.")
                raise ApiError("Crate Revenant returned HTTP %s." % status)
            finally:
                error.close()
        except (IOError, ValueError):
            raise ApiError("Could not connect to Crate Revenant.")

    def read_json(self, path):
        response = self.request(path)
        try:
            return json.load(response)
        except ValueError:
            raise ApiError("Crate Revenant returned an invalid response.")
        finally:
            response.close()

    def playlists(self):
        result = []
        for source in self.read_json("/sources"):
            path = "/sources/%s/playlists" % segment(source["id"])
            try:
                result.append((source, self.read_json(path)))
            except ApiError:
                result.append((dict(source, name=source["name"] + " (unavailable)"), []))
        return result

    def tracks(self, source_id, playlist_id):
        return self.read_json(
            "/sources/%s/playlists/%s/tracks" % (segment(source_id), segment(playlist_id))
        )

    def health(self):
        data = self.read_json("/health")
        if data.get("service") != "music-stream" or data.get("status") != "ok":
            raise ApiError("This address did not identify itself as the Crate Revenant API.")
        return data.get("api_version", "unknown")

    def search(self, query, receive, cancelled):
        response = self.request("/search?" + urlencode({"q": query.encode("utf-8")}))
        try:
            for line in response:
                if cancelled():
                    return
                try:
                    receive(json.loads(line))
                except ValueError:
                    raise ApiError("Crate Revenant returned an invalid search result.")
        finally:
            response.close()


class TrackCache(object):
    def __init__(self, directory, source_namespace):
        self.directory = os.path.join(directory, source_namespace)

    def revision_dir(self, track):
        size = track.get("size_bytes")
        revision = str(size) if isinstance(size, (int, long)) and size > 0 else "original"
        return os.path.join(self.directory, segment(track["id"]), revision)

    def cached(self, track):
        folder = self.revision_dir(track)
        try:
            with open(os.path.join(folder, ".completed"), "rb") as marker:
                filename = marker.read().decode("utf-8")
            if not filename or os.path.basename(filename) != filename or filename.startswith("."):
                return None
            path = os.path.join(folder, filename)
            return path if os.path.getsize(path) else None
        except (IOError, OSError, UnicodeError):
            return None

    def download(self, api, track, progress):
        existing = self.cached(track)
        if existing:
            return existing

        folder = self.revision_dir(track)
        try:
            os.makedirs(folder)
        except OSError as error:
            if error.errno != errno.EEXIST or not os.path.isdir(folder):
                raise
        suffix = track.get("format") or ""
        suffix = suffix if re.match(r"^[A-Za-z0-9]{1,12}\Z", suffix) else "audio"
        name = re.sub(ur'[<>:"/\\|?*\x00-\x1f]', u"_", track.get("title") or u"Track")
        filename = (name[:64].strip(" .") or u"Track") + "." + suffix
        path = os.path.join(folder, filename)
        response = api.request("/tracks/%s/audio" % segment(track["id"]))
        temporary = None
        marker_tmp = None
        try:
            length = response.info().get("Content-Length")
            expected = int(length) if length else track.get("size_bytes")
            received = 0
            fd, temporary = tempfile.mkstemp(dir=folder, prefix=".downloading-")
            with os.fdopen(fd, "wb") as output:
                while True:
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    received += len(chunk)
                    progress(received, expected)
            if not received or (expected and received != expected):
                raise ApiError("The download ended early. Try again.")
            try:
                # Preserve an existing local file if an earlier load added cue tags.
                os.link(temporary, path)
            except OSError as error:
                if error.errno != errno.EEXIST or not os.path.isfile(path):
                    raise
            fd, marker_tmp = tempfile.mkstemp(dir=folder, prefix=".complete-")
            with os.fdopen(fd, "wb") as marker:
                marker.write(filename.encode("utf-8"))
            os.rename(marker_tmp, os.path.join(folder, ".completed"))
            marker_tmp = None
            return path
        finally:
            response.close()
            for partial in (temporary, marker_tmp):
                if partial:
                    os.unlink(partial)
