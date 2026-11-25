"""Cuándo lanzar una campaña general: ventas por fecha de venta y el bloque «Campaña general» del panel.

- walcu_ventas_carga y walcu_ventas_semana (hechos, solo añadir): ventas de Walcu por semana de la VENTA (lunes),
  tipo y modelo. Cada carga (CSV de make walcu-ventas-cargar o generada desde la API de Walcu) dice qué semanas
  cubre (desde, hasta): una semana cubierta sin filas es una semana sin ventas. Para cada semana vale la última
  carga que la cubre, y la de la API manda sobre la del CSV (ingesta/walcu_ventas.py).
- panel_campana_general (contrato del panel): por día de cálculo y tipo (VN, VO), si conviene lanzar una campaña
  general, con sus motivos, lo pendiente, la próxima temporada y las cifras (analitica/campana_general.py, dentro
  de make precalcular). Reglas y textos en config/campana_general.yaml.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-25
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def _solo_anadir(tabla: str) -> str:
    return (f"CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON {tabla} FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();\n"
            f"CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON {tabla} FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();\n")


UPGRADE = f"""
CREATE TABLE walcu_ventas_carga (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           text        NOT NULL UNIQUE,
    fecha_carga      timestamptz NOT NULL DEFAULT now(),
    fuente           text        NOT NULL CHECK (fuente IN ('csv', 'api')),
    fichero_origen   text,
    huella           text        NOT NULL,              -- del fichero o del contenido generado
    desde            date        NOT NULL CHECK (extract(isodow FROM desde) = 1),
    hasta            date        NOT NULL CHECK (extract(isodow FROM hasta) = 1 AND hasta >= desde)
);
{_solo_anadir("walcu_ventas_carga")}
CREATE TABLE walcu_ventas_semana (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           text        NOT NULL REFERENCES walcu_ventas_carga (run_id),
    semana           date        NOT NULL CHECK (extract(isodow FROM semana) = 1),   -- lunes de la semana de la venta
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    modelo           text        NOT NULL,
    ventas           integer     NOT NULL CHECK (ventas >= 0),
    UNIQUE (run_id, semana, tipo, modelo)
);
{_solo_anadir("walcu_ventas_semana")}

CREATE TABLE panel_campana_general (
    dia              date        NOT NULL,              -- día del cálculo
    origen           text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at     timestamptz NOT NULL DEFAULT now(),
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    conviene         boolean     NOT NULL,              -- alguna regla se cumple
    motivos          jsonb       NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(motivos) = 'array'),      -- [{{regla, texto}}]
    pendientes       jsonb       NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(pendientes) = 'array'),   -- [{{regla, texto}}]
    proxima          text,                              -- próxima temporada («marzo-abril»)
    cifras           jsonb       NOT NULL DEFAULT '{{}}' CHECK (jsonb_typeof(cifras) = 'object'),
    version          text        NOT NULL,              -- versión de config/campana_general.yaml
    PRIMARY KEY (origen, dia, tipo)
);
GRANT SELECT ON panel_campana_general TO cdf_panel;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS panel_campana_general;
DROP TABLE IF EXISTS walcu_ventas_semana;
DROP TABLE IF EXISTS walcu_ventas_carga;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
