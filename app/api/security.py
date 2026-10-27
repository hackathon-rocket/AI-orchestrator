import secrets

from app.core.errors import ServiceError


def authenticate(header: str | None, expected: str) -> None:
    if len(expected) < 16:
        raise ServiceError("not_ready", "Služba nie je nakonfigurovaná.", 503)
    if not header:
        raise ServiceError("auth_missing", "Vyžaduje sa autentifikácia služby.", 401)
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token.encode(), expected.encode()):
        raise ServiceError("auth_invalid", "Neplatná autentifikácia služby.", 401)
