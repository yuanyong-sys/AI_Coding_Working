from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def template_analysis(request: object) -> list[str]:
    if request.template == "日报":
        return [f"当日执行：任务 {len(request.rows)} 项，重点展示逐任务处置明细"]
    if request.template == "周报":
        dates = sorted({str(row.get("日期", "未标注")) for row in request.rows})
        return [f"七日趋势：覆盖 {len(dates)} 个有数据日期", "日期分布：" + "、".join(dates)]
    if request.template == "月报":
        districts = sorted({str(row.get("辖区", "未标注")) for row in request.rows})
        return [f"月度覆盖：涉及 {len(districts)} 个辖区", "辖区分布：" + "、".join(districts)]
    return [f"自定义视图：按当前筛选导出 {len(request.fields)} 个字段、{len(request.rows)} 条记录"]


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
    lines.extend(template_analysis(request))
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
    analysis_row = header_row + len(request.rows) + 2
    for line in template_analysis(request):
        rows += f'<row r="{analysis_row}">{_cell("A", analysis_row, line)}</row>'
        analysis_row += 1
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
    page_width, page_height = document._pagesize
    left, right, leading = 48, 48, 18
    max_width = page_width - left - right
    y = page_height - 48
    for line in lines:
        wrapped, current = [], ""
        for char in line:
            candidate = current + char
            if current and pdfmetrics.stringWidth(candidate, font_name, 11) > max_width:
                wrapped.append(current)
                current = char
            else:
                current = candidate
        wrapped.append(current)
        for segment in wrapped:
            if y < 48:
                document.showPage()
                document.setFont(font_name, 11)
                y = page_height - 48
            document.drawString(left, y, segment)
            y -= leading
    document.save()
    return output.getvalue()
