import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from app.core_client import (
    CoreServiceError,
    get_prescription_for_pharmacy,
    list_patient_prescriptions,
)


class CoreClientTests(unittest.TestCase):
    def setUp(self):
        self.patient_id = uuid4()
        self.settings = SimpleNamespace(
            normalized_core_api_url="https://core.docya.test",
            internal_api_key="shared-secret",
        )

    @patch("app.core_client.get_settings")
    @patch("app.core_client.httpx.get")
    def test_loads_only_the_authenticated_patients_real_prescriptions(
        self, http_get, settings
    ):
        settings.return_value = self.settings
        prescription = {
            "external_prescription_id": "recetario:41",
            "medications": [{"line_ref": "1", "name": "Amoxicilina", "quantity": 1}],
        }
        http_get.return_value = Mock(status_code=200)
        http_get.return_value.json.return_value = [prescription]

        result = list_patient_prescriptions(self.patient_id)

        self.assertEqual(result, [prescription])
        http_get.assert_called_once_with(
            f"https://core.docya.test/interno/farmacias/pacientes/{self.patient_id}/recetas",
            headers={"X-Internal-API-Key": "shared-secret"},
            timeout=10.0,
        )

    @patch("app.core_client.get_settings")
    @patch("app.core_client.httpx.get")
    def test_rejects_an_invalid_core_response(self, http_get, settings):
        settings.return_value = self.settings
        http_get.return_value = Mock(status_code=200)
        http_get.return_value.json.return_value = {"unexpected": True}

        with self.assertRaisesRegex(CoreServiceError, "invalid_response"):
            list_patient_prescriptions(self.patient_id)

    @patch("app.core_client.get_settings")
    @patch("app.core_client.httpx.get")
    def test_loads_the_assigned_order_prescription_from_core(self, http_get, settings):
        settings.return_value = self.settings
        prescription = {"id": 41, "source": "recetario", "patient": {"name": "Ana"}}
        http_get.return_value = Mock(status_code=200)
        http_get.return_value.json.return_value = prescription

        result = get_prescription_for_pharmacy("recetario", 41)

        self.assertEqual(result, prescription)
        http_get.assert_called_once_with(
            "https://core.docya.test/interno/farmacias/recetas/recetario/41",
            headers={"X-Internal-API-Key": "shared-secret"},
            timeout=10.0,
        )

    @patch("app.core_client.get_settings")
    @patch("app.core_client.httpx.get")
    def test_reports_a_missing_prescription(self, http_get, settings):
        settings.return_value = self.settings
        http_get.return_value = Mock(status_code=404)

        with self.assertRaisesRegex(LookupError, "prescription_not_found"):
            get_prescription_for_pharmacy("recetario", 999)


if __name__ == "__main__":
    unittest.main()
