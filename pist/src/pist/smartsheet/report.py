"""Human-readable coverage reports and record-field metadata from Sheet inspections."""

from dataclasses import dataclass

from pist.records.fields import RECORD_ATTRIBUTES
from pist.smartsheet.models import FieldsResult, FieldType, InspectionReport

FIELD_TYPES: dict[int, str] = {
    FieldType.TEXT: '文本',
    FieldType.NUMBER: '数字',
    FieldType.CHECKBOX: '复选框',
    FieldType.DATE: '日期',
    FieldType.LINK: '链接',
    FieldType.MULTI_SELECT: '多选',
    FieldType.SINGLE_SELECT: '单选',
    FieldType.FORMULA: '公式',
}

FIELD_SOURCES = {
    'Mod元数据名': ('当前记录', 'everest.yaml'),
    'Mod名': ('计划自动', 'GameBanana 标题和链接'),
    '地图名': ('当前记录', 'Dialog 本地化名称'),
    '作者': ('计划自动', 'GameBanana Credits 或合集 Dialog'),
    '标注难度': ('计划自动', 'GameBanana 描述或游戏内标签'),
    '更新时间': ('计划自动', 'GameBanana 最后更新时间'),
    '起始日期': ('待定', '人工填写；后续研究日志或 Mod 数据'),
    '结束日期': ('待定', '人工填写；后续研究日志或 Mod 数据'),
    '用时': ('当前记录', '原生存档 TimePlayed'),
    '死亡数': ('当前记录', '原生存档 Deaths'),
    '红草莓数': ('计划自动', '.bin 地图实体分析'),
    '月莓数': ('计划自动', '.bin 地图实体分析'),
    '磁带': ('计划自动', '.bin 地图实体分析'),
    '水晶之心': ('计划自动', '.bin 地图实体分析'),
    '主房间数': ('当前记录', '用户保存的地图主路线房间数'),
    '状态': ('人工填写', '用户选择；存档推断的通关状态仅供参考'),
    '标签': ('人工填写', '尚无可靠自动提取规则'),
    '体感难度': ('人工填写', '主观评价'),
    '难度子阶': ('人工填写', '主观评价'),
    '标注难度子阶': ('人工填写', '需先确定标注难度规则'),
    'SL使用': ('人工填写', '练习与存档读取无法可靠区分'),
    '评分': ('人工填写', '主观评价'),
    '备注': ('人工填写', '自由文本'),
}

MAIN_TABLE_TITLE = '主表'


@dataclass(frozen=True, slots=True)
class ManualRecordField:
    """One editable public record field and its available sheet options."""

    title: str
    field_type: int
    options: tuple[str, ...] = ()


def manual_record_fields(report: InspectionReport) -> tuple[ManualRecordField, ...]:
    """Return supported editable fields declared by the inspected main table."""
    for inspection in report.sheets:
        if inspection.sheet.title != MAIN_TABLE_TITLE:
            continue
        return manual_fields_for_schema(inspection.fields)
    return ()


def manual_fields_for_schema(fields: FieldsResult) -> tuple[ManualRecordField, ...]:
    """Build editor controls from one freshly validated main-table schema."""
    return tuple(
        ManualRecordField(
            field.field_title,
            field.field_type,
            ()
            if field.property_single_select is None
            else tuple(option.text for option in field.property_single_select.options),
        )
        for field in fields.fields
        if field.field_title in RECORD_ATTRIBUTES and field.property_formula is None
    )


def field_coverage_report(report: InspectionReport) -> str:
    """Render the current automation coverage as Markdown."""
    lines = [
        '# Smart Sheet 字段覆盖报告',
        '',
        f'文件 ID：`{report.file_id}`',
        '',
        '“当前记录”表示生成本地记录时已经可以稳定填入；“计划自动”尚未实现。',
    ]
    for inspection in report.sheets:
        fields = inspection.fields.fields
        lines.extend(
            [
                '',
                f'## {inspection.sheet.title}（{len(fields)} 列）',
                '',
                '| 字段 | 类型 | 覆盖状态 | 数据来源 / 说明 |',
                '| --- | --- | --- | --- |',
            ]
        )
        for field in fields:
            field_type = FIELD_TYPES.get(field.field_type, f'未知（{field.field_type}）')
            if field.property_formula is not None:
                status, source = '公式列', '由表格公式计算，不写入'
            else:
                status, source = FIELD_SOURCES.get(field.field_title, ('待确认', '尚未定义规则'))
            lines.append(f'| {field.field_title} | {field_type} | {status} | {source} |')
    return '\n'.join(lines) + '\n'
