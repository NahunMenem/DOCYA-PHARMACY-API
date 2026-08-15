import unittest

from app.config import Settings


class PharmacyTestModeConfigTests(unittest.TestCase):
    def _settings(self, **overrides):
        values = {
            "database_url": "postgresql://user:password@localhost:5432/test",
            "core_api_url": "http://core.test",
            "jwt_secret": "test-secret",
            "internal_api_key": "test-internal-key",
            **overrides,
        }
        return Settings(**values)

    def test_simulated_payment_allows_test_auto_activation(self):
        settings = self._settings(
            pharmacy_payment_mode="simulated",
            pharmacy_auto_activate_test_registrations=True,
        )
        settings.validate_runtime_secrets()

    def test_auto_activation_is_rejected_outside_simulation(self):
        settings = self._settings(
            pharmacy_payment_mode="disabled",
            pharmacy_auto_activate_test_registrations=True,
        )
        with self.assertRaisesRegex(RuntimeError, "requires simulated payment mode"):
            settings.validate_runtime_secrets()


if __name__ == "__main__":
    unittest.main()
