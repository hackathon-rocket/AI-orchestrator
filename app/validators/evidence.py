"""Literal grounding and local role recognition are independent decisions."""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.processors.ocr_text import normalize
from app.schemas.extraction import Evidence
from app.schemas.requests import Content
from app.validators.dates import parse_date
from app.validators.financial import parse_money
from app.validators.semantics import DATE_FIELDS, LABELS, MONEY_FIELDS, TITLES


@dataclass(frozen=True)
class Support:
    matched: bool
    context: str = ""
    approximate: bool = False
    formula: bool = False
    source: str = ""
    ranges: tuple[tuple[int, int], ...] = ()
    observations: tuple["Observation", ...] | None = None


@dataclass(frozen=True)
class Observation:
    role: str
    value: str
    quote: str
    start: int
    end: int


class EvidenceIndex:
    """Request-scoped index; no document data remains in a global cache."""

    def __init__(self, content: Content):
        self.sheets = {
            sheet.name: ExcelSheetIndex({c.coordinate: c for row in sheet.rows for c in row.cells})
            for sheet in content.sheets
        }
        self.normalized = {p.page: normalize(p.text) for p in content.pages}
        self.observed = {p.page: tuple(observations(p.text)) for p in content.pages}


def equivalent(field: str, candidate: str | Decimal, observed: str) -> bool:
    try:
        if field in MONEY_FIELDS:
            return parse_money(candidate) == parse_money(observed)
        if field in DATE_FIELDS:
            return parse_date(str(candidate)) == parse_date(observed)
        if field == "quantity":
            return Decimal(str(candidate).replace(",", ".")) == Decimal(observed.replace(",", "."))
        if field.endswith(("_ico", "_dic", "_vat_id")) or field == "iban":
            return re.sub(r"\s", "", str(candidate)).upper() == re.sub(r"\s", "", observed).upper()
        return normalize(str(candidate)) == normalize(observed)
    except (ValueError, ArithmeticError):
        return False


def literal_support(field: str, value: str | Decimal, quote: str) -> bool:
    """Recognize whole numeric tokens; whitespace may change, digits may not."""
    if equivalent(field, value, quote):
        return True
    if field in DATE_FIELDS:
        tokens = re.findall(r"\d{4}-\d{2}-\d{2}|\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{4}", quote)
    elif field in MONEY_FIELDS:
        # Consume maximal numeric runs, including malformed groups. Do not let a
        # regex backtrack to a valid-looking suffix of an invalid/longer amount.
        tokens = []
        # OCR column delimiters separate amounts; single grouping spaces remain
        # inside a maximal token and must pass parse_money as a whole.
        for segment in re.split(r" {2,}|\t+|\|", quote):
            for match in re.finditer(r"(?<![\w/.,+-])[+-]?\d[\d., \u00a0\u202f]*", segment):
                token = match[0]
                rest = segment[match.end() :]
                if token[-1].isspace() or not rest or not re.match(r"[\w/.,+-]", rest):
                    tokens.append(token.strip())
                elif re.match(r"(?:EUR|CZK|USD|GBP|CHF|PLN|HUF)(?!\w)", rest, re.I):
                    tokens.append(token)
    elif field == "quantity":
        tokens = re.findall(
            r"(?<![\w/.,])-?\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?"
            r"(?:\s*(?:EUR|CZK|USD|GBP|CHF|PLN|HUF|€))?(?![\w/.,])",
            quote,
            re.I,
        )
    elif field.endswith(("_ico", "_dic", "_vat_id")) or field == "iban":
        tokens = re.findall(r"(?<!\w)(?:[A-Z]{2}\s*)?\d(?:[\d \t]*\d)?(?!\w)", quote, re.I)
    else:
        return (
            re.search(r"(?<!\w)" + re.escape(normalize(str(value))) + r"(?!\w)", normalize(quote))
            is not None
        )
    return any(equivalent(field, value, token) for token in tokens)


