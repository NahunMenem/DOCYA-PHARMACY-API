import unittest

from app.state_machine import OrderStatus, can_transition, require_transition


class OrderStateMachineTests(unittest.TestCase):
    def test_happy_path_reaches_delivery(self):
        path = [
            OrderStatus.SEARCHING_PHARMACY,
            OrderStatus.ASSIGNED,
            OrderStatus.QUOTED,
            OrderStatus.QUOTE_ACCEPTED,
            OrderStatus.PAYMENT_PENDING,
            OrderStatus.PAID,
            OrderStatus.PREPARING,
            OrderStatus.READY_FOR_DISPATCH,
            OrderStatus.IN_DELIVERY,
            OrderStatus.DELIVERED,
        ]
        for current, target in zip(path, path[1:]):
            self.assertTrue(can_transition(current, target), f"{current} -> {target}")

    def test_paid_order_cannot_be_cancelled(self):
        self.assertFalse(can_transition(OrderStatus.PAID, OrderStatus.CANCELLED))
        with self.assertRaises(ValueError):
            require_transition(OrderStatus.PAID, OrderStatus.CANCELLED)

    def test_expired_assignment_can_return_to_routing(self):
        self.assertTrue(
            can_transition(OrderStatus.ASSIGNED, OrderStatus.SEARCHING_PHARMACY)
        )

    def test_terminal_states_have_no_transitions(self):
        for terminal in (
            OrderStatus.DELIVERED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        ):
            self.assertFalse(can_transition(terminal, OrderStatus.SEARCHING_PHARMACY))


if __name__ == "__main__":
    unittest.main()

