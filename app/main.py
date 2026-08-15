import logging
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import psycopg2
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.core_client import CoreServiceError, list_patient_prescriptions
from app.database import close_pool, connection
from app.repository import (
    accept_quote,
    advance_order_status,
    confirm_pharmacy_payment,
    create_order,
    create_quote,
    get_order,
    list_pharmacy_assignments,
    list_patient_orders,
    set_branch_availability,
)
from app.schemas import (
    BranchAvailabilityInput,
    CreateOrderInput,
    CreateQuoteInput,
    OrderItemInput,
    OrderStatusUpdateInput,
    PaymentConfirmationInput,
    PatientOrderRequest,
    PharmacyActivationInput,
    PharmacyLoginInput,
    PharmacyRegistrationInput,
    PrescriptionInput,
    QuoteDecision,
)
from app.security import Actor, require_internal_api_key, require_patient, require_pharmacy
from app.pharmacy_auth import (
    activate_pharmacy,
    login_pharmacy,
    pharmacy_profile,
    register_pharmacy,
)


logger = logging.getLogger("docya.pharmacy")


def _serialized_datetime(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings().validate_runtime_secrets()
    yield
    close_pool()


app = FastAPI(
    title="DocYa Pharmacy API",
    version="0.1.0",
    docs_url="/docs" if get_settings().environment != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Internal-API-Key"],
)


def _translate_domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409, detail=str(exc))
    logger.exception("Unhandled pharmacy domain error")
    return HTTPException(status_code=500, detail="No se pudo completar la operación")


@app.get("/health", tags=["health"])
def health():
    return {
        "status": "ok",
        "service": "docya-pharmacy-api",
        "payment_mode": get_settings().pharmacy_payment_mode,
    }


@app.get("/ready", tags=["health"])
def ready():
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('pharmacy.medication_orders')")
            if cur.fetchone()[0] is None:
                raise HTTPException(status_code=503, detail="Migraciones pendientes")
        return {"status": "ready"}
    except HTTPException:
        raise
    except psycopg2.Error as exc:
        logger.warning("Readiness database check failed: %s", exc)
        raise HTTPException(status_code=503, detail="Base de datos no disponible") from exc


@app.post(
    "/v1/internal/orders",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_internal_api_key)],
    tags=["internal"],
)
def internal_create_order(payload: CreateOrderInput):
    try:
        with connection() as conn:
            return create_order(conn, payload)
    except psycopg2.Error as exc:
        logger.exception("Could not create medication order")
        raise HTTPException(status_code=503, detail="No se pudo crear el pedido") from exc


@app.post("/v1/auth/register", status_code=201, tags=["auth"])
def pharmacy_register(payload: PharmacyRegistrationInput):
    try:
        with connection() as conn:
            result = register_pharmacy(conn, payload)
            if get_settings().pharmacy_auto_activate_test_registrations:
                activate_pharmacy(
                    conn,
                    result["pharmacy_id"],
                    payload.regulatory_registry,
                )
                result["status"] = "active"
                result["test_auto_activated"] = True
            return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except psycopg2.Error as exc:
        logger.exception("Could not register pharmacy")
        raise HTTPException(status_code=503, detail="No se pudo registrar la farmacia") from exc


@app.post("/v1/auth/login", tags=["auth"])
def pharmacy_login(payload: PharmacyLoginInput):
    try:
        with connection() as conn:
            result = login_pharmacy(conn, payload.email, payload.password)
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudo iniciar sesión") from exc
    if not result:
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    return result


