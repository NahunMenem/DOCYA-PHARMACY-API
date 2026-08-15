import unittest
from uuid import uuid4

from pydantic import ValidationError

from app.schemas import (
    CreateOrderInput,
    CreateQuoteInput,
    OrderStatusUpdateInput,
    PharmacyRegistrationInput,
)


class OrderSchemaTests(unittest.TestCase):
    def base_payload(self):
        return {
            "patient_id": str(uuid4()),
            "prescription": {
                "source": "docya",
                "external_prescription_id": "RX-123",
            },
            "delivery": {
                "formatted_address": "Av. Corrientes 1234, CABA",
                "latitude": -34.6037,
                "longitude": -58.3816,
            },
            "items": [{"requested_name": "Medicamento indicado", "quantity": 1}],
        }

    def test_accepts_docya_prescription_reference(self):
        model = CreateOrderInput.model_validate(self.base_payload())
        self.assertEqual(model.prescription.external_prescription_id, "RX-123")

    def test_rejects_docya_prescription_without_reference(self):
        payload = self.base_payload()
        payload["prescription"].pop("external_prescription_id")
        with self.assertRaises(ValidationError):
            CreateOrderInput.model_validate(payload)

    def test_rejects_empty_order(self):
        payload = self.base_payload()
        payload["items"] = []
        with self.assertRaises(ValidationError):
            CreateOrderInput.model_validate(payload)

    def test_rejects_duplicate_quote_items(self):
        item_id = str(uuid4())
        item = {
            "order_item_id": item_id,
            "offered_name": "Medicamento",
            "quantity": 1,
            "unit_price": 100,
        }
        with self.assertRaises(ValidationError):
            CreateQuoteInput.model_validate({"items": [item, item]})

    def test_pharmacy_registration_requires_valid_cuit(self):
        with self.assertRaises(ValidationError):
            PharmacyRegistrationInput.model_validate(
                {
                    "legal_name": "Farmacia DocYa SAS",
                    "trade_name": "Farmacia DocYa",
                    "cuit": "123",
                    "regulatory_registry": "REG-123",
                    "owner_email": "farmacia@docya.test",
                    "password": "password-segura",
                    "branch_name": "Casa central",
                    "address": "Av. Corrientes 1234",
                    "locality": "CABA",
                    "province": "CABA",
                    "latitude": -34.60,
                    "longitude": -58.38,
                }
            )

    def test_pharmacy_can_only_advance_logistics_statuses(self):
        parsed = OrderStatusUpdateInput.model_validate({"status": "preparing"})
        self.assertEqual(parsed.status, "preparing")
        with self.assertRaises(ValidationError):
            OrderStatusUpdateInput.model_validate({"status": "paid"})


if __name__ == "__main__":
    unittest.main()
