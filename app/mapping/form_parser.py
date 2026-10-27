import re
from dataclasses import dataclass, field

from app.core.errors import ServiceError

TYPES = {
    "SIMPLE_STRING": "string",
    "TEXT": "string",
    "EMAIL": "string",
    "NUMBER": "number",
    "CURRENCY": "currency",
    "DATE": "date",
    "IBAN": "iban",
    "SELECT": "string",
    "TABLE_ITEM_STRING": "string",
    "TABLE_ITEM_TEXT": "string",
    "TABLE_ITEM_NUMBER": "number",
    "TABLE_ITEM_CURRENCY": "currency",
    "TABLE_ITEM_DATE": "date",
    "TABLE_ITEM_SELECT": "string",
}
CONTAINERS = {"root", "ROOT", "SECTION", "SUB_SECTION"}
PROTECTED_FLAGS = (
    "is_readonly",
    "force_source_value",
    "readonly_condition",
    "edit_condition",
    "fill_condition",
    "lock_condition",
    "formula",
    "computed",
    "expression",
)


def enabled(value) -> bool:
    return value not in (None, False, 0, "0", "false", "False", "")


@dataclass
class FormField:
    path: str
    tag: str
    label: str
    frontend_type: str | None
    table: str | None
    editable: bool
    required: bool
    options: set[str] = field(default_factory=set)


@dataclass
class ParsedForm:
    fields: dict[str, FormField]
    tables: dict[str, FormField]


class FormSchemaParser:
    def __init__(self, max_nodes: int = 5000, max_depth: int = 20):
        self.max_nodes = max_nodes
        self.max_depth = max_depth

    def parse(self, schema: dict) -> ParsedForm:
        fields: dict[str, FormField] = {}
        tables: dict[str, FormField] = {}
        seen: set[str] = set()
        count = 0

        def invalid():
            raise ServiceError(
                "invalid_form_schema", "Schéma formulára je neplatná alebo príliš zložitá."
            )

        def walk(node, parent="", table=None, blocked=False, depth=0):
            nonlocal count
            count += 1
            if not isinstance(node, dict) or count > self.max_nodes or depth > self.max_depth:
                invalid()
            kind = node.get("type")
            tag = node.get("tag", "")
            config = node.get("config") or {}
            if (
                not isinstance(kind, str)
                or not isinstance(config, dict)
                or not isinstance(tag, str)
            ):
                invalid()
            if tag and not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", tag):
                invalid()
            path = tag if "." in tag else ".".join(filter(None, [parent, tag]))
            if any(enabled(node.get(key)) or enabled(config.get(key)) for key in PROTECTED_FLAGS):
                blocked = True
            fixed = config.get("fixed_values", [])
            if not isinstance(fixed, list):
                invalid()
            if any(isinstance(v, dict) and enabled(v.get("readonly")) for v in fixed):
                blocked = True
            if kind not in TYPES and kind not in CONTAINERS and kind != "TABLE_LIST":
                blocked = True
            if kind.startswith("TABLE_ITEM_") and table is None:
                blocked = True
            if table and (not kind.startswith("TABLE_ITEM_") or path.rpartition(".")[0] != table):
                blocked = True
            if kind == "TABLE_LIST" and (not tag or not enabled(config.get("add_rows"))):
                # Updating fixed rows requires row identities that the request does not carry.
                blocked = True
            options = set()
            if kind in {"SELECT", "TABLE_ITEM_SELECT"}:
                values = config.get("values", {})
                if isinstance(values, dict):
                    options = set(values)
                if not options or enabled(config.get("roller_source")):
                    blocked = True
            if tag:
                if path in seen:
                    invalid()
                seen.add(path)
                if kind not in CONTAINERS:
                    destinations = tables if kind == "TABLE_LIST" else fields
                    destinations[path] = FormField(
                        path=path,
                        tag=tag.split(".")[-1],
                        label=re.sub(r"<[^>]*>", "", str(node.get("name") or tag))[:300],
                        frontend_type=TYPES.get(kind),
                        table=table,
                        editable=not blocked and (kind in TYPES or kind == "TABLE_LIST"),
                        required=enabled(node.get("mandatory")),
                        options=options,
                    )
            children = node.get("children", [])
            if not isinstance(children, list):
                invalid()
            for child in children:
                walk(
                    child,
                    parent=path,
                    table=path if kind == "TABLE_LIST" else table,
                    blocked=blocked,
                    depth=depth + 1,
                )

        walk(schema)
        return ParsedForm(fields, tables)
