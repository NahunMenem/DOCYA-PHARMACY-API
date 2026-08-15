from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import bcrypt
import jwt
from psycopg2.extras import RealDictCursor

from app.config import get_settings
from app.schemas import PharmacyRegistrationInput


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _token(user: dict) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user["id"]),
            "role": "pharmacy",
            "pharmacy_id": str(user["pharmacy_id"]),
            "iat": now,
            "exp": now + timedelta(minutes=settings.pharmacy_token_expire_minutes),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def register_pharmacy(conn, payload: PharmacyRegistrationInput) -> dict:
    pharmacy_id = uuid4()
    branch_id = uuid4()
    user_id = uuid4()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT 1 FROM pharmacy.pharmacies WHERE cuit = %s",
            (payload.cuit,),
        )
        if cur.fetchone():
            raise ValueError("cuit_already_registered")
        cur.execute(
            "SELECT 1 FROM pharmacy.pharmacy_users WHERE lower(email) = lower(%s)",
            (payload.owner_email,),
        )
        if cur.fetchone():
            raise ValueError("email_already_registered")
        cur.execute(
            """
            INSERT INTO pharmacy.pharmacies (
                id, legal_name, trade_name, cuit, status, regulatory_registry
            ) VALUES (%s, %s, %s, %s, 'pending', %s)
            """,
            (
                str(pharmacy_id), payload.legal_name, payload.trade_name,
                payload.cuit, payload.regulatory_registry,
            ),
        )
        cur.execute(
            """
            INSERT INTO pharmacy.pharmacy_branches (
                id, pharmacy_id, name, address, locality, province,
                latitude, longitude, service_radius_km, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
            """,
            (
                str(branch_id), str(pharmacy_id), payload.branch_name,
                payload.address, payload.locality, payload.province,
                payload.latitude, payload.longitude, payload.service_radius_km,
            ),
        )
        password_hash = _hash_password(payload.password)
        cur.execute(
            """
            INSERT INTO pharmacy.pharmacy_users (
                id, pharmacy_id, email, password_hash, role, status
            ) VALUES (%s, %s, lower(%s), %s, 'owner', 'active')
            RETURNING id, pharmacy_id, email, role
            """,
            (str(user_id), str(pharmacy_id), payload.owner_email, password_hash),
        )
        user = cur.fetchone()
        conn.commit()
        return {
            "access_token": _token(user),
            "token_type": "bearer",
            "pharmacy_id": pharmacy_id,
            "status": "pending",
        }


def login_pharmacy(conn, email: str, password: str) -> dict | None:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT u.id, u.pharmacy_id, u.email, u.password_hash, u.role,
                   u.status AS user_status, p.status AS pharmacy_status,
                   p.trade_name, p.billing_status
            FROM pharmacy.pharmacy_users u
            JOIN pharmacy.pharmacies p ON p.id = u.pharmacy_id
            WHERE lower(u.email) = lower(%s)
            LIMIT 1
            """,
            (email,),
        )
        user = cur.fetchone()
    if not user or user["user_status"] != "active" or not user["password_hash"]:
        return None
    if not _verify_password(password, user["password_hash"]):
        return None
    return {
        "access_token": _token(user),
        "token_type": "bearer",
        "pharmacy_id": user["pharmacy_id"],
        "pharmacy_status": user["pharmacy_status"],
        "billing_status": user["billing_status"],
        "trade_name": user["trade_name"],
    }


def pharmacy_profile(conn, pharmacy_id: UUID) -> dict | None:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT p.id, p.legal_name, p.trade_name, p.cuit, p.status,
                   p.regulatory_registry, p.regulatory_verified_at,
                   p.commission_rate, p.billing_status, p.blocked_reason,
                   COALESCE(jsonb_agg(jsonb_build_object(
                       'id', b.id, 'name', b.name, 'address', b.address,
                       'locality', b.locality, 'province', b.province,
                       'status', b.status, 'is_open', b.is_open,
                       'is_online', b.is_online,
                       'accepts_new_orders', b.accepts_new_orders
                   ) ORDER BY b.created_at) FILTER (WHERE b.id IS NOT NULL), '[]'::jsonb) AS branches
            FROM pharmacy.pharmacies p
            LEFT JOIN pharmacy.pharmacy_branches b ON b.pharmacy_id = p.id
            WHERE p.id = %s
            GROUP BY p.id
            """,
            (str(pharmacy_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def activate_pharmacy(conn, pharmacy_id: UUID, registry: str | None = None) -> dict:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE pharmacy.pharmacies
            SET status = 'active', billing_status = 'current',
                regulatory_registry = COALESCE(%s, regulatory_registry),
                regulatory_verified_at = NOW(), updated_at = NOW()
            WHERE id = %s AND status IN ('pending', 'suspended')
            RETURNING id, trade_name, status, regulatory_verified_at
            """,
            (registry, str(pharmacy_id)),
        )
        pharmacy = cur.fetchone()
        if not pharmacy:
            raise LookupError("pharmacy_not_found_or_not_activatable")
        cur.execute(
            """
            UPDATE pharmacy.pharmacy_branches
            SET status = 'active', accepts_new_orders = TRUE, updated_at = NOW()
            WHERE pharmacy_id = %s AND status = 'pending'
            """,
            (str(pharmacy_id),),
        )
        conn.commit()
        return dict(pharmacy)

