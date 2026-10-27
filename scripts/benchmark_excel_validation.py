"""Offline Excel validation benchmark; input construction is outside the timer."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from app.schemas.extraction import CellLocator, Evidence, ExtractionData, Fact
from app.schemas.requests import Cell, Content, Sheet, SheetRow
from app.validators.validation import validate_extraction
from scripts.settings import OfflineSettings


def workload(count, headers=True):
    rows = [
        SheetRow(
            row=1,
            cells=[
                Cell(coordinate="A1", value="Číslo faktúry"),
                Cell(coordinate="B1", value="Suma spolu"),
                Cell(coordinate="C1", value="Mena"),
            ],
        )
    ]
    for row in range(2, count + 2):
        rows.append(
            SheetRow(
                row=row,
                cells=[
                    Cell(coordinate=f"A{row}", value=f"F-{row}"),
                    Cell(
                        coordinate=f"B{row}",
                        value=120 if row % 2 else 999,
                        header_cells=["B1"] if headers else [],
                    ),
                    Cell(coordinate=f"C{row}", value="EUR"),
                ],
            )
        )
    content = Content(sheets=[Sheet(name="Sheet", rows=rows)])
    cell = rows[-1].cells[1]
    raw = ExtractionData()
    raw.fields.total_gross = Fact(
        value=str(cell.value),
        status="verified",
        confidence=0.8,
        evidence=Evidence(
            quote=str(cell.value),
            locator=CellLocator(sheet="Sheet", cell=cell.coordinate, context_cells=["B1"]),
        ),
    )
    return raw, content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = []
    settings = OfflineSettings()
    for count in (200, 500, 1000):
        raw, content = workload(count)
        start = perf_counter()
        data, _ = validate_extraction(raw, content, settings)
        report.append(
            {
                "rows": count,
                "seconds": round(perf_counter() - start, 6),
                "status": data.fields.total_gross.status,
            }
        )
        print(json.dumps(report[-1]), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
