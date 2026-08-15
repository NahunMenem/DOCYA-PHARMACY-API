import unittest
from decimal import Decimal
from uuid import uuid4

from app.repository import accept_quote


class FakeCursor:
    def __init__(self, order, updated):
        self.order = order
        self.updated = updated
        self.executions = []
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.executions.append((sql, params))
        normalized = " ".join(sql.split())
        if "SELECT o.id, o.patient_id" in normalized:
            self._row = self.order
        elif "SELECT * FROM pharmacy.medication_orders" in normalized:
            self._row = self.updated
        else:
            self._row = None

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, order, updated):
        self.cursor_instance = FakeCursor(order, updated)
        self.commits = 0

    def cursor(self, **_):
        return self.cursor_instance

    def commit(self):
        self.commits += 1


class SimulatedPaymentTests(unittest.TestCase):
    def test_accepting_quote_can_mark_paid_without_real_provider(self):
        order_id = uuid4()
        quote_id = uuid4()
        pharmacy_id = uuid4()
        patient_id = str(uuid4())
        conn = FakeConnection(
            {
                "id": order_id,
                "patient_id": patient_id,
                "status": "quoted",
                "active_quote_id": quote_id,
                "total_amount": Decimal("12500.00"),
                "pharmacy_id": pharmacy_id,
            },
            {"id": order_id, "status": "paid"},
        )

        result = accept_quote(
            conn,
            order_id,
            quote_id,
            patient_id,
            simulate_payment=True,
        )

        sql = "\n".join(statement for statement, _ in conn.cursor_instance.executions)
        self.assertEqual(result["status"], "paid")
        self.assertIn("'simulation'", sql)
        self.assertIn("payment_simulated", str(conn.cursor_instance.executions))
        self.assertNotIn("'mercadopago'", sql)
        self.assertEqual(conn.commits, 1)


if __name__ == "__main__":
    unittest.main()
