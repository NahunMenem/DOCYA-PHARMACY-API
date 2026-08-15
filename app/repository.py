from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from psycopg2.extras import Json, RealDictCursor

from app.schemas import CreateOrderInput, CreateQuoteInput, PaymentConfirmationInput
from app.state_machine import OrderStatus, require_transition


def _event(cur, order_id: UUID, event_type: str, actor_type: str, actor_id: str | None, data: dict | None = None):
    cur.execute(
        """
        INSERT INTO pharmacy.order_events
            (order_id, event_type, actor_type, actor_id, data)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (str(order_id), event_type, actor_type, actor_id, Json(data or {})),
    )


def _outbox(cur, aggregate_id: UUID, event_type: str, payload: dict):
    cur.execute(
        """
        INSERT INTO pharmacy.outbox_events
            (aggregate_type, aggregate_id, event_type, payload)
        VALUES ('medication_order', %s, %s, %s)
        """,
        (str(aggregate_id), event_type, Json(payload)),
    )


def _nearest_available_branch(cur, latitude: float, longitude: float):
    cur.execute(
        """
        WITH candidates AS (
            SELECT
                b.id,
                b.pharmacy_id,
                6371 * acos(
                    LEAST(1, GREATEST(-1,
                        cos(radians(%s)) * cos(radians(b.latitude))
                        * cos(radians(b.longitude) - radians(%s))
                        + sin(radians(%s)) * sin(radians(b.latitude))
                    ))
                ) AS distance_km
            FROM pharmacy.pharmacy_branches b
            JOIN pharmacy.pharmacies p ON p.id = b.pharmacy_id
            WHERE p.status = 'active'
              AND p.regulatory_verified_at IS NOT NULL
              AND b.status = 'active'
              AND b.is_open = TRUE
              AND b.is_online = TRUE
              AND b.accepts_new_orders = TRUE
        )
        SELECT id, pharmacy_id, distance_km
        FROM candidates
        WHERE distance_km <= (
            SELECT service_radius_km
            FROM pharmacy.pharmacy_branches
            WHERE id = candidates.id
        )
        ORDER BY distance_km ASC
        LIMIT 1
        """,
        (latitude, longitude, latitude),
    )
    return cur.fetchone()


def create_order(conn, payload: CreateOrderInput) -> dict:
    order_id = uuid4()
    prescription_id = uuid4()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO pharmacy.medication_orders (
                id, patient_id, status, delivery_address, delivery_notes,
                delivery_latitude, delivery_longitude
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(order_id), str(payload.patient_id), OrderStatus.SEARCHING_PHARMACY.value,
                payload.delivery.formatted_address, payload.delivery.notes,
                payload.delivery.latitude, payload.delivery.longitude,
            ),
        )
        order = cur.fetchone()
        cur.execute(
            """
            INSERT INTO pharmacy.order_prescriptions (
                id, order_id, source, external_prescription_id, object_key, sha256, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(prescription_id), str(order_id), payload.prescription.source,
                payload.prescription.external_prescription_id, payload.prescription.object_key,
                payload.prescription.sha256, Json(payload.prescription.metadata),
            ),
        )
        for item in payload.items:
            cur.execute(
                """
                INSERT INTO pharmacy.order_items
                    (order_id, requested_name, requested_quantity, prescription_line_ref)
                VALUES (%s, %s, %s, %s)
                """,
                (str(order_id), item.requested_name, item.quantity, item.prescription_line_ref),
            )
        _event(cur, order_id, "order_created", "service", "docya-core")

        branch = _nearest_available_branch(
            cur, payload.delivery.latitude, payload.delivery.longitude
        )
        if branch:
            assignment_id = uuid4()
            cur.execute(
                """
                INSERT INTO pharmacy.order_assignments (
                    id, order_id, pharmacy_branch_id, status, distance_km, offered_at, expires_at
                ) VALUES (%s, %s, %s, 'offered', %s, NOW(), NOW() + INTERVAL '5 minutes')
                """,
                (str(assignment_id), str(order_id), str(branch["id"]), branch["distance_km"]),
            )
            cur.execute(
                """
                UPDATE pharmacy.medication_orders
                SET status = %s, assigned_pharmacy_branch_id = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (OrderStatus.ASSIGNED.value, str(branch["id"]), str(order_id)),
            )
            order = cur.fetchone()
            _event(
                cur,
                order_id,
                "pharmacy_assignment_offered",
                "service",
                "routing",
                {"assignment_id": str(assignment_id), "distance_km": float(branch["distance_km"])},
            )
            _outbox(
                cur,
                order_id,
                "pharmacy_assignment_offered",
                {
                    "assignment_id": str(assignment_id),
                    "pharmacy_id": str(branch["pharmacy_id"]),
                    "pharmacy_branch_id": str(branch["id"]),
                },
            )
        conn.commit()
        return dict(order)


def get_order(conn, order_id: UUID) -> dict | None:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, patient_id, status, assigned_pharmacy_branch_id, active_quote_id,
                   delivery_address, delivery_notes, delivery_latitude,
                   delivery_longitude, medication_subtotal, delivery_fee,
                   total_amount, created_at, updated_at
            FROM pharmacy.medication_orders
            WHERE id = %s
            """,
            (str(order_id),),
        )
        order = cur.fetchone()
        if not order:
            return None
        cur.execute(
            """
            SELECT id, requested_name, requested_quantity, prescription_line_ref
            FROM pharmacy.order_items WHERE order_id = %s ORDER BY created_at, id
            """,
            (str(order_id),),
        )
        result = dict(order)
        result["items"] = [dict(row) for row in cur.fetchall()]
        if order["active_quote_id"]:
            cur.execute(
                """
                SELECT q.id, q.status, q.medication_subtotal, q.delivery_fee,
                       q.total_amount, q.notes, q.expires_at, q.accepted_at,
                       p.trade_name AS pharmacy_name
                FROM pharmacy.pharmacy_quotes q
                JOIN pharmacy.pharmacies p ON p.id = q.pharmacy_id
                WHERE q.id = %s
                """,
                (str(order["active_quote_id"]),),
            )
            quote = cur.fetchone()
            result["quote"] = dict(quote) if quote else None
        else:
            result["quote"] = None
        return result


