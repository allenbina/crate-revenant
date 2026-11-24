from html import escape

from fastapi.responses import HTMLResponse


def page(title: str, content: str, *, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} | Crate Revenant</title>
<style>
body {{ margin: 0; background: #f5f3ee; color: #202b26; font: 16px Arial, sans-serif; }}
.frame {{ max-width: 52em; margin: 0 auto; padding: 1.5em; }}
.masthead {{ background: #153e35; color: #fff; }}
.masthead a {{ color: #fff; font-size: 1.3em; font-weight: bold; text-decoration: none; }}
.masthead a.settings {{ float: right; font-size: 1em; }}
h1 {{ font-size: 1.8em; margin: .45em 0 .65em; }}
a {{ color: #145848; }}
ul {{ margin: 0; padding: 0; list-style: none; }}
li, td, th {{ border-bottom: 1px solid #c5cbc4; padding: .7em .55em; }}
table {{ width: 100%; border-collapse: collapse; text-align: left; word-wrap: break-word; }}
th {{ font-size: .85em; }}
.action {{ white-space: nowrap; }}
fieldset {{ margin: 0 0 1.5em; padding: .8em; border: 1px solid #c5cbc4; }}
legend {{ font-weight: bold; }}
label {{ display: block; margin: .65em 0; }}
input {{ display: block; width: 94%; max-width: 32em; margin-top: .25em; font: inherit; }}
button {{ margin: .4em .4em .1em 0; font: inherit; }}
.notice {{ padding: .65em; background: #e5ede6; }}
.error {{ background: #f4e1de; }}
</style>
</head>
<body>
<div class="masthead"><div class="frame"><a href="/">Crate Revenant</a><a class="settings" href="/settings">Settings</a></div></div>
<div class="frame" role="main">
<h1>{escape(title)}</h1>
{content}
</div>
</body>
</html>""",
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def settings_page(settings, csrf: str, *, spotify_origin="", kind=None, draft=None, notice=None, status: int = 200):
    config_sections = []
    for provider, title, secret_label in (
        ("navidrome", "Navidrome", "Password"), ("plex", "Plex", "Token"),
    ):
        stored = settings.get(provider)
        value = draft if kind == provider and draft is not None else stored
        result = ""
        if provider == kind and notice:
            style = "notice error" if status >= 400 else "notice"
            result = f'<p class="{style}" role="status">{escape(notice)}</p>'
        user_field = (
            f'<label>Username<input name="username" value="{escape(value.username)}" autocomplete="username" required></label>'
            if provider == "navidrome" else ""
        )
        placeholder = f"Leave blank to keep the saved {secret_label.lower()}" if stored.secret else ""
        config_sections.append(
            f"""<form action="/settings/{provider}" method="post">
<fieldset><legend>{title}</legend>
{result}
<input type="hidden" name="csrf" value="{escape(csrf)}">
<label>Server URL<input type="url" name="url" value="{escape(value.url)}" required></label>
{user_field}
<label>{secret_label}<input type="password" name="secret" autocomplete="new-password" placeholder="{placeholder}"></label>
<button name="action" value="save">Save settings</button>
<button name="action" value="test">Test connection</button>
</fieldset>
</form>"""
        )
    spotify = settings.get("spotify")
    spotify_notice = ""
    if kind == "spotify" and notice:
        style = "notice error" if status >= 400 else "notice"
        spotify_notice = f'<p class="{style}" role="status">{escape(notice)}</p>'
    connection = ""
    if spotify.client_id and spotify_origin:
        label = "Reconnect Spotify" if spotify.refresh_token else "Connect Spotify"
        connection = f'<p><a href="{escape(spotify_origin)}/settings/spotify/connect">{label}</a></p>'
    config_sections.append(f"""<form action="/settings/spotify" method="post">
<fieldset><legend>Spotify</legend>
{spotify_notice}
<input type="hidden" name="csrf" value="{escape(csrf)}">
<label>Spotify app client ID<input name="client_id" value="{escape(spotify.client_id)}" required></label>
<button>Save client ID</button>
{connection}
</fieldset>
</form>""")
    spotizerr = settings.get("spotizerr")
    spotizerr_value = draft if kind == "spotizerr" and draft is not None else spotizerr
    spotizerr_notice = ""
    if kind == "spotizerr" and notice:
        style = "notice error" if status >= 400 else "notice"
        spotizerr_notice = f'<p class="{style}" role="status">{escape(notice)}</p>'
    same_server = spotizerr_value.url == spotizerr.url
    token_placeholder = "Leave blank to keep saved token" if same_server and spotizerr.token else ""
    config_sections.append(f"""<form action="/settings/spotizerr" method="post">
<fieldset><legend>Spotizerr</legend>
{spotizerr_notice}
<input type="hidden" name="csrf" value="{escape(csrf)}">
<label>Server URL<input type="url" name="url" value="{escape(spotizerr_value.url)}" required></label>
<label>Application token (optional)<input type="password" name="token" autocomplete="new-password" placeholder="{token_placeholder}"></label>
<button name="action" value="save">Save settings</button>
<button name="action" value="test">Test connection</button>
</fieldset>
</form>""")
    return page("Provider settings", "".join(config_sections), status=status)


def source_list(sources: list[dict]) -> HTMLResponse:
    rows = [
        f'<li><a href="/browse/{escape(source["id"])}">{escape(source["name"])}</a></li>'
        for source in sources
    ]
    return page("Music sources", f"<ul>{''.join(rows)}</ul>" if rows else "<p>No music source is configured.</p>")


def playlist_list(source_id: str, playlists: list[dict]) -> HTMLResponse:
    rows = []
    for playlist in playlists:
        href = f'/browse/{escape(source_id)}/playlists/{escape(playlist["id"])}'
        name = escape(playlist["name"])
        rows.append(f'<li><a href="{href}">{name}</a></li>')
    return page("Playlists", f"<ul>{''.join(rows)}</ul>" if rows else "<p>No playlists are available.</p>")


def track_list(source_id: str, tracks: list[dict]) -> HTMLResponse:
    rows = []
    for track in tracks:
        href = f'/browse/tracks/{escape(track["id"])}/download'
        title = escape(track["title"])
        artist = escape(track["artist"] or "")
        action = f'<a href="{href}" aria-label="Download {title}">Download</a>'
        if track.get("spotify_url") and not track["audio_available"]:
            action = f'<a href="{escape(track["spotify_url"])}">Open in Spotify</a>'
        rows.append(
            f'<tr><td>{title}</td><td>{artist}</td>'
            f'<td class="action">{action}</td></tr>'
        )
    back = f'<p><a href="/browse/{escape(source_id)}">Back to playlists</a></p>'
    if not rows:
        return page("Playlist tracks", back + "<p>This playlist is empty.</p>")
    return page(
        "Playlist tracks",
        back + '<table><thead><tr><th scope="col">Track</th><th scope="col">Artist</th>'
        f'<th scope="col">File</th></tr></thead><tbody>{"".join(rows)}</tbody></table>',
    )
