"""Canonical content IDs cover complete validated mappings and raw schemas."""

import copy
import hashlib
import json
import shutil

import pytest

from app.mapping.config_loader import ConfigLoader, MappingConfig
from app.mapping.fingerprint import json_fingerprint, mapping_fingerprint
from app.mapping.form_parser import FormSchemaParser
from app.schemas.mapping import InlineMapping
from app.schemas.requests import FormContext


def test_canonical_json_vector():
    assert (
        json_fingerprint({})
        == "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )
    assert (
        json_fingerprint({"b": 2, "a": "Ž"})
        == "sha256:" + hashlib.sha256('{"a":"Ž","b":2}'.encode()).hexdigest()
    )
    assert json_fingerprint(json.loads('{ "b" : 2, "a" : "Ž" }')) == json_fingerprint(
        {"a": "Ž", "b": 2}
    )
    assert json_fingerprint(["a", "b"]) != json_fingerprint(["b", "a"])


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_fingerprint_does_not_emit_non_json_numbers(number):
    with pytest.raises(ValueError):
        json_fingerprint({"number": number})


@pytest.fixture
def mapping(settings):
    return MappingConfig.model_validate_json(
        (settings.form_config_dir / "bbsk_11824_mapping.json").read_text(encoding="utf-8")
    )


def test_configured_and_inline_models_have_same_identity(mapping):
    payload = mapping.model_dump(mode="json", exclude={"schema_file"})
    inline = InlineMapping.model_validate(payload)
    assert mapping_fingerprint(mapping) == mapping_fingerprint(inline)
    renamed = mapping.model_copy(update={"schema_file": "renamed_schema.json"})
    assert mapping_fingerprint(mapping) == mapping_fingerprint(renamed)
    null_version = {**payload, "version": None}
    omitted = {k: v for k, v in null_version.items() if k != "version"}
    assert mapping_fingerprint(InlineMapping.model_validate(null_version)) == mapping_fingerprint(
        InlineMapping.model_validate(omitted)
    )


@pytest.mark.parametrize(
    "change",
    [
        "id",
        "form_id",
        "version",
        "document_types",
        "source",
        "destination",
        "value_map",
        "required_currency",
        "review_warning",
        "reserved",
        "rule_order",
    ],
)
def test_every_mapping_element_is_identified(mapping, change):
    payload = mapping.model_dump(mode="json", exclude={"schema_file"})
    changed = copy.deepcopy(payload)
    if change in ("id", "form_id", "version"):
        changed[change] = "changed"
    elif change == "document_types":
        changed[change] = ["invoice", "contract"]
    elif change == "reserved":
        changed[change] = changed[change][:-1]
    elif change == "rule_order":
        changed["rules"].reverse()
    elif change == "source":
        changed["rules"][0][change] = "receipt_number"
    elif change == "destination":
        changed["rules"][0][change] = "another.target"
    elif change == "value_map":
        changed["rules"][-1][change]["invoice"] = "different"
    elif change == "required_currency":
        changed["rules"][1][change] = "USD"
    else:
        changed["rules"][0][change] = "New review warning"
    assert mapping_fingerprint(InlineMapping.model_validate(changed)) != mapping_fingerprint(
        mapping
    )


def test_paths_file_whitespace_and_key_order_are_irrelevant(settings, tmp_path):
    loader = ConfigLoader(settings.form_config_dir, FormSchemaParser())
    target = tmp_path / "different-location"
    shutil.copytree(settings.form_config_dir, target)
    for path in target.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    relocated = ConfigLoader(target, FormSchemaParser())
    context = FormContext(id="bbsk_11824", mapping_id="bbsk_11824_default")
    first = loader.resolve_with_metadata(context)
    second = relocated.resolve_with_metadata(context)
    assert first.mapping_fingerprint == second.mapping_fingerprint
    assert first.form_schema_fingerprint == second.form_schema_fingerprint
    config, form, source = loader.resolve(context)  # Existing call sites keep tuple unpacking.
    assert config == first.config and form == first.form and source == first.schema_source


def test_schema_fingerprint_keeps_details_not_used_by_parser(settings):
    loader = ConfigLoader(settings.form_config_dir, FormSchemaParser())
    context = FormContext(id="bbsk_11824", mapping_id="bbsk_11824_default")
    original = loader.resolve_with_metadata(context)
    schema = copy.deepcopy(loader.schemas[context.mapping_id])
    schema["tracking_note"] = "Schema information not represented in ParsedForm"
    changed = loader.resolve_with_metadata(
        FormContext(id=context.id, mapping_id=context.mapping_id, schema=schema)
    )
    assert original.form == changed.form
    assert original.mapping_fingerprint == changed.mapping_fingerprint
    assert original.form_schema_fingerprint != changed.form_schema_fingerprint