def list_patient_orders(conn, patient_id: UUID) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT o.id, o.status, o.medication_subtotal, o.delivery_fee,
                   o.total_amount, o.created_at, o.updated_at,
                   p.trade_name AS pharmacy_name
            FROM pharmacy.medication_orders o
            LEFT JOIN pharmacy.pharmacy_branches b ON b.id = o.assigned_pharmacy_branch_id
            LEFT JOIN pharmacy.pharmacies p ON p.id = b.pharmacy_id
            WHERE o.patient_id = %s
            ORDER BY o.created_at DESC
            LIMIT 50
            """,
            (str(patient_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def set_branch_availability(
    conn, pharmacy_id: UUID, branch_id: UUID, is_open: bool, is_online: bool
) -> dict:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE pharmacy.pharmacy_branches b
            SET is_open = %s, is_online = %s,
                accepts_new_orders = (%s AND %s), updated_at = NOW()
            FROM pharmacy.pharmacies p
            WHERE b.id = %s AND b.pharmacy_id = %s
              AND p.id = b.pharmacy_id
              AND b.status = 'active' AND p.status = 'active'
              AND p.billing_status <> 'blocked'
            RETURNING b.id, b.name, b.is_open, b.is_online, b.accepts_new_orders
            """,
            (
                is_open, is_online, is_open, is_online,
                str(branch_id), str(pharmacy_id),
            ),
        )
        row = cur.fetchone()
        if not row:
            raise PermissionError("branch_not_available")
        conn.commit()
        return dict(row)


