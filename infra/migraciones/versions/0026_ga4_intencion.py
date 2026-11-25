"""Señales semanales de intención de compra de GA4 (ingesta/ga4_series.py).

- ga4_intencion_carga: una fila por carga (run_id) y semana (lunes ISO) cargada, con la huella de su contenido y
  la de config/ga4_paginas.yaml. Una semana cargada sin filas también queda aquí (filas = 0).
- ga4_intencion_semana: por semana, tipo (VN, VO o sin_tipo, por la ruta), canal (organico, directo, pagado,
  otros) y marca (propia o sin_marca; las de otras marcas del grupo no se guardan): sesiones, sesiones con
  interacción y tiempo de interacción (por la página de destino), vistas de fichas y eventos de intención (por la
  página del evento). Solo agregados.
- ga4_fichas_modelo_semana: vistas de fichas por semana, tipo y modelo (sacado de la ruta).

Las tres son de solo añadir. Recargar una semana la sustituye sin borrar nada: las vistas *_vigente dan, para
cada semana, solo las filas de su última carga.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-25
"""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _solo_anadir(tabla: str) -> str:
    return (f"CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON {tabla} FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();\n"
            f"CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON {tabla} FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();\n")


CUENTAS = ", ".join(f"{c} bigint NOT NULL DEFAULT 0 CHECK ({c} >= 0)" for c in (
    "vistas_fichas", "configurar", "color", "opcionales", "precio", "form_start", "form_submit",
    "solicitar_informacion", "click_llamada_email", "lead_nuevos", "lead_segunda_mano"))

UPGRADE = f"""
CREATE TABLE ga4_intencion_carga (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           text        NOT NULL,
    cargado_at       timestamptz NOT NULL,
    origen           text        NOT NULL,              -- API y versión
    propiedad_id     text        NOT NULL,
    semana           date        NOT NULL CHECK (extract(isodow FROM semana) = 1),
    huella           text        NOT NULL,              -- del contenido de la semana
    filas            integer     NOT NULL CHECK (filas >= 0),
    version_paginas  text        NOT NULL,              -- huella de config/ga4_paginas.yaml
    UNIQUE (run_id, semana)
);
CREATE INDEX ga4_intencion_carga_semana ON ga4_intencion_carga (semana, cargado_at DESC, id DESC);
{_solo_anadir("ga4_intencion_carga")}
CREATE TABLE ga4_intencion_semana (
    id                    bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                text        NOT NULL,
    semana                date        NOT NULL CHECK (extract(isodow FROM semana) = 1),
    tipo                  text        NOT NULL CHECK (tipo IN ('VN', 'VO', 'sin_tipo')),
    canal                 text        NOT NULL CHECK (canal IN ('organico', 'directo', 'pagado', 'otros')),
    marca                 text        NOT NULL CHECK (marca IN ('propia', 'sin_marca')),
    sesiones              bigint      NOT NULL DEFAULT 0 CHECK (sesiones >= 0),
    sesiones_interaccion  bigint      NOT NULL DEFAULT 0 CHECK (sesiones_interaccion >= 0),
    tiempo_interaccion    double precision NOT NULL DEFAULT 0 CHECK (tiempo_interaccion >= 0),   -- segundos
    {CUENTAS},
    FOREIGN KEY (run_id, semana) REFERENCES ga4_intencion_carga (run_id, semana),
    UNIQUE (run_id, semana, tipo, canal, marca)
);
{_solo_anadir("ga4_intencion_semana")}
CREATE TABLE ga4_fichas_modelo_semana (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           text        NOT NULL,
    semana           date        NOT NULL CHECK (extract(isodow FROM semana) = 1),
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    modelo           text        NOT NULL,
    vistas           bigint      NOT NULL CHECK (vistas >= 0),
    FOREIGN KEY (run_id, semana) REFERENCES ga4_intencion_carga (run_id, semana),
    UNIQUE (run_id, semana, tipo, modelo)
);
{_solo_anadir("ga4_fichas_modelo_semana")}
-- Para cada semana, su última carga
CREATE VIEW ga4_intencion_carga_vigente AS
    SELECT DISTINCT ON (semana) * FROM ga4_intencion_carga ORDER BY semana, cargado_at DESC, id DESC;
CREATE VIEW ga4_intencion_semana_vigente AS
    SELECT s.* FROM ga4_intencion_semana s
    JOIN ga4_intencion_carga_vigente c ON c.semana = s.semana AND c.run_id = s.run_id;
CREATE VIEW ga4_fichas_modelo_semana_vigente AS
    SELECT f.* FROM ga4_fichas_modelo_semana f
    JOIN ga4_intencion_carga_vigente c ON c.semana = f.semana AND c.run_id = f.run_id;
"""

DOWNGRADE = """
DROP VIEW IF EXISTS ga4_fichas_modelo_semana_vigente;
DROP VIEW IF EXISTS ga4_intencion_semana_vigente;
DROP VIEW IF EXISTS ga4_intencion_carga_vigente;
DROP TABLE IF EXISTS ga4_fichas_modelo_semana;
DROP TABLE IF EXISTS ga4_intencion_semana;
DROP TABLE IF EXISTS ga4_intencion_carga;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
