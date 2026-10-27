import re
from decimal import Decimal, InvalidOperation


def parse_money(value: str | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    else:
        text = re.sub(
            r"\s*(?:EUR|€|CZK|USD|GBP|CHF|PLN|HUF)$", "", value.strip(), flags=re.I
        ).strip()
        # Validate complete groups before removing separators. A lone punctuation
        # separator with three trailing digits remains ambiguous (1.234 / 1,234).
        if "." in text and "," in text:
            decimal = "," if text.rfind(",") > text.rfind(".") else "."
            thousands = "." if decimal == "," else ","
            pattern = (
                r"[+-]?\d{1,3}(?:"
                + re.escape(thousands)
                + r"\d{3})+"
                + re.escape(decimal)
                + r"\d{1,2}"
            )
            if not re.fullmatch(pattern, text):
                raise ValueError("Ambiguous money")
            canonical = text.replace(thousands, "").replace(decimal, ".")
        else:
            if not re.fullmatch(
                r"[+-]?(?:\d+|\d{1,3}(?:[ \u00a0\u202f]\d{3})+)(?:[,.]\d{1,2})?",
                text,
            ):
                raise ValueError("Ambiguous money")
            canonical = re.sub(r"[ \u00a0\u202f]", "", text).replace(",", ".")
        try:
            result = Decimal(canonical)
        except InvalidOperation as exc:
            raise ValueError("Invalid money") from exc
    if not result.is_finite() or abs(result) > Decimal("1000000000000"):
        raise ValueError("Money outside supported range")
    if result.as_tuple().exponent < -2:
        raise ValueError("More than two currency decimal places")
    return result.quantize(Decimal("0.01"))


def amounts_balance(net: Decimal, vat: Decimal, gross: Decimal, tolerance: Decimal) -> bool:
    return abs(net + vat - gross) <= tolerance
