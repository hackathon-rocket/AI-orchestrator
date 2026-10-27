import io

import pytest
from openpyxl import Workbook


@pytest.mark.parametrize("language", ["slk+eng", "ces+eng", " SLK+ENG "])
def test_ocr_language_variants(client, load_fixture, language):
    payload = load_fixture("clean_invoice")
    payload["content"]["language"] = language
    assert client.post("/v1/analyze", json=payload).status_code == 200


def test_new_manager_form_without_server_registration(client, load_fixture):
    payload = load_fixture("clean_invoice")
    payload["form"] = {
        "id": "manager_form",
        "mapping_id": "manager_v1",
        "schema": {
            "type": "root",
            "children": [
                {
                    "type": "SECTION",
                    "tag": "doklad",
                    "children": [
                        {"type": "SIMPLE_STRING", "tag": "cislo", "name": "Číslo faktúry"}
                    ],
                }
            ],
        },
        "mapping": {
            "id": "manager_v1",
            "form_id": "manager_form",
            "document_types": ["invoice"],
            "rules": [{"source": "invoice_number", "destination": "doklad.cislo"}],
        },
    }
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["fields"][0]["value"] == "FA-2026-001"


def test_authenticated_xlsx_preparation(client):
    book = Workbook()
    book.active.title = "Doklad"
    book.active.append(["Číslo faktúry", "Suma spolu"])
    book.active.append(["F-001", 123.0])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    response = client.post(
        "/v1/prepare-xlsx",
        content=stream.getvalue(),
        headers={
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        },
    )
    assert response.status_code == 200, response.text
    cells = response.json()["content"]["sheets"][0]["rows"][1]["cells"]
    assert cells[1]["coordinate"] == "B2"
