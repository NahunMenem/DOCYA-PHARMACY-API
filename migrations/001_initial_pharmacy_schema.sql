CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE SCHEMA IF NOT EXISTS pharmacy;

CREATE TABLE pharmacy.pharmacies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    legal_name TEXT NOT NULL,
    trade_name TEXT NOT NULL,
    cuit VARCHAR(11) NOT NULL UNIQUE CHECK (cuit ~ '^[0-9]{11}$'),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'suspended', 'blocked', 'rejected')),
    regulatory_registry TEXT,
    regulatory_verified_at TIMESTAMPTZ,
    commission_rate NUMERIC(5,4) NOT NULL DEFAULT 0.1000
        CHECK (commission_rate >= 0 AND commission_rate <= 1),
    billing_status VARCHAR(20) NOT NULL DEFAULT 'current'
        CHECK (billing_status IN ('current', 'due', 'overdue', 'blocked')),
    blocked_at TIMESTAMPTZ,
    blocked_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE pharmacy.pharmacy_branches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pharmacy_id UUID NOT NULL REFERENCES pharmacy.pharmacies(id),
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    locality TEXT,
    province TEXT NOT NULL DEFAULT 'Ciudad Autónoma de Buenos Aires',
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    service_radius_km NUMERIC(6,2) NOT NULL DEFAULT 5 CHECK (service_radius_km > 0),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'inactive', 'rejected')),
    is_open BOOLEAN NOT NULL DEFAULT FALSE,
    is_online BOOLEAN NOT NULL DEFAULT FALSE,
    accepts_new_orders BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX pharmacy_branches_routing_idx
    ON pharmacy.pharmacy_branches (status, is_open, is_online, accepts_new_orders);

CREATE TABLE pharmacy.pharmacy_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pharmacy_id UUID NOT NULL REFERENCES pharmacy.pharmacies(id),
    core_user_id TEXT,
    email CITEXT NOT NULL,
    password_hash TEXT,
    role VARCHAR(20) NOT NULL DEFAULT 'operator'
        CHECK (role IN ('owner', 'pharmacist', 'operator', 'billing')),
    status VARCHAR(20) NOT NULL DEFAULT 'invited'
        CHECK (status IN ('invited', 'active', 'disabled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (email)
);

CREATE TABLE pharmacy.pharmacy_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pharmacy_id UUID NOT NULL REFERENCES pharmacy.pharmacies(id),
    document_type VARCHAR(50) NOT NULL,
    object_key TEXT NOT NULL,
    sha256 CHAR(64),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    expires_at TIMESTAMPTZ,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE pharmacy.medication_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'searching_pharmacy'
        CHECK (status IN (
            'searching_pharmacy', 'assigned', 'quoted', 'quote_accepted',
            'payment_pending', 'paid', 'preparing', 'ready_for_dispatch',
            'in_delivery', 'delivered', 'cancelled', 'rejected', 'expired'
        )),
    assigned_pharmacy_branch_id UUID REFERENCES pharmacy.pharmacy_branches(id),
    delivery_address TEXT NOT NULL,
    delivery_notes TEXT,
    delivery_latitude DOUBLE PRECISION NOT NULL CHECK (delivery_latitude BETWEEN -90 AND 90),
    delivery_longitude DOUBLE PRECISION NOT NULL CHECK (delivery_longitude BETWEEN -180 AND 180),
    medication_subtotal NUMERIC(12,2),
    delivery_fee NUMERIC(12,2),
    total_amount NUMERIC(12,2),
    paid_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX medication_orders_patient_idx
    ON pharmacy.medication_orders (patient_id, created_at DESC);
CREATE INDEX medication_orders_status_idx
    ON pharmacy.medication_orders (status, created_at);

CREATE TABLE pharmacy.order_prescriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES pharmacy.medication_orders(id) ON DELETE CASCADE,
    source VARCHAR(20) NOT NULL CHECK (source IN ('docya', 'uploaded')),
    external_prescription_id TEXT,
    object_key TEXT,
    sha256 CHAR(64),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (source = 'docya' AND external_prescription_id IS NOT NULL)
        OR (source = 'uploaded' AND object_key IS NOT NULL)
    )
);

CREATE TABLE pharmacy.order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES pharmacy.medication_orders(id) ON DELETE CASCADE,
    requested_name TEXT NOT NULL,
    requested_quantity INTEGER NOT NULL CHECK (requested_quantity > 0),
    prescription_line_ref TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE pharmacy.order_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES pharmacy.medication_orders(id) ON DELETE CASCADE,
    pharmacy_branch_id UUID NOT NULL REFERENCES pharmacy.pharmacy_branches(id),
    status VARCHAR(20) NOT NULL DEFAULT 'offered'
        CHECK (status IN ('offered', 'accepted', 'rejected', 'expired', 'cancelled')),
    distance_km NUMERIC(8,3) NOT NULL,
    offered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    responded_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE (order_id, pharmacy_branch_id)
);

