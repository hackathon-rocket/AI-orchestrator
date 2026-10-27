import io
from datetime import date
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

from app.core.errors import ServiceError
from app.processors.excel import read_xlsx_bytes

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def workbook():
    book = Workbook()
    ws = book.active
    ws.title = "Faktúry"
    ws.append(["Číslo faktúry", "Suma spolu", "Dátum vystavenia"])
    ws.append(["F-1", 123, date(2026, 10, 2)])
    ws["B3"] = "=B2*2"
    book.create_sheet("Prázdny")
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def rewrite(payload, name, value):
    output = io.BytesIO()
    with ZipFile(io.BytesIO(payload)) as original, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for entry in original.infolist():
            target.writestr(
                entry.filename, value if entry.filename == name else original.read(entry.filename)
            )
    return output.getvalue()


def test_multiple_sheets_dates_headers_formula(settings):
    result = read_xlsx_bytes(workbook(), settings)
    assert len(result.sheets) == 2 and result.sheets[1].rows == []
    cells = {c.coordinate: c for r in result.sheets[0].rows for c in r.cells}
    assert cells["B2"].header_cells == ["B1"]
    assert cells["C2"].value == "2026-10-02"
    assert cells["B3"].value is None and cells["B3"].formula == "=B2*2"


@pytest.mark.parametrize("payload", [b"not a zip", b"PK\x03\x04broken", b""])
def test_invalid_archive_is_sanitized(client, payload):
    response = client.post("/v1/prepare-xlsx", content=payload, headers={"Content-Type": MIME})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_workbook"


def test_xlsx_auth_before_parsing(client):
    response = client.post(
        "/v1/prepare-xlsx",
        content=b"bad",
        headers={"Content-Type": MIME, "Authorization": "Bearer bad"},
    )
    assert response.status_code == 401


def test_empty_workbook(settings):
    book = Workbook()
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    with pytest.raises(ServiceError, match="empty_workbook"):
        read_xlsx_bytes(stream.getvalue(), settings)


@pytest.mark.parametrize(
    "limit,value",
    [
        ("max_xlsx_bytes", 1000),
        ("max_xlsx_expanded_bytes", 1000),
        ("max_xlsx_compression_ratio", 1),
        ("max_xlsx_xml_nodes", 100),
        ("max_rows", 1),
        ("max_cells", 2),
        ("max_sheets", 1),
    ],
)
def test_resource_limits(settings, limit, value):
    setattr(settings, limit, value)
    with pytest.raises(ServiceError) as error:
        read_xlsx_bytes(workbook(), settings)
    assert error.value.status_code == 413


def test_xml_entity_rejected(settings):
    xml = b'<!DOCTYPE x [<!ENTITY unsafe "secret">]><x>&unsafe;</x>'
    with pytest.raises(ServiceError, match="invalid_workbook"):
        read_xlsx_bytes(rewrite(workbook(), "xl/workbook.xml", xml), settings)


def test_external_relationship_rejected(settings):
    xml = b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="external" Target="https://example.invalid" TargetMode="External"/></Relationships>'
    with pytest.raises(ServiceError, match="unsupported_workbook_features"):
        read_xlsx_bytes(rewrite(workbook(), "_rels/.rels", xml), settings)


def test_inconsistent_dimensions_rejected(settings):
    with ZipFile(io.BytesIO(workbook())) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml").replace(b'ref="A1:C3"', b'ref="A1:A1"')
    with pytest.raises(ServiceError, match="invalid_workbook"):
        read_xlsx_bytes(rewrite(workbook(), "xl/worksheets/sheet1.xml", xml), settings)


def test_macro_archive_rejected(settings):
    payload = io.BytesIO(workbook())
    with ZipFile(payload, "a") as archive:
        archive.writestr("xl/vbaProject.bin", b"not executable")
    with pytest.raises(ServiceError, match="unsupported_workbook_features"):
        read_xlsx_bytes(payload.getvalue(), settings)


def test_unexpected_media_type(client):
    assert (
        client.post(
            "/v1/prepare-xlsx", content=workbook(), headers={"Content-Type": "application/json"}
        ).status_code
        == 415
    )


def test_xlsx_preparation_then_authenticated_analysis(client):
    book = Workbook()
    sheet = book.active
    sheet.append(["FAKTÚRA č. XLS-1"])
    sheet.append(["Číslo faktúry", "Suma spolu", "Mena"])
    sheet.append(["XLS-1", 123.0, "EUR"])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    response = client.post(
        "/v1/prepare-xlsx", content=stream.getvalue(), headers={"Content-Type": MIME}
    )
    assert response.status_code == 200
    content = response.json()["content"]
    assert all(c["header_cells"] == [] for c in content["sheets"][0]["rows"][1]["cells"])
    result = client.post(
        "/v1/analyze",
        json={
            "document_id": "xlsx-flow",
            "content": content,
            "form": {"id": "bbsk_11824", "mapping_id": "bbsk_11824_default"},
        },
    )
    assert result.status_code == 200, result.text
    row = result.json()["tables"][0]["rows"][0]
    assert row["suma_doklad"]["value"] == 123.0
    assert row["c_dokladu"]["value"] == "XLS-1"
    assert row["suma_doklad"]["source_locator"]["cell"] == "B3"
    assert row["suma_dotacia"]["value"] is None
