"""Bounded, non-executing XLSX preparation shared by CLI and internal HTTP API."""

import io
import re
import time
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from defusedxml.ElementTree import iterparse
from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string, range_boundaries

from app.core.config import Settings
from app.core.errors import ServiceError
from app.processors.ocr_text import normalize, validate_content_limits
from app.schemas.requests import Cell, Content, Sheet, SheetRow
from app.validators.semantics import LABELS


def read_xlsx(path: Path, settings: Settings) -> Content:
    if path.stat().st_size > settings.max_xlsx_bytes:
        raise ServiceError("workbook_too_large", "Zošit prekračuje povolený limit.", 413)
    return read_xlsx_bytes(path.read_bytes(), settings)


def read_xlsx_bytes(payload: bytes, settings: Settings) -> Content:
    deadline = time.monotonic() + settings.xlsx_timeout_seconds

    def check_time():
        if time.monotonic() > deadline:
            raise ServiceError(
                "workbook_timeout", "Časový limit spracovania zošita bol prekročený.", 504
            )

    def too_large():
        raise ServiceError("workbook_too_large", "Zošit prekračuje povolený limit.", 413)

    if len(payload) > settings.max_xlsx_bytes:
        too_large()
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            names = [entry.filename for entry in infos]
            if (
                len(infos) > settings.max_xlsx_entries
                or sum(i.file_size for i in infos) > settings.max_xlsx_expanded_bytes
            ):
                too_large()
            if len(names) != len(set(names)) or not {
                "[Content_Types].xml",
                "xl/workbook.xml",
            } <= set(names):
                raise ValueError("Invalid workbook archive")
            xml_nodes, actual_cells, actual_rows = 0, 0, 0
            for entry in infos:
                check_time()
                name = entry.filename
                if (
                    PurePosixPath(name).is_absolute()
                    or ".." in PurePosixPath(name).parts
                    or "\\" in name
                    or entry.flag_bits & 1
                    or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
                ):
                    raise ValueError("Unsafe archive entry")
                if (
                    entry.file_size / max(entry.compress_size, 1)
                    > settings.max_xlsx_compression_ratio
                ):
                    too_large()
                if any(
                    part in name.lower()
                    for part in ["vbaproject", "macrosheet", "externallinks", "embeddings/"]
                ):
                    raise ServiceError(
                        "unsupported_workbook_features",
                        "Makrá, externé prepojenia a vložené objekty nie sú podporované.",
                    )
                if not name.endswith((".xml", ".rels")):
                    continue
                dimensions = None
                max_row = max_col = 0
                with archive.open(entry) as stream:
                    for _, element in iterparse(
                        stream,
                        events=("end",),
                        forbid_dtd=True,
                        forbid_entities=True,
                        forbid_external=True,
                    ):
                        check_time()
                        xml_nodes += 1
                        if xml_nodes > settings.max_xlsx_xml_nodes:
                            too_large()
                        local = element.tag.rsplit("}", 1)[-1]
                        if local == "Relationship" and element.get("TargetMode") == "External":
                            raise ServiceError(
                                "unsupported_workbook_features",
                                "Externé prepojenia nie sú podporované.",
                            )
                        if name.startswith("xl/worksheets/"):
                            if local == "dimension":
                                dimensions = range_boundaries(element.get("ref", ""))
                            elif local == "c":
                                actual_cells += 1
                                coord = element.get("r", "")
                                match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", coord)
                                if (
                                    not match
                                    or int(match[2]) > settings.max_rows
                                    or actual_cells > settings.max_cells
                                ):
                                    too_large()
                                col = column_index_from_string(match[1])
                                max_row, max_col = max(max_row, int(match[2])), max(max_col, col)
                                if col > 1000 or max_row * max_col > settings.max_cells * 10:
                                    too_large()
                            elif local == "row":
                                actual_rows += 1
                                if actual_rows > settings.max_rows:
                                    too_large()
                        element.clear()
                if dimensions and (max_col > dimensions[2] or max_row > dimensions[3]):
                    raise ValueError("Inconsistent worksheet dimensions")
        check_time()
        book = load_workbook(io.BytesIO(payload), read_only=True, data_only=False, keep_links=False)
        try:
            if len(book.sheetnames) > settings.max_sheets:
                too_large()
            sheets, cell_count, chars = [], 0, 0
            known_headers = {
                normalize(label).rstrip(" :") for values in LABELS.values() for label in values
            }
            for ws in book:
                check_time()
                if (ws.max_row or 0) > settings.max_rows or (ws.max_column or 0) > 1000:
                    too_large()
                # Reject pathological dense dimensions before iter_rows pads missing cells.
                if (ws.max_row or 0) * (ws.max_column or 0) > settings.max_cells * 10:
                    too_large()
                rows, column_headers = [], {}
                for row in ws.iter_rows():
                    check_time()
                    cells, row_header = [], None
                    for c in row:
                        if c.value is None:
                            continue
                        cell_count += 1
                        if cell_count > settings.max_cells:
                            too_large()
                        value = c.value
                        if isinstance(value, (date, datetime)):
                            value = (
                                value.date().isoformat()
                                if isinstance(value, datetime)
                                else value.isoformat()
                            )
                        if isinstance(value, bool):
                            value = str(value)
                        chars += len(str(value))
                        if chars > settings.max_content_chars:
                            too_large()
                        formula = str(value) if c.data_type == "f" else None
                        headers = []
                        is_header = (
                            not formula
                            and isinstance(value, str)
                            and normalize(value).rstrip(" :") in known_headers
                        )
                        if not formula and not is_header:
                            if c.column in column_headers:
                                headers.append(column_headers[c.column])
                            if row_header:
                                headers.append(row_header)
                        cells.append(
                            Cell(
                                coordinate=c.coordinate,
                                value=None if formula else value,
                                formula=formula,
                                header_cells=headers,
                            )
                        )
                        if is_header:
                            column_headers[c.column] = c.coordinate
                            row_header = c.coordinate
                    if cells:
                        rows.append(
                            SheetRow(
                                row=int(re.search(r"\d+", cells[0].coordinate)[0]), cells=cells
                            )
                        )
                sheets.append(Sheet(name=ws.title, rows=rows))
            check_time()
            if not any(
                c.value is not None and str(c.value).strip()
                for s in sheets
                for r in s.rows
                for c in r.cells
            ):
                raise ServiceError("empty_workbook", "Zošit neobsahuje žiadne priame hodnoty.")
            content = Content(sheets=sheets)
            validate_content_limits(content, settings)
            return content
        finally:
            book.close()
    except ServiceError:
        raise
    except Exception:
        # XML/library diagnostics can contain paths or source values; never forward them.
        raise ServiceError(
            "invalid_workbook", "Súbor nie je platný podporovaný XLSX zošit."
        ) from None
