"""Conservative label vocabulary: unmatched meanings stay reviewable, never verified."""

from app.processors.ocr_text import normalize

LABELS = {
    "invoice_number": [
        "faktúra č.",
        "číslo faktúry",
        "faktúra číslo",
        "invoice no.",
        "invoice number",
        "faktura č.",
        "daňový doklad - faktúra č.",
        "daňový doklad – faktúra č.",
    ],
    "receipt_number": ["číslo bločku", "pokladničný doklad č."],
    "contract_number": ["zmluva č.", "číslo zmluvy"],
    "supplier_name": ["dodávateľ", "predávajúci", "dodavatel", "supplier"],
    "supplier_ico": ["IČO dodávateľa", "dodávateľ IČO"],
    "supplier_dic": ["DIČ dodávateľa", "dodávateľ DIČ"],
    "supplier_vat_id": ["IČ DPH dodávateľa"],
    "customer_name": ["odberateľ", "kupujúci", "odběratel", "customer"],
    "customer_ico": ["IČO odberateľa", "odberateľ IČO"],
    "customer_dic": ["DIČ odberateľa", "odberateľ DIČ"],
    "customer_vat_id": ["IČ DPH odberateľa"],
    "issue_date": ["dátum vystavenia", "vystavené", "datum vystavení", "issue date"],
    "due_date": ["dátum splatnosti", "splatnosť", "datum splatnosti", "splatnost", "due date"],
    "delivery_date": ["dátum dodania", "dátum dodania/služieb"],
    "payment_date": ["dátum úhrady", "uhradené dňa", "zaplatené dňa"],
    "total_net": [
        "základ DPH",
        "celkom bez DPH",
        "suma bez DPH",
        "základ dane",
        "základ daně",
        "net total",
    ],
    "total_vat": ["DPH spolu", "suma DPH", "DPH celkom", "VAT total"],
    "total_gross": [
        "suma spolu",
        "celkom s DPH",
        "celkom k úhrade",
        "celkom na úhradu",
        "celkem k úhradě",
        "total including VAT",
        "gross total",
    ],
    "paid_amount": ["uhradená suma", "zaplatená suma"],
    "currency": ["mena", "mena dokladu", "měna", "currency", "document currency"],
    "iban": ["IBAN"],
    "payment_method": ["spôsob úhrady", "forma úhrady"],
    "expense_description": ["predmet fakturácie", "názov výdavku"],
    "contract_subject": ["predmet zmluvy"],
    "contract_date": ["dátum uzavretia zmluvy"],
    "contract_amount": ["zmluvná suma"],
    "statement_period_start": ["obdobie od"],
    "statement_period_end": ["obdobie do"],
    "opening_balance": ["počiatočný zostatok"],
    "closing_balance": ["konečný zostatok"],
    "description": ["položka", "popis", "popis transakcie"],
    "quantity": ["množstvo"],
    "unit": ["jednotka"],
    "unit_price": ["jednotková cena"],
    "net": ["bez DPH"],
    "vat": ["DPH"],
    "gross": ["s DPH"],
    "date": ["dátum transakcie"],
    "amount": ["suma transakcie"],
    "reference": ["referencia"],
}
TITLES = {
    "invoice": ["faktúra", "invoice", "daňový doklad - faktúra", "daňový doklad – faktúra"],
    "receipt": ["pokladničný doklad", "bloček", "účtenka", "receipt"],
    "contract": ["zmluva", "smlouva", "contract"],
    "bank_statement": ["výpis z účtu", "výpis z bankového účtu", "bank statement"],
}
MONEY_FIELDS = {
    "total_net",
    "total_vat",
    "total_gross",
    "paid_amount",
    "contract_amount",
    "opening_balance",
    "closing_balance",
    "unit_price",
    "net",
    "vat",
    "gross",
    "amount",
}
DATE_FIELDS = {
    "issue_date",
    "due_date",
    "delivery_date",
    "payment_date",
    "contract_date",
    "statement_period_start",
    "statement_period_end",
    "date",
}


def canonical_classification(value: str) -> str | None:
    """Normalize exact known type names, never headings, numbers or free text.

    This only repairs the proposal's vocabulary; source evidence is still required.
    """
    name = normalize(value)
    if name in TITLES or name == "unknown":
        return name
    return next((kind for kind, titles in TITLES.items() if name in map(normalize, titles)), None)
