#!/usr/bin/python
# -*- coding: utf-8 -*-
"""A native Music Stream library window for Mac OS X 10.6."""

import hashlib
import json
import os
import sys
import threading
import traceback
from Queue import Empty, Queue

import AppKit as A
from Foundation import NSIndexSet, NSObject, NSTimer, NSUserDefaults
from Quartz import CGDisplayPixelsHigh, CGEventCreate, CGEventGetLocation, CGMainDisplayID

from music_api import ApiError, MusicApi, TrackCache
from shortcut_probe import move_file, scratch_live_at


COLUMNS = (
    ("title", "Title", 208, True),
    ("artist", "Artist", 124, True),
    ("source_id", "Source", 57, True),
    ("playlists", "Known playlists", 143, True),
    ("bpm", "BPM", 50, True),
    ("key", "Key", 53, True),
    ("duration_seconds", "Length", 63, True),
    ("comment", "Comment", 170, True),
    ("album", "Album", 165, False),
    ("genre", "Genre", 100, False),
    ("filename", "Filename", 180, False),
    ("format", "Format", 65, False),
)
LEFT, RIGHT, TAB = 123, 124, 48
PREFERENCES = "local.music-stream.scratch-live"


def label(frame, title):
    control = A.NSTextField.alloc().initWithFrame_(frame)
    control.setStringValue_(title)
    control.setBezeled_(False)
    control.setDrawsBackground_(False)
    control.setEditable_(False)
    control.setSelectable_(False)
    return control


def button(frame, title, target, action):
    control = A.NSButton.alloc().initWithFrame_(frame)
    control.setTitle_(title)
    control.setBezelStyle_(A.NSRoundedBezelStyle)
    control.setTarget_(target)
    control.setAction_(action)
    return control


def column(identifier, title, width):
    item = A.NSTableColumn.alloc().initWithIdentifier_(identifier)
    item.headerCell().setStringValue_(title)
    item.setWidth_(width)
    item.setEditable_(False)
    return item


def display_value(track, field):
    value = track.get(field)
    if value is None:
        return u""
    if field == "duration_seconds":
        return u"%d:%02d" % divmod(int(value), 60)
    if field == "source_id":
        return {"navidrome": "NAV", "plex": "PLEX", "spotify": "SPOT"}.get(value.split("-")[0], value)
    if field == "playlists":
        return u", ".join(playlist["name"] for playlist in value) or u"—"
    return unicode(value)


class LibraryTable(A.NSTableView):
    def keyDown_(self, event):
        if self.controller.handle_key(self, event):
            return
        A.NSTableView.keyDown_(self, event)

    def mouseDown_(self, event):
        if self.controller.finish_drag(self, event):
            return
        A.NSTableView.mouseDown_(self, event)