class ExcelSheetIndex:
    """Request-scoped geometry and structural role associations, independent of hints."""

    def __init__(self, cells: dict):
        self.cells = cells
        self.positions = {}
        self.rows = {}
        self.columns = {}
        self.text = {}
        self.roles = {}
        self.targets = {}
        self.role_values = {}
        self.scan_steps = 0
        self.units = {"eur", "usd", "czk", "gbp", "chf", "pln", "huf", "€"}
        vocabulary = {}
        for role, labels in LABELS.items():
            for label in labels:
                vocabulary.setdefault(normalize(label).rstrip(" .:"), set()).add(role)
        for key, cell in cells.items():
            match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", key)
            if not match:
                continue
            column = 0
            for letter in match[1]:
                column = column * 26 + ord(letter) - ord("A") + 1
            row = int(match[2])
            self.positions[key] = column, row
            self.text[key] = normalize(str(cell.value or ""))
            self.roles[key] = vocabulary.get(self.text[key].rstrip(" .:"), set())
            if cell.formula or cell.value is not None and str(cell.value).strip():
                self.rows.setdefault(row, {})[column] = key
                self.columns.setdefault(column, []).append(key)
        self.offsets = {}
        for keys in self.columns.values():
            keys.sort(key=lambda key: self.positions[key][1])
            self.offsets.update({key: offset for offset, key in enumerate(keys)})
        self.right = {}
        self.header_rows = set()
        for row, columns in self.rows.items():
            keys = [columns[col] for col in sorted(columns)]
            self.right.update(zip(keys, keys[1:], strict=False))
            if all(
                not cells[key].formula
                and isinstance(cells[key].value, str)
                and self.text[key] not in self.units
                and not any(char.isdigit() for char in cells[key].value)
                for key in keys
            ):
                self.header_rows.add(row)
        # Known semantic headers terminate each other's runs, so their columns are
        # traversed linearly, not rescanned for every distinct candidate value.
        for key, roles in self.roles.items():
            if roles and not cells[key].formula:
                for target in self.values_for_label(key):
                    if cells[target].formula or self.roles[target]:
                        continue
                    for role in roles:
                        if role in MONEY_FIELDS and self.text[target] in self.units:
                            continue
                        self.role_values.setdefault(role, set()).add(target)

    def values_for_label(self, key: str) -> set[str]:
        if key in self.targets:
            return self.targets[key]
        result = self.targets[key] = set()
        if key not in self.offsets or self.cells[key].formula:
            return result
        if key in self.right:
            result.add(self.right[key])
        column, row = self.positions[key]
        if row not in self.header_rows:
            return result
        header_columns = self.rows[row].keys()
        keys = self.columns[column]
        previous_data = None
        for offset in range(self.offsets[key] + 1, len(keys)):
            self.scan_steps += 1
            target = keys[offset]
            cell = self.cells[target]
            target_row = self.positions[target][1]
            if cell.formula or self.roles[target]:
                break
            has_peer = any(col != column and col in header_columns for col in self.rows[target_row])
            if previous_data is not None and (target_row != previous_data + 1 or not has_peer):
                break
            result.add(target)
            if self.text[target] in self.units:
                continue
            # First vertical value is supported. Continuing farther requires a
            # real table row, a compatible value and no intervening section.
            if len(header_columns) < 2 or not has_peer:
                break
            try:
                if self.roles[key] & MONEY_FIELDS:
                    parse_money(str(cell.value))
                elif self.roles[key] & DATE_FIELDS:
                    parse_date(str(cell.value))
            except (ValueError, ArithmeticError):
                break
            previous_data = target_row
        return result

    def related(self, label: str, value: str) -> bool:
        return label != value and value in self.values_for_label(label)


