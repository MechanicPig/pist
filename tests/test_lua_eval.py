from pist.loenn.eval import UNKNOWN, LuaTable, evaluate


def test_evaluator_keeps_known_fields_when_a_table_has_dynamic_values() -> None:
    module = evaluate(
        """local attrs = {
    moon = true,
    order = -1,
    skin = getCurrentSkin(),
}
return attrs
"""
    )

    table = module.returns[0]
    assert isinstance(table, LuaTable)
    assert table.get('moon') is True
    assert table.get('order') == -1
    assert table.get('skin') is UNKNOWN


def test_evaluator_resolves_static_variable_and_member_assignments() -> None:
    module = evaluate(
        """local attrs = {moon = true}
local entity = {}
entity.name = 'Test/Entity'
entity.placements = {{name = 'normal', data = attrs}}
return entity
"""
    )

    entity = module.returns[0]
    assert isinstance(entity, LuaTable)
    placements = entity.get('placements')
    assert isinstance(placements, LuaTable)
    placement = placements.get(1)
    assert isinstance(placement, LuaTable)
    data = placement.get('data')
    assert isinstance(data, LuaTable)
    assert data.get('moon') is True


def test_evaluator_skips_dynamic_table_keys_without_shifting_array_values() -> None:
    module = evaluate("""return {[getKey()] = 'dynamic', 'first'}""")

    table = module.returns[0]
    assert isinstance(table, LuaTable)
    assert table.get(1) == 'first'


def test_evaluator_calls_a_local_function_with_static_arguments() -> None:
    module = evaluate(
        """local function create_handler(name)
    local handler = {}
    handler.name = name
    handler.placements = {name = 'normal', data = {order = -1}}
    return handler
end
return create_handler('Test/Entity')
"""
    )

    entity = module.returns[0]
    assert isinstance(entity, LuaTable)
    assert entity.get('name') == 'Test/Entity'
    placements = entity.get('placements')
    assert isinstance(placements, LuaTable)
    assert placements.get('name') == 'normal'


def test_evaluator_expands_static_numeric_and_ipairs_loops() -> None:
    module = evaluate(
        """local colors = {'blue', 'rose'}
local placements = {}
for i, _ in ipairs(colors) do
    placements[i] = {
        name = string.format('cassette_%s', i - 1),
        data = {index = i - 1},
    }
end
for i = 3, 4 do
    table.insert(placements, {name = string.format('cassette_%s', i - 1)})
end
return placements
"""
    )

    placements = module.returns[0]
    assert isinstance(placements, LuaTable)
    assert tuple(
        placement.get('name')
        for index in range(1, 5)
        if isinstance(placement := placements.get(index), LuaTable)
    ) == ('cassette_0', 'cassette_1', 'cassette_2', 'cassette_3')
    first = placements.get(1)
    assert isinstance(first, LuaTable)
    data = first.get('data')
    assert isinstance(data, LuaTable)
    assert data.get('index') == 0


def test_evaluator_stops_ipairs_at_nil_or_unknown_values() -> None:
    module = evaluate(
        """local nil_items = {'first', nil, 'third'}
local unknown_items = {'first', getItem(), 'third'}
local nil_result = {}
local unknown_result = {}
for _, item in ipairs(nil_items) do
    table.insert(nil_result, item)
end
for _, item in ipairs(unknown_items) do
    table.insert(unknown_result, item)
end
return {nil_result, unknown_result}
"""
    )

    results = module.returns[0]
    assert isinstance(results, LuaTable)
    nil_result = results.get(1)
    unknown_result = results.get(2)
    assert isinstance(nil_result, LuaTable)
    assert isinstance(unknown_result, LuaTable)
    assert nil_result.get(1) == 'first'
    assert nil_result.get(2) is UNKNOWN
    assert unknown_result.get(1) == 'first'
    assert unknown_result.get(2) is UNKNOWN


def test_evaluator_skips_numeric_loop_with_overflowing_length() -> None:
    module = evaluate(
        """local items = {}
for index = 1, 999999999999999999999999999999999999999999999999999999999999999999 do
    table.insert(items, index)
end
return items
"""
    )

    items = module.returns[0]
    assert isinstance(items, LuaTable)
    assert items.fields == {}


def test_evaluator_assigns_nil_to_missing_loop_values() -> None:
    module = evaluate(
        """local extra = 'old'
for index, item, missing in ipairs({'item'}) do
    extra = missing
end
return extra
"""
    )

    assert module.returns == (None,)


def test_evaluator_expands_static_pairs_and_safe_copy_helpers() -> None:
    module = evaluate(
        """local axes = {Both = 'both', Horizontal = 'horizontal'}
local fields = {enabled = true}
local function placement(axis)
    local data = utils.deepcopy(fields)
    data['axis'] = string.lower(axis)
    return {name = axis, data = data}
end
local placements = {}
for _, axis in pairs(axes) do
    table.insert(placements, placement(axis))
end
return placements
"""
    )

    placements = module.returns[0]
    assert isinstance(placements, LuaTable)
    first = placements.get(1)
    second = placements.get(2)
    assert isinstance(first, LuaTable)
    assert isinstance(second, LuaTable)
    assert first.get('name') == 'both'
    assert second.get('name') == 'horizontal'


def test_evaluator_clones_an_explicit_static_module() -> None:
    strawberry = LuaTable(
        {
            'name': 'strawberry',
            'placements': LuaTable(
                {1: LuaTable({'name': 'normal', 'data': LuaTable({'moon': False})})}
            ),
        }
    )
    module = evaluate(
        """local strawberry = require('utils').deepcopy(require('entities.strawberry'))
strawberry.name = 'TestHelper/TrollStrawberry'
for _, placement in pairs(strawberry.placements) do
    placement.data.reappear = false
end
return strawberry
""",
        modules={'entities.strawberry': strawberry},
    )

    entity = module.returns[0]
    assert isinstance(entity, LuaTable)
    assert entity.get('name') == 'TestHelper/TrollStrawberry'
    placements = entity.get('placements')
    assert isinstance(placements, LuaTable)
    placement = placements.get(1)
    assert isinstance(placement, LuaTable)
    data = placement.get('data')
    assert isinstance(data, LuaTable)
    assert data.get('reappear') is False
    assert strawberry.get('name') == 'strawberry'
