"""Personal record value types shared across Pist modules."""

type CellValue = int | bool | str | tuple[str, ...]
type RecordValues = dict[str, dict[str, CellValue]]
