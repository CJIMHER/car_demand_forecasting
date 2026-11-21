"""Matriz de precios de VO filtrable por combustible y motor: columna combustible y cuatro niveles
nuevos en panel_mercado_segmento.

Niveles nuevos, todos con la matriz antigüedad × km (solo VO):
  combustible_antiguedad_km · motor_antiguedad_km ·
  acabado_combustible_antiguedad_km · acabado_motor_antiguedad_km
No hace falta el cruce de acabado + combustible + motor: el motor ya implica su combustible.

combustible: el grupo del filtro (Gasolina, Diésel, Híbrido, Eléctrico, según config/versiones.yaml).
Va en el nivel motor y en los niveles de la matriz con combustible o motor; el motor se identifica
por su combustible y su texto, así que la clave única pasa a incluirlo.

La tabla es derivada (la rellena make precalcular): las filas del nivel motor calculadas antes de
esta migración no tienen combustible. La comprobación de esa columna se añade NOT VALID, para no
borrar nada: vale para las filas nuevas, y el siguiente make precalcular rehace las antiguas.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-21
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

MATRIZ_0007 = ("antiguedad_km", "acabado_antiguedad_km")
MATRIZ = MATRIZ_0007 + ("combustible_antiguedad_km", "motor_antiguedad_km", "acabado_combustible_antiguedad_km",
                        "acabado_motor_antiguedad_km")


def _lista(niveles) -> str:
    return ", ".join(f"'{n}'" for n in niveles)


def _restricciones(matriz: tuple[str, ...], con_combustible: bool) -> str:
    """Las restricciones de la tabla, con nombre (las de 0007 las nombraba Postgres)."""
    con_acabado = ("acabado", "motor") + tuple(n for n in matriz if n.startswith("acabado"))
    con_motor = ("motor",) + tuple(n for n in matriz if "motor" in n)
    clave = "dia, tipo, zona, nivel, modelo, acabado" + (", combustible" if con_combustible else "") + \
            ", motor, antiguedad, km, color"
    sql = f"""
ALTER TABLE panel_mercado_segmento
    ADD CONSTRAINT segmento_clave UNIQUE NULLS NOT DISTINCT ({clave}),
    ADD CONSTRAINT segmento_nivel CHECK (nivel IN ('modelo', 'acabado', 'motor', 'color', {_lista(matriz)})),
    ADD CONSTRAINT segmento_precios CHECK (precio_p25_cents <= precio_p50_cents
                                           AND precio_p50_cents <= precio_p75_cents),
    ADD CONSTRAINT segmento_n_precio CHECK ((precio_p50_cents IS NULL) = (n_precio = 0)),
    ADD CONSTRAINT segmento_acabado CHECK ((acabado IS NOT NULL) = (nivel IN ({_lista(con_acabado)}))),
    ADD CONSTRAINT segmento_reconocido CHECK ((acabado_reconocido IS NOT NULL) = (acabado IS NOT NULL)),
    ADD CONSTRAINT segmento_motor CHECK ((motor IS NOT NULL) = (nivel IN ({_lista(con_motor)}))),
    ADD CONSTRAINT segmento_tramos CHECK ((antiguedad IS NOT NULL AND km IS NOT NULL)
                                          = (nivel IN ({_lista(matriz)}))),
    ADD CONSTRAINT segmento_color CHECK ((color IS NOT NULL) = (nivel = 'color'));
"""
    if con_combustible:
        con_comb = ("motor",) + tuple(n for n in matriz if "combustible" in n or "motor" in n)
        sql += f"""
ALTER TABLE panel_mercado_segmento ADD CONSTRAINT segmento_combustible
    CHECK ((combustible IS NOT NULL) = (nivel IN ({_lista(con_comb)}))) NOT VALID;
"""
    return sql


QUITAR = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'panel_mercado_segmento'::regclass AND contype IN ('c', 'u') LOOP
        EXECUTE format('ALTER TABLE panel_mercado_segmento DROP CONSTRAINT %I', r.conname);
    END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute("ALTER TABLE panel_mercado_segmento ADD COLUMN combustible text;")
    op.execute(QUITAR)
    op.execute(_restricciones(MATRIZ, con_combustible=True))


def downgrade() -> None:
    op.execute(f"DELETE FROM panel_mercado_segmento WHERE nivel NOT IN ('modelo', 'acabado', 'motor', 'color', "
               f"{_lista(MATRIZ_0007)});")
    # Sin combustible, dos motores con el mismo texto y distinto combustible chocarían: se quedan
    # sin nivel motor hasta el siguiente make precalcular.
    op.execute("""DELETE FROM panel_mercado_segmento s WHERE nivel = 'motor' AND EXISTS (
                    SELECT 1 FROM panel_mercado_segmento o WHERE o.nivel = 'motor' AND o.dia = s.dia
                      AND o.tipo = s.tipo AND o.zona = s.zona AND o.modelo = s.modelo AND o.acabado = s.acabado
                      AND o.motor = s.motor AND o.combustible IS DISTINCT FROM s.combustible);""")
    op.execute(QUITAR)
    op.execute("ALTER TABLE panel_mercado_segmento DROP COLUMN combustible;")
    op.execute(_restricciones(MATRIZ_0007, con_combustible=False))
