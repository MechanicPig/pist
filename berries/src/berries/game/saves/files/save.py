"""In-memory save domain objects with one-shot encoding and decoding."""

from typing import ClassVar

from berries.text_doc import TextDocMixin, TextFile

type SaveFile = TextFile


class Save(TextDocMixin):
    """Editable save data, independent of its source or destination.

    Reading consumes a borrowed stream from its current position. Writing starts
    at the destination's current position without seeking, truncating or closing
    it. Callers manage borrowed streams and any backup or concurrency policy.
    """


class ModSave(Save):
    """One Mod's save format; slot access derives its filename from mod_name."""

    mod_name: ClassVar[str]
