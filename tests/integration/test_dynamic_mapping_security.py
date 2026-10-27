import copy

import pytest


def request(load_fixture):
    payload = load_fixture("clean_invoice")
    payload["form"] = {
        "id": "new_form",
        "mapping_id": "new_v1",
        "schema": {"type": "root", "children": [{"type": "SIMPLE_STRING", "tag": "number"}]},
        "mapping": {
            "id": "new_v1",
            "form_id": "new_form",
            "rules": [{"source": "invoice_number", "destination": "number"}],
        },
    }
    return payload


@pytest.mark.parametrize(
    "kind",
    [
        "unknown_source",
        "unknown_destination",
        "duplicate",
        "readonly",
        "forced",
        "computed",
        "select",
        "reserved",
        "date_type",
        "identity",
        "no_schema",
        "unknown_doc_type",
    ],
)
def test_invalid_inline_mapping_is_rejected(client, load_fixture, kind):
    payload = request(load_fixture)
    form = payload["form"]
    rule = form["mapping"]["rules"][0]
    node = form["schema"]["children"][0]
    if kind == "unknown_source":
        rule["source"] = "grant_amount"
    if kind == "unknown_destination":
        rule["destination"] = "absent"
    if kind == "duplicate":
        form["mapping"]["rules"].append(copy.deepcopy(rule))
    if kind == "readonly":
        node["config"] = {"is_readonly": True}
    if kind == "forced":
        node["force_source_value"] = True
    if kind == "computed":
        node["config"] = {"formula": "1+1"}
    if kind == "select":
        node.update(type="SELECT", config={"values": {"FA": "Faktúra"}})
        rule["value_map"] = {"invoice": "WRONG"}
    if kind == "reserved":
        form["mapping"]["reserved"] = ["number"]
    if kind == "date_type":
        node["type"] = "DATE"
    if kind == "identity":
        form["mapping"]["form_id"] = "somebody_else"
    if kind == "no_schema":
        del form["schema"]
    if kind == "unknown_doc_type":
        form["mapping"]["document_types"] = ["unknown"]
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "source,tag,currency",
    [
        ("total_gross", "suma_dotacia", None),
        ("due_date", "datum_1", None),
        ("issue_date", "datum", None),
        ("total_net", "suma_doklad", "EUR"),
        ("total_gross", "suma_doklad", "CZK"),
        ("total_gross", "suma_doklad", None),
    ],
)
def test_builtin_bbsk_rules_cannot_be_overridden(client, load_fixture, source, tag, currency):
    payload = request(load_fixture)
    form = payload["form"]
    path = "rozpocet.zoznamuctovnychdokladov." + tag
    form["schema"] = {
        "type": "root",
        "children": [
            {
                "type": "TABLE_LIST",
                "tag": "rozpocet.zoznamuctovnychdokladov",
                "config": {"add_rows": True},
                "children": [{"type": "TABLE_ITEM_STRING", "tag": tag}],
            }
        ],
    }
    form["mapping"]["rules"] = [
        {"source": source, "destination": path, "required_currency": currency}
    ]
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 422, response.text


def test_inline_request_does_not_register_mapping(client, load_fixture):
    payload = request(load_fixture)
    assert client.post("/v1/analyze", json=payload).status_code == 200
    del payload["form"]["mapping"]
    assert client.post("/v1/analyze", json=payload).status_code == 422


def test_foreign_table_column_rejected(client, load_fixture):
    payload = request(load_fixture)
    payload["form"]["schema"] = {
        "type": "root",
        "children": [
            {
                "type": "TABLE_LIST",
                "tag": "table",
                "config": {"add_rows": True},
                "children": [{"type": "TABLE_ITEM_STRING", "tag": "other.number"}],
            }
        ],
    }
    payload["form"]["mapping"]["rules"][0]["destination"] = "other.number"
    assert client.post("/v1/analyze", json=payload).status_code == 422


@pytest.mark.parametrize(
    "table_tag,column_tag,destination",
    [("", "number", "number"), ("table", "table.nested.number", "table.nested.number")],
)
def test_table_columns_require_named_direct_parent(
    client, load_fixture, table_tag, column_tag, destination
):
    payload = request(load_fixture)
    payload["form"]["schema"] = {
        "type": "root",
        "children": [
            {
                "type": "TABLE_LIST",
                "tag": table_tag,
                "config": {"add_rows": True},
                "children": [{"type": "TABLE_ITEM_STRING", "tag": column_tag}],
            }
        ],
    }
    payload["form"]["mapping"]["rules"][0]["destination"] = destination
    assert client.post("/v1/analyze", json=payload).status_code == 422


def test_empty_select_mapping_does_not_pass_through_source(client, load_fixture):
    payload = request(load_fixture)
    payload["form"]["schema"]["children"][0].update(
        type="SELECT", config={"values": {"prevodom": "Bank transfer"}}
    )
    payload["form"]["mapping"]["rules"][0].update(source="payment_method", value_map={})
    response = client.post("/v1/analyze", json=payload)

    assert response.status_code == 200, response.text
    field = response.json()["fields"][0]
    assert field["value"] is None
    assert field["status"] == "not_found"


@pytest.mark.parametrize(
    "language", ["", "sk++en", "slovak", "sk/en", "sk+", "123", "unknown", "sk+en+cs+de+hu+pl"]
)
def test_invalid_language_rejected(client, load_fixture, language):
    payload = load_fixture("clean_invoice")
    payload["content"]["language"] = language
    assert client.post("/v1/analyze", json=payload).status_code == 422