def list_pharmacy_assignments(conn, pharmacy_id: UUID) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT a.id AS assignment_id, a.status AS assignment_status,
                   a.distance_km, a.offered_at, a.expires_at,
                   o.id, o.status, o.created_at, o.updated_at,
                   o.delivery_address, o.medication_subtotal,
                   o.delivery_fee, o.total_amount, o.paid_at,
                   COALESCE((
                       SELECT jsonb_agg(jsonb_build_object(
                           'id', oi.id,
                           'requested_name', oi.requested_name,
                           'requested_quantity', oi.requested_quantity,
                           'prescription_line_ref', oi.prescription_line_ref
                       ) ORDER BY oi.created_at, oi.id)
                       FROM pharmacy.order_items oi
                       WHERE oi.order_id = o.id
                   ), '[]'::jsonb) AS items
            FROM pharmacy.order_assignments a
            JOIN pharmacy.pharmacy_branches b ON b.id = a.pharmacy_branch_id
            JOIN pharmacy.medication_orders o ON o.id = a.order_id
            WHERE b.pharmacy_id = %s
              AND a.status IN ('offered', 'accepted')
              AND o.status NOT IN ('cancelled', 'rejected', 'expired')
            ORDER BY o.updated_at DESC
            LIMIT 100
            """,
            (str(pharmacy_id),),
        )
        return [dict(row) for row in cur.fetchall()]


def create_quote(conn, order_id: UUID, pharmacy_id: UUID, payload: CreateQuoteInput) -> dict:
    quote_id = uuid4()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT o.status, o.patient_id, b.pharmacy_id,
                   a.id AS assignment_id, a.status AS assignment_status
            FROM pharmacy.medication_orders o
            JOIN pharmacy.pharmacy_branches b ON b.id = o.assigned_pharmacy_branch_id
            JOIN pharmacy.order_assignments a
              ON a.order_id = o.id AND a.pharmacy_branch_id = b.id
            WHERE o.id = %s
              AND a.status IN ('offered', 'accepted')
              AND (a.status = 'accepted' OR a.expires_at > NOW())
            FOR UPDATE OF o
            """,
            (str(order_id),),
        )
        order = cur.fetchone()
        if not order:
            raise LookupError("order_not_found")
        if str(order["pharmacy_id"]) != str(pharmacy_id):
            raise PermissionError("order_not_assigned_to_pharmacy")
        require_transition(order["status"], OrderStatus.QUOTED)

        subtotal = sum(
            item.unit_price * item.quantity for item in payload.items if item.available
        )
        total = subtotal + payload.delivery_fee
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=payload.expires_in_minutes)
        cur.execute(
            """
            INSERT INTO pharmacy.pharmacy_quotes (
                id, order_id, pharmacy_id, status, medication_subtotal,
                delivery_fee, total_amount, notes, expires_at
            ) VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                str(quote_id), str(order_id), str(pharmacy_id), subtotal,
                payload.delivery_fee, total, payload.notes, expires_at,
            ),
        )
        quote = cur.fetchone()
        for item in payload.items:
            cur.execute(
                """
                INSERT INTO pharmacy.quote_items (
                    quote_id, order_item_id, offered_name, quantity, unit_price,
                    available, substitution_requires_approval
                )
                SELECT %s, oi.id, %s, %s, %s, %s, %s
                FROM pharmacy.order_items oi
                WHERE oi.id = %s AND oi.order_id = %s
                RETURNING id
                """,
                (
                    str(quote_id), item.offered_name,
                    item.quantity, item.unit_price, item.available,
                    item.substitution_requires_approval,
                    str(item.order_item_id), str(order_id),
                ),
            )
            if cur.fetchone() is None:
                raise ValueError("quote_item_does_not_belong_to_order")
        cur.execute(
            """
            UPDATE pharmacy.order_assignments
            SET status = 'accepted', responded_at = COALESCE(responded_at, NOW())
            WHERE id = %s
            """,
            (str(order["assignment_id"]),),
        )
        cur.execute(
            """
            UPDATE pharmacy.medication_orders
            SET status = %s, active_quote_id = %s,
                medication_subtotal = %s, delivery_fee = %s, total_amount = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                OrderStatus.QUOTED.value, str(quote_id), subtotal,
                payload.delivery_fee, total, str(order_id),
            ),
        )
        _event(cur, order_id, "quote_created", "pharmacy", str(pharmacy_id), {"quote_id": str(quote_id)})
        _outbox(
            cur,
            order_id,
            "quote_created",
            {"quote_id": str(quote_id), "patient_id": str(order["patient_id"])},
        )
        conn.commit()
        return dict(quote)


