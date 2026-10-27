import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from app.core.errors import ServiceError
from app.mapping.fingerprint import json_fingerprint, mapping_fingerprint
from app.mapping.form_parser import FormSchemaParser, ParsedForm
from app.schemas.mapping import InlineMapping
from app.schemas.requests import FormContext
from app.validators.semantics import DATE_FIELDS, MONEY_FIELDS

_SOURCES_BY_TYPE = {
    "date": DATE_FIELDS,
    "currency": MONEY_FIELDS,
    "number": MONEY_FIELDS,
    "iban": {"iban"},
}
_BBSK_TABLE = "rozpocet.zoznamuctovnychdokladov."
_BBSK_RESERVED = {"suma_dotacia", "druh", "p_c", "uctovny_doklad", "tovar"}
_BBSK_SOURCES = {
    "datum_1": "payment_date",
    "datum": "delivery_date",
    "suma_doklad": "total_gross",
    "c_dokladu": "invoice_number",
}


class MappingConfig(InlineMapping):
    schema_file: str

    @model_validator(mode="after")
    def schema_basename(self):
        if Path(self.schema_file).name != self.schema_file or not self.schema_file.endswith(
            ".json"
        ):
            raise ValueError("Schema file must be a JSON basename")
        return self


@dataclass(frozen=True)
class ResolvedMapping:
    config: InlineMapping
    form: ParsedForm
    schema_source: Literal["request", "configured"]
    mapping_version: str | None
    mapping_fingerprint: str
    form_schema_fingerprint: str
    authoritative_constraints_fingerprint: str | None = None

    @classmethod
    def create(
        cls,
        config: InlineMapping,
        form: ParsedForm,
        schema: dict,
        schema_source: Literal["request", "configured"],
        *,
        authoritative_constraints: dict | None = None,
    ) -> "ResolvedMapping":
        try:
            schema_fingerprint = json_fingerprint(schema)
        except (TypeError, ValueError) as exc:
            raise ServiceError(
                "invalid_form_schema", "Schéma formulára nie je platný JSON."
            ) from exc
        return cls(
            config=config,
            form=form,
            schema_source=schema_source,
            mapping_version=config.version,
            mapping_fingerprint=mapping_fingerprint(config),
            form_schema_fingerprint=schema_fingerprint,
            authoritative_constraints_fingerprint=(
                json_fingerprint(authoritative_constraints) if authoritative_constraints else None
            ),
        )


