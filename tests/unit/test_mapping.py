import json
from pathlib import Path

import pytest

from app.core.errors import ServiceError
from app.mapping.config_loader import ConfigLoader, MappingConfig
from app.mapping.form_parser import FormSchemaParser
from app.mapping.mapper import map_extraction
from app.schemas.extraction import Evidence, ExtractionData, Fact
from app.schemas.mapping import MappingRule
from app.schemas.requests import Content, FormContext, Page
from app.validators.validation import validate_fact


def simple_schema(kind="SIMPLE_STRING", config=None, **extra):
    return {
        "type": "root",
        "children": [
            {
                "type": "SECTION",
                "tag": "section",
                "children": [
                    {
                        "type": kind,
                        "tag": "number",
                        "name": "Číslo",
                        "config": config or {},
                        **extra,
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "kind,config,extra",
    [
        ("SIMPLE_STRING", {"is_readonly": 1}, {}),
        ("SIMPLE_STRING", {"is_readonly": "1"}, {}),
        ("SIMPLE_STRING", {}, {"force_source_value": True, "source_tag": "other"}),
        ("SIMPLE_STRING", {"fixed_values": [{"value": "preset", "readonly": "1"}]}, {}),
        ("SIMPLE_STRING", {}, {"fill_condition": "eq(role, admin)"}),
        ("FILE_LIST", {}, {}),
        ("SUM", {}, {}),
        ("TABLE_ITEM_PRODUCT", {}, {}),
    ],
)
def test_protected_targets_never_mapped(kind, config, extra):
    form = FormSchemaParser().parse(simple_schema(kind, config, **extra))
    mapping = MappingConfig(
        id="test",
        form_id="test",
        schema_file="test.json",
        rules=[MappingRule(source="invoice_number", destination="section.number")],
    )
    data = ExtractionData(document_type="invoice")
    data.fields.invoice_number = Fact(
        value="FA-001",
        status="verified",
        confidence=0.8,
        evidence=Evidence(page=1, quote="FAKTÚRA č. FA-001"),
    )
    fields, tables, warnings = map_extraction(data, mapping, form)
    assert fields == tables == []
    assert warnings


def test_parent_forced_source_inherited():
    schema = simple_schema()
    schema["children"][0]["force_source_value"] = True
    field = FormSchemaParser().parse(schema).fields["section.number"]
    assert not field.editable


def test_zero_strings_and_nonforced_source_editable():
    field = (
        FormSchemaParser()
        .parse(
            simple_schema(config={"is_readonly": "0"}, source_tag="other", force_source_value=False)
        )
        .fields["section.number"]
    )
    assert field.editable


def test_full_paths_and_duplicate_leaf_tags():
    schema = {
        "type": "root",
        "children": [
            {"type": "SECTION", "tag": x, "children": [{"type": "SIMPLE_STRING", "tag": "number"}]}
            for x in ["one", "two"]
        ],
    }
    assert set(FormSchemaParser().parse(schema).fields) == {"one.number", "two.number"}
    schema["children"][0]["children"][0]["tag"] = "one.number"
    assert "one.number" in FormSchemaParser().parse(schema).fields
    schema["children"][1]["children"][0]["tag"] = "one.number"
    with pytest.raises(ServiceError):
        FormSchemaParser().parse(schema)


def test_schema_complexity_limits():
    with pytest.raises(ServiceError):
        FormSchemaParser(max_nodes=2).parse(simple_schema())
    with pytest.raises(ServiceError):
        FormSchemaParser(max_depth=1).parse(simple_schema())


@pytest.mark.parametrize("protection", ["field", "parent", "unsupported_type"])
def test_request_schema_cannot_unlock_configured_target(tmp_path, protection):
    configured = simple_schema()
    if protection == "field":
        configured["children"][0]["children"][0]["config"]["is_readonly"] = True
    elif protection == "parent":
        configured["children"][0]["config"] = {"is_readonly": True}
    else:
        configured["children"][0]["children"][0]["type"] = "FILE_LIST"
    mapping = MappingConfig(
        id="test",
        form_id="test",
        schema_file="test_schema.json",
        rules=[MappingRule(source="invoice_number", destination="section.number")],
    )
    (tmp_path / "test_mapping.json").write_text(mapping.model_dump_json(), encoding="utf-8")
    (tmp_path / "test_schema.json").write_text(json.dumps(configured), encoding="utf-8")
    loader = ConfigLoader(tmp_path, FormSchemaParser())

    _, form, source = loader.resolve(
        FormContext(id="test", mapping_id="test", schema=simple_schema())
    )

    assert source == "request"
    assert not form.fields["section.number"].editable


def test_real_bbsk_schema(settings):
    loader = ConfigLoader(settings.form_config_dir, FormSchemaParser())
    config, form, source = loader.resolve(
        FormContext(id="bbsk_11824", mapping_id="bbsk_11824_default")
    )
    path = "rozpocet.zoznamuctovnychdokladov"
    assert form.fields[path + ".typ"].options == {"FA", "PPD", "VPD", "Zmluva"}
    assert form.fields[path + ".uhrada"].options == {"prevodom", "v hotovosti"}
    assert not form.fields[path + ".uctovny_doklad"].editable
    assert source == "configured"
    config.rules[-1].value_map = {"invoice": "invalid"}
    with pytest.raises(ServiceError, match="invalid_select_mapping"):
        loader.validate(config, form)


def test_alternate_real_snapshots():
    fixtures = Path(__file__).parents[1] / "fixtures/forms"
    sila = FormSchemaParser().parse(
        json.loads((fixtures / "sila_10318.json").read_text(encoding="utf-8"))
    )
    assert not sila.fields["predkladatel.cislo_projektu"].editable
    magna = FormSchemaParser().parse(
        json.loads((fixtures / "magna_8812.json").read_text(encoding="utf-8"))
    )
    assert magna.fields["zakladne_udaje.nazov_org"].editable


def test_invalid_select_value_dropped():
    form = FormSchemaParser().parse(simple_schema("SELECT", {"values": {"FA": "Faktúra"}}))
    mapping = MappingConfig(
        id="test",
        form_id="test",
        schema_file="test.json",
        rules=[MappingRule(source="payment_method", destination="section.number")],
    )
    data = ExtractionData(document_type="invoice")
    data.fields.payment_method = Fact(
        value="card",
        status="verified",
        confidence=0.8,
        evidence=Evidence(page=1, quote="Spôsob úhrady: card"),
    )
    candidate = data.fields.payment_method
    data.fields.payment_method = validate_fact(
        "payment_method",
        candidate,
        Content(pages=[Page(page=1, text=candidate.evidence.quote)]),
        [],
    )
    assert data.fields.payment_method._safe_to_suggest
    fields, _, warnings = map_extraction(data, mapping, form)
    assert fields[0].value is None
    assert warnings


def test_iban_target_revalidates_mapped_source():
    form = FormSchemaParser().parse(simple_schema("IBAN"))
    mapping = MappingConfig(
        id="test",
        form_id="test",
        schema_file="test.json",
        rules=[MappingRule(source="invoice_number", destination="section.number")],
    )
    data = ExtractionData(document_type="invoice")
    data.fields.invoice_number = Fact(
        value="FA-001",
        status="verified",
        confidence=0.8,
        evidence=Evidence(page=1, quote="FAKTÚRA č. FA-001"),
    )
    candidate = data.fields.invoice_number
    data.fields.invoice_number = validate_fact(
        "invoice_number",
        candidate,
        Content(pages=[Page(page=1, text=candidate.evidence.quote)]),
        [],
    )
    assert data.fields.invoice_number._safe_to_suggest
    fields, _, warnings = map_extraction(data, mapping, form)
    assert fields[0].value is None
    assert warnings
