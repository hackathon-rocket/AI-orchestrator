"""Server-authoritative restrictions are a separate configuration dependency."""

import copy
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ServiceError
from app.main import create_app
from app.mapping.config_loader import ConfigLoader
from app.mapping.form_parser import FormSchemaParser
from app.schemas.requests import FormContext
from tests.conftest import TOKEN
from tests.integration.test_mapping_status import mapping_form

AMOUNT = "rozpocet.zoznamuctovnychdokladov.suma_doklad"
KEY = "authoritative_constraints_fingerprint"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def node_with_tag(node, tag):
    if node.get("tag") == tag:
        return node
    for child in node.get("children", []):
        found = node_with_tag(child, tag)
        if found is not None:
            return found
    return None


def registered_inline(settings, schema=True):
    mapping = json.loads(
        (settings.form_config_dir / "bbsk_11824_mapping.json").read_text(encoding="utf-8")
    )
    mapping.pop("schema_file")
    form = {"id": "bbsk_11824", "mapping_id": mapping["id"], "mapping": mapping}
    if schema:
        form["schema"] = json.loads(
            (settings.form_config_dir / "bbsk_11824_schema.json").read_text(encoding="utf-8")
        )
    return form


def assert_identity(meta):
    return (meta["mapping_fingerprint"], meta["form_schema_fingerprint"], meta.get(KEY))


@pytest.mark.parametrize("tag", ["suma_doklad", "zoznamuctovnychdokladov"])
def test_readonly_changes_output_and_complete_identity(settings, load_fixture, tmp_path, tag):
    payload = load_fixture("clean_invoice")
    payload["form"]["schema"] = json.loads(
        (settings.form_config_dir / "bbsk_11824_schema.json").read_text(encoding="utf-8")
    )
    before = copy.deepcopy(payload["form"]["schema"])
    original_directory = settings.form_config_dir
    with TestClient(create_app(settings), headers=AUTH) as client:
        first_response = client.post("/v1/analyze", json=payload)
        assert first_response.status_code == 200
        first = first_response.json()
    target = tmp_path / "changed-authority"
    shutil.copytree(original_directory, target)
    path = target / "bbsk_11824_schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    node_with_tag(schema, tag).setdefault("config", {})["is_readonly"] = 1
    path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    settings.form_config_dir = target
    with TestClient(create_app(settings), headers=AUTH) as client:
        second_response = client.post("/v1/analyze", json=payload)
        assert second_response.status_code == 200
        second = second_response.json()
    assert payload["form"]["schema"] == before
    assert first["tables"][0]["rows"][0]["suma_doklad"]["value"] == 1230.0
    cells = [cell for table in second["tables"] for row in table["rows"] for cell in row.values()]
    assert all(cell["value"] is None for cell in cells if cell["tag"] == "suma_doklad")
    if tag == "suma_doklad":
        assert second["tables"][0]["rows"][0].get("suma_doklad", {}).get("value") is None
    assert first["meta"]["mapping_fingerprint"] == second["meta"]["mapping_fingerprint"]
    assert first["meta"]["form_schema_fingerprint"] == second["meta"]["form_schema_fingerprint"]
    assert assert_identity(first["meta"]) != assert_identity(second["meta"])
    assert first["meta"][KEY] is not None and second["meta"][KEY] is not None
    assert second["extracted_data"]["fields"]["total_gross"]["value"] == "1230.00"
    assert second["extracted_data"]["classification"]["status"] == "verified"


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("override", [False, True])
def test_dependencies_are_stable_and_not_redundant(settings, load_fixture, inline, override):
    payload = load_fixture("clean_invoice")
    if inline:
        payload["form"] = registered_inline(settings, schema=override)
    elif override:
        payload["form"]["schema"] = json.loads(
            (settings.form_config_dir / "bbsk_11824_schema.json").read_text(encoding="utf-8")
        )
    with TestClient(create_app(settings), headers=AUTH) as client:
        first = client.post("/v1/analyze", json=payload).json()
        second = client.post("/v1/analyze", json=payload).json()
    assert assert_identity(first["meta"]) == assert_identity(second["meta"])
    if not inline and not override:
        assert first["meta"][KEY] is None  # Both dependencies already have content hashes.
    else:
        assert first["meta"][KEY].startswith("sha256:")


@pytest.mark.parametrize("inline", [False, True])
def test_authority_ignores_formatting_order_and_paths(settings, tmp_path, inline):
    context_dict = registered_inline(settings)
    if not inline:
        context_dict.pop("mapping")
    context = FormContext.model_validate(context_dict)
    original = ConfigLoader(settings.form_config_dir, FormSchemaParser()).resolve_with_metadata(
        context
    )
    target = tmp_path / "elsewhere"
    shutil.copytree(settings.form_config_dir, target)
    for path in target.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(data, sort_keys=True, indent=7), encoding="utf-8")
    relocated = ConfigLoader(target, FormSchemaParser()).resolve_with_metadata(context)
    assert (
        original.authoritative_constraints_fingerprint
        == relocated.authoritative_constraints_fingerprint
    )


@pytest.mark.parametrize("override", [False, True])
def test_registered_inline_cannot_bypass_authoritative_readonly(settings, override):
    loader = ConfigLoader(settings.form_config_dir, FormSchemaParser())
    context = FormContext.model_validate(registered_inline(settings, schema=override))
    good = loader.resolve_with_metadata(context)
    assert good.authoritative_constraints_fingerprint is not None
    node_with_tag(loader.schemas["bbsk_11824_default"], "suma_doklad")["config"]["is_readonly"] = 1
    with pytest.raises(ServiceError) as exc:
        loader.resolve_with_metadata(context)
    assert exc.value.status_code == 422
    assert exc.value.code == ("protected_mapping_destination")


def test_multiple_registered_contributors_are_order_independent(settings):
    loader = ConfigLoader(settings.form_config_dir, FormSchemaParser())
    original = loader.configs["bbsk_11824_default"]
    extra = original.model_copy(
        update={
            "id": "additional_authority",
            "schema_file": "extra.json",
            "rules": [rule for rule in original.rules if rule.destination != AMOUNT],
        }
    )
    loader.configs[extra.id] = extra
    loader.schemas[extra.id] = copy.deepcopy(loader.schemas[original.id])
    context = FormContext.model_validate(registered_inline(settings))
    first = loader.resolve_with_metadata(context)
    loader.configs = dict(reversed(list(loader.configs.items())))
    second = loader.resolve_with_metadata(context)
    assert (
        first.authoritative_constraints_fingerprint == second.authoritative_constraints_fingerprint
    )
    # A second contributor's reserved destinations remain enforced.
    loader.configs[extra.id] = type(extra).model_validate(
        {**extra.model_dump(), "reserved": [AMOUNT]}
    )
    with pytest.raises(ServiceError, match="protected_mapping_destination"):
        loader.resolve_with_metadata(context)


@pytest.mark.parametrize("form", [None, "unregistered"])
def test_no_external_dependency_is_explicit_null(client, load_fixture, form):
    payload = load_fixture("clean_invoice")
    payload["form"] = mapping_form() if form else None
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200
    assert KEY in response.json()["meta"]
    assert response.json()["meta"][KEY] is None
