"""Version metadata follows exactly the selected mapping and raw form schema."""

import copy
import json
import re

import pytest

from app.mapping.fingerprint import json_fingerprint, mapping_fingerprint
from app.schemas.mapping import InlineMapping
from tests.integration.test_mapping_status import mapping_form

FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}\Z")
KEYS = {
    "mapping_version",
    "mapping_fingerprint",
    "form_schema_fingerprint",
    "authoritative_constraints_fingerprint",
}


def analyze(client, payload):
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "completed" and data["requires_review"] is True
    assert data["meta"]["schema_version"] == "1.0"
    return data


def check_meta(meta, mapping, schema):
    assert meta["mapping_version"] == mapping.version
    assert FINGERPRINT.fullmatch(meta["mapping_fingerprint"])
    assert FINGERPRINT.fullmatch(meta["form_schema_fingerprint"])
    assert meta["mapping_fingerprint"] == mapping_fingerprint(mapping)
    assert meta["form_schema_fingerprint"] == json_fingerprint(schema)


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("schema_override", [False, True])
def test_all_registered_resolution_paths(client, load_fixture, settings, inline, schema_override):
    payload = load_fixture("clean_invoice")
    configured = json.loads(
        (settings.form_config_dir / "bbsk_11824_mapping.json").read_text(encoding="utf-8")
    )
    configured.pop("schema_file")
    schema = json.loads(
        (settings.form_config_dir / "bbsk_11824_schema.json").read_text(encoding="utf-8")
    )
    if inline:
        configured["version"] = "manager-2"
        payload["form"]["mapping"] = configured
    if schema_override:
        schema["tracking_note"] = "Request schema is the effective content"
        payload["form"]["schema"] = schema
    data = analyze(client, payload)
    check_meta(data["meta"], InlineMapping.model_validate(configured), schema)
    assert data["meta"]["schema_source"] == ("request" if schema_override else "configured")
    assert data["mapping_status"] == "mapped"
    row = data["tables"][0]["rows"][0]
    assert (
        row["typ"]["value"],
        row["c_dokladu"]["value"],
        row["suma_doklad"]["value"],
        row["c_dokladu"]["page"],
    ) == ("FA", "FA-2026-001", 1230.0, 3)


@pytest.mark.parametrize("version", ["release-2", None, "omitted"])
def test_new_inline_form_and_optional_version(client, load_fixture, version):
    payload = load_fixture("clean_invoice")
    payload["form"] = mapping_form()
    if version != "omitted":
        payload["form"]["mapping"]["version"] = version
    data = analyze(client, payload)
    check_meta(
        data["meta"],
        InlineMapping.model_validate(payload["form"]["mapping"]),
        payload["form"]["schema"],
    )
    assert data["meta"]["schema_source"] == "request"


@pytest.mark.parametrize("explicit_null", [False, True])
@pytest.mark.parametrize("include_unmapped", [False, True])
def test_no_form_metadata_is_null(client, load_fixture, explicit_null, include_unmapped):
    payload = load_fixture("clean_invoice")
    if explicit_null:
        payload["form"] = None
    else:
        payload.pop("form")
    payload["options"]["include_unmapped"] = include_unmapped
    data = analyze(client, payload)
    assert data["mapping_status"] == "no_form"
    assert all(data["meta"][key] is None for key in KEYS | {"mapping_id"})
    assert data["meta"]["schema_source"] == "none"


@pytest.mark.parametrize(
    "status,fixture_name",
    [
        ("mapped", "clean_invoice"),
        ("unsupported_document_type", "receipt"),
        ("unsupported_document_type", "unknown"),
        ("no_values", "clean_invoice"),
    ],
)
def test_resolved_metadata_independent_of_mapping_status(
    client, load_fixture, status, fixture_name
):
    payload = load_fixture(fixture_name)
    if status == "no_values":
        payload["form"] = mapping_form(source="payment_date")
    payload["options"]["include_unmapped"] = False
    data = analyze(client, payload)
    assert data["mapping_status"] == status
    assert data["extracted_data"] is None
    assert FINGERPRINT.fullmatch(data["meta"]["mapping_fingerprint"])
    assert FINGERPRINT.fullmatch(data["meta"]["form_schema_fingerprint"])


def test_payload_mutation_changes_only_corresponding_identifier(client, load_fixture):
    payload = load_fixture("clean_invoice")
    payload["form"] = mapping_form()
    first = analyze(client, payload)
    payload["form"]["mapping"]["rules"][0]["review_warning"] = "A review message"
    second = analyze(client, payload)
    assert first["meta"]["mapping_fingerprint"] != second["meta"]["mapping_fingerprint"]
    assert first["meta"]["form_schema_fingerprint"] == second["meta"]["form_schema_fingerprint"]
    payload["form"]["schema"]["children"][0]["children"][0]["name"] = "New field label"
    third = analyze(client, payload)
    assert second["meta"]["mapping_fingerprint"] == third["meta"]["mapping_fingerprint"]
    assert second["meta"]["form_schema_fingerprint"] != third["meta"]["form_schema_fingerprint"]
    payload["content"]["pages"][0]["text"] += "\nUnrelated OCR text"
    fourth = analyze(client, payload)
    assert third["meta"]["mapping_fingerprint"] == fourth["meta"]["mapping_fingerprint"]
    assert third["meta"]["form_schema_fingerprint"] == fourth["meta"]["form_schema_fingerprint"]


def test_metadata_neither_logs_schema_nor_turns_invalid_config_into_success(
    client, load_fixture, caplog
):
    payload = load_fixture("clean_invoice")
    payload["form"] = mapping_form()
    sentinel = "synthetic-private-mapping-payload"
    payload["form"]["schema"]["tracking_note"] = sentinel
    payload["form"]["mapping"]["rules"][0]["review_warning"] = sentinel
    analyze(client, payload)
    assert sentinel not in caplog.text
    assert "FA-2026-001" not in caplog.text
    bad = copy.deepcopy(payload)
    bad["form"]["mapping"]["rules"][0]["destination"] = "missing.target"
    response = client.post("/v1/analyze", json=bad)
    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == "invalid_mapping_destination"
    assert sentinel not in response.text


def test_repeated_requests_have_stable_identifiers(client, load_fixture):
    payload = load_fixture("clean_invoice")
    first = analyze(client, payload)
    second = analyze(client, payload)
    assert {key: first["meta"][key] for key in KEYS} == {key: second["meta"][key] for key in KEYS}
