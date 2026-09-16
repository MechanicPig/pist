"""Conservative static evaluation for a parsed Lua module.

Only values derivable without executing Lua are represented as known.  Calls,
functions, and unsupported expressions remain :data:`UNKNOWN`, while known
fields in their surrounding tables are retained.
"""

from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Final, TypeIs, overload

from luaparser import ast
from typing_extensions import Sentinel as sentinel

UNKNOWN: Final = sentinel('UNKNOWN')

MAX_STATIC_LOOP_ITERATIONS = 256
MAX_STATIC_CALL_DEPTH = 2

type LuaKey = str | int | float
type LuaScalar = str | int | float | bool | None
type LuaNumber = int | float
type LuaNumericOperation = (
    ast.AddOp | ast.SubOp | ast.MultOp | ast.FloatDivOp | ast.FloorDivOp | ast.ModOp | ast.ExpoOp
)


@dataclass(slots=True)
class LuaTable:
    """The statically known fields of one Lua table.

    Missing fields and fields assigned :data:`UNKNOWN` remain distinguishable:
    the latter still occur in ``fields`` and indicate that the source did
    define the field but its value is dynamic.
    """

    fields: dict[LuaKey, LuaValue] = field(default_factory=dict)

    def get(self, key: LuaKey) -> LuaValue:
        """Return a known field value, or :data:`UNKNOWN` when absent."""
        return self.fields.get(key, UNKNOWN)


@dataclass(frozen=True, slots=True)
class LuaFunction:
    """A locally declared Lua function eligible for limited static calls."""

    args: tuple[str, ...]
    body: ast.Block
    values: Mapping[str, LuaValue]


type LuaValue = LuaScalar | LuaFunction | LuaTable | UNKNOWN


@dataclass(frozen=True, slots=True)
class LuaModule:
    """Statically evaluated top-level bindings and returned values."""

    values: Mapping[str, LuaValue]
    returns: tuple[LuaValue, ...]


def _first[T](values: Iterable[T]) -> T | None:
    """Return the first value from an AST argument sequence."""
    return next(iter(values), None)


def _ipairs(table: LuaTable) -> tuple[tuple[int, LuaValue], ...]:
    """Return the statically known non-nil prefix of a Lua table."""
    items: list[tuple[int, LuaValue]] = []
    index = 1
    while (value := table.get(index)) is not UNKNOWN and value is not None:
        items.append((index, value))
        index += 1
    return tuple(items)


def _is_key(value: LuaValue) -> TypeIs[LuaKey]:
    """Return whether a statically known value can be a Lua table key."""
    return type(value) in {str, int, float}


def _is_number(value: LuaValue) -> TypeIs[LuaNumber]:
    """Return whether a statically known value is a non-boolean Lua number."""
    return type(value) in {int, float}


def evaluate(content: str, *, modules: Mapping[str, LuaValue] | None = None) -> LuaModule:
    """Parse and conservatively evaluate one standard-Lua module.

    ``modules`` is a read-only mapping of known static modules. Third-party
    Lua is never loaded or executed.
    """
    return _Evaluator(modules=modules).module(ast.parse(content))


