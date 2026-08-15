import hmac
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings


bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Actor:
    subject: str
    role: str
    pharmacy_id: str | None = None


def _decode(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Autenticación requerida")
    try:
        return jwt.decode(
            credentials.credentials,
            get_settings().jwt_secret,
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expirado") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Token inválido") from exc


def current_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Actor:
    payload = _decode(credentials)
    role = str(payload.get("role") or payload.get("rol") or "patient").lower()
    return Actor(
        subject=str(payload["sub"]),
        role=role,
        pharmacy_id=str(payload["pharmacy_id"]) if payload.get("pharmacy_id") else None,
    )


def require_patient(actor: Annotated[Actor, Depends(current_actor)]) -> Actor:
    if actor.role not in {"patient", "paciente"}:
        raise HTTPException(status_code=403, detail="Se requiere una cuenta de paciente")
    return actor


def require_pharmacy(actor: Annotated[Actor, Depends(current_actor)]) -> Actor:
    if actor.role not in {"pharmacy", "farmacia"} or not actor.pharmacy_id:
        raise HTTPException(status_code=403, detail="Se requiere una cuenta de farmacia")
    return actor


def require_internal_api_key(
    x_internal_api_key: Annotated[str | None, Header()] = None,
) -> None:
    expected = get_settings().internal_api_key
    if not x_internal_api_key or not hmac.compare_digest(x_internal_api_key, expected):
        raise HTTPException(status_code=401, detail="Credencial interna inválida")

