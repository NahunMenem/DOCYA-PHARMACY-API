import unittest
from contextlib import contextmanager
from unittest.mock import ANY, patch
from uuid import uuid4

from fastapi import HTTPException

from app.main import pharmacy_order_prescription
from app.security import Actor


@contextmanager
def _fake_connection():
    yield object()


class PharmacyPrescriptionAccessTests(unittest.TestCase):
    def setUp(self):
        self.pharmacy_id = uuid4()
        self.order_id = uuid4()
        self.actor = Actor(
            subject=str(uuid4()),
            role="pharmacy",
            pharmacy_id=str(self.pharmacy_id),
        )

    @patch("app.main.get_prescription_for_pharmacy")
    @patch("app.main.get_pharmacy_order_prescription_reference")
    @patch("app.main.connection", side_effect=_fake_connection)
    def test_assigned_pharmacy_can_load_its_prescription(
        self, _connection, reference, load_prescription
    ):
        reference.return_value = "recetario:41"
        expected = {"id": 41, "source": "recetario"}
        load_prescription.return_value = expected

        result = pharmacy_order_prescription(self.order_id, self.actor)

        self.assertEqual(result, expected)
        reference.assert_called_once_with(
            ANY,
            self.pharmacy_id,
            self.order_id,
        )
        load_prescription.assert_called_once_with("recetario", 41)

    @patch("app.main.get_prescription_for_pharmacy")
    @patch(
        "app.main.get_pharmacy_order_prescription_reference",
        side_effect=PermissionError("order_not_assigned_to_pharmacy"),
    )
    @patch("app.main.connection", side_effect=_fake_connection)
    def test_other_pharmacy_cannot_load_the_prescription(
        self, _connection, _reference, load_prescription
    ):
        with self.assertRaises(HTTPException) as raised:
            pharmacy_order_prescription(self.order_id, self.actor)

        self.assertEqual(raised.exception.status_code, 403)
        load_prescription.assert_not_called()


if __name__ == "__main__":
    unittest.main()
