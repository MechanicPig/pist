"""A standalone local-record workflow independent of game installation or scanning."""

from textual.app import ComposeResult
from textual.widgets import Footer, Header

from pist.records.lock import record_writer_lock
from pist.records.store import RecordStore
from pist.records.sync import RecordSheetClient
from pist.settings import PistSettings, SettingsStore
from pist.ui.records.controller import RecordEditorController
from pist.ui.records.list import RecordListScreen
from pist.ui.tui import RefreshableCssApp


class RecordBrowserApp(RefreshableCssApp[None]):
    """Browse historical records using the same editing policy as map browsing."""

    TITLE = 'Pist · 本地记录'

    def __init__(
        self,
        store: RecordStore,
        *,
        client: RecordSheetClient | None = None,
        source: str | None = None,
        settings_store: SettingsStore | None = None,
    ) -> None:
        super().__init__()
        settings = settings_store.load() if settings_store is not None else PistSettings()
        self.theme = settings.theme
        self._record_list_max_widths = settings.record_list_max_widths
        self._settings_store = settings_store
        self.controller = RecordEditorController(
            self,
            store,
            client=client,
            source=source,
            manual_fields=(),
        )

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()

    def on_mount(self) -> None:
        self.push_screen(
            RecordListScreen(
                self.controller,
                max_widths=self._record_list_max_widths,
                settings_store=self._settings_store,
            ),
            lambda _: self.exit(),
        )


async def browse_records(
    *,
    settings_store: SettingsStore,
    client: RecordSheetClient | None = None,
    source: str | None = None,
) -> None:
    """Hold the shared writer lock before reading history and until the UI exits."""
    with record_writer_lock():
        await RecordBrowserApp(
            RecordStore(),
            client=client,
            source=source,
            settings_store=settings_store,
        ).run_async()
