"""Segmentos de Mercado por modelo: potencia, cilindrada y color en el stock externo y las tablas
panel_mercado_segmento y panel_mercado_sin_clasificar.

stock_externo_snapshot ya tenía versión (version), combustible, primera matriculación
(fecha_matriculacion) y km. Faltaba la potencia en CV, que estaba solo dentro de datos
(datos.business.power_cv). Se añade, con la cilindrada y el color exterior, como columnas generadas
a partir de datos: Postgres las rellena para las filas ya cargadas y para las nuevas, sin UPDATE
(la tabla sigue siendo de solo añadir) y sin tocar los scrapers, la carga ni las bases SQLite.
El color exterior viene hoy vacío en todos los anuncios: la columna queda lista para cuando llegue.

La función cdf_solo_anadir deja de comparar las columnas generadas al cambiar es_propio (en un
trigger BEFORE, Postgres aún no las ha calculado en NEW).

- panel_mercado_segmento: por día, tipo, zona y modelo, solo los niveles que muestra el panel
  (modelo; modelo+acabado; modelo+acabado+motor; en VO, modelo+antigüedad×km y
  modelo+acabado+antigüedad×km; y modelo+color, vacío hasta que haya color). Anuncios, propios,
  precio p25/p50/p75 de los que tienen precio y días publicado (mediana, con la cota «≥ N»).
- panel_mercado_sin_clasificar: textos de versión cuyo acabado no está en config/versiones.yaml,
  con sus anuncios en España, para ir completando el catálogo.

Las dos son derivadas, como el resto de panel_mercado_*: las rellena analitica/mercado.py (make
precalcular) y el panel solo las lee (cdf_panel: SELECT).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-21
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

GENERADAS = ("potencia_cv", "cilindrada_cc", "color_exterior")


def _numero(campo: str) -> str:
    return (f"CASE WHEN jsonb_typeof(datos -> 'business' -> '{campo}') = 'number' "
            f"THEN round((datos -> 'business' ->> '{campo}')::numeric)::integer END")


SOLO_ANADIR = r"""
CREATE OR REPLACE FUNCTION cdf_solo_anadir() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'stock_externo_snapshot'
       AND (to_jsonb(NEW) - '{columnas}'::text[]) = (to_jsonb(OLD) - '{columnas}'::text[]) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'La tabla % solo admite añadir filas (% rechazado)', TG_TABLE_NAME, TG_OP;
END $$;
"""

UPGRADE = rf"""
ALTER TABLE stock_externo_snapshot
    ADD COLUMN potencia_cv integer GENERATED ALWAYS AS ({_numero('power_cv')}) STORED,
    ADD COLUMN cilindrada_cc integer GENERATED ALWAYS AS ({_numero('engine_capacity_cc')}) STORED,
    ADD COLUMN color_exterior text GENERATED ALWAYS AS (
        CASE WHEN jsonb_typeof(datos -> 'business' -> 'exterior_color') = 'string'
             THEN nullif(btrim(datos -> 'business' ->> 'exterior_color'), '') END) STORED;

{SOLO_ANADIR.replace('{columnas}', '{' + ','.join(('es_propio',) + GENERADAS) + '}')}

CREATE TABLE panel_mercado_segmento (
    dia                 date     NOT NULL,
    tipo                text     NOT NULL CHECK (tipo IN ('VN', 'VO')),
    zona                text     NOT NULL CHECK (zona IN ('provincia', 'comunidad', 'espana')),
    nivel               text     NOT NULL CHECK (nivel IN ('modelo', 'acabado', 'motor', 'antiguedad_km',
                                                           'acabado_antiguedad_km', 'color')),
    modelo              text     NOT NULL,
    acabado             text,    -- niveles acabado, motor y acabado_antiguedad_km
    acabado_reconocido  boolean, -- false: «Sin clasificar» (acabado fuera de config/versiones.yaml)
    motor               text,    -- nivel motor
    antiguedad          text,    -- etiqueta del tramo (config/concesionario.yaml), niveles *_antiguedad_km
    antiguedad_orden    smallint,
    km                  text,    -- etiqueta del tramo de km
    km_orden            smallint,
    color               text,    -- nivel color
    anuncios            integer  NOT NULL,
    propios             integer  NOT NULL,
    n_precio            integer  NOT NULL,   -- anuncios con precio: n de los percentiles
    precio_p25_cents    bigint,
    precio_p50_cents    bigint,
    precio_p75_cents    bigint,
    dias_mediana        integer,
    dias_cota           boolean  NOT NULL DEFAULT false,   -- la mediana es una cota inferior («≥ N»)
    calculado_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (dia, tipo, zona, nivel, modelo, acabado, motor, antiguedad, km, color),
    CHECK (precio_p25_cents <= precio_p50_cents AND precio_p50_cents <= precio_p75_cents),
    CHECK ((precio_p50_cents IS NULL) = (n_precio = 0)),
    CHECK ((acabado IS NOT NULL) = (nivel IN ('acabado', 'motor', 'acabado_antiguedad_km'))),
    CHECK ((acabado_reconocido IS NOT NULL) = (acabado IS NOT NULL)),
    CHECK ((motor IS NOT NULL) = (nivel = 'motor')),
    CHECK ((antiguedad IS NOT NULL AND km IS NOT NULL) = (nivel IN ('antiguedad_km', 'acabado_antiguedad_km'))),
    CHECK ((color IS NOT NULL) = (nivel = 'color'))
);
CREATE INDEX panel_mercado_segmento_modelo ON panel_mercado_segmento (tipo, zona, modelo, dia);

CREATE TABLE panel_mercado_sin_clasificar (
    dia           date    NOT NULL,
    tipo          text    NOT NULL CHECK (tipo IN ('VN', 'VO')),
    modelo        text    NOT NULL,
    version       text    NOT NULL,   -- texto de versión tal como lo publica la web
    anuncios      integer NOT NULL,   -- en España ese día
    calculado_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dia, tipo, modelo, version)
);

GRANT SELECT ON panel_mercado_segmento, panel_mercado_sin_clasificar TO cdf_panel;
"""

DOWNGRADE = rf"""
DROP TABLE IF EXISTS panel_mercado_sin_clasificar;
DROP TABLE IF EXISTS panel_mercado_segmento;
{SOLO_ANADIR.replace('{columnas}', '{es_propio}')}
ALTER TABLE stock_externo_snapshot DROP COLUMN IF EXISTS color_exterior,
    DROP COLUMN IF EXISTS cilindrada_cc, DROP COLUMN IF EXISTS potencia_cv;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
