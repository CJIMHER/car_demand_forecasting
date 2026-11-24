"""Campañas de Meta de otras marcas del grupo, fuera de Marketing y de los criterios.

- panel_campana_otra_marca (contrato del panel, bloque de campañas de Marketing): por día y campaña de
  Meta de otra marca (propio.prefijos_excluidos y propio.palabras_excluidas de config/concesionario.yaml),
  su gasto, impresiones y clics. Esas campañas ya no van a panel_campana_dia (ni a las tarjetas, ni al
  bloque de campañas, ni a los criterios): solo se cuentan, en la línea «Campañas de otras marcas en esta
  cuenta». La rehace ingesta/campanas.py.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-24
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE panel_campana_otra_marca (
    dia           date        NOT NULL,
    origen        text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at  timestamptz NOT NULL DEFAULT now(),
    campana_id    text        NOT NULL,
    titulo        text        NOT NULL,
    gasto_cents   bigint      NOT NULL CHECK (gasto_cents >= 0),
    impresiones   bigint      NOT NULL CHECK (impresiones >= 0),
    clics         bigint      NOT NULL CHECK (clics >= 0),
    PRIMARY KEY (origen, dia, campana_id)
);
GRANT SELECT ON panel_campana_otra_marca TO cdf_panel;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS panel_campana_otra_marca;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
