"""Precio provisional de nuestros VO: panel_precio_vo (contrato del panel).

Una fila por día del precálculo de Mercado y anuncio VO propio (fuente + id del anuncio en la web de stock,
nunca datos de clientes), con su precio, sus días publicado, el segmento de comparación usado y la
recomendación que sale de las reglas de config/concesionario.yaml (precio_provisional). La escribe
analitica/precio.py dentro de make precalcular; recalcular un día sustituye ese día.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-24
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE panel_precio_vo (
    dia               date        NOT NULL,              -- día del precálculo de Mercado
    origen            text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at      timestamptz NOT NULL DEFAULT now(),
    fuente            text        NOT NULL,              -- scraper (vo)
    id_anuncio        text        NOT NULL,              -- identificador del anuncio en la web de stock
    modelo            text        NOT NULL,
    version           text,                              -- texto de versión del anuncio
    acabado           text,                              -- del catálogo; NULL si no se reconoce
    motor             text,
    antiguedad        text,                              -- etiqueta del tramo (config/concesionario.yaml)
    km                text,                              -- etiqueta del tramo
    precio_cents      bigint,                            -- NULL: el anuncio no publica precio
    dias_publicado    integer     NOT NULL CHECK (dias_publicado >= 0),
    dias_cota         boolean     NOT NULL,              -- true: «al menos N días» (visto desde el primer día)
    nivel             text CHECK (nivel IN ('segmento', 'sin_km', 'sin_antiguedad', 'comunidad')),
    zona              text CHECK (zona IN ('cercana', 'comunidad')),
    n_comparables     integer     NOT NULL CHECK (n_comparables >= 0),
    p25_cents         bigint,
    mediana_cents     bigint,
    p75_cents         bigint,
    regla             text        NOT NULL CHECK (regla IN ('muy_por_encima', 'por_encima_parado',
                                  'en_precio_no_sale', 'barato', 'en_linea', 'sin_comparables', 'sin_precio')),
    orden             smallint    NOT NULL CHECK (orden BETWEEN 1 AND 4),
    recomendacion     text        NOT NULL,
    CHECK ((nivel IS NULL) = (mediana_cents IS NULL) AND (nivel IS NULL) = (zona IS NULL)),
    CHECK (p25_cents <= mediana_cents AND mediana_cents <= p75_cents),
    PRIMARY KEY (origen, dia, fuente, id_anuncio)
);
GRANT SELECT ON panel_precio_vo TO cdf_panel;
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS panel_precio_vo")
