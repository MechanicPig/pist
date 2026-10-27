"""File codec fixtures independent of Pist and installed game data."""

from io import BytesIO

from lxml import etree

from berries.game.duration import Duration
from berries.game.levels import LevelSide
from berries.game.saves.codecs.xml import tree as xml_tree
from berries.game.saves.files.enders_blender import EndersBlenderSave
from berries.game.saves.files.main import MainSave
from berries.game.saves.files.mod_backup import ModBackupSave
from berries.game.saves.files.save import Save
from berries.game.saves.progress.map import MapProgress
from berries.game.saves.progress.session import Session
from berries.game.vanilla import VANILLA_SIDS

_ZERO_DURATION = Duration()


def get_map(save: MainSave, sid: str, side: LevelSide) -> MapProgress | None:
    """Project an optional side for synthetic read/query tests."""
    level = save.flat_levels.get(sid)
    return None if level is None or side >= len(level.maps) else level.maps[side]


def parsed_session(
    sid: str,
    side: LevelSide,
    *,
    room: str = '',
    respawn: tuple[float, float] | None = None,
    time_played: Duration = _ZERO_DURATION,
    deaths: int = 0,
    room_deaths: int = 0,
    in_level: bool = True,
) -> Session:
    """Parse synthetic session XML, then apply editable values for codec tests.

    This fixture is not a complete game-session initialization contract.
    """
    root = etree.Element(
        'Session', InArea='true', Level='', Time='0', Deaths='0', DeathsInCurrentLevel='0'
    )
    etree.SubElement(root, 'Area', SID=sid, Mode=('Normal', 'BSide', 'CSide')[side])
    old_stats = etree.SubElement(
        root,
        'OldStats',
        SID=sid,
        ID=str(VANILLA_SIDS.index(sid) if sid in VANILLA_SIDS else 0),
    )
    modes = etree.SubElement(old_stats, 'Modes')
    for _ in LevelSide:
        etree.SubElement(modes, 'AreaModeStats', TimePlayed='0', Deaths='0')
    session = Session.from_xml(root)
    session.room = room
    session.respawn = respawn
    session.time_played = time_played
    session.deaths = deaths
    session.room_deaths = room_deaths
    session.in_level = in_level
    return session


def complete_vanilla(data: str | bytes) -> str:
    """Build a complete original-game fixture around explicitly supplied chapters.

    Deliberately malformed chapter-layout tests must bypass this helper.
    Existing chapter fields and extension nodes are retained, not repaired.
    """
    doc = xml_tree.parse(data)
    root = doc.getroot()
    assert root.tag == 'SaveData'
    areas = root.find('Areas')
    if areas is None:
        areas = etree.SubElement(root, 'Areas')
    chapters = {}
    for node in tuple(areas.iterchildren('AreaStats')):
        sid = node.get('SID')
        if sid is None:
            sid = VANILLA_SIDS[int(node.attrib['ID'])]
        assert sid in VANILLA_SIDS and sid not in chapters, sid
        chapters[sid] = node
        areas.remove(node)
    for i, sid in enumerate(VANILLA_SIDS):
        node = chapters.get(sid)
        if node is None:
            node = etree.Element('AreaStats', ID=str(i))
            modes = etree.SubElement(node, 'Modes')
            for _ in range(3):
                etree.SubElement(modes, 'AreaModeStats', TimePlayed='0', Deaths='0')
        areas.insert(i, node)
    return etree.tostring(doc, encoding='unicode')


def normal_save_xml(data: str | bytes, *, complete: bool = True) -> str:
    """Add normal serializer defaults to synthetic INPUT fixtures, never product output."""
    root = xml_tree.parse(data).getroot()
    if root.tag == 'SaveData' and complete:
        root = xml_tree.parse(complete_vanilla(data)).getroot()
    for name, value in {
        'Name': 'Madeline',
        'Time': '0',
        'TotalDeaths': '0',
        'AssistMode': 'false',
        'VariantMode': 'false',
        'UnlockedAreas': '0',
        'TotalStrawberries': '0',
        'TotalGoldenStrawberries': '0',
        'Poem': '',
        'Areas': '',
    }.items():
        if root.tag == 'SaveData' and root.find(name) is None:
            etree.SubElement(root, name).text = value
    for node in root.iter('AreaModeStats'):
        for name, value in {
            'TimePlayed': '0',
            'Deaths': '0',
            'TotalStrawberries': '0',
            'Completed': 'false',
            'SingleRunCompleted': 'false',
            'FullClear': 'false',
            'HeartGem': 'false',
            'BestTime': '0',
            'BestFullClearTime': '0',
            'BestDeaths': '0',
            'BestDashes': '0',
        }.items():
            if name not in node.attrib:
                node.set(name, value)
        for name in ('Strawberries', 'Checkpoints'):
            if node.find(name) is None:
                etree.SubElement(node, name)
    return etree.tostring(root.getroottree(), encoding='unicode')


def require_map(progress: MapProgress | None) -> MapProgress:
    """Assert that a complete fixture supplies the selected side's progress."""
    assert progress is not None
    return progress


def read_save[T: Save](save_type: type[T], data: bytes | str) -> T:
    """Decode synthetic codec fixtures without normal-game layout or timer constraints.

    Default strict entry points have independent coverage using matching and
    mismatching campaign identities. Encoding fixtures do not model a catalog.
    """
    return (
        save_type.fromstring(data, strict=False)
        if issubclass(save_type, (MainSave, ModBackupSave, EndersBlenderSave))
        else save_type.fromstring(data)
    )


def save_bytes(save: Save) -> bytes:
    """Encode through the public one-shot writer without touching game files."""
    stream = BytesIO()
    save.write(stream)
    return stream.getvalue()


def reopen_save[T: Save](save: T) -> T:
    """An independent decode, never a copy of a file session or its resources."""
    return read_save(type(save), save_bytes(save))