class Companion(NSObject):
    def setup(self, config):
        self.config = config
        self.defaults = NSUserDefaults.standardUserDefaults()
        self.preferences = dict(self.defaults.persistentDomainForName_(PREFERENCES) or {})
        self.server = None
        self.cache = None
        self.groups = []
        self.search_groups = []
        self.search_active = False
        self.search_errors = []
        self.rows = []
        self.tracks = []
        self.playlist_order = []
        self.playlist = None
        self.serial = 0
        self.downloads = {}
        self.results = Queue()
        self.pending_drag = None
        self.sort_field = None
        self.ascending = True
        self.save_column_order = False
        self.make_window()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self, "drain:", None, True,
        )

    def make_window(self):
        width, height = 1050, 482
        self.window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            A.NSMakeRect(150, 70, width, height),
            A.NSTitledWindowMask | A.NSClosableWindowMask | A.NSMiniaturizableWindowMask | A.NSResizableWindowMask,
            A.NSBackingStoreBuffered, False,
        )
        self.window.setTitle_("Crate Revenant")
        self.window.setDelegate_(self)
        self.window.setMinSize_(A.NSMakeSize(760, 365))
        self.window.setLevel_(A.NSFloatingWindowLevel)
        root = self.window.contentView()
        top = A.NSViewMinYMargin
        self.url = A.NSTextField.alloc().initWithFrame_(A.NSMakeRect(12, height - 36, 241, 23))
        self.url.setToolTip_("Crate Revenant server URL")
        self.url.setStringValue_(
            self.config.get("server", "http://127.0.0.1:8080") if self.config.get("connect_on_launch")
            else self.preferences.get("ServerURL", self.config.get("server", "http://127.0.0.1:8080"))
        )
        self.token = A.NSSecureTextField.alloc().initWithFrame_(A.NSMakeRect(260, height - 36, 144, 23))
        self.token.cell().setPlaceholderString_("API token, if required")
        self.token.setToolTip_("Crate Revenant API token")
        self.connect_button = button(A.NSMakeRect(410, height - 39, 86, 27), "Connect", self, "connect:")
        self.health_button = button(A.NSMakeRect(499, height - 39, 70, 27), "Health", self, "health:")
        self.health_button.setToolTip_("Test the Windows API without checking any provider.")
        self.arm = button(A.NSMakeRect(581, height - 37, 190, 24), "Enable deck shortcuts", self, "armDecks:")
        self.arm.setButtonType_(A.NSSwitchButton)
        calibrated = bool(self.config.get("targets"))
        self.arm.setEnabled_(calibrated)
        if calibrated and self.preferences.get("DeckShortcutsEnabled", True):
            self.arm.setState_(A.NSOnState)
        self.arm.setToolTip_("Enable only for the calibrated Scratch Live view with Lock Playing Deck turned on.")
        for control in (self.url, self.token, self.connect_button, self.health_button, self.arm):
            control.setAutoresizingMask_(top)
            root.addSubview_(control)

        for x, text in ((12, "Playlists"), (275, "Tracks")):
            heading = label(A.NSMakeRect(x, height - 66, 175, 18), text)
            heading.setFont_(A.NSFont.boldSystemFontOfSize_(13))
            heading.setAutoresizingMask_(top)
            root.addSubview_(heading)

        self.search_field = A.NSSearchField.alloc().initWithFrame_(A.NSMakeRect(338, height - 69, 278, 22))
        self.search_field.cell().setPlaceholderString_("Search tracks and playlists")
        self.search_field.cell().setSendsWholeSearchString_(True)
        self.search_field.setTarget_(self)
        self.search_field.setAction_("search:")
        self.search_field.setAutoresizingMask_(top)
        self.search_field.setEnabled_(False)
        root.addSubview_(self.search_field)
        self.clear_search_button = button(
            A.NSMakeRect(622, height - 71, 65, 25), "Clear", self, "clearSearch:"
        )
        self.clear_search_button.setAutoresizingMask_(top)
        self.clear_search_button.setEnabled_(False)
        root.addSubview_(self.clear_search_button)

        self.playlists = self.attach_table(root, A.NSMakeRect(12, 55, 250, height - 124), False)
        self.playlists.addTableColumn_(column("playlist", "Playlists", 246))
        self.playlists.setHeaderView_(None)
        self.songs = self.attach_table(root, A.NSMakeRect(275, 55, width - 287, height - 124), True)
        for table in (self.playlists, self.songs):
            table.setDelegate_(self)
            table.setDataSource_(self)
        self.songs.setAllowsColumnReordering_(True)
        self.songs.setDraggingSourceOperationMask_forLocal_(A.NSDragOperationCopy, False)
        self.column_menu = A.NSMenu.alloc().initWithTitle_("Columns")
        for field, title, size, visible in COLUMNS:
            item = column(field, title, size)
            self.songs.addTableColumn_(item)
            menu_item = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "toggleColumn:", "")
            menu_item.setRepresentedObject_(field)
            menu_item.setTarget_(self)
            self.column_menu.addItem_(menu_item)
        self.update_columns()
        self.restore_column_order()
        self.save_column_order = True
        self.songs.headerView().setMenu_(self.column_menu)
        order = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Restore playlist order", "restoreOrder:", "")
        order.setTarget_(self)
        self.column_menu.addItem_(A.NSMenuItem.separatorItem())
        self.column_menu.addItem_(order)

        self.status = label(A.NSMakeRect(12, 16, width - 160, 22), "Connect to Crate Revenant.")
        self.status.setAutoresizingMask_(A.NSViewWidthSizable)
        root.addSubview_(self.status)
        self.download_button = button(A.NSMakeRect(width - 130, 12, 118, 29), "Download", self, "download:")
        self.download_button.setAutoresizingMask_(A.NSViewMinXMargin)
        self.download_button.setEnabled_(False)
        root.addSubview_(self.download_button)
        self.window.makeKeyAndOrderFront_(None)

    def attach_table(self, root, frame, horizontal):
        scroll = A.NSScrollView.alloc().initWithFrame_(frame)
        scroll.setBorderType_(A.NSBezelBorder)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(horizontal)
        mask = A.NSViewHeightSizable | (A.NSViewWidthSizable if horizontal else 0)
        scroll.setAutoresizingMask_(mask)
        table = LibraryTable.alloc().initWithFrame_(scroll.contentView().bounds())
        table.controller = self
        table.setUsesAlternatingRowBackgroundColors_(True)
        table.setAllowsMultipleSelection_(False)
        table.setAllowsEmptySelection_(True)
        table.setColumnAutoresizingStyle_(A.NSTableViewNoColumnAutoresizing)
        scroll.setDocumentView_(table)
        root.addSubview_(scroll)
        return table

    def note(self, message):
        self.status.setStringValue_(message)
        print(message.encode("utf-8") if isinstance(message, unicode) else message)
        sys.stdout.flush()

    def work(self, task, finish):
        def run():
            try:
                value, error = task(), None
            except Exception as exc:
                value = None
                if isinstance(exc, ApiError):
                    error = unicode(exc)
                else:
                    traceback.print_exc()
                    error = u"The operation failed. Check the companion log."
            self.results.put((finish, value, error))
        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()

    def drain_(self, _timer):
        while True:
            try:
                finish, value, error = self.results.get_nowait()
            except Empty:
                return
            finish(value, error)

    def connect_(self, _sender):
        try:
            server = MusicApi(unicode(self.url.stringValue()), unicode(self.token.stringValue()))
        except (ApiError, ValueError) as error:
            self.note(unicode(error))
            return
        self.serial += 1
        generation = self.serial
        self.connect_button.setEnabled_(False)
        self.server = None
        self.search_active = False
        self.search_groups = []
        self.search_field.setStringValue_("")
        self.search_field.setEnabled_(False)
        self.clear_search_button.setEnabled_(False)
        self.update_columns()
        self.groups, self.rows, self.tracks, self.playlist_order = [], [], [], []
        self.playlist = None
        self.playlists.reloadData()
        self.songs.reloadData()
        self.download_button.setEnabled_(False)
        self.note("Connecting...")

        def ready(groups, error):
            self.connect_button.setEnabled_(True)
            if generation != self.serial:
                return
            if error:
                self.note(error)
                return
            self.server = server
            self.search_field.setEnabled_(True)
            if not self.config.get("connect_on_launch"):
                self.save_preference("ServerURL", server.base)
            namespace = hashlib.sha256(server.base.encode("utf-8")).hexdigest()[:16]
            root = os.path.expanduser("~/Music/Music Stream Downloads")
            self.cache = TrackCache(root, namespace)
            self.groups = [(source, playlists, True) for source, playlists in groups]
            self.refresh_playlists()
            count = sum(len(playlists) for _, playlists in groups)
            self.note("%d playlists" % count if count else "No playlists are available.")
            if count:
                first = next(index for index, row in enumerate(self.rows) if row[1] is not None)
                self.select_row(self.playlists, first)
                self.window.makeFirstResponder_(self.playlists)

        self.work(server.playlists, ready)

    def health_(self, _sender):
        try:
            server = MusicApi(unicode(self.url.stringValue()), "")
        except (ApiError, ValueError) as error:
            self.note(unicode(error))
            return
        self.health_button.setEnabled_(False)
        self.note("Checking the Windows API...")

        def ready(version, error):
            self.health_button.setEnabled_(True)
            self.note(error or ("Windows API reachable (%s)." % version))

        self.work(server.health, ready)

    def search_(self, _sender):
        if not self.server:
            return
        query = unicode(self.search_field.stringValue()).strip()
        if not query:
            self.clearSearch_(None)
            return
        if len(query) > 200:
            self.note("Limit the search to 200 characters.")
            return
        self.serial += 1
        generation = self.serial
        server = self.server
        self.search_active = True
        self.search_groups = []
        self.search_errors = []
        self.playlist_order = []
        self.sort_field = None
        self.tracks = []
        self.refresh_playlists()
        self.songs.reloadData()
        self.update_columns()
        self.download_button.setEnabled_(False)
        self.clear_search_button.setEnabled_(True)
        self.window.makeFirstResponder_(self.songs)
        self.note(u"Searching for “%s”..." % query)

        def receive(event):
            self.results.put((show_packet, event, None))

        def show_packet(event, _error):
            if generation != self.serial:
                return
            if event.get("error"):
                self.search_errors.append(event["error"])
                return
            rows = event["rows"]
            if event["kind"] == "tracks":
                self.playlist_order.extend(rows)
                self.replace_tracks(self.tracks + rows)
                if self.songs.selectedRow() < 0:
                    self.select_row(self.songs, 0)
            elif event["kind"] == "playlists" and rows:
                source = dict(event["source"])
                source["name"] += u" · matching playlists"
                self.search_groups.append((source, rows, True))
                self.refresh_playlists()
            self.search_status("Searching")

        def finished(_value, error):
            if generation != self.serial:
                return
            if error:
                self.search_errors.append(error)
            self.search_status("Search complete")

        self.work(lambda: server.search(query, receive, lambda: generation != self.serial), finished)

    def search_status(self, label_text):
        count = sum(len(rows) for _, rows, _ in self.search_groups)
        message = u"%s — %d tracks, %d playlists" % (label_text, len(self.tracks), count)
        if self.search_errors:
            message += u". " + u"; ".join(self.search_errors)
        self.note(message)

    def clearSearch_(self, _sender):
        self.end_search(self.playlist)

    def end_search(self, target):
        if not self.search_active:
            return
        self.serial += 1
        self.search_active = False
        self.search_groups = []
        self.search_field.setStringValue_("")
        self.clear_search_button.setEnabled_(False)
        self.update_columns()
        self.playlist = None
        self.tracks = []
        self.playlist_order = []
        self.songs.reloadData()
        self.download_button.setEnabled_(False)
        if target:
            self.groups = [
                (source, rows, expanded or source["id"] == target[0])
                for source, rows, expanded in self.groups
            ]
        self.refresh_playlists()
        self.playlists.deselectAll_(None)
        for index, (group, playlist) in enumerate(self.rows):
            if playlist is not None and (self.groups[group][0]["id"], playlist["id"]) == target:
                self.select_row(self.playlists, index)
                break
        self.window.makeFirstResponder_(self.playlists)

    def visible_groups(self):
        return self.search_groups if self.search_active else self.groups

    def refresh_playlists(self):
        self.rows = []
        for index, (_, playlists, expanded) in enumerate(self.visible_groups()):
            self.rows.append((index, None))
            if expanded:
                self.rows.extend((index, playlist) for playlist in playlists)
        self.playlists.reloadData()

    def select_row(self, table, index):
        if 0 <= index < table.numberOfRows():
            table.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(index), False)
            table.scrollRowToVisible_(index)

    def numberOfRowsInTableView_(self, table):
        return len(self.rows) if table is self.playlists else len(self.tracks)

    def tableView_objectValueForTableColumn_row_(self, table, item, index):
        if table is self.playlists:
            group_index, playlist = self.rows[index]
            if playlist is None:
                source, _, expanded = self.visible_groups()[group_index]
                return (u"▾ " if expanded else u"▸ ") + source["name"]
            return u"    " + playlist["name"]
        return display_value(self.tracks[index], item.identifier())

    def tableViewSelectionDidChange_(self, notification):
        if notification.object() is self.songs:
            self.download_button.setEnabled_(self.selected_track() is not None)
            return
        row = self.playlists.selectedRow()
        if row < 0 or row >= len(self.rows) or not self.server or self.search_active:
            return
        group, playlist = self.rows[row]
        if playlist is None:
            return
        source = self.groups[group][0]
        key = (source["id"], playlist["id"])
        if self.playlist == key:
            return
        self.playlist = key
        self.serial += 1
        generation = self.serial
        server = self.server
        self.tracks, self.playlist_order = [], []
        self.sort_field = None
        self.songs.reloadData()
        self.download_button.setEnabled_(False)
        self.note("Loading playlist...")

        def ready(tracks, error):
            if generation != self.serial:
                return
            if error:
                self.playlist = None
                self.note(error)
                return
            self.tracks = list(tracks)
            self.playlist_order = list(tracks)
            self.songs.reloadData()
            self.select_row(self.songs, 0)
            self.note("%d tracks in %s" % (len(tracks), playlist["name"]))

        self.work(lambda: server.tracks(*key), ready)

    def handle_key(self, table, event):
        code = event.keyCode()
        if code == TAB:
            self.focus_songs() if table is self.playlists else self.window.makeFirstResponder_(self.playlists)
            return True
        if table is self.songs:
            if code in (LEFT, RIGHT) and event.modifierFlags() & A.NSShiftKeyMask:
                if not event.isARepeat():
                    self.start_download("left" if code == LEFT else "right")
                return True
            if code == LEFT:
                self.window.makeFirstResponder_(self.playlists)
                return True
            return False
        if code not in (LEFT, RIGHT):
            return False
        row = self.playlists.selectedRow()
        if not 0 <= row < len(self.rows):
            return True
        index, playlist = self.rows[row]
        if playlist is not None and code == RIGHT and self.search_active:
            self.end_search((self.search_groups[index][0]["id"], playlist["id"]))
            self.focus_songs()
        elif playlist is not None and code == RIGHT:
            self.focus_songs()
        elif playlist is None:
            groups = self.visible_groups()
            source, playlists, expanded = groups[index]
            next_state = code == RIGHT
            if next_state != expanded:
                groups[index] = (source, playlists, next_state)
                self.refresh_playlists()
                self.select_row(self.playlists, self.rows.index((index, None)))
        elif code == LEFT:
            self.select_row(self.playlists, self.rows.index((index, None)))
        return True

    def focus_songs(self):
        if self.songs.selectedRow() < 0:
            self.select_row(self.songs, 0)
        self.window.makeFirstResponder_(self.songs)

    def selected_track(self):
        row = self.songs.selectedRow()
        return self.tracks[row] if 0 <= row < len(self.tracks) else None

    def armDecks_(self, _sender):
        if self.arm.state() != A.NSOnState:
            self.save_preference("DeckShortcutsEnabled", False)
            self.note("Deck shortcuts off. Download a track and drag its row to Scratch Live.")
            return
        if not self.deck_targets_visible():
            self.arm.setState_(A.NSOffState)
            self.note("Scratch Live is covered at a calibrated deck. Shortcuts are off.")
            return
        self.save_preference("DeckShortcutsEnabled", True)
        self.note("Deck shortcuts on for this layout. Use idle decks with Lock Playing Deck on.")
        self.focus_songs()

    def deck_targets_visible(self):
        targets = self.config.get("targets", {})
        return all(side in targets and scratch_live_at(tuple(targets[side])) for side in ("left", "right"))

    def download_(self, _sender):
        self.start_download(None)

    def start_download(self, side):
        track = self.selected_track()
        if not track or not self.cache or not self.server:
            self.note("Select a track first.")
            return
        if not track.get("audio_available"):
            self.note("This track has no downloadable audio.")
            return
        if side and self.arm.state() != A.NSOnState:
            side = None
        if self.downloads:
            self.note("A download is in progress. Please wait.")
            return
        cache, server, generation = self.cache, self.server, self.serial
        path = cache.cached(track)
        if path:
            self.ready_track(path, side, track, generation)
            return
        self.downloads[track["id"]] = True
        self.note("Downloading " + track["title"] + "...")
        last_percent = [-1]

        def progress(received, expected):
            percent = int(100 * received / expected) if expected else -1
            if percent >= last_percent[0] + 5:
                last_percent[0] = percent
                self.results.put((lambda value, _: self.note(value), "Downloading %d%%" % percent, None))

        def ready(value, error):
            self.downloads.pop(track["id"], None)
            if error:
                self.note(error)
            else:
                self.ready_track(value, side, track, generation)

        self.work(lambda: cache.download(server, track, progress), ready)

    def ready_track(self, path, side, track, generation):
        selected = self.selected_track()
        if (side and self.arm.state() == A.NSOnState and generation == self.serial
                and selected and selected["id"] == track["id"] and self.window.isKeyWindow()
                and self.window.firstResponder() is self.songs):
            self.begin_drag(path, side)
        else:
            self.note("Ready: " + track["title"] + ". Drag its row to Scratch Live.")

    def begin_drag(self, path, side):
        if not self.deck_targets_visible():
            self.arm.setState_(A.NSOffState)
            self.note("Deck obscured. Drag this downloaded track manually.")
            return
        if self.pending_drag:
            return
        target = tuple(self.config["targets"][side])
        row = self.songs.selectedRow()
        self.songs.scrollRowToVisible_(row)
        rect = self.songs.rectOfRow_(row)
        visible = self.songs.visibleRect()
        if not visible.size.height or not visible.size.width:
            return
        x = visible.origin.x + min(25, visible.size.width / 2)
        point = self.songs.convertPoint_toView_(A.NSMakePoint(x, rect.origin.y + 9), None)
        point = self.window.convertBaseToScreen_(point)
        start = (point.x, CGDisplayPixelsHigh(CGMainDisplayID()) - point.y)
        cursor = CGEventGetLocation(CGEventCreate(None))
        ready = threading.Event()
        self.pending_drag = (path, side, ready)
        self.note("Sending track to the " + side + " deck...")
        thread = threading.Thread(target=move_file, args=(start, target, (cursor.x, cursor.y), ready))
        thread.daemon = True
        thread.start()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.5, self, "checkDrag:", None, False,
        )

    def checkDrag_(self, _timer):
        if self.pending_drag and not self.pending_drag[2].isSet():
            self.pending_drag = None
            self.note("The deck shortcut did not start. Drag the track manually.")

    def finish_drag(self, table, event):
        if table is not self.songs or self.pending_drag is None:
            return False
        path, side, ready = self.pending_drag
        ready.set()
        point = table.convertPoint_fromView_(event.locationInWindow(), None)
        try:
            sent = table.dragFile_fromRect_slideBack_event_(
                path, A.NSMakeRect(point.x - 8, point.y - 8, 16, 16), False, event,
            )
            if sent:
                self.note("File drop sent to the " + side + " deck.")
            else:
                self.note("Scratch Live did not accept the drop. Drag the track manually.")
        finally:
            self.pending_drag = None
        return True

    def tableView_writeRowsWithIndexes_toPasteboard_(self, table, rows, pasteboard):
        if table is not self.songs or not rows.count() or not self.cache:
            return False
        track = self.tracks[rows.firstIndex()]
        path = self.cache.cached(track)
        if not path:
            self.note("Download the selected track before dragging.")
            return False
        pasteboard.declareTypes_owner_([A.NSFilenamesPboardType], None)
        return pasteboard.setPropertyList_forType_([path], A.NSFilenamesPboardType)

    def toggleColumn_(self, menu_item):
        field = menu_item.representedObject()
        column_item = self.songs.tableColumnWithIdentifier_(field)
        hidden = not column_item.isHidden()
        if hidden and sum(not item.isHidden() for item in self.songs.tableColumns()) == 1:
            return
        overrides = dict(self.preferences.get("ColumnVisibility", {}))
        overrides[unicode(field)] = not hidden
        self.save_preference("ColumnVisibility", overrides)
        self.update_columns()

    def update_columns(self):
        overrides = self.preferences.get("ColumnVisibility", {})
        previously_hidden = self.preferences.get("HiddenColumns", [])
        for field, _, _, visible in COLUMNS:
            shown = overrides.get(field, visible and field not in previously_hidden)
            if field == "playlists" and field not in overrides:
                shown = self.search_active
            item = self.songs.tableColumnWithIdentifier_(field)
            item.setHidden_(not shown)
            menu_item = next(entry for entry in self.column_menu.itemArray()
                             if entry.representedObject() == field)
            menu_item.setState_(A.NSOnState if shown else A.NSOffState)

    def restore_column_order(self):
        for position, field in enumerate(self.preferences.get("ColumnOrder", [])):
            index = self.songs.columnWithIdentifier_(field)
            if 0 <= index < self.songs.numberOfColumns() and position < self.songs.numberOfColumns():
                self.songs.moveColumn_toColumn_(index, position)

    def tableViewColumnDidMove_(self, notification):
        if notification.object() is self.songs and self.save_column_order:
            self.save_preference(
                "ColumnOrder", [unicode(item.identifier()) for item in self.songs.tableColumns()],
            )

    def save_preference(self, key, value):
        self.preferences[key] = value
        self.defaults.setPersistentDomain_forName_(self.preferences, PREFERENCES)

    def restoreOrder_(self, _sender):
        self.sort_field = None
        self.replace_tracks(list(self.playlist_order))

    def tableView_didClickTableColumn_(self, table, item):
        if table is not self.songs:
            return
        field = item.identifier()
        self.ascending = not self.ascending if self.sort_field == field else True
        self.sort_field = field

        def sort_value(track):
            value = track.get(field)
            if field in ("bpm", "duration_seconds"):
                return float(value or 0)
            return display_value(track, field).lower()

        present = [track for track in self.playlist_order if track.get(field) not in (None, "")]
        missing = [track for track in self.playlist_order if track.get(field) in (None, "")]
        self.replace_tracks(sorted(present, key=sort_value, reverse=not self.ascending) + missing)

    def replace_tracks(self, tracks):
        selected = self.selected_track()
        self.tracks = tracks
        self.songs.reloadData()
        if selected:
            matching = (index for index, item in enumerate(tracks) if item is selected)
            self.select_row(self.songs, next(matching, 0))

    def windowWillClose_(self, _notification):
        A.NSApplication.sharedApplication().terminate_(self)


def main():
    with open(sys.argv[1], "rb") as source:
        config = json.load(source)
    app = A.NSApplication.sharedApplication()
    controller = Companion.alloc().init()
    controller.setup(config)
    app.activateIgnoringOtherApps_(True)
    print("PID %d" % os.getpid())
    sys.stdout.flush()
    if config.get("connect_on_launch"):
        controller.connect_(None)
    app.run()


if __name__ == "__main__":
    main()
