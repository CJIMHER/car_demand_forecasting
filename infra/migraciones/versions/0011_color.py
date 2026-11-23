"""Color de los anuncios: tabla de enriquecimiento color_anuncio y familias de color en Mercado.

- color_anuncio: el color de cada anuncio tal como lo exporta el componente de fichas
  (/opt/cdf/data/vw_details/exports/colors_latest.jsonl.gz), independiente de los scrapers. Solo se
  añaden filas: una por cada estado distinto que se lee de un anuncio (estado, VIN, colores y
  fetched_at). Es tabla de hechos: el panel no la lee. Nunca toca stock_externo_snapshot ni
  eventos_externos.
- panel_mercado_segmento: el nivel «color» pasa a guardar la familia (config/colores.yaml); seis
  niveles nuevos para el filtro de color de la matriz de VO (color, y color con acabado, combustible,
  motor, acabado+combustible y acabado+motor, siempre con antigüedad × km); con_color, los anuncios del
  segmento con color conocido, para la cobertura.
- panel_mercado_color_fuente: por día, fuente y estado de la ficha, anuncios y cuántos tienen color
  (y cuántos lo pierden por un VIN distinto: bastidor_distinto). panel_mercado_color_sin_familia: textos de color sin
  familia, con sus anuncios en España. Las dos, para «Estado de las fuentes».

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-23
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

MATRIZ_0008 = ("antiguedad_km", "acabado_antiguedad_km", "combustible_antiguedad_km", "motor_antiguedad_km",
               "acabado_combustible_antiguedad_km", "acabado_motor_antiguedad_km")
MATRIZ_COLOR = ("color_antiguedad_km", "acabado_color_antiguedad_km", "combustible_color_antiguedad_km",
                "motor_color_antiguedad_km", "acabado_combustible_color_antiguedad_km",
                "acabado_motor_color_antiguedad_km")


def _lista(niveles) -> str:
    return ", ".join(f"'{n}'" for n in niveles)


def _restricciones(matriz: tuple[str, ...]) -> str:
    """Las restricciones de nivel de panel_mercado_segmento (nombradas en 0008) para estos niveles de matriz."""
    con_acabado = ("acabado", "motor") + tuple(n for n in matriz if n.startswith("acabado"))
    con_motor = ("motor",) + tuple(n for n in matriz if "motor" in n)
    con_comb = ("motor",) + tuple(n for n in matriz if "combustible" in n or "motor" in n)
    con_color = ("color",) + tuple(n for n in matriz if "color" in n)
    return f"""
ALTER TABLE panel_mercado_segmento
    DROP CONSTRAINT segmento_nivel, DROP CONSTRAINT segmento_acabado, DROP CONSTRAINT segmento_motor,
    DROP CONSTRAINT segmento_tramos, DROP CONSTRAINT segmento_color, DROP CONSTRAINT segmento_combustible;
ALTER TABLE panel_mercado_segmento
    ADD CONSTRAINT segmento_nivel CHECK (nivel IN ('modelo', 'acabado', 'motor', 'color', {_lista(matriz)})),
    ADD CONSTRAINT segmento_acabado CHECK ((acabado IS NOT NULL) = (nivel IN ({_lista(con_acabado)}))),
    ADD CONSTRAINT segmento_motor CHECK ((motor IS NOT NULL) = (nivel IN ({_lista(con_motor)}))),
    ADD CONSTRAINT segmento_tramos CHECK ((antiguedad IS NOT NULL AND km IS NOT NULL) = (nivel IN ({_lista(matriz)}))),
    ADD CONSTRAINT segmento_color CHECK ((color IS NOT NULL) = (nivel IN ({_lista(con_color)})));
ALTER TABLE panel_mercado_segmento ADD CONSTRAINT segmento_combustible
    CHECK ((combustible IS NOT NULL) = (nivel IN ({_lista(con_comb)}))) NOT VALID;
"""


UPGRADE = r"""
CREATE TABLE color_anuncio (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    captured_at     timestamptz NOT NULL DEFAULT now(),   -- cuándo se leyó de la exportación
    run_id          text        NOT NULL,                 -- la lectura: «colores-» + huella del fichero
    fuente          text        NOT NULL CHECK (fuente IN ('vo', 'vn', 'vn_otros')),
    id_anuncio      text        NOT NULL,                 -- vehicle_id del scraper
    bastidor        text,                                 -- VIN de la ficha, si viene
    color_exterior  text,                                 -- tal cual («Gris Indy metalizado»)
    color_interior  text,
    estado          text        NOT NULL,                 -- ok, pending, empty, error, exhausted, listing_color…
    fetched_at      timestamptz,                          -- cuándo leyó la ficha el componente
    UNIQUE NULLS NOT DISTINCT (fuente, id_anuncio, estado, bastidor, color_exterior, color_interior, fetched_at)
);
CREATE INDEX color_anuncio_anuncio ON color_anuncio (fuente, id_anuncio, id);

CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON color_anuncio
    FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON color_anuncio
    FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();

ALTER TABLE panel_mercado_segmento
    ADD COLUMN con_color integer CHECK (con_color >= 0 AND con_color <= anuncios);

CREATE TABLE panel_mercado_color_fuente (
    dia           date    NOT NULL,
    fuente        text    NOT NULL CHECK (fuente IN ('vo', 'vn', 'vn_otros')),
    estado        text    NOT NULL,   -- estado de la ficha ese día; «sin_ficha» si no está en la exportación
    anuncios      integer NOT NULL CHECK (anuncios >= 0),
    con_color     integer NOT NULL CHECK (con_color >= 0 AND con_color <= anuncios),
    bastidor_distinto integer NOT NULL CHECK (bastidor_distinto >= 0),   -- color anulado por un VIN distinto
    calculado_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dia, fuente, estado)
);

CREATE TABLE panel_mercado_color_sin_familia (
    dia           date    NOT NULL,
    tipo          text    NOT NULL CHECK (tipo IN ('VN', 'VO')),
    color         text    NOT NULL,   -- texto de color tal como lo publica la ficha
    anuncios      integer NOT NULL,   -- en España ese día
    calculado_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dia, tipo, color)
);

GRANT SELECT ON panel_mercado_color_fuente, panel_mercado_color_sin_familia TO cdf_panel;
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS panel_mercado_color_sin_familia;
DROP TABLE IF EXISTS panel_mercado_color_fuente;
ALTER TABLE panel_mercado_segmento DROP COLUMN IF EXISTS con_color;
DROP TABLE IF EXISTS color_anuncio;
"""


def upgrade() -> None:
    op.execute(UPGRADE)
    op.execute(_restricciones(MATRIZ_0008 + MATRIZ_COLOR))


def downgrade() -> None:
    # Tablas derivadas: las filas de los niveles nuevos y las de color (familias) se quitan; el
    # siguiente make precalcular con 0010 rellena el nivel color con los textos del scraper.
    op.execute(f"DELETE FROM panel_mercado_segmento WHERE nivel IN ('color', {_lista(MATRIZ_COLOR)});")
    op.execute(_restricciones(MATRIZ_0008))
    op.execute(DOWNGRADE)
