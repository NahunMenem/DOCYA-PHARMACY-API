import unittest
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from app.main import patient_create_order
from app.schemas import PatientOrderRequest
from app.security import Actor


@contextmanager
def _fake_connection():
    yield object()


class PatientOrderPrescriptionTests(unittest.TestCase):
    def setUp(self):
        self.patient_id = uuid4()
        self.actor = Actor(subject=str(self.patient_id), role="paciente")
        self.request = PatientOrderRequest.model_validate(
            {
                "external_prescription_id": "recetario:41",
                "delivery": {
                    "formatted_address": "Av. Corrientes 1234, CABA",
                    "latitude": -34.6037,
                    "longitude": -58.3816,
                },
            }
        )

    @patch("app.main.create_order")
    @patch("app.main.connection", side_effect=_fake_connection)
    @patch("app.main.list_patient_prescriptions")
    def test_copies_medications_from_the_revalidated_real_prescription(
        self, prescriptions, _connection, create_order
    ):
        prescriptions.return_value = [
            {
                "external_prescription_id": "recetario:41",
                "doctor": "Dra. DocYa",
                "diagnosis": "Diagnostico",
                "issued_at": "2026-08-14T12:00:00-03:00",
                "medications": [
                    {"line_ref": "1", "name": "Amoxicilina 500 mg", "quantity": 2}
                ],
            }
        ]
        create_order.return_value = {"status": "searching_pharmacy"}

        result = patient_create_order(self.request, self.actor)

        self.assertEqual(result["status"], "searching_pharmacy")
        prescriptions.assert_called_once_with(self.patient_id)
        created_payload = create_order.call_args.args[1]
        self.assertEqual(created_payload.patient_id, self.patient_id)
        self.assertEqual(created_payload.items[0].requested_name, "Amoxicilina 500 mg")
        self.assertEqual(created_payload.items[0].quantity, 2)

    @patch("app.main.list_patient_prescriptions", return_value=[])
    def test_rejects_a_prescription_not_owned_by_the_patient(self, _prescriptions):
        with self.assertRaises(HTTPException) as raised:
            patient_create_order(self.request, self.actor)
        self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
