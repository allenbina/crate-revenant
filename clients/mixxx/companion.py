"""Crate Revenant companion for desktop DJ software."""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from PySide6.QtCore import QMimeData, QObject, QSettings, QStandardPaths, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from client import ClientError, MusicClient


ROLE = Qt.ItemDataRole.UserRole
COLUMNS = ("Title", "Artist", "Source", "Length", "BPM", "Key", "Playlists", "File")


class Dispatch(QObject):
    finished = Signal(object, object, object)

    @Slot(object, object, object)
    def deliver(self, callback, value, error):
        callback(value, error)


class TrackTable(QTableWidget):
    def __init__(self, window):
        super().__init__(0, len(COLUMNS))
        self.window = window
        self.setHorizontalHeaderLabels(COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        for column, width in enumerate((240, 152, 100, 65, 60, 60, 155, 77)):
            self.setColumnWidth(column, width)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionsMovable(True)

    def selected_track(self):
        item = self.item(self.currentRow(), 0)
        return item.data(ROLE) if item else None

    def startDrag(self, _actions):
        track = self.selected_track()
        if not track:
            return
        path = self.window.prepared.get(track["id"])
        if not path or not path.is_file():
            self.window.prepare_track()
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class Companion(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Crate Revenant")
        self.resize(1060, 580)
        self.setMinimumSize(660, 360)
        self.preferences = QSettings()
        data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        self.downloads = Path(data) / "Downloads"
        self.worker = ThreadPoolExecutor(max_workers=6, thread_name_prefix="crate")
        self.dispatch = Dispatch(self)
        self.dispatch.finished.connect(self.dispatch.deliver, Qt.ConnectionType.QueuedConnection)
        self.client = None
        self.generation = 0
        self.view = 0
        self.search_stop = Event()
        self.prepared = {}
        self.preparing = set()
        self.source_names = {}
        self.search_seen = set()
        self.matching_playlists = None

        page = QWidget()
        self.setCentralWidget(page)
        layout = QVBoxLayout(page)
        connection = QHBoxLayout()
        self.server = QLineEdit(self.preferences.value("server", "", type=str))
        self.server.setPlaceholderText("http://127.0.0.1:8080")
        self.server.setAccessibleName("Server URL")
        self.server.returnPressed.connect(self.connect_server)
        self.token = QLineEdit()
        self.token.setPlaceholderText("Optional API token")
        self.token.setAccessibleName("API token")
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setMaximumWidth(185)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.connect_server)
        connection.addWidget(QLabel("Server"))
        connection.addWidget(self.server, 1)
        connection.addWidget(self.token)
        connection.addWidget(self.connect_button)
        layout.addLayout(connection)

        search = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search tracks and playlists")
        self.query.setAccessibleName("Search tracks and playlists")
        self.query.returnPressed.connect(self.search)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.search)
        search.addWidget(self.query, 1)
        search.addWidget(self.search_button)
        layout.addLayout(search)
        panes = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Playlists")
        self.tree.setMinimumWidth(155)
        self.tree.itemExpanded.connect(self.expand_source)
        self.tree.itemSelectionChanged.connect(self.select_playlist)
        self.table = TrackTable(self)
        self.table.itemSelectionChanged.connect(self.refresh_buttons)
        self.table.itemDoubleClicked.connect(self.prepare_track)
        panes.addWidget(self.tree)
        panes.addWidget(self.table)
        panes.setSizes([220, 820])
        panes.setStretchFactor(1, 1)
        layout.addWidget(panes, 1)

        footer = QHBoxLayout()
        self.status = QLabel("Enter the Crate Revenant server URL to connect.")
        self.status.setWordWrap(True)
        self.prepare = QPushButton("Prepare file")
        self.prepare.clicked.connect(self.prepare_track)
        footer.addWidget(self.status, 1)
        footer.addWidget(self.prepare)
        layout.addLayout(footer)
        self.search_button.setEnabled(False)
        self.query.setEnabled(False)
        self.refresh_buttons()

    def run_task(self, task, done):
        def work():
            try:
                value = task()
            except (ClientError, OSError, ValueError, KeyError, TypeError) as error:
                message = str(error) if isinstance(error, ClientError) else "The request or local file operation failed."
                self.dispatch.finished.emit(done, None, message)
            else:
                self.dispatch.finished.emit(done, value, None)

        self.worker.submit(work)

    def begin_view(self):
        self.view += 1
        self.search_stop.set()
        self.table.clearSelection()
        self.table.setRowCount(0)
        self.refresh_buttons()
        return self.view

    def connect_server(self):
        try:
            candidate = MusicClient(self.server.text(), self.token.text().strip())
        except ClientError as error:
            self.status.setText(str(error))
            return
        self.generation += 1
        self.client = None
        self.tree.clear()
        self.matching_playlists = None
        self.begin_view()
        self.prepared.clear()
        self.preparing.clear()
        self.query.setEnabled(False)
        self.search_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.status.setText("Checking server…")

        def connect():
            candidate.health()
            return candidate.read("/sources")

        def ready(sources, error):
            self.connect_button.setEnabled(True)
            if error:
                self.status.setText(error)
                return
            self.client = candidate
            self.preferences.setValue("server", candidate.url)
            self.source_names = {source["id"]: source["name"] for source in sources}
            for source in sources:
                item = QTreeWidgetItem(self.tree, [source["name"]])
                item.setData(0, ROLE, (source["id"], None))
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            self.query.setEnabled(bool(sources))
            self.search_button.setEnabled(bool(sources))
            self.status.setText("Choose a playlist or search across sources." if sources else "No sources are configured.")

        self.run_task(connect, ready)

    def expand_source(self, item):
        data = item.data(0, ROLE)
        if not self.client or item.childCount() or not data or data[1] is not None:
            return
        source_id, _ = data
        loading = QTreeWidgetItem(item, ["Loading…"])
        client, generation = self.client, self.generation

        def ready(playlists, error):
            if generation != self.generation:
                return
            item.removeChild(loading)
            if error:
                self.status.setText(error + " Collapse and expand the source to retry.")
                return
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless)
            for playlist in playlists:
                self.add_playlist(item, source_id, playlist)

        self.run_task(lambda: client.playlists(source_id), ready)

    @staticmethod
    def add_playlist(parent, source_id, playlist):
        item = QTreeWidgetItem(parent, [playlist["name"]])
        item.setData(0, ROLE, (source_id, playlist["id"]))

    def select_playlist(self):
        selected = self.tree.selectedItems()
        data = selected[0].data(0, ROLE) if selected else None
        if not self.client or not data or data[1] is None:
            return
        client = self.client
        view = self.begin_view()
        self.status.setText("Loading playlist…")

        def ready(tracks, error):
            if view != self.view:
                return
            if not error:
                self.append_tracks(tracks)
            self.status.setText(error or f"{len(tracks)} tracks")

        self.run_task(lambda: client.tracks(*data), ready)

    def search(self):
        query = self.query.text().strip()
        if not self.client or not query:
            return
        view = self.begin_view()
        self.tree.clearSelection()
        if self.matching_playlists is not None:
            index = self.tree.indexOfTopLevelItem(self.matching_playlists)
            self.tree.takeTopLevelItem(index)
            self.matching_playlists = None
        self.search_stop = Event()
        cancelled, client = self.search_stop, self.client
        self.search_seen = set()
        failures = []
        self.status.setText("Searching…")

        def receive(event, _error):
            if view != self.view:
                return
            source = event["source"]
            if event.get("error"):
                failures.append(source["name"])
            elif event["kind"] == "tracks":
                tracks = [track for track in event["rows"] if track["id"] not in self.search_seen]
                self.search_seen.update(track["id"] for track in tracks)
                self.append_tracks(tracks)
                self.status.setText(f"{self.table.rowCount()} tracks · searching…")
            elif event["kind"] == "playlists" and event["rows"]:
                if self.matching_playlists is None:
                    self.matching_playlists = QTreeWidgetItem(["Matching playlists"])
                    self.tree.insertTopLevelItem(0, self.matching_playlists)
                group = QTreeWidgetItem(self.matching_playlists, [source["name"]])
                for playlist in event["rows"]:
                    self.add_playlist(group, source["id"], playlist)
                self.matching_playlists.setExpanded(True)
                group.setExpanded(True)

        def task():
            for event in client.search(query, cancelled):
                self.dispatch.finished.emit(receive, event, None)

        def finished(_value, error):
            if view != self.view:
                return
            partial = f" · unavailable: {', '.join(dict.fromkeys(failures))}" if failures else ""
            self.status.setText(error or f"{self.table.rowCount()} matching tracks{partial}")

        self.run_task(task, finished)

    def append_tracks(self, tracks):
        for track in tracks:
            duration = track.get("duration_seconds")
            length = f"{duration // 60}:{duration % 60:02}" if isinstance(duration, int) else ""
            memberships = ", ".join(playlist["name"] for playlist in track.get("playlists", []))
            values = (
                track["title"], track.get("artist"), self.source_names.get(track["source_id"], track["source_id"]),
                length, track.get("bpm"), track.get("key"), memberships, self.file_status(track),
            )
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, value in enumerate(values):
                cell = QTableWidgetItem("" if value is None else str(value))
                if column == 0:
                    cell.setData(ROLE, track)
                self.table.setItem(row, column, cell)
        self.refresh_buttons()

    def file_status(self, track):
        if track["id"] in self.prepared:
            return "Ready"
        if track["id"] in self.preparing:
            return "Fetching…"
        return "" if track.get("audio_available", True) else "Unavailable"

    def refresh_buttons(self):
        track = self.table.selected_track()
        self.prepare.setEnabled(bool(self.client and track and track.get("audio_available", True)
                                     and track["id"] not in self.preparing))

    def refresh_track(self, track):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(ROLE)["id"] == track["id"]:
                self.table.item(row, len(COLUMNS) - 1).setText(self.file_status(track))
        self.refresh_buttons()

    def prepare_track(self, *_args):
        track = self.table.selected_track()
        if not self.client or not track or not track.get("audio_available", True):
            return
        identity = track["id"]
        if identity in self.preparing:
            return
        client, generation = self.client, self.generation
        self.preparing.add(identity)
        self.refresh_track(track)
        self.status.setText(f"Preparing {track['title']}…")

        def ready(path, error):
            if generation != self.generation:
                return
            self.preparing.discard(identity)
            if path:
                self.prepared[identity] = path
            self.refresh_track(track)
            self.status.setText(error or f"Ready: {track['title']}. Drag its row to a deck or library.")

        self.run_task(lambda: client.download(track, self.downloads), ready)

    def closeEvent(self, event):
        self.search_stop.set()
        self.worker.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("Crate Revenant")
    app.setApplicationName("DJ Companion")
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget { background: #191e24; color: #e7edf3; }
        QTreeWidget, QTableWidget, QLineEdit { background: #101419; alternate-background-color: #181d23;
            border: 1px solid #39434f; selection-background-color: #285159; }
        QLineEdit, QPushButton { padding: 6px; }
        QHeaderView::section { background: #27313b; padding: 5px; border: 0; }
        QPushButton { background: #29434a; border: 1px solid #537078; border-radius: 3px; }
        QPushButton:disabled { background: #252a31; color: #85909d; }
        QPushButton:focus, QLineEdit:focus { border: 1px solid #78c6bb; }
    """)
    window = Companion()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
