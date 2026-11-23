"""Color en la tabla «Por acabado y motor» del detalle de un modelo: dos niveles nuevos en
panel_mercado_segmento, para VN y VO.

  acabado_color · modelo + acabado + familia de color
  motor_color   · modelo + acabado + combustible + motor + familia de color

Con ellos, el filtro de color de la página (que sustituye al de la matriz) cuenta solo los anuncios de
esa familia en el total (nivel color), los acabados (acabado_color) y los motores (motor_color), y la
tabla despliega el color bajo cada motor. La matriz de VO ya tenía sus niveles con color (0011). No se
calcula el cruce completo.

La tabla es derivada: las filas de los niveles nuevos las escribe el siguiente make precalcular.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-23
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

MATRIZ = ("antiguedad_km", "acabado_antiguedad_km", "combustible_antiguedad_km", "motor_antiguedad_km",
          "acabado_combustible_antiguedad_km", "acabado_motor_antiguedad_km",
          "color_antiguedad_km", "acabado_color_antiguedad_km", "combustible_color_antiguedad_km",
          "motor_color_antiguedad_km", "acabado_combustible_color_antiguedad_km", "acabado_motor_color_antiguedad_km")
NUEVOS = ("acabado_color", "motor_color")


def _lista(niveles) -> str:
    return ", ".join(f"'{n}'" for n in niveles)


def _restricciones(tabla_niveles: tuple[str, ...]) -> str:
    """Restricciones de nivel de panel_mercado_segmento (nombradas en 0008) para estos niveles fuera de la matriz."""
    sueltos = ("modelo", "acabado", "motor", "color") + tabla_niveles
    con_acabado = ("acabado", "motor") + tuple(n for n in tabla_niveles if n.startswith(("acabado", "motor")))
    con_acabado += tuple(n for n in MATRIZ if n.startswith("acabado"))
    con_motor = ("motor",) + tuple(n for n in tabla_niveles if n.startswith("motor")) + tuple(n for n in MATRIZ if "motor" in n)
    con_comb = ("motor",) + tuple(n for n in tabla_niveles if n.startswith("motor")) + \
        tuple(n for n in MATRIZ if "combustible" in n or "motor" in n)
    con_color = ("color",) + tabla_niveles + tuple(n for n in MATRIZ if "color" in n)
    return f"""
ALTER TABLE panel_mercado_segmento
    DROP CONSTRAINT segmento_nivel, DROP CONSTRAINT segmento_acabado, DROP CONSTRAINT segmento_motor,
    DROP CONSTRAINT segmento_color, DROP CONSTRAINT segmento_combustible;
ALTER TABLE panel_mercado_segmento
    ADD CONSTRAINT segmento_nivel CHECK (nivel IN ({_lista(sueltos + MATRIZ)})),
    ADD CONSTRAINT segmento_acabado CHECK ((acabado IS NOT NULL) = (nivel IN ({_lista(con_acabado)}))),
    ADD CONSTRAINT segmento_motor CHECK ((motor IS NOT NULL) = (nivel IN ({_lista(con_motor)}))),
    ADD CONSTRAINT segmento_color CHECK ((color IS NOT NULL) = (nivel IN ({_lista(con_color)})));
ALTER TABLE panel_mercado_segmento ADD CONSTRAINT segmento_combustible
    CHECK ((combustible IS NOT NULL) = (nivel IN ({_lista(con_comb)}))) NOT VALID;
"""


def upgrade() -> None:
    op.execute(_restricciones(NUEVOS))


def downgrade() -> None:
    op.execute(f"DELETE FROM panel_mercado_segmento WHERE nivel IN ({_lista(NUEVOS)});")
    op.execute(_restricciones(()))