class _Evaluator:
    def __init__(
        self,
        values: Mapping[str, LuaValue] | None = None,
        *,
        modules: Mapping[str, LuaValue] | None = None,
        call_depth: int = 0,
    ) -> None:
        if values is None:
            values = {}

        self._values = dict(values)
        self._modules = modules or {}
        self._returns: list[LuaValue] = []
        self._call_depth = call_depth

    def module(self, tree: ast.Chunk) -> LuaModule:
        """Evaluate supported top-level statements from one Lua AST."""
        for stat in tree.body.body:
            self._statement(stat)
        return LuaModule(values=self._values.copy(), returns=tuple(self._returns))

    def _statement(self, stat: ast.Node) -> None:
        match stat:
            case ast.Assign():
                self._assign(stat)
            case ast.LocalFunction():
                self._local_function(stat)
            case ast.Fornum():
                self._fornum(stat)
            case ast.Forin():
                self._forin(stat)
            case ast.Return():
                self._returns.extend(self.value(value) for value in stat.values)
            case ast.Call():
                self.value(stat)

    def _local_function(self, stat: ast.LocalFunction) -> None:
        if not isinstance(stat.name, ast.Name):
            return
        args = tuple(arg.id for arg in stat.args if isinstance(arg, ast.Name))
        values = self._values.copy()
        self._values[stat.name.id] = LuaFunction(args, stat.body, values)

    def _assign(self, stat: ast.Assign) -> None:
        for target, value in zip(stat.targets, stat.values):
            self._set(target, self.value(value))

    def _set(self, target: ast.Node, value: LuaValue) -> None:
        match target:
            case ast.Name():
                self._values[target.id] = value
            case ast.Index():
                if not isinstance(table := self.value(target.value), LuaTable):
                    return
                if (key := self._index_key(target)) is UNKNOWN:
                    return
                table.fields[key] = value

    def value(self, node: ast.Node | None) -> LuaValue:
        """Evaluate one expression, returning :data:`UNKNOWN` when dynamic."""
        match node:
            case ast.String():
                try:
                    return node.s.decode('utf-8')
                except UnicodeDecodeError:
                    return UNKNOWN
            case ast.Number():
                return node.n
            case ast.TrueExpr():
                return True
            case ast.FalseExpr():
                return False
            case ast.Nil():
                return None
            case ast.Name():
                return self._values.get(node.id, UNKNOWN)
            case ast.Index():
                if not isinstance(table := self.value(node.value), LuaTable):
                    return UNKNOWN
                if (key := self._index_key(node)) is UNKNOWN:
                    return UNKNOWN
                return table.get(key)
            case ast.Table():
                return self._table(node.fields)
            case ast.Call():
                return self._call(node)
            case ast.UMinusOp():
                return -value if _is_number(value := self.value(node.operand)) else UNKNOWN
            case (
                ast.AddOp()
                | ast.SubOp()
                | ast.MultOp()
                | ast.FloatDivOp()
                | ast.FloorDivOp()
                | ast.ModOp()
                | ast.ExpoOp()
            ):
                return self._numeric(node)
            case ast.Concat():
                if not isinstance(left := self.value(node.left), str):
                    return UNKNOWN
                if not isinstance(right := self.value(node.right), str):
                    return UNKNOWN
                return left + right
            case _:
                return UNKNOWN

    def _call(self, node: ast.Call) -> LuaValue:
        """Evaluate a simple local function call without loading Lua modules."""
        match self._call_name(node):
            case ['table', 'insert']:
                return self._table_insert(node.args)
            case ['require']:
                return self._require(node.args)
            case ['utils', 'deepcopy']:
                return self._deepcopy(self.value(_first(node.args)))
            case ['string', 'format']:
                return self._string_format(node.args)
            case ['string', 'lower']:
                return self._string_case(node.args, lower=True)
            case ['string', 'upper']:
                return self._string_case(node.args, lower=False)
            case ['tostring']:
                return self._tostring(node.args)

        if not isinstance(func := self.value(node.func), LuaFunction):
            return UNKNOWN

        args: list[LuaScalar | LuaTable] = []
        for arg in node.args:
            if (value := self.value(arg)) is UNKNOWN or isinstance(value, LuaFunction):
                return UNKNOWN
            args.append(value)

        if len(args) != len(func.args) or self._call_depth > MAX_STATIC_CALL_DEPTH:
            return UNKNOWN

        values = {name: value for name, value in func.values.items() if value is not UNKNOWN}
        values.update(zip(func.args, args))
        evaluator = _Evaluator(
            values,
            modules=self._modules,
            call_depth=self._call_depth + 1,
        )
        for stat in func.body.body:
            evaluator._statement(stat)
        return evaluator._returns[0] if len(evaluator._returns) == 1 else UNKNOWN

    def _require(self, nodes: Iterable[ast.Node]) -> LuaValue:
        """Return an isolated copy of one explicitly supplied static module."""
        name = self.value(_first(nodes))
        if not isinstance(name, str):
            return UNKNOWN
        module = self._modules.get(name, UNKNOWN)
        return self._deepcopy(module)

    def _fornum(self, stat: ast.Fornum) -> None:
        """Evaluate a numeric loop with fully known integer bounds."""
        start = self.value(stat.start)
        stop = self.value(stat.stop)
        step = self.value(stat.step) if isinstance(stat.step, ast.Node) else stat.step
        target = stat.target.id
        if type(start) is not int or type(stop) is not int or type(step) is not int or step == 0:
            return
        end = stop + (1 if step > 0 else -1)
        values = range(start, end, step)
        try:
            if len(values) > MAX_STATIC_LOOP_ITERATIONS:
                return
        except OverflowError:
            return
        self._loop((target,), ((value,) for value in values), stat.body)

    def _forin(self, stat: ast.Forin) -> None:
        """Evaluate ``ipairs`` and ``pairs`` over a fully known table."""
        match stat.iter:
            case [ast.Call() as iterator]:
                match self._call_name(iterator):
                    case ['ipairs' | 'pairs'] as name:
                        pass
                    case _:
                        return
            case _:
                return

        args = iterator.args
        if len(args) != 1 or not isinstance(table := self.value(args[0]), LuaTable):
            return
        if not stat.targets:
            return
        targets = tuple(target.id for target in stat.targets)
        items = _ipairs(table) if name[0] == 'ipairs' else tuple(table.fields.items())
        if len(items) > MAX_STATIC_LOOP_ITERATIONS:
            return
        self._loop(targets, items, stat.body)

    def _loop(
        self,
        targets: tuple[str, ...],
        values: Iterable[tuple[LuaValue, ...]],
        body: ast.Block,
    ) -> None:
        """Run a bounded loop while keeping its iterator variables local."""
        saved: dict[str, LuaValue] = {
            target: self._values[target] for target in targets if target in self._values
        }
        for item in values:
            for target, value in zip(targets, item, strict=False):
                self._values[target] = value
            for target in targets[len(item) :]:
                self._values[target] = None
            for stat in body.body:
                self._statement(stat)
        for target in targets:
            if target not in saved:
                self._values.pop(target, None)
            else:
                self._values[target] = saved[target]

    @staticmethod
    def _call_name(node: ast.Node) -> tuple[str, ...] | None:
        """Return a syntactic call path without resolving or executing it."""
        if not isinstance(node, ast.Call):
            return None
        match node.func:
            case ast.Name():
                return (node.func.id,)
            case ast.Index(value=root_node, idx=ast.Name(id=member)):
                root = root_node.id if isinstance(root_node, ast.Name) else None
                if root is None and _Evaluator._call_name(root_node) == ('require',):
                    args = root_node.args if isinstance(root_node, ast.Call) else ()
                    if len(args) == 1 and isinstance(args[0], ast.String):
                        try:
                            root = args[0].s.decode('utf-8')
                        except UnicodeDecodeError:
                            return None
                return (root, member) if root is not None else None
            case _:
                return None

    def _table_insert(self, nodes: Iterable[ast.Node]) -> LuaValue:
        """Append a statically known value to a statically known Lua table."""
        args: tuple[LuaValue, ...] = tuple(self.value(node) for node in nodes)
        if len(args) != 2 or not isinstance(table := args[0], LuaTable):
            return UNKNOWN
        indices = [key for key in table.fields if type(key) is int and key > 0]
        table.fields[max(indices, default=0) + 1] = args[1]
        return None

    @overload
    def _deepcopy(self, value: LuaTable) -> LuaTable: ...

    @overload
    def _deepcopy[T: LuaValue](self, value: T) -> T: ...

    def _deepcopy(self, value: LuaValue) -> LuaValue:
        """Copy a known Lua value without invoking third-party Lua code."""
        if not isinstance(value, LuaTable):
            return value
        return LuaTable({key: self._deepcopy(item) for key, item in value.fields.items()})

    def _string_format(self, nodes: Iterable[ast.Node]) -> LuaValue:
        """Format a static Lua string with Python-compatible format specifiers."""
        args = tuple(self.value(node) for node in nodes)
        if not args or not isinstance(args[0], str) or any(value is UNKNOWN for value in args[1:]):
            return UNKNOWN
        try:
            # TODO: Replace this Python-compatible subset with conservative Lua formatting.
            return args[0] % args[1:]
        except TypeError, ValueError:
            return UNKNOWN

    def _string_case(self, nodes: Iterable[ast.Node], *, lower: bool) -> LuaValue:
        """Apply one statically known Lua string case conversion."""
        value = self.value(_first(nodes))
        if not isinstance(value, str):
            return UNKNOWN
        return value.lower() if lower else value.upper()

    def _tostring(self, nodes: Iterable[ast.Node]) -> LuaValue:
        """Return the Lua string form of a statically known scalar."""
        match self.value(_first(nodes)):
            case bool() as value:
                return str(value).lower()
            case str() | int() | float() as value:
                return str(value)
            case _:
                return UNKNOWN

    def _table(self, fields: Iterable[ast.Field]) -> LuaTable:
        table = LuaTable()
        array_index = 1
        for field_node in fields:
            if field_node.key is None:
                key = array_index
                array_index += 1
            elif (key := self._key(field_node.key)) is UNKNOWN:
                continue
            table.fields[key] = self.value(field_node.value)
        return table

    def _key(self, node: ast.Node) -> LuaKey | UNKNOWN:
        if isinstance(node, ast.Name):
            return node.id
        return value if _is_key(value := self.value(node)) else UNKNOWN

    def _index_key(self, node: ast.Index) -> LuaKey | UNKNOWN:
        """Resolve a table key, respecting Lua's dot and square index forms."""
        if node.notation is ast.IndexNotation.DOT:
            return self._key(node.idx)
        return value if _is_key(value := self.value(node.idx)) else UNKNOWN

    def _numeric(
        self,
        node: LuaNumericOperation,
    ) -> LuaValue:
        if not _is_number(left := self.value(node.left)):
            return UNKNOWN
        if not _is_number(right := self.value(node.right)):
            return UNKNOWN
        with suppress(ArithmeticError):
            match node:
                case ast.AddOp():
                    return left + right
                case ast.SubOp():
                    return left - right
                case ast.MultOp():
                    return left * right
                case ast.FloatDivOp():
                    return left / right
                case ast.FloorDivOp():
                    return left // right
                case ast.ModOp():
                    return left % right
                case ast.ExpoOp():
                    return left**right

        return UNKNOWN
