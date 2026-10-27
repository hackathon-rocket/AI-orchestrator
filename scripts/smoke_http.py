"""Start a temporary Uvicorn process and demonstrate authenticated mock HTTP analysis."""

import io
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from openpyxl import Workbook

from scripts.settings import OfflineSettings

ROOT = Path(__file__).resolve().parents[1]


def create_smoke_app():
    from app.main import create_app

    return create_app(OfflineSettings(agent_api_key=os.environ["SMOKE_HTTP_TOKEN"]))


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    token = secrets.token_urlsafe(32)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ)
    env.update(SMOKE_HTTP_TOKEN=token, PYTHONUTF8="1")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "scripts.smoke_http:create_smoke_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False
        ) as client:
            deadline = time.monotonic() + 20
            while True:
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Temporary service failed to boot")
                time.sleep(0.1)
            checks = {
                path: client.get(path).status_code
                for path in ["/health", "/ready", "/version", "/docs", "/openapi.json"]
            }
            payload = json.loads(
                (ROOT / "tests/fixtures/clean_invoice.json").read_text(encoding="utf-8")
            )
            unauthorized = client.post("/v1/analyze", json=payload)
            result = client.post(
                "/v1/analyze", json=payload, headers={"Authorization": f"Bearer {token}"}
            )
            result.raise_for_status()
            data = result.json()
            assert all(v == 200 for v in checks.values())
            assert unauthorized.status_code == 401
            row = data["tables"][0]["rows"][0]
            assert row["c_dokladu"]["value"] == "FA-2026-001"
            assert row["suma_doklad"]["value"] == 1230
            assert row["suma_dotacia"]["value"] is None
            assert data["requires_review"] is True
            out = ROOT / "docs/examples"
            out.mkdir(parents=True, exist_ok=True)
            _write_json(out / "mock_response.json", data)
            _write_json(out / "openapi.json", client.get("/openapi.json").json())
            dynamic_payload = json.loads((out / "dynamic_request.json").read_text(encoding="utf-8"))
            dynamic = client.post(
                "/v1/analyze", json=dynamic_payload, headers={"Authorization": f"Bearer {token}"}
            )
            dynamic.raise_for_status()
            assert dynamic.json()["fields"][0]["value"] == "FA-2026-001"
            _write_json(out / "dynamic_response.json", dynamic.json())
            book = Workbook()
            sheet = book.active
            sheet.title = "Doklad"
            sheet.append(["FAKTÚRA č. XLS-1"])
            sheet.append(["Číslo faktúry", "Suma spolu", "Mena"])
            sheet.append(["XLS-1", 123, "EUR"])
            stream = io.BytesIO()
            book.save(stream)
            book.close()
            prepared = client.post(
                "/v1/prepare-xlsx",
                content=stream.getvalue(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": (
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    ),
                },
            )
            prepared.raise_for_status()
            _write_json(out / "prepared_xlsx.json", prepared.json())
            xlsx = client.post(
                "/v1/analyze",
                json={
                    "document_id": "synthetic-xlsx-http",
                    "content": prepared.json()["content"],
                    "form": payload["form"],
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            xlsx.raise_for_status()
            assert xlsx.json()["tables"][0]["rows"][0]["suma_doklad"]["value"] == 123
            _write_json(out / "xlsx_response.json", xlsx.json())
            print(
                json.dumps(
                    {
                        "http_checks": checks,
                        "unauthenticated_post": unauthorized.status_code,
                        "authenticated_post": result.status_code,
                        "provider": data["meta"]["provider"],
                        "dynamic_mapping_post": dynamic.status_code,
                        "prepare_xlsx_post": prepared.status_code,
                        "xlsx_analysis_post": xlsx.status_code,
                        "warning_count": len(data["warnings"]),
                        "requires_review": data["requires_review"],
                    },
                    indent=2,
                )
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
