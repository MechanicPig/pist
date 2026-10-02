"""Compose the personal application's independent local stores."""

from pathlib import Path

from pist.catalog_cache import CatalogCache
from pist.record_store import RecordStore
from pist.route_store import RouteStore
from pist.sqlite_store import LOCAL_DATA_PATH


class AppDataStores:
    """Application wiring for stores that happen to share one database file."""

    def __init__(self, path: Path = LOCAL_DATA_PATH) -> None:
        self.records = RecordStore(path)
        self.routes = RouteStore(path)
        self.catalog = CatalogCache(path)
