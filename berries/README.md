# Berries

Reusable Python tools for reading Celeste, Everest Mods, saves, map metadata, entity rules, GameBanana metadata, and read-only map previews. Berries is the reusable library in this workspace; Pist adds personal records, auditing workflows, and spreadsheet integration.

```python
from pathlib import Path

from berries import GameInstallation

catalog = GameInstallation(Path(r'D:\Games\Celeste')).load_catalog()
for campaign in catalog.campaigns:
    print(campaign.directory)
```

For complete workflows, see the [usage guide](../docs/berries-usage.md) and runnable examples:

- [List active maps](../examples/list_maps.py).
- [Inspect a map, query saves, or open a preview](../examples/inspect_map.py).

Run examples from the repository after `uv sync --all-packages --frozen`:

```sh
uv run --package berries python examples/list_maps.py "D:/Games/Celeste"
uv run --package berries python examples/inspect_map.py --help
```
