import re


def normalize_ico(value: str) -> str:
    result = re.sub(r"\s", "", value)
    if not re.fullmatch(r"\d{8}", result):
        raise ValueError("IČO must have eight digits")
    check = (
        11 - sum(int(n) * w for n, w in zip(result[:7], range(8, 1, -1), strict=True)) % 11
    ) % 10
    if check != int(result[-1]) or result == "00000000":
        raise ValueError("Invalid IČO checksum")
    return result


def normalize_dic(value: str) -> str:
    result = re.sub(r"\s", "", value)
    if not re.fullmatch(r"\d{10}", result):
        raise ValueError("Invalid DIČ format")
    return result


def normalize_iban(value: str) -> str:
    result = re.sub(r"\s", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", result):
        raise ValueError("Invalid IBAN format")
    if result.startswith("SK") and len(result) != 24:
        raise ValueError("Invalid Slovak IBAN length")
    rearranged = result[4:] + result[:4]
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    if int(digits) % 97 != 1:
        raise ValueError("Invalid IBAN checksum")
    return result