CREATE INDEX order_assignments_active_idx
    ON pharmacy.order_assignments (status, expires_at);

CREATE TABLE pharmacy.pharmacy_quotes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES pharmacy.medication_orders(id) ON DELETE CASCADE,
    pharmacy_id UUID NOT NULL REFERENCES pharmacy.pharmacies(id),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'accepted', 'rejected', 'expired', 'cancelled')),
    medication_subtotal NUMERIC(12,2) NOT NULL CHECK (medication_subtotal >= 0),
    delivery_fee NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (delivery_fee >= 0),
    total_amount NUMERIC(12,2) NOT NULL CHECK (total_amount >= 0),
    notes TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE pharmacy.medication_orders
    ADD COLUMN active_quote_id UUID REFERENCES pharmacy.pharmacy_quotes(id);

CREATE TABLE pharmacy.quote_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id UUID NOT NULL REFERENCES pharmacy.pharmacy_quotes(id) ON DELETE CASCADE,
    order_item_id UUID NOT NULL REFERENCES pharmacy.order_items(id),
    offered_name TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    available BOOLEAN NOT NULL DEFAULT TRUE,
    substitution_requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (quote_id, order_item_id)
);

CREATE TABLE pharmacy.pharmacy_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES pharmacy.medication_orders(id),
    pharmacy_id UUID NOT NULL REFERENCES pharmacy.pharmacies(id),
    provider VARCHAR(30) NOT NULL DEFAULT 'mercadopago',
    provider_payment_id TEXT NOT NULL,
    amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    currency CHAR(3) NOT NULL DEFAULT 'ARS',
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('pending', 'approved', 'rejected', 'refunded', 'cancelled')),
    raw_reference JSONB NOT NULL DEFAULT '{}'::jsonb,
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pharmacy_id, provider_payment_id)
);

CREATE TABLE pharmacy.delivery_tracking (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL UNIQUE REFERENCES pharmacy.medication_orders(id),
    provider VARCHAR(30) NOT NULL DEFAULT 'pharmacy',
    external_delivery_id TEXT,
    courier_name TEXT,
    courier_phone TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'assigned', 'picked_up', 'in_delivery', 'delivered', 'failed')),
    public_tracking_token_hash TEXT,
    last_latitude DOUBLE PRECISION,
    last_longitude DOUBLE PRECISION,
    last_location_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE pharmacy.order_events (
    id BIGSERIAL PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES pharmacy.medication_orders(id) ON DELETE CASCADE,
    event_type VARCHAR(80) NOT NULL,
    actor_type VARCHAR(30) NOT NULL,
    actor_id TEXT,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX order_events_order_idx
    ON pharmacy.order_events (order_id, created_at);

CREATE TABLE pharmacy.pharmacy_commission_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pharmacy_id UUID NOT NULL REFERENCES pharmacy.pharmacies(id),
    order_id UUID NOT NULL UNIQUE REFERENCES pharmacy.medication_orders(id),
    medication_subtotal NUMERIC(12,2) NOT NULL CHECK (medication_subtotal >= 0),
    commission_rate NUMERIC(5,4) NOT NULL DEFAULT 0.1000,
    commission_amount NUMERIC(12,2) NOT NULL CHECK (commission_amount >= 0),
    status VARCHAR(20) NOT NULL DEFAULT 'unbilled'
        CHECK (status IN ('unbilled', 'invoiced', 'paid', 'void')),
    earned_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE pharmacy.pharmacy_monthly_closures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pharmacy_id UUID NOT NULL REFERENCES pharmacy.pharmacies(id),
    period DATE NOT NULL CHECK (EXTRACT(DAY FROM period) = 1),
    delivered_orders INTEGER NOT NULL DEFAULT 0,
    gross_medication_sales NUMERIC(14,2) NOT NULL DEFAULT 0,
    commission_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'issued', 'paid', 'overdue', 'void')),
    due_at TIMESTAMPTZ,
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pharmacy_id, period)
);

CREATE TABLE pharmacy.pharmacy_commission_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    closure_id UUID NOT NULL REFERENCES pharmacy.pharmacy_monthly_closures(id),
    provider_payment_id TEXT,
    amount NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    status VARCHAR(20) NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'refunded')),
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE pharmacy.outbox_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type VARCHAR(50) NOT NULL,
    aggregate_id UUID NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    payload JSONB NOT NULL,
    published_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX outbox_pending_idx
    ON pharmacy.outbox_events (next_attempt_at)
    WHERE published_at IS NULL;

COMMENT ON COLUMN pharmacy.pharmacies.commission_rate IS
    'DocYa invoices this rate monthly over delivered medication subtotal; delivery fee is excluded.';
COMMENT ON COLUMN pharmacy.pharmacy_payments.provider_payment_id IS
    'Payment belongs to the pharmacy Mercado Pago account, never to DocYa.';
