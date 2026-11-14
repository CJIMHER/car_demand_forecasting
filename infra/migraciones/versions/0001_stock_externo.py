"""Stock externo de los scrapers de Volkswagen y estado de las fuentes (hito 0).

Revision ID: 0001
Revises:
Create Date: 2026-11-14
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE = r"""
-- Una fila por pasada success de cada scraper que ya se ha copiado a Postgres.
-- Es el registro que hace idempotente la carga: un run_id solo se carga una vez.
CREATE TABLE scraper_runs (
    fuente          text        NOT NULL CHECK (fuente IN ('vo', 'vn', 'vn_otros')),
    run_id          text        NOT NULL,
    tipo_stock      text        NOT NULL CHECK (tipo_stock IN ('VN', 'VO')),
    snapshot_day    date        NOT NULL,
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz,
    expected_count  integer,
    observed_count  integer     NOT NULL,
    scope           text        NOT NULL,
    cargado_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fuente, run_id),
    UNIQUE (fuente, snapshot_day)
);

-- Un anuncio observado en una captura completa. Solo se añaden filas.
CREATE TABLE stock_externo_snapshot (
    id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    captured_at            timestamptz NOT NULL,
    snapshot_day           date        NOT NULL,
    run_id                 text        NOT NULL,
    fuente                 text        NOT NULL,
    tipo_stock             text        NOT NULL CHECK (tipo_stock IN ('VN', 'VO')),
    id_anuncio             text        NOT NULL,
    bastidor               text,
    dealer_id              text,
    concesionario          text,
    ciudad                 text,
    codigo_postal          text,
    provincia_codigo       text,
    provincia              text,
    es_propio              boolean     NOT NULL DEFAULT false,
    modelo                 text,
    version                text,
    combustible            text,
    anio                   smallint,
    fecha_matriculacion    date,
    km                     integer,
    precio_cents           bigint,
    etiqueta_precio        text,
    estado_entrega_codigo  text,
    estado_entrega         text,
    datos                  jsonb       NOT NULL,
    FOREIGN KEY (fuente, run_id) REFERENCES scraper_runs (fuente, run_id),
    UNIQUE (fuente, run_id, id_anuncio)
);
CREATE INDEX stock_externo_snapshot_dia ON stock_externo_snapshot (snapshot_day, tipo_stock);
CREATE INDEX stock_externo_snapshot_anuncio ON stock_externo_snapshot (fuente, id_anuncio, captured_at);
CREATE INDEX stock_externo_snapshot_provincia ON stock_externo_snapshot (provincia_codigo, snapshot_day);
CREATE INDEX stock_externo_snapshot_dealer ON stock_externo_snapshot (dealer_id, snapshot_day);

-- Eventos tal como los registra cada scraper (new, changed, not_seen, removed, returned).
CREATE TABLE eventos_externos (
    fuente          text        NOT NULL,
    event_id        bigint      NOT NULL,
    run_id          text        NOT NULL,
    tipo_stock      text        NOT NULL CHECK (tipo_stock IN ('VN', 'VO')),
    id_anuncio      text        NOT NULL,
    detected_at     timestamptz NOT NULL,
    tipo            text        NOT NULL,
    campo           text,
    valor_anterior  jsonb,
    valor_nuevo     jsonb,
    PRIMARY KEY (fuente, event_id),
    FOREIGN KEY (fuente, run_id) REFERENCES scraper_runs (fuente, run_id)
);
CREATE INDEX eventos_externos_anuncio ON eventos_externos (fuente, id_anuncio, event_id);
CREATE INDEX eventos_externos_dia ON eventos_externos (detected_at, tipo);

-- Estado actual de cada anuncio, derivado de snapshots y eventos tras cada carga.
-- Desaparecer no es vender: no existe el estado «vendido».
CREATE TABLE anuncios_externos (
    fuente          text        NOT NULL,
    id_anuncio      text        NOT NULL,
    tipo_stock      text        NOT NULL CHECK (tipo_stock IN ('VN', 'VO')),
    first_seen      timestamptz NOT NULL,
    last_seen       timestamptz NOT NULL,
    ausente_desde   timestamptz,
    estado          text        NOT NULL CHECK (estado IN ('presente', 'ausente', 'retirado')),
    reapariciones   integer     NOT NULL DEFAULT 0,
    actualizado_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fuente, id_anuncio),
    CHECK ((estado = 'presente') = (ausente_desde IS NULL))
);
CREATE INDEX anuncios_externos_estado ON anuncios_externos (tipo_stock, estado);

-- Registro de cargas, comprobaciones y copias por fuente. Solo se añaden filas.
CREATE TABLE fuentes_estado (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fuente         text        NOT NULL,
    tipo           text        NOT NULL CHECK (tipo IN ('carga', 'comprobacion', 'copia')),
    dia            date        NOT NULL,
    estado         text        NOT NULL CHECK (estado IN ('ok', 'sin_captura', 'sin_cargar', 'error')),
    detalle        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    registrado_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX fuentes_estado_fuente ON fuentes_estado (fuente, tipo, registrado_at DESC);

-- Nada se sobrescribe: las tablas de hechos no admiten UPDATE, DELETE ni TRUNCATE.
-- Única excepción: es_propio de stock_externo_snapshot, que depende de config/concesionario.yaml.
CREATE FUNCTION cdf_solo_anadir() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'stock_externo_snapshot'
       AND (to_jsonb(NEW) - 'es_propio') = (to_jsonb(OLD) - 'es_propio') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'La tabla % solo admite añadir filas (% rechazado)', TG_TABLE_NAME, TG_OP;
END $$;

CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON scraper_runs
    FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON stock_externo_snapshot
    FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON eventos_externos
    FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON fuentes_estado
    FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON scraper_runs
    FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON stock_externo_snapshot
    FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON eventos_externos
    FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON fuentes_estado
    FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();
"""

DOWNGRADE = """
DROP TABLE IF EXISTS fuentes_estado;
DROP TABLE IF EXISTS anuncios_externos;
DROP TABLE IF EXISTS eventos_externos;
DROP TABLE IF EXISTS stock_externo_snapshot;
DROP TABLE IF EXISTS scraper_runs;
DROP FUNCTION IF EXISTS cdf_solo_anadir();
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
