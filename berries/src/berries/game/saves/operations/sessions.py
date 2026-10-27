"""Preflight current and Collab session changes alongside progress edits."""

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from itertools import chain

from berries.game.levels import LevelSide
from berries.game.saves.errors import ProgressConflictError
from berries.game.saves.files.collab_utils2 import CollabUtils2Save
from berries.game.saves.files.main import MainSave
from berries.game.saves.progress.level import campaign_name
from berries.game.saves.progress.session import Session
from berries.game.saves.session_input import SessionInput


@dataclass(frozen=True)
class Scope:
    name: str
    sid: str | None
    sides: tuple[LevelSide, ...]

    def includes_sid(self, sid: str) -> bool:
        return sid == self.sid if self.sid is not None else campaign_name(sid) == self.name

    def includes(self, sid: str, side: LevelSide) -> bool:
        return self.includes_sid(sid) and side in self.sides


@dataclass
class RetainedChange:
    save: CollabUtils2Save
    sid: str
    session: SessionInput | None
    text: dict[str, str] | None = None
    binary: dict[str, str] | None = None

    def apply(self) -> None:
        if self.session is None:
            self.save.sessions.pop(self.sid, None)  # pyright: ignore[reportUnusedCallResult]
        else:
            self.save.sessions.add(self.session)
        for values, incoming in (
            (self.save.mod_sessions, self.text),
            (self.save.mod_sessions_binary, self.binary),
        ):
            if incoming is None:
                values.pop(self.sid, None)  # pyright: ignore[reportUnusedCallResult]
            else:
                values[self.sid] = incoming


@dataclass
class PreparedSessions:
    currents: list[tuple[MainSave, Session | None]] = field(default_factory=list)
    retained: list[RetainedChange] = field(default_factory=list)

    def apply(self) -> None:
        for save, session in self.currents:
            save.session = session
        for change in self.retained:
            change.apply()


def _allows(discard: bool | Mapping[str, bool] | None, sid: str) -> bool:
    return discard if isinstance(discard, bool) else discard is not None and discard.get(sid, False)


def _selected(
    save: CollabUtils2Save, scope: Scope, *, exclude: AbstractSet[str] | None = None
) -> dict[str, SessionInput]:
    selected: dict[str, SessionInput] = {}
    candidates = dict.fromkeys(chain(save.sessions, save.mod_sessions, save.mod_sessions_binary))
    for sid in candidates:
        if exclude is not None and sid in exclude:
            continue
        if not scope.includes_sid(sid):
            continue
        value = save.sessions.get(sid)
        if value is None:
            # An orphan payload has no side. Do not silently attribute it to the
            # selected map or leave it behind while moving a whole Level.
            raise ProgressConflictError(f'Collab Mod payload has no retained session: {sid!r}.')
        if len(scope.sides) == len(LevelSide) or scope.includes(sid, value.session.side):
            selected[sid] = value
    return selected


def prepare_clear(
    save: MainSave,
    scope: Scope,
    *,
    collab: CollabUtils2Save | None,
    discard_current_session: bool,
    discard_collab_session: bool | Mapping[str, bool] | None,
) -> PreparedSessions:
    result = PreparedSessions()
    current = save.current_map
    if current is not None and scope.includes(*current):
        if not discard_current_session:
            raise ProgressConflictError(
                f'Related current session must be handled first: {current!r}.'
            )
        result.currents.append((save, None))
    if collab is not None:
        for sid in _selected(collab, scope):
            if not _allows(discard_collab_session, sid):
                raise ProgressConflictError(
                    f'Related Collab session must be handled first: {sid!r}.'
                )
            result.retained.append(RetainedChange(collab, sid, None))
    return result


def prepare_transfer(
    source: MainSave,
    target: MainSave,
    scope: Scope,
    *,
    source_collab: CollabUtils2Save | None,
    target_collab: CollabUtils2Save | None,
    remove_source: bool,
    discard_current_session: bool,
    discard_collab_session: bool | Mapping[str, bool] | None,
) -> PreparedSessions:
    if (source_collab is None) != (target_collab is None):
        raise ValueError('Both Collab saves must be supplied together.')
    if (
        source_collab is not None
        and target_collab is not None
        and (
            source_collab is target_collab
            or source_collab.sessions is target_collab.sessions
            or source_collab.mod_sessions is target_collab.mod_sessions
            or source_collab.mod_sessions_binary is target_collab.mod_sessions_binary
        )
    ):
        raise ProgressConflictError('Source and target Collab session containers overlap.')
    result = PreparedSessions()
    current = source.current_map
    incoming = source.session if current is not None and scope.includes(*current) else None
    previous = target.current_map
    if previous is not None and (incoming is not None or scope.includes(*previous)):
        if not discard_current_session:
            raise ProgressConflictError(
                f'Related target current session must be handled first: {previous!r}.'
            )
        result.currents.append((target, None))
    if incoming is not None:
        # Validate prospective protocol output before installing any progress.
        # Decode temporary elements into independent values, without retaining or
        # reparsing serialized XML, or simulating game exit hooks.
        incoming = Session.from_xml(incoming.to_xml())
        result.currents.append((target, incoming))
        if remove_source:
            result.currents.append((source, None))
    if result.currents and source.mods is target.mods:
        raise ProgressConflictError('Source and target share current-session storage.')
    if source_collab is None or target_collab is None:
        return result
    incoming_sids = _selected(source_collab, scope)
    # Incoming sessions compete for the entire SID slot, regardless of side.
    # Other target sessions only conflict when their own progress is edited.
    affected = _selected(target_collab, scope, exclude=incoming_sids.keys())
    for sid, value in incoming_sids.items():
        if (
            sid in target_collab.sessions
            or sid in target_collab.mod_sessions
            or sid in target_collab.mod_sessions_binary
        ) and not _allows(discard_collab_session, sid):
            raise ProgressConflictError(f'Target Collab session slot is occupied: {sid!r}.')
        result.retained.append(
            RetainedChange(
                target_collab,
                sid,
                SessionInput(sid, value.tostring()),
                None
                if sid not in source_collab.mod_sessions
                else source_collab.mod_sessions[sid].copy(),
                None
                if sid not in source_collab.mod_sessions_binary
                else source_collab.mod_sessions_binary[sid].copy(),
            )
        )
        if remove_source:
            result.retained.append(RetainedChange(source_collab, sid, None))
    for sid in affected:
        if not _allows(discard_collab_session, sid):
            raise ProgressConflictError(
                f'Related target Collab session must be handled first: {sid!r}.'
            )
        result.retained.append(RetainedChange(target_collab, sid, None))
    return result
