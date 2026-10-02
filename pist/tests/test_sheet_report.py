from pist.sheet_report import (
    ManualRecordField,
    field_coverage_report,
    inspection_fields,
    manual_record_fields,
)
from pist.smartsheet import (
    FieldsResult,
    FileIdConversion,
    GetSheetsData,
    InspectionReport,
    RecordsResult,
    SmartSheet,
    SubSheetInspection,
    ViewsResult,
)


def test_field_coverage_report_classifies_current_planned_and_formula_fields() -> None:
    report = InspectionReport(
        file_id='file',
        sheets=[
            SubSheetInspection(
                sheet=SmartSheet(sheet_id='sheet', title='初见'),
                views=ViewsResult(),
                fields=FieldsResult.model_validate(
                    {
                        'fields': [
                            {'fieldID': 'mod', 'fieldTitle': 'Mod元数据名', 'fieldType': 1},
                            {'fieldID': 'berry', 'fieldTitle': '红草莓数', 'fieldType': 2},
                            {
                                'fieldID': 'average',
                                'fieldTitle': '平均单面死亡数',
                                'fieldType': 19,
                                'propertyFormula': {},
                            },
                        ]
                    }
                ),
                records=RecordsResult(records=[]),
            )
        ],
    )

    assert [field.field_id for field in inspection_fields(report)] == ['mod', 'berry', 'average']
    assert field_coverage_report(report) == (
        '# Smart Sheet 字段覆盖报告\n\n'
        '文件 ID：`file`\n\n'
        '“当前记录”表示生成本地记录时已经可以稳定填入；“计划自动”尚未实现。\n\n'
        '## 初见（3 列）\n\n'
        '| 字段 | 类型 | 覆盖状态 | 数据来源 / 说明 |\n'
        '| --- | --- | --- | --- |\n'
        '| Mod元数据名 | 文本 | 当前记录 | everest.yaml |\n'
        '| 红草莓数 | 数字 | 计划自动 | .bin 地图实体分析 |\n'
        '| 平均单面死亡数 | 公式 | 公式列 | 由表格公式计算，不写入 |\n'
    )


def test_api_models_accept_camel_case_aliases() -> None:
    assert FileIdConversion.model_validate({'fileID': 'first', 'id': 'second'}).file_id == 'first'
    sheets = GetSheetsData.model_validate(
        {'getSheet': [{'sheetID': 'sheet', 'isVisible': True}]}
    ).sheets

    assert sheets[0].sheet_id == 'sheet'
    assert sheets[0].is_visible


def test_manual_record_fields_use_main_table_metadata_and_select_options() -> None:
    report = InspectionReport(
        file_id='file',
        sheets=[
            SubSheetInspection(
                sheet=SmartSheet(sheet_id='other', title='其他'),
                views=ViewsResult(),
                fields=FieldsResult.model_validate(
                    {'fields': [{'fieldID': 'note', 'fieldTitle': '备注', 'fieldType': 1}]}
                ),
                records=RecordsResult(records=[]),
            ),
            SubSheetInspection(
                sheet=SmartSheet(sheet_id='main', title='主表'),
                views=ViewsResult(),
                fields=FieldsResult.model_validate(
                    {
                        'fields': [
                            {
                                'fieldID': 'felt-difficulty',
                                'fieldTitle': '体感难度',
                                'fieldType': 17,
                                'propertySingleSelect': {
                                    'options': [{'id': 'advanced', 'text': '高级'}]
                                },
                            },
                            {
                                'fieldID': 'felt-subtier',
                                'fieldTitle': '难度子阶',
                                'fieldType': 17,
                                'propertySingleSelect': {'options': [{'id': 'low', 'text': '低'}]},
                            },
                            {
                                'fieldID': 'rated-difficulty',
                                'fieldTitle': '标注难度',
                                'fieldType': 17,
                                'propertySingleSelect': {
                                    'options': [{'id': 'expert', 'text': '专家'}]
                                },
                            },
                            {
                                'fieldID': 'rated-subtier',
                                'fieldTitle': '标注难度子阶',
                                'fieldType': 17,
                                'propertySingleSelect': {'options': [{'id': 'high', 'text': '高'}]},
                            },
                            {'fieldID': 'start', 'fieldTitle': '起始日期', 'fieldType': 4},
                            {
                                'fieldID': 'status',
                                'fieldTitle': '状态',
                                'fieldType': 17,
                                'propertySingleSelect': {
                                    'options': [
                                        {'id': 'done', 'text': '通关'},
                                        {'id': 'playing', 'text': '进行中'},
                                    ]
                                },
                            },
                            {'fieldID': 'score', 'fieldTitle': '评分', 'fieldType': 2},
                            {'fieldID': 'note', 'fieldTitle': '备注', 'fieldType': 1},
                            {'fieldID': 'tag', 'fieldTitle': '标签', 'fieldType': 9},
                        ]
                    }
                ),
                records=RecordsResult(records=[]),
            ),
        ],
    )

    assert manual_record_fields(report) == (
        ManualRecordField('体感难度', 17, ('高级',)),
        ManualRecordField('难度子阶', 17, ('低',)),
        ManualRecordField('标注难度', 17, ('专家',)),
        ManualRecordField('标注难度子阶', 17, ('高',)),
        ManualRecordField('起始日期', 4),
        ManualRecordField('状态', 17, ('通关', '进行中')),
        ManualRecordField('评分', 2),
        ManualRecordField('备注', 1),
    )
