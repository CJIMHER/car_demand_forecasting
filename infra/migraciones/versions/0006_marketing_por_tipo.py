"""Marketing por tipo (VN / VO): ámbito de cada campaña y leads, recomendaciones, semanas y resultados por tipo.

- panel_campana_dia.ambito: 'VN', 'VO' o 'general' (las dos). El gasto es de la campaña: no se reparte.
- panel_leads_dia.tipo: tipo ('VN'/'VO') del vehículo del lead. Clave: día × modelo × tipo × campaña.
- panel_recomendacion_modelo.tipo: una recomendación por tipo y modelo (VN, del stock nuevo; VO, del de ocasión).
- panel_semana_campana.tipo: 'todo', 'VN' o 'VO': la venta esperada de cada semana por tipo.
- panel_campana_resultado.ambito y .tipo: una fila 'todo' por campaña (todos sus leads y ventas) y,
  en las generales, además una 'VN' y una 'VO' con los del tipo.

Son tablas derivadas sin filas reales todavía (ninguna fuente las rellena aún). Sus filas de ejemplo
no tienen tipo: se borran y se vuelven a escribir con `make ejemplo-cargar`. Si alguna tuviera
filas reales, la migración se detiene sin tocar nada (habría que rehacerlas desde su fuente).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15
"""

from alembic import op
from sqlalchemy import text

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLAS = ("panel_campana_dia", "panel_leads_dia", "panel_recomendacion_modelo", "panel_semana_campana",
          "panel_campana_resultado")


def _sin_filas_reales() -> None:
    con = op.get_bind()
    for t in TABLAS:
        if con.execute(text(f"SELECT EXISTS (SELECT 1 FROM {t} WHERE origen = 'real')")).scalar():
            raise RuntimeError(f"{t} tiene filas reales: rehacerlas con su tipo desde la fuente antes de migrar")


def _quitar_unica(tabla: str) -> None:
    op.execute(f"""DO $$ DECLARE n text; BEGIN
        FOR n IN SELECT conname FROM pg_constraint WHERE conrelid = '{tabla}'::regclass AND contype = 'u' LOOP
            EXECUTE format('ALTER TABLE {tabla} DROP CONSTRAINT %I', n);
        END LOOP; END $$""")


def upgrade() -> None:
    _sin_filas_reales()
    for t in TABLAS:
        op.execute(f"DELETE FROM {t} WHERE origen = 'ejemplo'")
    op.execute("""ALTER TABLE panel_campana_dia
        ADD COLUMN ambito text NOT NULL CHECK (ambito IN ('VN', 'VO', 'general'))""")
    _quitar_unica("panel_leads_dia")
    op.execute("""ALTER TABLE panel_leads_dia
        ADD COLUMN tipo text NOT NULL CHECK (tipo IN ('VN', 'VO')),
        ADD CONSTRAINT panel_leads_dia_clave UNIQUE NULLS NOT DISTINCT (origen, dia, modelo, tipo, campana_id)""")
    op.execute("""ALTER TABLE panel_recomendacion_modelo
        ADD COLUMN tipo text NOT NULL CHECK (tipo IN ('VN', 'VO')),
        DROP CONSTRAINT panel_recomendacion_modelo_pkey,
        ADD PRIMARY KEY (origen, dia, tipo, modelo)""")
    op.execute("""ALTER TABLE panel_semana_campana
        ADD COLUMN tipo text NOT NULL CHECK (tipo IN ('todo', 'VN', 'VO')),
        DROP CONSTRAINT panel_semana_campana_pkey,
        ADD PRIMARY KEY (origen, dia, tipo, semana)""")
    op.execute("""ALTER TABLE panel_campana_resultado
        ADD COLUMN ambito text NOT NULL CHECK (ambito IN ('VN', 'VO', 'general')),
        ADD COLUMN tipo text NOT NULL CHECK (tipo IN ('todo', 'VN', 'VO')),
        ADD CONSTRAINT panel_campana_resultado_tipo CHECK (ambito = 'general' OR tipo = 'todo'),
        DROP CONSTRAINT panel_campana_resultado_pkey,
        ADD PRIMARY KEY (origen, dia, campana_id, tipo)""")


def downgrade() -> None:
    _sin_filas_reales()
    for t in TABLAS:
        op.execute(f"DELETE FROM {t} WHERE origen = 'ejemplo'")
    op.execute("""ALTER TABLE panel_campana_resultado DROP CONSTRAINT panel_campana_resultado_pkey,
        DROP COLUMN tipo, DROP COLUMN ambito, ADD PRIMARY KEY (origen, dia, campana_id)""")
    op.execute("""ALTER TABLE panel_semana_campana DROP CONSTRAINT panel_semana_campana_pkey,
        DROP COLUMN tipo, ADD PRIMARY KEY (origen, dia, semana)""")
    op.execute("""ALTER TABLE panel_recomendacion_modelo DROP CONSTRAINT panel_recomendacion_modelo_pkey,
        DROP COLUMN tipo, ADD PRIMARY KEY (origen, dia, modelo)""")
    op.execute("""ALTER TABLE panel_leads_dia DROP CONSTRAINT panel_leads_dia_clave, DROP COLUMN tipo,
        ADD UNIQUE NULLS NOT DISTINCT (origen, dia, modelo, campana_id)""")
    op.execute("ALTER TABLE panel_campana_dia DROP COLUMN ambito")
