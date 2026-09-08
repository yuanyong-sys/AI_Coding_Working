from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def report_lines(request: object, generated_at: str) -> list[str]:
    template_summary = {
        "日报": "统计周期：当日任务与告警摘要",
        "周报": "统计周期：七日趋势与办结对比",
        "月报": "统计周期：月度辖区覆盖与成效",
        "自定义": "统计周期：自定义范围与字段",
    }[request.template]
    lines = [
        f"无人机巡检{request.template}", f"生成时间：{generated_at}",
        template_summary, f"统计口径：{request.filters}", f"演示操作者：{request.operator}",
        "统计指标：" + "；".join(f"{key}={value}" for key, value in request.stats.items()),
    ]
    lines.append(" | ".join(request.fields))
    lines.extend(" | ".join(str(row.get(field, "")) for field in request.fields) for row in request.rows)
    return lines


def _cell(column: str, row: int, value: object) -> str:
    return f'<c r="{column}{row}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'


def build_xlsx(request: object, lines: list[str]) -> bytes:
    summary_lines = lines[:6]
    rows = "".join(f'<row r="{index}">{_cell("A", index, line)}</row>' for index, line in enumerate(summary_lines, 1))
    header_row = len(summary_lines) + 1
    columns = [chr(ord("A") + index) for index in range(len(request.fields))]
    rows += f'<row r="{header_row}">' + "".join(_cell(column, header_row, field) for column, field in zip(columns, request.fields)) + '</row>'
    for row_index, record in enumerate(request.rows, header_row + 1):
        rows += f'<row r="{row_index}">' + "".join(_cell(column, row_index, record.get(field, "")) for column, field in zip(columns, request.fields)) + '</row>'
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="巡检报表" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{rows}</sheetData></worksheet>')
    return output.getvalue()


def build_pdf(lines: list[str]) -> bytes:
    output = BytesIO()
    font_path = Path("/System/Library/Fonts/STHeiti Light.ttc")
    font_name = "ReportChinese"
    pdfmetrics.registerFont(TTFont(font_name, str(font_path), subfontIndex=0))
    document = canvas.Canvas(output)
    document.setFont(font_name, 11)
    y = 800
    for line in lines:
        document.drawString(48, y, line[:90]); y -= 20
    document.save()
    return output.getvalue()
