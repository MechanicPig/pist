"""List active maps without using Pist's personal record or spreadsheet application."""

import argparse
from pathlib import Path

from berries import GameInstallation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('game_dir', type=Path)
    args = parser.parse_args()

    catalog = GameInstallation(args.game_dir).load_catalog()
    for campaign in catalog.campaigns:
        print(campaign.directory)
        for level, side in campaign.iter_sides():
            print(f'  {side.value}: {level.sid} -> {level[side].map_info.file_path}')


if __name__ == '__main__':
    main()
