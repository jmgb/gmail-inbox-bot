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
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Índices para consultas frecuentes del dashboard
CREATE INDEX IF NOT EXISTS idx_email_metrics_created_at ON email_metrics (created_at);
CREATE INDEX IF NOT EXISTS idx_email_metrics_mailbox ON email_metrics (mailbox);
CREATE INDEX IF NOT EXISTS idx_email_metrics_category ON email_metrics (category);
CREATE INDEX IF NOT EXISTS idx_email_metrics_msg_id ON email_metrics (msg_id);

-- Habilitar RLS (Row Level Security) — acceso solo con service_role key
ALTER TABLE email_metrics ENABLE ROW LEVEL SECURITY;

-- Política: permitir todo con service_role (la API key secreta)
-- No se crean políticas para anon — solo el bot con service_role puede leer/escribir
