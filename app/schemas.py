from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class DeliveryAddress(BaseModel):
    formatted_address: str = Field(min_length=5, max_length=500)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    notes: str | None = Field(default=None, max_length=500)


class PrescriptionInput(BaseModel):
    source: Literal["docya", "uploaded"]
    external_prescription_id: str | None = Field(default=None, max_length=200)
    object_key: str | None = Field(default=None, max_length=500)
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_source_reference(self):
        if self.source == "docya" and not self.external_prescription_id:
            raise ValueError("A DocYa prescription requires external_prescription_id")
        if self.source == "uploaded" and not self.object_key:
            raise ValueError("An uploaded prescription requires object_key")
        return self


class OrderItemInput(BaseModel):
    requested_name: str = Field(min_length=2, max_length=300)
    quantity: int = Field(default=1, ge=1, le=100)
    prescription_line_ref: str | None = Field(default=None, max_length=100)


class CreateOrderInput(BaseModel):
    patient_id: UUID
    prescription: PrescriptionInput
    delivery: DeliveryAddress
    items: list[OrderItemInput] = Field(min_length=1, max_length=50)


class OrderSummary(BaseModel):
    id: UUID
    patient_id: UUID
    status: str
    assigned_pharmacy_branch_id: UUID | None = None
    delivery_address: str
    created_at: datetime
    updated_at: datetime


class QuoteItemInput(BaseModel):
    order_item_id: UUID
    offered_name: str = Field(min_length=2, max_length=300)
    quantity: int = Field(ge=1, le=100)
    unit_price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    available: bool = True
    substitution_requires_approval: bool = False


class CreateQuoteInput(BaseModel):
    items: list[QuoteItemInput] = Field(min_length=1, max_length=50)
    delivery_fee: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    expires_in_minutes: int = Field(default=15, ge=5, le=120)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def reject_duplicate_order_items(self):
        item_ids = [item.order_item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("A quote cannot repeat an order item")
        return self


class QuoteDecision(BaseModel):
    quote_id: UUID


class PaymentConfirmationInput(BaseModel):
    pharmacy_id: UUID
    pharmacy_payment_id: str = Field(min_length=3, max_length=200)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: Literal["ARS"] = "ARS"
    raw_reference: dict = Field(default_factory=dict)


class PharmacyLoginInput(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=8, max_length=128)


class PharmacyRegistrationInput(BaseModel):
    legal_name: str = Field(min_length=3, max_length=200)
    trade_name: str = Field(min_length=2, max_length=200)
    cuit: str = Field(pattern=r"^[0-9]{11}$")
    regulatory_registry: str = Field(min_length=3, max_length=100)
    owner_email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    branch_name: str = Field(min_length=2, max_length=200)
    address: str = Field(min_length=5, max_length=500)
    locality: str = Field(min_length=2, max_length=150)
    province: str = Field(min_length=2, max_length=150)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    service_radius_km: Decimal = Field(default=Decimal("5"), gt=0, le=50)


class PharmacyActivationInput(BaseModel):
    regulatory_registry: str | None = Field(default=None, max_length=100)


class PatientOrderRequest(BaseModel):
    external_prescription_id: str = Field(min_length=3, max_length=200)
    delivery: DeliveryAddress


class BranchAvailabilityInput(BaseModel):
    is_open: bool
    is_online: bool


class OrderStatusUpdateInput(BaseModel):
    status: Literal[
        "preparing",
        "ready_for_dispatch",
        "in_delivery",
        "delivered",
    ]
