"""Google Trends: entregas manuales y atención relativa real (ingesta/gtrends.py).

- gtrends_entrega: una fila por entrega cargada (fecha de la descarga, huella y contenido del
  manifiesto). Solo añadir.
- gtrends_semana: hechos de solo añadir. Por entrega, comparación, término y semana: el valor de
  Trends (0–100; «<1» se guarda como 0,5 con menor_que_1) y la huella del fichero. La clave única
  hace que repetir la carga de la misma entrega no duplique.
- panel_atencion_relativa: la entrega de la que sale cada fila, la última semana usada, la cuota del
  modelo en esas semanas y en las anteriores, y en cuántas comparaciones se apoya. Vacías en las
  filas de ejemplo.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-23
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE gtrends_entrega (
    id                 bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cargado_at         timestamptz NOT NULL DEFAULT now(),
    fecha_descarga     date        NOT NULL,
    huella_manifiesto  text        NOT NULL,
    carpeta            text        NOT NULL,
    manifiesto         jsonb       NOT NULL CHECK (jsonb_typeof(manifiesto) = 'object'),
    UNIQUE (fecha_descarga, huella_manifiesto)
);

CREATE TABLE gtrends_semana (
    id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cargado_at      timestamptz NOT NULL DEFAULT now(),
    fecha_descarga  date        NOT NULL,
    comparacion     text        NOT NULL,
    modelo          text        NOT NULL,              -- el modelo propio de la comparación
    termino         text        NOT NULL,              -- tal como viene en la cabecera del CSV
    es_modelo       boolean     NOT NULL,              -- el término es la consulta del modelo (el ancla)
    semana          date        NOT NULL,
    valor           numeric     NOT NULL CHECK (valor >= 0 AND valor <= 100),
    menor_que_1     boolean     NOT NULL,              -- Trends daba «<1»: valor 0,5
    huella_fichero  text        NOT NULL,
    CHECK (NOT menor_que_1 OR valor = 0.5),
    UNIQUE (fecha_descarga, comparacion, termino, semana, huella_fichero)
);
CREATE INDEX gtrends_semana_entrega ON gtrends_semana (fecha_descarga, comparacion);

CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON gtrends_entrega FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON gtrends_entrega FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON gtrends_semana FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON gtrends_semana FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();

ALTER TABLE panel_atencion_relativa
    ADD COLUMN entrega             date,
    ADD COLUMN semana_fin          date,
    ADD COLUMN cuota_pct           numeric CHECK (cuota_pct >= 0 AND cuota_pct <= 100),
    ADD COLUMN cuota_anterior_pct  numeric CHECK (cuota_anterior_pct >= 0 AND cuota_anterior_pct <= 100),
    ADD COLUMN n_comparaciones     integer CHECK (n_comparaciones >= 1);
"""

DOWNGRADE = """
ALTER TABLE panel_atencion_relativa DROP COLUMN n_comparaciones, DROP COLUMN cuota_anterior_pct,
    DROP COLUMN cuota_pct, DROP COLUMN semana_fin, DROP COLUMN entrega;
DROP TABLE IF EXISTS gtrends_semana;
DROP TABLE IF EXISTS gtrends_entrega;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
