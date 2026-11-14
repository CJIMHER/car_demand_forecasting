"""fuentes_estado admite el tipo «aviso» (resumen diario por Telegram).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_tipo_check;
        ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_tipo_check
            CHECK (tipo IN ('carga', 'comprobacion', 'copia', 'aviso'));
    """)


def downgrade() -> None:
    # Las filas de tipo «aviso» no se borran (la tabla es de solo añadir): el downgrade solo es
    # posible si todavía no hay ninguna.
    op.execute("""
        ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_tipo_check;
        ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_tipo_check
            CHECK (tipo IN ('carga', 'comprobacion', 'copia'));
    """)
