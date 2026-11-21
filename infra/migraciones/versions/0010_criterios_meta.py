"""Criterios de Cayetano para las campañas de Meta (docs/criterios_marketing_meta.md).

- Zona «cercana» en Mercado: la provincia propia y las de propio.provincias_cercanas de
  config/concesionario.yaml. Las tablas panel_mercado_dia, _modelo y _segmento la admiten (las filas
  se escriben con el siguiente make precalcular).
- panel_mercado_segmento: mediana de nuestro precio y de la competencia por separado (con su n),
  para la regla e (lanzar una campaña) y la d (pocas ventas).
- panel_leads_dia: leads_cp_zona y leads_cp_fuera, el recuento de leads con código postal dentro y
  fuera de la zona cercana. El código postal se lee del lead de Walcu y nunca se guarda. NULL: sin dato.
- panel_campana_decision: la decisión de cada campaña (continuar, revisar, parar, subir presupuesto)
  con sus reglas, cifras, umbrales y n. La escribe el motor de alertas (como alertas_evaluacion).
- panel_recomendacion_modelo: acciones de los criterios (revisar, parar, lanzar, no_lanzar) además
  de las de antes, y la regla, las cifras y la prioridad de cada recomendación.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-21
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

ZONAS_ANTES = "'provincia', 'comunidad', 'espana'"
ZONAS = "'provincia', 'cercana', 'comunidad', 'espana'"
ACCIONES_ANTES = "'aumentar', 'mantener', 'reducir', 'cambiar'"
ACCIONES = ACCIONES_ANTES + ", 'revisar', 'parar', 'lanzar', 'no_lanzar'"


def _zonas(lista: str) -> str:
    """Quita la restricción de zona de las tres tablas de Mercado y la vuelve a poner con `lista`."""
    return f"""
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conrelid::regclass::text AS tabla, conname FROM pg_constraint
             WHERE contype = 'c' AND conrelid IN ('panel_mercado_dia'::regclass, 'panel_mercado_modelo'::regclass,
                                                  'panel_mercado_segmento'::regclass)
               AND pg_get_constraintdef(oid) LIKE '%zona%' LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tabla, r.conname);
    END LOOP;
END $$;
ALTER TABLE panel_mercado_dia ADD CONSTRAINT panel_mercado_dia_zona CHECK (zona IN ({lista}));
ALTER TABLE panel_mercado_modelo ADD CONSTRAINT panel_mercado_modelo_zona CHECK (zona IN ({lista}));
ALTER TABLE panel_mercado_segmento ADD CONSTRAINT segmento_zona CHECK (zona IN ({lista}));
"""


UPGRADE = _zonas(ZONAS) + f"""
ALTER TABLE panel_mercado_segmento
    ADD COLUMN n_precio_propios              integer CHECK (n_precio_propios >= 0),
    ADD COLUMN precio_propios_p50_cents      bigint,
    ADD COLUMN n_precio_competencia          integer CHECK (n_precio_competencia >= 0),
    ADD COLUMN precio_competencia_p50_cents  bigint;

ALTER TABLE panel_leads_dia
    ADD COLUMN leads_cp_zona   integer CHECK (leads_cp_zona >= 0),
    ADD COLUMN leads_cp_fuera  integer CHECK (leads_cp_fuera >= 0),
    ADD CONSTRAINT panel_leads_dia_cp CHECK ((leads_cp_zona IS NULL) = (leads_cp_fuera IS NULL)
                                             AND coalesce(leads_cp_zona + leads_cp_fuera, 0) <= leads);

CREATE TABLE panel_campana_decision (
    dia             date        NOT NULL,
    origen          text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at    timestamptz NOT NULL DEFAULT now(),
    campana_id      text        NOT NULL,
    titulo          text        NOT NULL,
    modelo          text,
    ambito          text        NOT NULL CHECK (ambito IN ('VN', 'VO', 'general')),
    dias_vida       integer     NOT NULL CHECK (dias_vida >= 0),
    decision        text        NOT NULL CHECK (decision IN ('continuar', 'revisar', 'parar', 'subir', 'sin_juzgar')),
    reglas          jsonb       NOT NULL CHECK (jsonb_typeof(reglas) = 'array'),   -- [{{regla, estado, texto, cifras}}]
    leads           integer     NOT NULL CHECK (leads >= 0),
    citas           integer     NOT NULL CHECK (citas >= 0),
    ventas          integer     NOT NULL CHECK (ventas >= 0),
    modelo_version  text        NOT NULL,                  -- versión de alertas/reglas.yaml
    n               integer     NOT NULL CHECK (n >= 0),   -- leads de la campaña
    PRIMARY KEY (origen, dia, campana_id)
);
GRANT SELECT ON panel_campana_decision TO cdf_panel;

ALTER TABLE panel_recomendacion_modelo DROP CONSTRAINT panel_recomendacion_modelo_accion_check;
ALTER TABLE panel_recomendacion_modelo
    ADD CONSTRAINT panel_recomendacion_modelo_accion_check CHECK (accion IN ({ACCIONES})),
    ADD COLUMN regla      text,              -- a–f, e o subir (criterios de Meta)
    ADD COLUMN cifras     jsonb CHECK (cifras IS NULL OR jsonb_typeof(cifras) = 'object'),
    ADD COLUMN prioridad  integer;           -- orden dentro de su acción (1: primero)
"""

DOWNGRADE = f"""
DELETE FROM panel_recomendacion_modelo WHERE accion NOT IN ({ACCIONES_ANTES});
ALTER TABLE panel_recomendacion_modelo DROP COLUMN prioridad, DROP COLUMN cifras, DROP COLUMN regla,
    DROP CONSTRAINT panel_recomendacion_modelo_accion_check;
ALTER TABLE panel_recomendacion_modelo ADD CONSTRAINT panel_recomendacion_modelo_accion_check
    CHECK (accion IN ({ACCIONES_ANTES}));
DROP TABLE IF EXISTS panel_campana_decision;
ALTER TABLE panel_leads_dia DROP CONSTRAINT panel_leads_dia_cp, DROP COLUMN leads_cp_fuera, DROP COLUMN leads_cp_zona;
ALTER TABLE panel_mercado_segmento DROP COLUMN precio_competencia_p50_cents, DROP COLUMN n_precio_competencia,
    DROP COLUMN precio_propios_p50_cents, DROP COLUMN n_precio_propios;
DELETE FROM panel_mercado_segmento WHERE zona = 'cercana';
DELETE FROM panel_mercado_modelo WHERE zona = 'cercana';
DELETE FROM panel_mercado_dia WHERE zona = 'cercana';
""" + _zonas(ZONAS_ANTES)


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
