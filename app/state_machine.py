from enum import Enum


class OrderStatus(str, Enum):
    SEARCHING_PHARMACY = "searching_pharmacy"
    ASSIGNED = "assigned"
    QUOTED = "quoted"
    QUOTE_ACCEPTED = "quote_accepted"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    PREPARING = "preparing"
    READY_FOR_DISPATCH = "ready_for_dispatch"
    IN_DELIVERY = "in_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.SEARCHING_PHARMACY: frozenset(
        {OrderStatus.ASSIGNED, OrderStatus.CANCELLED, OrderStatus.EXPIRED}
    ),
    OrderStatus.ASSIGNED: frozenset(
        {OrderStatus.QUOTED, OrderStatus.SEARCHING_PHARMACY, OrderStatus.CANCELLED}
    ),
    OrderStatus.QUOTED: frozenset(
        {OrderStatus.QUOTE_ACCEPTED, OrderStatus.SEARCHING_PHARMACY, OrderStatus.CANCELLED, OrderStatus.EXPIRED}
    ),
    OrderStatus.QUOTE_ACCEPTED: frozenset({OrderStatus.PAYMENT_PENDING, OrderStatus.CANCELLED}),
    OrderStatus.PAYMENT_PENDING: frozenset({OrderStatus.PAID, OrderStatus.CANCELLED, OrderStatus.EXPIRED}),
    OrderStatus.PAID: frozenset({OrderStatus.PREPARING}),
    OrderStatus.PREPARING: frozenset({OrderStatus.READY_FOR_DISPATCH}),
    OrderStatus.READY_FOR_DISPATCH: frozenset({OrderStatus.IN_DELIVERY}),
    OrderStatus.IN_DELIVERY: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


def can_transition(current: OrderStatus | str, target: OrderStatus | str) -> bool:
    return OrderStatus(target) in ALLOWED_TRANSITIONS[OrderStatus(current)]


def require_transition(current: OrderStatus | str, target: OrderStatus | str) -> None:
    if not can_transition(current, target):
        raise ValueError(f"Invalid order transition: {current} -> {target}")
