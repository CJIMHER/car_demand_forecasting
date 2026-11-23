"""Tráfico de la web por canal (GA4) y campañas de Google Ads.

- ga4_trafico_dia: campana_id (sessionCampaignId) y canal_ga4 (sessionDefaultChannelGroup, la
  agrupación de canales por defecto de GA4, respaldo de las reglas de config/canales.yaml). No son
  parte de la clave de la fila: GA4 da uno por fuente, medio y campaña. Las filas anteriores quedan
  con NULL hasta la siguiente recarga de esos días.
- panel_trafico_canal_dia (contrato del panel, bloque «Tráfico de la web por canal» de Marketing):
  sesiones, sesiones con interacción y eventos clave por día, canal y tipo de stock de la página de
  destino (VN, VO o sin_tipo).
- panel_trafico_otros: fuente y medio de GA4 que caen en «otros», para completar config/canales.yaml
  («Estado de las fuentes»).
- panel_campanas_google_ads: campañas de Google Ads con su nombre, sin reglas.

Las tres las rehace ingesta/campanas.py (tras cada descarga de GA4 y con make campanas).

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-23
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE ga4_trafico_dia ADD COLUMN campana_id text, ADD COLUMN canal_ga4 text;
-- La vista fija sus columnas al crearse: se rehace para que lleve las nuevas
CREATE OR REPLACE VIEW ga4_trafico_dia_vigente AS
    SELECT DISTINCT ON (propiedad_id, dia, fuente, medio, campana, pagina_destino) *
    FROM ga4_trafico_dia ORDER BY propiedad_id, dia, fuente, medio, campana, pagina_destino, id DESC;

CREATE TABLE panel_trafico_canal_dia (
    dia                   date        NOT NULL,
    origen                text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at          timestamptz NOT NULL DEFAULT now(),
    canal                 text        NOT NULL,
    tipo                  text        NOT NULL CHECK (tipo IN ('VN', 'VO', 'sin_tipo')),
    sesiones              bigint      NOT NULL CHECK (sesiones >= 0),
    sesiones_interaccion  bigint      NOT NULL CHECK (sesiones_interaccion >= 0
                                                      AND sesiones_interaccion <= sesiones),
    eventos_clave         numeric     NOT NULL CHECK (eventos_clave >= 0),
    PRIMARY KEY (origen, dia, canal, tipo)
);

CREATE TABLE panel_trafico_otros (
    fuente       text        NOT NULL,
    medio        text        NOT NULL,
    canal_ga4    text,
    primer_dia   date        NOT NULL,
    ultimo_dia   date        NOT NULL,
    sesiones     bigint      NOT NULL CHECK (sesiones >= 0),
    calculado_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (fuente, medio, canal_ga4)
);

CREATE TABLE panel_campanas_google_ads (
    campana               text        NOT NULL,
    campana_id            text,
    primer_dia            date        NOT NULL,
    ultimo_dia            date        NOT NULL,
    sesiones              bigint      NOT NULL CHECK (sesiones >= 0),
    sesiones_interaccion  bigint      NOT NULL CHECK (sesiones_interaccion >= 0),
    eventos_clave         numeric     NOT NULL CHECK (eventos_clave >= 0),
    calculado_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (campana, campana_id)
);

GRANT SELECT ON panel_trafico_canal_dia, panel_trafico_otros, panel_campanas_google_ads TO cdf_panel;
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE IF EXISTS panel_campanas_google_ads;
DROP TABLE IF EXISTS panel_trafico_otros;
DROP TABLE IF EXISTS panel_trafico_canal_dia;
DROP VIEW ga4_trafico_dia_vigente;
ALTER TABLE ga4_trafico_dia DROP COLUMN canal_ga4, DROP COLUMN campana_id;
CREATE VIEW ga4_trafico_dia_vigente AS
    SELECT DISTINCT ON (propiedad_id, dia, fuente, medio, campana, pagina_destino) *
    FROM ga4_trafico_dia ORDER BY propiedad_id, dia, fuente, medio, campana, pagina_destino, id DESC;
""")
