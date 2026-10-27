import re
import unicodedata

from app.core.config import Settings
from app.core.errors import ServiceError
from app.schemas.requests import Content


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", unaccented).strip()


def validate_content_limits(content: Content, settings: Settings) -> None:
    row_count = sum(len(sheet.rows) for sheet in content.sheets)
    cells = [cell for sheet in content.sheets for row in sheet.rows for cell in row.cells]
    character_count = sum(len(page.text) for page in content.pages) + sum(
        (len(str(cell.value)) if cell.value is not None else 0) + len(cell.formula or "")
        for cell in cells
    )
    if (
        len(content.pages) > settings.max_pages
        or len(content.sheets) > settings.max_sheets
        or row_count > settings.max_rows
        or len(cells) > settings.max_cells
        or character_count > settings.max_content_chars
    ):
        raise ServiceError("content_too_large", "Obsah prekračuje povolený limit.", 413)
