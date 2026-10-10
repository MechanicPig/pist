"""Explicit mapping between personal record attributes and public sheet columns."""

RECORD_ATTRIBUTES = {
    'Mod元数据名': 'mod_metadata_name',
    'Mod名': 'mod_name',
    '地图名': 'map_name',
    '作者': 'authors',
    '更新时间': 'mod_updated_at',
    '用时': 'time_played',
    '死亡数': 'deaths',
    '红草莓数': 'n_strawberries',
    '月莓数': 'n_moonberries',
    '磁带': 'cassette',
    '水晶之心': 'heart',
    '主房间数': 'n_main_rooms',
    '状态': 'status',
    '标签': 'tags',
    '体感难度': 'perceived_difficulty',
    '难度子阶': 'perceived_difficulty_tier',
    '标注难度': 'rated_difficulty',
    '标注难度子阶': 'rated_difficulty_tier',
    '起始日期': 'started_at',
    '结束日期': 'finished_at',
    'SL使用': 'save_load_usage',
    '评分': 'rating',
    '备注': 'notes',
}

FORMULA_FIELD_TITLES = frozenset(
    {
        '平均单面死亡数',
        '平均单面用时',
        '草莓数',
        '平均单面用时（秒）',
        '用时（秒）',
        '体感难度权重',
    }
)
