"""Small project-wide value types with no owning domain."""

type CellValue = int | bool | str
type RecordValues = dict[str, dict[str, CellValue]]
