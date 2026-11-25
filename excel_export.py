"""
配方 Excel 导出（工厂加工用）
================================
批量生成一份标准 Excel 配方表，覆盖本次批量里的所有目标色：
每个目标色（配方）在表里占若干行（等于它的颜色数量），目标色相关的列
（配方编号/目标色名称/目标色色块/HEX/材料/ΔE00）纵向合并成一格，
配比色号相关的列（色号/颜色块/比例）每行各自独立 —— 阅读起来像一张
真实的工厂配方交底表，而不是一条条重复的流水账。

只保留「比例」，不包含重量(kg)：比例 + 总产量就能在需要时换算出具体
用量，主表里重复展示反而增加干扰，这和界面上的简化保持一致。

颜色相关的单元格用实际色值填充背景色（openpyxl PatternFill），
让工厂人员核对色号时能直接用眼睛比对色块，减少读错色号的成本。
"""

import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="232326")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
BAND_FILL = PatternFill("solid", fgColor="F5F4F2")
THIN = Side(style="thin", color="D9D7D3")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

COLUMNS = [
    ("配方编号", 12),
    ("目标色名称", 16),
    ("目标色", 10),
    ("目标色 HEX", 13),
    ("材料", 9),
    ("ΔE00", 8),
    ("配比色号", 13),
    ("色号 HEX", 13),
    ("对应颜色块", 10),
    ("配比比例", 10),
]


def _fill_for(hex_color):
    h = str(hex_color).lstrip("#").upper()
    if len(h) != 6:
        return None
    return PatternFill("solid", fgColor=h)


def build_recipe_workbook(batch_items):
    """
    batch_items: [
      {
        "formula_id": "R1", "name": "深海蓝", "target_hex": "#07166B",
        "material": "EPDM", "delta_e": 3.2,
        "components": [{"色号":"FK-048","HEX":"#0B1A5C","比例":76}, ...],
      }, ...
    ]
    返回 .xlsx 文件的字节内容。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "EPDM配方表"

    for col_idx, (name, width) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width
        cell = ws.cell(row=1, column=col_idx, value=name)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
        cell.border = BORDER
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 22

    row = 2
    for band_idx, item in enumerate(batch_items):
        comps = item["components"]
        n = max(1, len(comps))
        start_row = row
        end_row = row + n - 1
        band = BAND_FILL if band_idx % 2 == 1 else None

        # ---- 目标色相关列：纵向合并 ----
        target_values = [
            item.get("formula_id", ""),
            item.get("name", ""),
            None,  # 目标色色块，用填色表示，不写文字
            item.get("target_hex", ""),
            item.get("material", ""),
            item.get("delta_e", ""),
        ]
        for col_idx, val in enumerate(target_values, start=1):
            top_cell = ws.cell(row=start_row, column=col_idx, value=val)
            if n > 1:
                ws.merge_cells(start_row=start_row, start_column=col_idx,
                               end_row=end_row, end_column=col_idx)
            for r in range(start_row, end_row + 1):
                c = ws.cell(row=r, column=col_idx)
                c.alignment = CENTER
                c.border = BORDER
                if band and col_idx != 3:
                    c.fill = band
            if col_idx == 3:  # 目标色色块列，整段合并区域填色
                fill = _fill_for(item.get("target_hex", ""))
                if fill:
                    top_cell.fill = fill

        # ---- 配比色号相关列：逐行 ----
        for i, c in enumerate(comps):
            r = start_row + i
            ws.cell(row=r, column=7, value=c.get("色号", "")).border = BORDER
            ws.cell(row=r, column=8, value=c.get("HEX", "")).border = BORDER
            for col_idx in (7, 8):
                ws.cell(row=r, column=col_idx).alignment = CENTER
                if band:
                    ws.cell(row=r, column=col_idx).fill = band
            chip_cell = ws.cell(row=r, column=9)
            chip_cell.border = BORDER
            fill = _fill_for(c.get("HEX", ""))
            if fill:
                chip_cell.fill = fill
            pct_cell = ws.cell(row=r, column=10, value=f"{c.get('比例', '')}%")
            pct_cell.alignment = CENTER
            pct_cell.border = BORDER
            if band:
                pct_cell.fill = band

        row = end_row + 1

    return io.BytesIO(_save_to_bytes(wb)).getvalue()


def _save_to_bytes(wb):
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


if __name__ == "__main__":
    demo_items = [
        {
            "formula_id": "R1", "name": "1", "target_hex": "#07166B",
            "material": "EPDM", "delta_e": 3.2,
            "components": [
                {"色号": "FK-048", "HEX": "#0B1A5C", "比例": 76},
                {"色号": "FK-021", "HEX": "#1E3A8C", "比例": 12},
                {"色号": "FK-061", "HEX": "#B8B8B8", "比例": 6},
                {"色号": "FK-093", "HEX": "#6B7A2C", "比例": 6},
            ],
        },
        {
            "formula_id": "R2", "name": "橘红", "target_hex": "#EC7539",
            "material": "EPDM", "delta_e": 4.8,
            "components": [
                {"色号": "FK-041", "HEX": "#E17A46", "比例": 82},
                {"色号": "FK-092", "HEX": "#C0392B", "比例": 18},
            ],
        },
    ]
    data = build_recipe_workbook(demo_items)
    with open("recipe_demo.xlsx", "wb") as f:
        f.write(data)
    print(f"已生成 recipe_demo.xlsx，大小 {len(data)/1024:.1f} KB")