def accept_quote(
    conn,
    order_id: UUID,
    quote_id: UUID,
    patient_id: str,
    *,
    simulate_payment: bool = False,
) -> dict:
    """Acepta una cotizacion y, en modo prueba, registra un pago ficticio atomico."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT o.id, o.patient_id, o.status, o.active_quote_id,
                   o.total_amount, b.pharmacy_id
            FROM pharmacy.medication_orders o
            JOIN pharmacy.pharmacy_branches b
              ON b.id = o.assigned_pharmacy_branch_id
            WHERE o.id = %s
            FOR UPDATE OF o
            """,
            (str(order_id),),
        )
        order = cur.fetchone()
        if not order:
            raise LookupError("order_not_found")
        if str(order["patient_id"]) != patient_id:
            raise PermissionError("not_order_owner")
        if str(order["active_quote_id"]) != str(quote_id):
            raise ValueError("quote_is_not_active")

        current = OrderStatus(order["status"])
        if current == OrderStatus.QUOTED:
            require_transition(current, OrderStatus.QUOTE_ACCEPTED)
            cur.execute(
                """
                UPDATE pharmacy.pharmacy_quotes
                SET status = 'accepted', accepted_at = COALESCE(accepted_at, NOW())
                WHERE id = %s
                """,
                (str(quote_id),),
            )
            cur.execute(
                """
                UPDATE pharmacy.medication_orders
                SET status = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (OrderStatus.QUOTE_ACCEPTED.value, str(order_id)),
            )
            _event(
                cur,
                order_id,
                "quote_accepted",
                "patient",
                patient_id,
                {"quote_id": str(quote_id)},
            )
            _outbox(
                cur,
                order_id,
                "quote_accepted",
                {"quote_id": str(quote_id), "patient_id": patient_id},
            )
            current = OrderStatus.QUOTE_ACCEPTED
        elif current not in {
            OrderStatus.QUOTE_ACCEPTED,
            OrderStatus.PAYMENT_PENDING,
            OrderStatus.PAID,
        }:
            require_transition(current, OrderStatus.QUOTE_ACCEPTED)

        if simulate_payment and current != OrderStatus.PAID:
            total = Decimal(str(order["total_amount"] or 0))
            if total <= 0:
                raise ValueError("quote_total_must_be_positive")
            if current == OrderStatus.QUOTE_ACCEPTED:
                require_transition(current, OrderStatus.PAYMENT_PENDING)
                cur.execute(
                    "UPDATE pharmacy.medication_orders SET status = %s WHERE id = %s",
                    (OrderStatus.PAYMENT_PENDING.value, str(order_id)),
                )
                current = OrderStatus.PAYMENT_PENDING
            require_transition(current, OrderStatus.PAID)
            cur.execute(
                """
                INSERT INTO pharmacy.pharmacy_payments (
                    order_id, pharmacy_id, provider, provider_payment_id,
                    amount, currency, status, raw_reference, confirmed_at
                ) VALUES (%s, %s, 'simulation', %s, %s, 'ARS', 'approved', %s, NOW())
                ON CONFLICT (pharmacy_id, provider_payment_id) DO NOTHING
                """,
                (
                    str(order_id),
                    str(order["pharmacy_id"]),
                    f"simulation:{order_id}",
                    total,
                    Json({"simulation": True, "no_real_charge": True}),
                ),
            )
            cur.execute(
                """
                UPDATE pharmacy.medication_orders
                SET status = %s, paid_at = COALESCE(paid_at, NOW()), updated_at = NOW()
                WHERE id = %s
                """,
                (OrderStatus.PAID.value, str(order_id)),
            )
            _event(
                cur,
                order_id,
                "payment_simulated",
                "service",
                "test-payment",
                {"amount": str(total), "currency": "ARS", "no_real_charge": True},
            )
            _outbox(
                cur,
                order_id,
                "payment_confirmed",
                {"pharmacy_id": str(order["pharmacy_id"]), "simulated": True},
            )

        cur.execute(
            "SELECT * FROM pharmacy.medication_orders WHERE id = %s",
            (str(order_id),),
        )
        updated = cur.fetchone()
        conn.commit()
        return dict(updated)


def advance_order_status(
    conn,
    order_id: UUID,
    pharmacy_id: UUID,
    target_status: str,
) -> dict:
    """Avanza el pedido por el circuito logistico permitido para la farmacia."""
    target = OrderStatus(target_status)
    if target not in {
        OrderStatus.PREPARING,
        OrderStatus.READY_FOR_DISPATCH,
        OrderStatus.IN_DELIVERY,
        OrderStatus.DELIVERED,
    }:
        raise ValueError("unsupported_pharmacy_order_status")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT o.id, o.status, o.medication_subtotal,
                   b.pharmacy_id, p.commission_rate
            FROM pharmacy.medication_orders o
            JOIN pharmacy.pharmacy_branches b
              ON b.id = o.assigned_pharmacy_branch_id
            JOIN pharmacy.pharmacies p ON p.id = b.pharmacy_id
            WHERE o.id = %s
            FOR UPDATE OF o
            """,
            (str(order_id),),
        )
        order = cur.fetchone()
        if not order:
            raise LookupError("order_not_found")
        if str(order["pharmacy_id"]) != str(pharmacy_id):
            raise PermissionError("order_not_assigned_to_pharmacy")

        current = OrderStatus(order["status"])
        if current == target:
            cur.execute(
                "SELECT * FROM pharmacy.medication_orders WHERE id = %s",
                (str(order_id),),
            )
            return dict(cur.fetchone())
        require_transition(current, target)

        cur.execute(
            """
            UPDATE pharmacy.medication_orders
            SET status = %s,
                delivered_at = CASE WHEN %s = 'delivered' THEN NOW() ELSE delivered_at END,
                updated_at = NOW()
            WHERE id = %s
            RETURNING *
            """,
            (target.value, target.value, str(order_id)),
        )
        updated = cur.fetchone()

        if target == OrderStatus.IN_DELIVERY:
            cur.execute(
                """
                INSERT INTO pharmacy.delivery_tracking (order_id, status)
                VALUES (%s, 'in_delivery')
                ON CONFLICT (order_id) DO UPDATE
                SET status = 'in_delivery', updated_at = NOW()
                """,
                (str(order_id),),
            )
        elif target == OrderStatus.DELIVERED:
            cur.execute(
                """
                UPDATE pharmacy.delivery_tracking
                SET status = 'delivered', updated_at = NOW()
                WHERE order_id = %s
                """,
                (str(order_id),),
            )
            subtotal = Decimal(str(order["medication_subtotal"] or 0))
            rate = Decimal(str(order["commission_rate"] or 0))
            commission = (subtotal * rate).quantize(Decimal("0.01"))
            cur.execute(
                """
                INSERT INTO pharmacy.pharmacy_commission_ledger (
                    pharmacy_id, order_id, medication_subtotal,
                    commission_rate, commission_amount, earned_at
                ) VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (order_id) DO NOTHING
                """,
                (str(pharmacy_id), str(order_id), subtotal, rate, commission),
            )

        _event(
            cur,
            order_id,
            f"order_{target.value}",
            "pharmacy",
            str(pharmacy_id),
        )
        _outbox(
            cur,
            order_id,
            "order_status_changed",
            {"status": target.value, "pharmacy_id": str(pharmacy_id)},
        )
        conn.commit()
        return dict(updated)


