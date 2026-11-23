"""Campañas clasificadas por reglas sobre el nombre (config/campanas.yaml, ingesta/campanas.py).

- Ámbito «ambos» en lugar de «general» en panel_campana_dia, panel_campana_resultado y
  panel_campana_decision: «general» queda solo para el tipo de campaña (tipo_campana), que ahora es
  independiente del ámbito («VW [VO][2026] Black Friday» es general de ocasión).
- panel_campana_dia.sin_mercado: campaña de un modelo que no está en el catálogo de turismos que
  capturamos (config/versiones.yaml), como Caravelle. La regla e y la comparación con la competencia
  dicen que no se pueden aplicar.
- panel_campanas_sin_regla.motivo: por qué una campaña no casa con ninguna regla.

Las filas reales de panel_campana_dia las escribe ingesta/campanas.py (tras cada descarga de Meta y
con make campanas): es derivada de campanas_meta_dia.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-23
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TABLAS = ("panel_campana_dia", "panel_campana_resultado", "panel_campana_decision")


def _ambito(de: str, a: str) -> None:
    for t in TABLAS:
        op.execute(f"ALTER TABLE {t} DROP CONSTRAINT {t}_ambito_check")
    op.execute("ALTER TABLE panel_campana_resultado DROP CONSTRAINT panel_campana_resultado_tipo")
    for t in TABLAS:
        op.execute(f"UPDATE {t} SET ambito = '{a}' WHERE ambito = '{de}'")
        op.execute(f"ALTER TABLE {t} ADD CONSTRAINT {t}_ambito_check CHECK (ambito IN ('VN', 'VO', '{a}'))")
    op.execute(f"ALTER TABLE panel_campana_resultado ADD CONSTRAINT panel_campana_resultado_tipo "
               f"CHECK (ambito = '{a}' OR tipo = 'todo')")


def upgrade() -> None:
    _ambito("general", "ambos")
    op.execute("""ALTER TABLE panel_campana_dia
        ADD COLUMN sin_mercado boolean NOT NULL DEFAULT false,
        ADD CONSTRAINT panel_campana_dia_sin_mercado CHECK (NOT sin_mercado OR tipo_campana = 'modelo')""")
    op.execute("ALTER TABLE panel_campanas_sin_regla ADD COLUMN motivo text")


def downgrade() -> None:
    op.execute("ALTER TABLE panel_campanas_sin_regla DROP COLUMN motivo")
    op.execute("ALTER TABLE panel_campana_dia DROP CONSTRAINT panel_campana_dia_sin_mercado, DROP COLUMN sin_mercado")
    _ambito("ambos", "general")
