"""Previsión bayesiana de leads, citas y ventas de las campañas de Meta (analitica/prevision.py).

- panel_prevision_modelo (contrato del panel): la posterior de cada tipo (modelo NULL) y de cada modelo con
  campaña: coeficientes de la regresión de leads (media y V, con σ² ~ Gamma inversa(a, b)), Beta de lead a
  cita y de cita a venta, y lo que hace falta para simular de nuevo en el panel (semanas, gasto previsto,
  semilla). Con ella el panel recalcula los escenarios de gasto sin volver a entrenar.
- panel_prevision_semana (contrato del panel): la previsión de cada grupo, por semana y en total (semana
  NULL), para cada escenario de gasto (% sobre el previsto), con p10, p50 y p90.
- panel_prevision_validacion: la validación hacia atrás (error absoluto medio del modelo y de la referencia
  simple, cobertura del intervalo). Siempre real; la enseña «Estado de las fuentes».

Las escribe make prevision (cron, después del precálculo); recalcular un día sustituye sus filas reales.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-24
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def _q(m: str) -> str:
    return f"""
    {m}_p10        integer     NOT NULL CHECK ({m}_p10 >= 0),
    {m}_p50        integer     NOT NULL,
    {m}_p90        integer     NOT NULL,
    CHECK ({m}_p10 <= {m}_p50 AND {m}_p50 <= {m}_p90),"""


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE panel_prevision_modelo (
    dia                 date        NOT NULL,              -- día del cálculo
    origen              text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at        timestamptz NOT NULL DEFAULT now(),
    tipo                text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    modelo              text,                              -- NULL: el tipo entero
    columnas            jsonb       NOT NULL CHECK (jsonb_typeof(columnas) = 'array'),
    media               jsonb       NOT NULL CHECK (jsonb_typeof(media) = 'array'),
    covarianza          jsonb       NOT NULL CHECK (jsonb_typeof(covarianza) = 'array'),
    a                   double precision NOT NULL CHECK (a > 0),
    b                   double precision NOT NULL CHECK (b > 0),
    cita_a              double precision NOT NULL CHECK (cita_a > 0),
    cita_b              double precision NOT NULL CHECK (cita_b > 0),
    venta_a             double precision NOT NULL CHECK (venta_a > 0),
    venta_b             double precision NOT NULL CHECK (venta_b > 0),
    trends              double precision,                  -- índice de Trends usado; NULL: no se usa
    semanas             integer     NOT NULL CHECK (semanas >= 1),        -- semanas con leads y gasto
    leads_citas         integer     NOT NULL CHECK (leads_citas >= 0),     -- leads de semanas con citas completas
    citas_ventas        integer     NOT NULL CHECK (citas_ventas >= 0),    -- citas de semanas con ventas completas
    pocos_datos         boolean     NOT NULL,
    motivo              text,                              -- por qué «pocos datos»
    ultima_semana       date        NOT NULL,              -- última semana con datos (el aviso del panel)
    primera_semana      date        NOT NULL,              -- primera semana prevista
    gasto_semanal_cents bigint      NOT NULL CHECK (gasto_semanal_cents >= 0),   -- gasto previsto por semana
    regimen_desde       date        NOT NULL,
    simulaciones        integer     NOT NULL CHECK (simulaciones >= 1),
    semilla             bigint      NOT NULL,
    intervalo           numeric     NOT NULL CHECK (intervalo > 0 AND intervalo < 1),
    -- Regla del proyecto: si no bate a la referencia simple en la validación, no se publica
    validada            boolean     NOT NULL,              -- leads con el gasto que hubo: mejor que la referencia
    validacion          text        NOT NULL,              -- la cifra de la validación, o por qué no se publica
    aviso_gasto         text,                              -- con el gasto previsto no mejora: se dice
    UNIQUE NULLS NOT DISTINCT (origen, dia, tipo, modelo)
);

CREATE TABLE panel_prevision_semana (
    dia              date        NOT NULL,
    origen           text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at     timestamptz NOT NULL DEFAULT now(),
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    modelo           text,                                 -- NULL: el tipo entero
    semana           date,                                 -- lunes; NULL: total de las semanas previstas
    escenario        integer     NOT NULL,                 -- % sobre el gasto previsto (0: el actual)
    gasto_cents      bigint      NOT NULL CHECK (gasto_cents >= 0),{_q("leads")}{_q("citas")}{_q("ventas")}
    modelo_version   text        NOT NULL,
    n                integer     NOT NULL CHECK (n >= 1),  -- semanas de entrenamiento
    pocos_datos      boolean     NOT NULL,
    UNIQUE NULLS NOT DISTINCT (origen, dia, tipo, modelo, semana, escenario)
);

CREATE TABLE panel_prevision_validacion (
    dia              date        NOT NULL,
    origen           text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at     timestamptz NOT NULL DEFAULT now(),
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    nivel            text        NOT NULL CHECK (nivel IN ('tipo', 'modelo')),
    medida           text        NOT NULL CHECK (medida IN ('leads', 'citas', 'ventas')),
    variante         text        NOT NULL CHECK (variante IN ('gasto_real', 'gasto_previsto')),
    n                integer     NOT NULL CHECK (n >= 1),  -- semanas previstas comparadas
    mae_modelo       numeric     NOT NULL CHECK (mae_modelo >= 0),
    mae_referencia   numeric     NOT NULL CHECK (mae_referencia >= 0),
    cobertura        numeric     NOT NULL CHECK (cobertura >= 0 AND cobertura <= 1),
    mejora           boolean     NOT NULL,                 -- el modelo tiene menos error que la referencia
    desde_semana     date        NOT NULL,
    hasta_semana     date        NOT NULL,
    trends           boolean     NOT NULL,                 -- con Google Trends
    PRIMARY KEY (origen, dia, tipo, nivel, medida, variante)
);
GRANT SELECT ON panel_prevision_modelo, panel_prevision_semana, panel_prevision_validacion TO cdf_panel;
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS panel_prevision_validacion, panel_prevision_semana, panel_prevision_modelo")
