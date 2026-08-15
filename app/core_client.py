"""Cliente interno de solo lectura hacia los datos clinicos de DocYa Core."""

from uuid import UUID

import httpx

from app.config import get_settings


class CoreServiceError(RuntimeError):
    """El servicio clinico no pudo responder correctamente."""


def list_patient_prescriptions(patient_id: UUID) -> list[dict]:
    settings = get_settings()
    url = (
        f"{settings.normalized_core_api_url}"
        f"/interno/farmacias/pacientes/{patient_id}/recetas"
    )
    try:
        response = httpx.get(
            url,
            headers={"X-Internal-API-Key": settings.internal_api_key},
            timeout=10.0,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise CoreServiceError("docya_core_unavailable") from exc

    if response.status_code == 404:
        raise LookupError("patient_not_found")
    if response.status_code != 200:
        raise CoreServiceError(
            f"docya_core_error:{response.status_code}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise CoreServiceError("docya_core_invalid_response") from exc
    if not isinstance(payload, list):
        raise CoreServiceError("docya_core_invalid_response")
    return payload
