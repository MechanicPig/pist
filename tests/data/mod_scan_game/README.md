# Synthetic Mod scanner game

This fixture is a small, reviewable Celeste `Mods` directory used by the scanner integration test.

- `archives/` stores the readable source trees that the test packages as ZIP files.
- `directories/` stores directory Mods exactly as they appear below `Mods/`.
- `blacklist.txt` is copied to the materialized `Mods` directory.

Focused malformed-input tests still construct only the files needed for their individual boundary.