def confirm_pharmacy_payment(
    conn, order_id: UUID, pharmacy_id: UUID, payload: PaymentConfirmationInput
) -> dict:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT o.*, b.pharmacy_id
            FROM pharmacy.medication_orders o
            JOIN pharmacy.pharmacy_branches b ON b.id = o.assigned_pharmacy_branch_id
            WHERE o.id = %s FOR UPDATE OF o
            """,
            (str(order_id),),
        )
        order = cur.fetchone()
        if not order:
            raise LookupError("order_not_found")
        if str(order["pharmacy_id"]) != str(pharmacy_id):
            raise PermissionError("order_not_assigned_to_pharmacy")
        if order["status"] == OrderStatus.QUOTE_ACCEPTED.value:
            current = OrderStatus.PAYMENT_PENDING
            cur.execute(
                "UPDATE pharmacy.medication_orders SET status = %s WHERE id = %s",
                (current.value, str(order_id)),
            )
        else:
            current = OrderStatus(order["status"])
        require_transition(current, OrderStatus.PAID)
        if Decimal(str(payload.amount)) != Decimal(str(order["total_amount"])):
            raise ValueError("payment_amount_mismatch")
        cur.execute(
            """
            INSERT INTO pharmacy.pharmacy_payments (
                order_id, pharmacy_id, provider, provider_payment_id,
                amount, currency, status, raw_reference, confirmed_at
            ) VALUES (%s, %s, 'mercadopago', %s, %s, %s, 'approved', %s, NOW())
            ON CONFLICT (pharmacy_id, provider_payment_id) DO NOTHING
            RETURNING id
            """,
            (
                str(order_id), str(pharmacy_id), payload.pharmacy_payment_id,
                payload.amount, payload.currency, Json(payload.raw_reference),
            ),
        )
        if cur.fetchone() is None:
            raise ValueError("payment_already_registered")
        cur.execute(
            """
            UPDATE pharmacy.medication_orders
            SET status = %s, paid_at = NOW(), updated_at = NOW()
            WHERE id = %s RETURNING *
            """,
            (OrderStatus.PAID.value, str(order_id)),
        )
        updated = cur.fetchone()
        _event(cur, order_id, "payment_confirmed", "pharmacy", str(pharmacy_id))
        _outbox(
            cur,
            order_id,
            "payment_confirmed",
            {"pharmacy_id": str(pharmacy_id)},
        )
        conn.commit()
        return dict(updated)
