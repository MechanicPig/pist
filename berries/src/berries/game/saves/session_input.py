"""Indexed session XML and its independently cached domain value."""

from functools import cached_property

from .codecs.xml import tree
from .progress.session import Session
from .unknown import UnknownFieldPolicy, UnknownSaveFieldError


class SessionInput:
    """Stable SID/XML input with an editable, explicitly lazy Session.

    The session reference is read-only, but its domain fields remain editable.
    Deleting session discards the cache; deleting an absent cache raises.
    """

    def __init__(self, sid: str, orig_xml: str, *, unknown: UnknownFieldPolicy = 'error') -> None:
        if not sid:
            raise ValueError('A session SID must not be empty.')
        self._sid = sid
        self._orig_xml = orig_xml
        self._unknown: UnknownFieldPolicy = unknown

    @property
    def sid(self) -> str:
        return self._sid

    @property
    def orig_xml(self) -> str:
        return self._orig_xml

    @cached_property
    def _session(self) -> Session:
        try:
            session = Session.from_xml(
                tree.parse(self.orig_xml).getroot(),
                unknown=self._unknown,
                path=f'SessionsPerLevel[{self.sid!r}]',
            )
            if session.sid != self.sid:
                raise ValueError(f'Session key/SID mismatch: {self.sid!r}, {session.sid!r}.')
            return session
        except UnknownSaveFieldError:
            raise
        except ValueError as error:
            raise ValueError(f'Invalid session {self.sid!r}: {error}') from error

    @property
    def session(self) -> Session:
        return self._session

    @session.deleter
    def session(self) -> None:
        del self._session

    def tostring(self) -> str:
        session = (
            self.session
            if '_session' in vars(self)
            else Session.from_xml(
                tree.parse(self.orig_xml).getroot(),
                unknown=self._unknown,
                path=f'SessionsPerLevel[{self.sid!r}]',
            )
        )
        if session.sid != self.sid:
            raise ValueError(f'Session key/SID mismatch: {self.sid!r}, {session.sid!r}.')
        return session.tostring()