def quote_support(
    evidence: Evidence, content: Content, index: EvidenceIndex | None = None
) -> Support:
    if evidence.locator:
        loc = evidence.locator
        sheet_index = index.sheets.get(loc.sheet) if index else None
        if sheet_index is None:
            sheet_index = ExcelSheetIndex(
                {
                    c.coordinate: c
                    for sheet in content.sheets
                    if sheet.name == loc.sheet
                    for row in sheet.rows
                    for c in row.cells
                }
            )
        cells = sheet_index.cells
        cell = cells.get(loc.cell)
        if cell is None or cell.value is None:
            return Support(False)
        col, row = re.fullmatch(r"([A-Z]+)(\d+)", loc.cell).groups()
        context_keys = list(dict.fromkeys(loc.context_cells + cell.header_cells))
        for key in context_keys:
            match = re.fullmatch(r"([A-Z]+)(\d+)", key)
            if key not in cells or not match or cells[key].formula:
                return Support(False)
            other_col, other_row = match.groups()
            if not ((other_col == col and int(other_row) < int(row)) or other_row == row):
                return Support(False)
        context = "\n".join(
            str(cells[k].value or "") for k in context_keys if sheet_index.related(k, loc.cell)
        )
        return Support(
            normalize(evidence.quote) == normalize(str(cell.value)),
            context,
            formula=bool(cell.formula),
        )
    source = next((p.text for p in content.pages if p.page == evidence.page), None)
    if source is None:
        return Support(False)
    quote = normalize(evidence.quote)
    normalized = index.normalized[evidence.page] if index else normalize(source)
    matches = tuple(
        (m.start(), m.end())
        for m in re.finditer(r"(?<!\w)" + re.escape(quote) + r"(?![\w.,/-])", normalized)
    )
    if matches:
        return Support(
            True,
            source=source,
            ranges=matches,
            observations=index.observed[evidence.page] if index else None,
        )

    def compact(s):
        return re.sub(r"\s*([.,:])\s*", r"\1", s)

    if re.search(r"(?<!\w)" + re.escape(compact(quote)) + r"(?![\w.,/-])", compact(normalized)):
        return Support(True, approximate=True, source=source)
    return Support(False)


def heading_types(text: str, *, first_only: bool = False) -> set[str]:
    """Only standalone headings or heading + number/subtitle qualify."""
    result = set()
    lines = text.splitlines()
    for index, line in enumerate(lines[:1] if first_only else lines):
        line = re.sub(r"\s*[-–]\s*", " - ", normalize(line))
        for kind, titles in TITLES.items():
            for title in titles:
                title = re.sub(r"\s*[-–]\s*", " - ", normalize(title))
                candidate = line
                # Join only actual prefixes of known headings, never arbitrary prose.
                if title.startswith(line + " "):
                    for following in lines[index + 1 : index + 3]:
                        candidate += " " + re.sub(r"\s*[-–]\s*", " - ", normalize(following))
                        if not title.startswith(candidate + " "):
                            break
                match = re.match(r"^" + re.escape(title) + r"(?:\b|$)(.*)$", candidate)
                if not match:
                    continue
                tail = match[1].strip(" :-–")
                if not tail or re.match(
                    r"^(?:c\.?\s|cislo\b|no\.?\s|number\b|o\b|na\b|za\b|[A-Z]{0,8}\s*[-/]?\s*\d)",
                    tail,
                    re.I,
                ):
                    result.add(kind)
    return result


def primary_heading_lines(text: str) -> dict[int, set[str]]:
    """Keep competing headings; exclude only inline contract references in invoice details."""
    lines = text.splitlines()
    headings = {
        i: kinds
        for i in range(len(lines))
        if (kinds := heading_types("\n".join(lines[i : i + 3]), first_only=True))
    }
    positions = list(headings)
    result = dict(headings)
    for index, position in enumerate(positions):
        if headings[position] != {"contract"} or index == 0:
            continue
        previous = positions[index - 1]
        following = positions[index + 1] if index + 1 < len(positions) else len(lines)
        caption = lines[position].strip()
        # Capitalized numbered references inside a continuous invoice detail block
        # are weaker than standalone, uppercase, page-separated or substantive titles.
        numbered = re.fullmatch(
            r"(?:zmluva|smlouva|contract)\s+(?:c\.?|cislo|no\.?|number)\s*:?\s+"
            r"[a-z0-9][a-z0-9/.-]*",
            normalize(caption),
        )
        details = lines[previous + 1 : position]
        has_total = any(
            re.match(re.escape(normalize(label)) + r"\s*:\s*\d", normalize(line))
            for line in details
            for label in LABELS["total_gross"]
        )
        contract_body = any(
            re.match(re.escape(normalize(label)) + r"(?:\s*:|\s+|$)", normalize(line))
            for line in lines[position + 1 : following]
            for field in ("contract_subject", "contract_date", "contract_amount")
            for label in LABELS[field]
        )
        if (
            numbered
            and not caption.split()[0].isupper()
            and headings[previous] == {"invoice"}
            and details
            and all(line.strip() for line in details)
            and has_total
            and not contract_body
        ):
            result.pop(position)
    return result


