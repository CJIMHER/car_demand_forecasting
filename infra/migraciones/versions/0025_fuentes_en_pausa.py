"""fuentes_estado admite el estado «en_pausa»: una fuente de fuentes_en_pausa (config/concesionario.yaml) no se
carga ni se comprueba y se anota así, con desde y motivo en el detalle.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-25
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_estado_check;
ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_estado_check
    CHECK (estado IN ('ok', 'sin_captura', 'sin_cargar', 'error', 'sin_credenciales', 'sin_configurar', 'en_pausa'));
"""

# fuentes_estado es de solo añadir: si ya tiene filas «en_pausa», la restricción anterior no se puede
# volver a poner y la vuelta atrás falla (nada se borra).
DOWNGRADE = """
ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_estado_check;
ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_estado_check
    CHECK (estado IN ('ok', 'sin_captura', 'sin_cargar', 'error', 'sin_credenciales', 'sin_configurar'));
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
