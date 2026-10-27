import re
from datetime import date


def parse_date(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value).isoformat()
    match = re.fullmatch(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", value)
    if not match:
        raise ValueError("Unsupported or ambiguous date")
    day, month, year = map(int, match.groups())
    return date(year, month, day).isoformat()
