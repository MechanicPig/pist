"""Inspect one final SID, with optional save statistics and a read-only preview."""

import argparse
import asyncio
from pathlib import Path

from berries import GameInstallation, LevelSide, MapLayout, MapPreview, classify_map_entities
from berries.entities import config
from berries.entities.rules import EntityKindLayer, EntityRuleLayer, EntityRules
from berries.game.map_data import load_map_data
from berries.game.saves import operations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('game_dir', type=Path)
    parser.add_argument('sid', help='Final SID printed by list_maps.py')
    parser.add_argument('--side', choices=tuple(LevelSide.__members__), default='A')
    rules = parser.add_mutually_exclusive_group()
    rules.add_argument('--local-rules', type=Path, help='Optional local entity-rule override')
    rules.add_argument(
        '--packaged-rules', action='store_true', help='Use only packaged entity rules (default)'
    )
    parser.add_argument('--save-slot', type=int, help='Also read native statistics from this slot')
    parser.add_argument('--preview', action='store_true', help='Open a read-only browser preview')
    args = parser.parse_args()
    side = LevelSide[args.side]

    game = GameInstallation(args.game_dir)
    catalog = game.load_catalog()
    level = next(
        (
            level
            for campaign in catalog.campaigns
            for level in campaign.levels
            if level.sid == args.sid and level.map(side) is not None
        ),
        None,
    )
    if level is None:
        parser.error(f'No active map for SID {args.sid!r}, side {args.side}')
    map_file = level[side]
    rule_set = config.load_default_entity_rules()
    if args.local_rules is not None:
        rule_set = EntityRules.from_layers(
            EntityKindLayer(kinds=rule_set.kinds),
            EntityRuleLayer(entities=rule_set.entities),
            config.load_entity_rule_layer(args.local_rules),
        )
    data = load_map_data(map_file)
    print(f'{level.sid} [{args.side}]: {map_file.file_path}')
    for entity in classify_map_entities(data, rule_set=rule_set):
        print(f'  {entity.room} / {entity.name} #{entity.entity_id}: {entity.kind}')

    if args.save_slot is not None:
        slot = game.saves().slots[args.save_slot]
        save = slot.main()
        try:
            backup = slot.backup()
            operations.restore_mod_progress(save, backup)
        except FileNotFoundError:
            pass
        progress_level = save.flat_levels.get(level.sid)
        stats = (
            None
            if progress_level is None or side >= len(progress_level.maps)
            else progress_level.maps[side]
        )
        if stats is None:
            print(f'Save {slot.number}: no progress for this Map')
        else:
            print(
                f'Save {slot.number}: time_played={stats.time_played}, deaths={stats.deaths}, '
                f'completed={stats.completed}'
            )
        print(
            'Personal bests:',
            None if stats is None else stats.bests,
        )
        print(
            'Checkpoints:',
            None if stats is None else stats.checkpoints,
        )

    if args.preview:
        layout = MapLayout.from_bin_map(data, entity_rules=rule_set)
        asyncio.run(
            MapPreview(map_file, layout, dialogs=catalog.dialogs, entity_rules=rule_set).preview()
        )


if __name__ == '__main__':
    main()
