# Berries

Reusable Python tools for reading Celeste, Everest Mods, saves, map metadata, entity rules,
GameBanana metadata, and read-only map previews.

```python
from pathlib import Path

from berries import GameInstallation

catalog = GameInstallation(Path(r'D:\Games\Celeste')).load_catalog()
for campaign in catalog.campaigns:
    print(campaign.directory)
```