def primary_heading_types(text: str) -> set[str]:
    return set().union(*primary_heading_lines(text).values())


_PARTY_SECTION_CAPTION = (
    r"(?:bankov[eé] +spojenie|platobn[eé] +[uú]daje|faktura[čc]n[eé] +[uú]daje|"
    r"pozn[aá]mky|podmienky +[uú]hrady|predmet +zmluvy)"
)


def _party_section_boundary(text: str) -> bool:
    """Explicit section captions/markers, never uppercase alone or ordinary addresses."""
    text = normalize(text)
    if re.fullmatch(
        rf"{_PARTY_SECTION_CAPTION}(?:\s*:.*)?",
        text,
    ):
        return True
    if re.search(r"\b(?:s\.?\s*r\.?\s*o\.?|a\.?\s*s\.?|k\.?\s*s\.?)\s*:?$", text):
        return False  # A company named e.g. SEKCIA S.R.O. is not a section marker.
    return bool(
        re.fullmatch(
            r"(?:[-=]{3,}\s*[^\d]+?\s*[-=]{3,}|"
            r"(?:oddiel|sekcia|cast)\s+(?:\d+[.:]?\s+)?[^\d:]+:?)",
            text,
        )
    )


def observations(source: str) -> list[Observation]:
    """Conservative label/value associations, sections and aligned text columns."""
    entries = []
    normalized = normalize(source)
    cursor = 0
    sections: dict[int, str] = {}
    pending: dict[int, tuple[str, str, int]] = {}
    column_starts: dict[int, int] = {}
    labels = sorted(
        ((normalize(label), role) for role, values in LABELS.items() for label in values),
        key=lambda item: -len(item[0]),
    )
    for raw_line in source.splitlines():
        line = normalize(raw_line)
        if not line:
            pending.clear()
            continue
        line_start = normalized.find(line, cursor)
        if line_start < 0:
            continue
        cursor = line_start + len(line)
        # Protect only spaces within known captions, not their inline content or
        # neighbouring columns. Slice the original text to retain evidence offsets.
        captions = list(re.finditer(rf"\b{_PARTY_SECTION_CAPTION}\b", raw_line, re.I))
        parts = []
        part_start = 0
        for separator in re.finditer(r"\s*\|\s*|\t+| {2,}", raw_line.rstrip(" ")):
            if any(
                caption.start() < separator.start() and separator.end() < caption.end()
                for caption in captions
            ):
                continue
            # Leading indentation is not an empty text column; an explicit pipe
            # or tab, however, may represent an empty first column.
            if separator.start() or separator.group().strip(" "):
                parts.append(raw_line[part_start : separator.start()])
            part_start = separator.end()
        parts.append(raw_line[part_start:])
        # OCR may insert large spaces *inside* a standalone section caption.
        if (
            ":" not in line.rstrip(":")
            and not re.search(r"[|\t]", raw_line)
            and _party_section_boundary(line)
        ):
            parts = [raw_line.strip()]
        local_cursor = 0
        raw_cursor = 0
        for column, original in enumerate(parts):
            part = normalize(original)
            if not part:
                continue
            raw_start = raw_line.find(original, raw_cursor)
            raw_cursor = raw_start + len(original)
            if len(parts) == 1 and len(column_starts) > 1:
                aligned = [
                    key for key, offset in column_starts.items() if abs(raw_start - offset) <= 2
                ]
                column = aligned[0] if len(aligned) == 1 else -1
            start = line_start + line.find(part, local_cursor)
            local_cursor = max(0, start - line_start) + len(part)
            if _party_section_boundary(part):
                if len(parts) == 1 and column in {0, -1}:
                    # A standalone heading at the block's left edge has no reliable
                    # evidence that the neighbouring party column continues.
                    sections.clear()
                    pending.clear()
                else:
                    sections.pop(column, None)
                    pending.pop(column, None)
            role, rest = None, None
            for label, candidate_role in labels:
                match = re.match(r"^" + re.escape(label) + r"(?:\s*:\s*|\s+|$)(.*)$", part)
                if match:
                    role, rest = candidate_role, match[1].strip()
                    break
            generic = re.match(r"^(ico|dic|ic dph)\s*:\s*(.*)$", part)
            if generic:
                suffix = {"ico": "ico", "dic": "dic", "ic dph": "vat_id"}[generic[1]]
                section = sections.get(column)
                role = f"{section}_{suffix}" if section else f"ambiguous_{suffix}"
                rest = generic[2]
            if role:
                pending.pop(column, None)
                if role in {"supplier_name", "customer_name"}:
                    sections[column] = role.split("_")[0]
                    column_starts[column] = raw_start
                elif not generic and not role.startswith(("supplier_", "customer_")):
                    sections.pop(column, None)
                if rest:
                    entries.append(Observation(role, rest, original, start, start + len(part)))
                else:
                    pending[column] = (role, original, start)
            elif column in pending:
                role, prior, prior_start = pending.pop(column)
                entries.append(
                    Observation(role, part, prior + "\n" + original, prior_start, start + len(part))
                )
    return entries


