"""Flat SQLite representation of a personal map record."""

import json
from sqlite3 import Row

from pydantic import JsonValue

from pist.records.models import MapRecord

RECORD_COLUMNS = {
    'record_number': 'INTEGER UNIQUE CHECK (record_number > 0)',
    'created_at': 'TEXT NOT NULL',
    'mod_metadata_name': 'TEXT',
    'mod_name': 'TEXT',
    'mod_url': 'TEXT',
    'mod_updated_at': 'TEXT',
    'credits': "TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(credits))",
    'map_name': 'TEXT NOT NULL',
    'video_url': 'TEXT',
    'map_english_name': 'TEXT',
    'map_file': 'TEXT',
    'sid': 'TEXT',
    'side': "TEXT CHECK (side IN ('A', 'B', 'C'))",
    'authors': "TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(authors))",
    'difficulty': 'TEXT',
    'save_slot': 'INTEGER CHECK (save_slot >= 0)',
    'time_played': 'TEXT',
    'deaths': 'INTEGER CHECK (deaths >= 0)',
    'completed': 'INTEGER CHECK (completed IN (0, 1))',
    'n_strawberries': 'INTEGER CHECK (n_strawberries >= 0)',
    'n_moonberries': 'INTEGER CHECK (n_moonberries >= 0)',
    'cassette': 'INTEGER NOT NULL DEFAULT 0 CHECK (cassette IN (0, 1))',
    'heart': 'TEXT',
    'n_main_rooms': 'INTEGER CHECK (n_main_rooms > 0)',
    'status': 'TEXT',
    'tags': "TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags))",
    'perceived_difficulty': 'TEXT',
    'perceived_difficulty_tier': 'TEXT',
    'rated_difficulty': 'TEXT',
    'rated_difficulty_tier': 'TEXT',
    'started_at': 'TEXT',
    'finished_at': 'TEXT',
    'save_load_usage': 'TEXT',
    'rating': 'INTEGER CHECK (rating BETWEEN 0 AND 10)',
    'notes': 'TEXT',
}
COLLECTION_COLUMNS = frozenset({'authors', 'credits', 'tags'})
RECORD_TABLE_SQL = (
    'CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, '
    + ', '.join(f'{name} {declaration}' for name, declaration in RECORD_COLUMNS.items())
    + ", sync_state TEXT NOT NULL CHECK (sync_state IN ('unchecked', 'pending', 'synced')));"
)


def record_columns(record: MapRecord) -> dict[str, JsonValue]:
    """Encode scalar columns and the three explicitly collection-valued columns."""
    values = record.model_dump(mode='json')
    return {
        name: json.dumps(values[name], ensure_ascii=False)
        if name in COLLECTION_COLUMNS
        else values[name]
        for name in RECORD_COLUMNS
    }


def record_from_row(row: Row) -> MapRecord:
    """Validate one flat SQLite row as a complete personal record."""
    values = {name: row[name] for name in RECORD_COLUMNS}
    for name in COLLECTION_COLUMNS:
        values[name] = json.loads(values[name])
    values['local_id'] = row['id']
    return MapRecord.model_validate(values)