@app.post(
    "/v1/internal/pharmacies/{pharmacy_id}/activate",
    dependencies=[Depends(require_internal_api_key)],
    tags=["internal"],
)
def internal_activate_pharmacy(pharmacy_id: UUID, payload: PharmacyActivationInput):
    try:
        with connection() as conn:
            return activate_pharmacy(conn, pharmacy_id, payload.regulatory_registry)
    except (LookupError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudo habilitar la farmacia") from exc


@app.get("/v1/prescriptions", tags=["patient"])
def patient_prescriptions(actor: Annotated[Actor, Depends(require_patient)]):
    try:
        return list_patient_prescriptions(UUID(actor.subject))
    except (LookupError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc
    except CoreServiceError as exc:
        logger.warning("Could not list patient prescriptions from DocYa Core: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="No se pudieron cargar las recetas de DocYa",
        ) from exc


@app.get("/v1/orders", tags=["patient"])
def patient_orders(actor: Annotated[Actor, Depends(require_patient)]):
    try:
        with connection() as conn:
            orders = list_patient_orders(conn, UUID(actor.subject))
            for order in orders:
                order["payment_mode"] = get_settings().pharmacy_payment_mode
            return orders
    except (ValueError, psycopg2.Error) as exc:
        raise HTTPException(status_code=503, detail="No se pudieron cargar los pedidos") from exc


@app.post("/v1/orders", status_code=201, tags=["patient"])
def patient_create_order(
    payload: PatientOrderRequest,
    actor: Annotated[Actor, Depends(require_patient)],
):
    try:
        patient_id = UUID(actor.subject)
        eligible = list_patient_prescriptions(patient_id)
        selected = next(
            (
                item
                for item in eligible
                if item["external_prescription_id"] == payload.external_prescription_id
            ),
            None,
        )
        if not selected:
            raise PermissionError("prescription_not_eligible_for_patient")
        with connection() as conn:
            order_payload = CreateOrderInput(
                patient_id=patient_id,
                prescription=PrescriptionInput(
                    source="docya",
                    external_prescription_id=payload.external_prescription_id,
                    metadata={
                        "doctor": selected.get("doctor"),
                        "diagnosis": selected.get("diagnosis"),
                        "issued_at": _serialized_datetime(selected.get("issued_at")),
                    },
                ),
                delivery=payload.delivery,
                items=[
                    OrderItemInput(
                        requested_name=item["name"],
                        quantity=item["quantity"],
                        prescription_line_ref=item["line_ref"],
                    )
                    for item in selected["medications"]
                ],
            )
            return create_order(conn, order_payload)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc
    except CoreServiceError as exc:
        logger.warning("Could not validate prescription in DocYa Core: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="No se pudo validar la receta con DocYa",
        ) from exc
    except psycopg2.Error as exc:
        logger.exception("Could not create patient medication order")
        raise HTTPException(status_code=503, detail="No se pudo solicitar la cotización") from exc


@app.get("/v1/orders/{order_id}", tags=["patient"])
def patient_get_order(
    order_id: UUID,
    actor: Annotated[Actor, Depends(require_patient)],
):
    try:
        with connection() as conn:
            order = get_order(conn, order_id)
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudo consultar el pedido") from exc
    if not order:
        raise HTTPException(status_code=404, detail="Pedido inexistente")
    if str(order["patient_id"]) != actor.subject:
        raise HTTPException(status_code=403, detail="El pedido no pertenece al paciente")
    order["payment_mode"] = get_settings().pharmacy_payment_mode
    return order


@app.post("/v1/orders/{order_id}/quote/accept", tags=["patient"])
def patient_accept_quote(
    order_id: UUID,
    payload: QuoteDecision,
    actor: Annotated[Actor, Depends(require_patient)],
):
    try:
        with connection() as conn:
            result = accept_quote(
                conn,
                order_id,
                payload.quote_id,
                actor.subject,
                simulate_payment=get_settings().pharmacy_payment_mode == "simulated",
            )
            result["payment_mode"] = get_settings().pharmacy_payment_mode
            result["real_charge_performed"] = False
            return result
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudo aceptar la cotización") from exc


@app.get("/v1/pharmacy/assignments", tags=["pharmacy"])
def pharmacy_assignments(actor: Annotated[Actor, Depends(require_pharmacy)]):
    try:
        with connection() as conn:
            return list_pharmacy_assignments(conn, UUID(actor.pharmacy_id))
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudieron consultar los pedidos") from exc


@app.get("/v1/pharmacy/me", tags=["pharmacy"])
def pharmacy_me(actor: Annotated[Actor, Depends(require_pharmacy)]):
    try:
        with connection() as conn:
            profile = pharmacy_profile(conn, UUID(actor.pharmacy_id))
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudo cargar la farmacia") from exc
    if not profile:
        raise HTTPException(status_code=404, detail="Farmacia inexistente")
    profile["payment_mode"] = get_settings().pharmacy_payment_mode
    return profile


@app.patch("/v1/pharmacy/branches/{branch_id}/availability", tags=["pharmacy"])
def pharmacy_branch_availability(
    branch_id: UUID,
    payload: BranchAvailabilityInput,
    actor: Annotated[Actor, Depends(require_pharmacy)],
):
    try:
        with connection() as conn:
            return set_branch_availability(
                conn,
                UUID(actor.pharmacy_id),
                branch_id,
                payload.is_open,
                payload.is_online,
            )
    except (PermissionError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudo actualizar la disponibilidad") from exc


@app.post("/v1/pharmacy/orders/{order_id}/quotes", status_code=201, tags=["pharmacy"])
def pharmacy_create_quote(
    order_id: UUID,
    payload: CreateQuoteInput,
    actor: Annotated[Actor, Depends(require_pharmacy)],
):
    try:
        with connection() as conn:
            return create_quote(conn, order_id, UUID(actor.pharmacy_id), payload)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudo crear la cotización") from exc


@app.post("/v1/pharmacy/orders/{order_id}/status", tags=["pharmacy"])
def pharmacy_update_order_status(
    order_id: UUID,
    payload: OrderStatusUpdateInput,
    actor: Annotated[Actor, Depends(require_pharmacy)],
):
    try:
        with connection() as conn:
            return advance_order_status(
                conn,
                order_id,
                UUID(actor.pharmacy_id),
                payload.status,
            )
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudo actualizar el pedido") from exc


@app.post(
    "/v1/internal/orders/{order_id}/payment-confirmed",
    dependencies=[Depends(require_internal_api_key)],
    tags=["internal"],
)
def internal_payment_confirmed(order_id: UUID, payload: PaymentConfirmationInput):
    """Solo un adaptador de webhook verificado puede confirmar cobros de la farmacia."""
    try:
        with connection() as conn:
            return confirm_pharmacy_payment(conn, order_id, payload.pharmacy_id, payload)
    except (LookupError, PermissionError, ValueError) as exc:
        raise _translate_domain_error(exc) from exc
    except psycopg2.Error as exc:
        raise HTTPException(status_code=503, detail="No se pudo confirmar el pago") from exc