def role_support(field: str, value: str | Decimal, evidence: Evidence, support: Support) -> str:
    """Return supported / ambiguous / conflicting independently of literal grounding."""
    if field == "classification":
        if evidence.locator:
            return "supported" if str(value) in heading_types(evidence.quote) else "conflicting"
        offset = 0
        normalized = normalize(support.source)
        lines = support.source.splitlines()
        headings = primary_heading_lines(support.source)
        supported_ranges = set()
        for position, line in enumerate(lines):
            text = normalize(line)
            if not text:
                continue
            start = normalized.find(text, offset)
            offset = start + len(text)
            if str(value) not in headings.get(position, set()):
                continue
            window = normalize("\n".join(lines[position : position + 3]))
            for title in TITLES[str(value)]:
                pattern = r"\s*[-–]\s*".join(
                    re.escape(part).replace(r"\ ", r"\s+")
                    for part in re.split(r"\s*[-–]\s*", normalize(title))
                )
                match = re.match(pattern + r"(?:\b|$)", window)
                if match:
                    # Numbers and prose on the same line cannot prove the type:
                    # every literal quote occurrence must overlap the title itself.
                    supported_ranges.update(
                        (left, right)
                        for left, right in support.ranges
                        if start < right and left < start + match.end()
                    )
        if supported_ranges and len(supported_ranges) == len(support.ranges):
            return "supported"
        return "ambiguous" if supported_ranges else "conflicting"
    if evidence.locator:
        context = support.context
        if any(
            normalize(evidence.quote).rstrip(" :") == normalize(label).rstrip(" :")
            for labels in LABELS.values()
            for label in labels
        ):
            return "conflicting"
        roles = {
            role
            for role, labels in LABELS.items()
            if any(
                normalize(label).rstrip(" .:") == normalize(line).rstrip(" .:")
                for label in labels
                for line in context.splitlines()
            )
        }
        if not roles:
            roles = {o.role for o in observations(context + ": " + evidence.quote)}
        if field in roles and len(roles) == 1:
            return "supported"
        return "conflicting" if roles and field not in roles else "ambiguous"
    roles = set()
    for observed in (
        support.observations if support.observations is not None else observations(support.source)
    ):
        overlaps = any(
            observed.start < end and start < observed.end for start, end in support.ranges
        )
        if not support.ranges:
            overlaps = normalize(observed.quote) in normalize(evidence.quote)
        if overlaps and equivalent(field, value, observed.value):
            roles.add(observed.role)
    if roles == {field}:
        return "supported"
    if roles and field not in roles and not all(r.startswith("ambiguous_") for r in roles):
        return "conflicting"
    return "ambiguous"
