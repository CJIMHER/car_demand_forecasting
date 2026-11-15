"""Mercado: propios por modelo y tabla por concesionario (provincia del concesionario propio).

- panel_mercado_modelo.propios: anuncios del concesionario propio en ese modelo, zona y tipo. Las
  filas ya calculadas quedan con NULL (el panel muestra «—») hasta el siguiente precálculo:
  tras desplegar, make precalcular.
- panel_mercado_concesionario: una fila por día, tipo y concesionario con anuncios en la provincia
  del concesionario propio. Derivada, como el resto de panel_mercado_*: la rellena
  analitica/mercado.py y el panel solo la lee (cdf_panel: SELECT).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-15
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE = r"""
ALTER TABLE panel_mercado_modelo ADD COLUMN propios integer;

CREATE TABLE panel_mercado_concesionario (
    dia                   date    NOT NULL,
    tipo                  text    NOT NULL CHECK (tipo IN ('VN', 'VO')),
    dealer_id             text    NOT NULL,
    concesionario         text    NOT NULL,   -- nombre publicado por la web (el más reciente)
    es_propio             boolean NOT NULL,
    anuncios              integer NOT NULL,
    anuncios_hace7        integer,            -- NULL si falta la captura completa de hace 7 días
    precio_mediano_cents  bigint,
    n_precio              integer NOT NULL,
    calculado_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dia, tipo, dealer_id)
);
GRANT SELECT ON panel_mercado_concesionario TO cdf_panel;
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS panel_mercado_concesionario;
ALTER TABLE panel_mercado_modelo DROP COLUMN IF EXISTS propios;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
