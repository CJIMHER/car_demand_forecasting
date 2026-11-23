"""Leads según Meta en panel_campana_dia.

- panel_campana_dia.leads_meta: los leads que cuenta Meta (campanas_meta_dia.leads, nivel campaña)
  ese día. Con ellos se aplican ya las reglas b, c y f de los criterios de Meta, mientras no hay leads
  de Walcu (panel_leads_dia). Cuando entren los de Walcu, mandan los de Walcu. NULL: Meta no dio la
  cifra (o es una fila de ejemplo).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-23
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE panel_campana_dia ADD COLUMN leads_meta integer "
               "CONSTRAINT panel_campana_dia_leads_meta CHECK (leads_meta >= 0)")


def downgrade() -> None:
    op.execute("ALTER TABLE panel_campana_dia DROP COLUMN leads_meta")
