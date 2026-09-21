-- Tabla de métricas de emails procesados por Gmail Inbox Bot
-- Ejecutar en Supabase SQL Editor

CREATE TABLE IF NOT EXISTS email_metrics (
    id              BIGSERIAL PRIMARY KEY,
    mailbox         TEXT NOT NULL,
    category        TEXT NOT NULL,
    action          TEXT,
    msg_id          TEXT UNIQUE,
    model           TEXT,
    draft_mode      BOOLEAN DEFAULT FALSE,
    classification_reason TEXT,
    error           BOOLEAN DEFAULT FALSE,
    sender          TEXT,
    subject         TEXT,
    received_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    -- Métricas de uso/coste del LLM de clasificación (escritas por metrics.record_email)
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    total_tokens    INTEGER,
    input_cost_usd  DOUBLE PRECISION,
    output_cost_usd DOUBLE PRECISION,
    total_cost_usd  DOUBLE PRECISION,
    llm_provider    TEXT
);

-- Migración idempotente: añade las columnas de uso/coste a tablas ya existentes.
-- (El código de metrics.py escribe estas columnas; sin ellas el upsert REST da 400.)
ALTER TABLE email_metrics
    ADD COLUMN IF NOT EXISTS input_tokens    INTEGER,
    ADD COLUMN IF NOT EXISTS output_tokens   INTEGER,
    ADD COLUMN IF NOT EXISTS total_tokens    INTEGER,
    ADD COLUMN IF NOT EXISTS input_cost_usd  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS output_cost_usd DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS total_cost_usd  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS llm_provider    TEXT;

-- Migración idempotente: clasificación sombra con Jev (TypeSafe.ai).
-- Ejecutar ANTES de desplegar el código que las escribe; si no, PostgREST devuelve 400
-- y se pierde la fila entera de métricas de ese email.
ALTER TABLE email_metrics
    ADD COLUMN IF NOT EXISTS jev_category      TEXT,
    ADD COLUMN IF NOT EXISTS jev_confidence    DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS jev_probabilities JSONB,
    ADD COLUMN IF NOT EXISTS jev_latency_ms    INTEGER,
    ADD COLUMN IF NOT EXISTS jev_model         TEXT,
    ADD COLUMN IF NOT EXISTS jev_error         TEXT;

-- Índices para consultas frecuentes del dashboard
CREATE INDEX IF NOT EXISTS idx_email_metrics_created_at ON email_metrics (created_at);
CREATE INDEX IF NOT EXISTS idx_email_metrics_mailbox ON email_metrics (mailbox);
CREATE INDEX IF NOT EXISTS idx_email_metrics_category ON email_metrics (category);
CREATE INDEX IF NOT EXISTS idx_email_metrics_msg_id ON email_metrics (msg_id);
CREATE INDEX IF NOT EXISTS idx_email_metrics_jev_category ON email_metrics (jev_category);

-- Habilitar RLS (Row Level Security) — acceso solo con service_role key
ALTER TABLE email_metrics ENABLE ROW LEVEL SECURITY;

-- Política: permitir todo con service_role (la API key secreta)
-- No se crean políticas para anon — solo el bot con service_role puede leer/escribir
