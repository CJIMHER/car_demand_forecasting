"""Salidas de nuestro stock publicado (estimación de ventas): panel_salidas_stock (contrato del panel).

Una fila por salida: un anuncio NUESTRO (fuente + id del anuncio en la web de stock, nunca datos de clientes;
ni matrícula ni bastidor) que deja de estar publicado según las reglas de config/concesionario.yaml
(salidas_stock). `dia` es la fecha de salida (el día siguiente al último en que se vio). La escribe
analitica/salidas.py dentro de make precalcular, recalculando cada noche la ventana de los últimos R + K días.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-24
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE panel_salidas_stock (
    dia               date        NOT NULL,              -- fecha de salida (último día visto + 1)
    origen            text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at      timestamptz NOT NULL DEFAULT now(),
    tipo              text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    fuente            text        NOT NULL,              -- scraper (vo, vn, vn_otros)
    id_anuncio        text        NOT NULL,              -- identificador del anuncio en la web de stock
    modelo            text        NOT NULL,
    version           text,                              -- texto de versión del anuncio
    acabado           text,                              -- del catálogo; NULL si no se reconoce
    motor             text,                              -- NULL: sin dato de motor
    anio              smallint,                          -- año de matriculación (VO)
    km                text,                              -- etiqueta del tramo de km (VO)
    color             text,                              -- color exterior publicado
    precio_cents      bigint,                            -- último precio publicado; NULL sin precio
    dias_publicado    integer     NOT NULL CHECK (dias_publicado >= 0),
    dias_cota         boolean     NOT NULL,              -- true: «al menos N días» (no se vio aparecer)
    ultimo_dia        date        NOT NULL,              -- último día en que se vio
    provisional       boolean     NOT NULL,              -- aún puede reaparecer (últimos R días)
    nivel             text CHECK (nivel IN ('segmento', 'sin_km', 'sin_antiguedad', 'comunidad')),
    n_comparables     integer CHECK (n_comparables >= 0),
    mediana_cents     bigint,                            -- mediana de su segmento el último día visto
    CHECK (ultimo_dia < dia),
    CHECK ((nivel IS NULL) = (mediana_cents IS NULL)),
    PRIMARY KEY (origen, dia, fuente, id_anuncio)
);
GRANT SELECT ON panel_salidas_stock TO cdf_panel;
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS panel_salidas_stock")