class ConfigLoader:
    def __init__(self, directory: Path, parser: FormSchemaParser):
        self.parser = parser
        self.configs: dict[str, MappingConfig] = {}
        self.schemas: dict[str, dict] = {}
        try:
            for path in directory.glob("*_mapping.json"):
                config = MappingConfig.model_validate_json(path.read_text(encoding="utf-8"))
                if config.id in self.configs:
                    raise ValueError("Duplicate mapping id")
                schema = json.loads((directory / config.schema_file).read_text(encoding="utf-8"))
                self.validate(config, parser.parse(schema))
                self.configs[config.id] = config
                self.schemas[config.id] = schema
            if not self.configs:
                raise ValueError("No mappings")
        except (OSError, ValueError, ServiceError) as exc:
            raise ServiceError(
                "invalid_mapping_configuration", "Konfigurácia mapovania je neplatná.", 503
            ) from exc

    @staticmethod
    def validate(config: InlineMapping, form: ParsedForm, strict: bool = False) -> None:
        for rule in config.rules:
            target = form.fields.get(rule.destination)
            if target is None:
                raise ServiceError(
                    "invalid_mapping_destination", "Cieľ mapovania sa nenachádza v schéme."
                )
            if strict:
                if not target.editable or (target.table and not form.tables[target.table].editable):
                    raise ServiceError(
                        "protected_mapping_destination", "Cieľ je chránený alebo nepodporovaný."
                    )
                allowed_sources = _SOURCES_BY_TYPE.get(target.frontend_type)
                incompatible_source = (
                    allowed_sources is not None and rule.source not in allowed_sources
                )
                currency_on_non_money_field = (
                    rule.required_currency and rule.source not in MONEY_FIELDS
                )
                value_map_without_options = rule.value_map is not None and not target.options
                invalid_classification_map = (
                    rule.source == "classification"
                    and rule.value_map is not None
                    and not set(rule.value_map) <= set(config.document_types)
                )
                if (
                    incompatible_source
                    or currency_on_non_money_field
                    or value_map_without_options
                    or invalid_classification_map
                ):
                    raise ServiceError(
                        "invalid_mapping_type",
                        "Zdroj a cieľ mapovania majú nezlučiteľný význam alebo typ.",
                    )
            # BBSK accounting semantics remain invariant even with a different mapping id.
            if rule.destination.startswith(_BBSK_TABLE):
                tag = rule.destination.removeprefix(_BBSK_TABLE)
                wrong_source = tag in _BBSK_SOURCES and rule.source != _BBSK_SOURCES[tag]
                wrong_currency = tag == "suma_doklad" and rule.required_currency != "EUR"
                if tag in _BBSK_RESERVED or wrong_source or wrong_currency:
                    raise ServiceError(
                        "reserved_mapping_destination",
                        "Mapovanie porušuje pravidlá účtovného dokladu.",
                    )
            if (
                rule.value_map is not None
                and target.options
                and not set(rule.value_map.values()) <= target.options
            ):
                raise ServiceError(
                    "invalid_select_mapping", "Mapovanie obsahuje nepovolenú možnosť výberu."
                )
        for destination in config.reserved:
            if destination not in form.fields:
                raise ServiceError(
                    "invalid_mapping_destination", "Rezervované pole sa nenachádza v schéme."
                )

    def resolve(self, context: FormContext):
        """Retain the existing tuple interface for mapping callers."""
        resolved = self.resolve_with_metadata(context)
        return resolved.config, resolved.form, resolved.schema_source

    def resolve_with_metadata(self, context: FormContext) -> ResolvedMapping:
        if context.mapping is not None:
            config = context.mapping
            if config.id != context.mapping_id or config.form_id != context.id:
                raise ServiceError(
                    "mapping_identity_mismatch", "Identita formulára a mapovania sa nezhoduje."
                )
            registered = [
                mapping for mapping in self.configs.values() if mapping.form_id == context.id
            ]
            if context.schema_ is None and not registered:
                raise ServiceError(
                    "missing_form_schema", "Dynamické mapovanie vyžaduje schému formulára."
                )
            schema = (
                context.schema_ if context.schema_ is not None else self.schemas[registered[0].id]
            )
            parsed = self.parser.parse(schema)
            self.validate(config, parsed, strict=True)
            constraints: dict = {}
            for original in registered:
                authoritative = self.parser.parse(self.schemas[original.id])
                for rule in config.rules:
                    existing = authoritative.fields.get(rule.destination)
                    is_reserved = rule.destination in original.reserved
                    is_protected = existing is not None and not existing.editable
                    # Hash only external decisions used by this request, not paths,
                    # labels or protections already covered by the selected schema.
                    reserved = constraints.setdefault("reserved", {})
                    reserved[rule.destination] = (
                        reserved.get(rule.destination, False) or is_reserved
                    )
                    if context.schema_ is not None or original.id != registered[0].id:
                        protected = constraints.setdefault("protected", {})
                        protected[rule.destination] = (
                            protected.get(rule.destination, False) or is_protected
                        )
                    if is_reserved or is_protected:
                        raise ServiceError(
                            "protected_mapping_destination", "Registrované ochrany nemožno obísť."
                        )
            return ResolvedMapping.create(
                config,
                parsed,
                schema,
                "request" if context.schema_ is not None else "configured",
                authoritative_constraints=constraints,
            )

        config = self.configs.get(context.mapping_id)
        if config is None or config.form_id != context.id:
            raise ServiceError(
                "unknown_mapping", "Formulár alebo mapovanie nie je nakonfigurované."
            )
        supplied = context.schema_ is not None
        schema = context.schema_ if supplied else self.schemas[context.mapping_id]
        parsed = self.parser.parse(schema)
        self.validate(config, parsed)
        constraints = {}
        if supplied:
            authoritative = self.parser.parse(self.schemas[config.id])
            for rule in config.rules:
                original = authoritative.fields[rule.destination]
                constraints.setdefault("protected", {})[rule.destination] = not original.editable
                if not original.editable:
                    parsed.fields[rule.destination].editable = False
        return ResolvedMapping.create(
            config,
            parsed,
            schema,
            "request" if supplied else "configured",
            authoritative_constraints=constraints,
        )
