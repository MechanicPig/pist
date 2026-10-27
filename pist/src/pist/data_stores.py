"""Compose the personal application's independent local stores."""

from pathlib import Path

from .catalog_cache import CatalogCache
from .records.store import RecordStore
from .routes.store import RouteStore
from .sqlite_store import LOCAL_DATA_PATH


class DataStores:
    """Application wiring for stores that happen to share one database file."""

    def __init__(self, path: Path = LOCAL_DATA_PATH) -> None:
        self.records = RecordStore(path)
        self.routes = RouteStore(path)
        self.catalog = CatalogCache(path)
