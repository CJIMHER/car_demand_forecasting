"""Contrato de datos del panel: tablas panel_* de Dirección, Ventas y Marketing.

El significado de cada tabla y columna (unidad, grano, fuente que la rellenará y en qué hito) está
en docs/contrato_panel.md. Reglas comunes:

- Todas llevan dia, origen ('real' o 'ejemplo') y calculado_at. El origen forma parte de la clave:
  las filas de ejemplo y las reales conviven sin pisarse, y el panel nunca las mezcla en un bloque
  (panel/contrato.py).
- Sin datos personales: leads solo agregados por día, modelo y campaña; vehículos por número de stock.
- Toda salida de un modelo lleva p10, p50 y p90, modelo_version y el n en que se apoya.
- Son derivadas, como panel_mercado_*: quien las rellena sustituye sus filas; el panel solo las lee
  (cdf_panel: SELECT).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-15
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

COMUNES = """
    dia            date        NOT NULL,
    origen         text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at   timestamptz NOT NULL DEFAULT now(),"""

TABLAS = {
    # Walcu (hito 1): stock propio por vehículo y día
    "panel_stock_vehiculo": """
    stock_id         text    NOT NULL,           -- número de stock de Walcu
    tipo             text    NOT NULL CHECK (tipo IN ('VN', 'VO')),
    modelo           text    NOT NULL,
    version          text,
    dias_exposicion  integer NOT NULL CHECK (dias_exposicion >= 0),
    precio_cents     bigint  CHECK (precio_cents >= 0),
    PRIMARY KEY (origen, dia, stock_id)""",

    # Walcu (ventas) + referencia del hito 4 / modelo del hito 5 (banda): acumulado del mes
    "panel_ventas_mes": """
    tipo               text     NOT NULL CHECK (tipo IN ('VN', 'VO', 'todo')),
    dia_mes            smallint NOT NULL CHECK (dia_mes BETWEEN 1 AND 31),
    ventas_acumuladas  integer  CHECK (ventas_acumuladas >= 0),   -- NULL en los días que aún no han pasado
    previsto_p10       numeric  NOT NULL,
    previsto_p50       numeric  NOT NULL,
    previsto_p90       numeric  NOT NULL,
    modelo_version     text     NOT NULL,
    n                  integer  NOT NULL CHECK (n >= 0),
    CHECK (previsto_p10 <= previsto_p50 AND previsto_p50 <= previsto_p90),
    PRIMARY KEY (origen, dia, tipo, dia_mes)""",

    # Motor de alertas (hito 4): alertas abiertas ese día
    "panel_alertas": """
    alerta_id      text   NOT NULL,
    regla          text   NOT NULL,
    tema           text   NOT NULL CHECK (tema IN ('ventas', 'stock', 'campanas', 'mercado', 'datos')),
    tipo           text   CHECK (tipo IN ('VN', 'VO')),          -- NULL: afecta a los dos
    destinatarios  text[] NOT NULL,                               -- roles del panel
    nivel          text   NOT NULL CHECK (nivel IN ('aviso', 'info')),
    texto          text   NOT NULL,
    abierta_desde  date   NOT NULL,
    stock_id       text,
    campana_id     text,
    modelo         text,
    PRIMARY KEY (origen, dia, alerta_id)""",

    # Modelo de supervivencia (hito 3): riesgo de cada vehículo en stock
    "panel_riesgo_vehiculo": """
    stock_id            text    NOT NULL,
    tipo                text    NOT NULL CHECK (tipo IN ('VN', 'VO')),
    modelo              text    NOT NULL,
    version             text,
    detalle             text,
    dias_exposicion     integer NOT NULL CHECK (dias_exposicion >= 0),
    grupo               text    NOT NULL,
    p_venta90_p10       numeric NOT NULL CHECK (p_venta90_p10 BETWEEN 0 AND 1),
    p_venta90_p50       numeric NOT NULL CHECK (p_venta90_p50 BETWEEN 0 AND 1),
    p_venta90_p90       numeric NOT NULL CHECK (p_venta90_p90 BETWEEN 0 AND 1),
    precio_mercado_pct  numeric,                       -- NULL: sin similares en el mercado
    similares_n         integer CHECK (similares_n >= 0),
    campana_activa      boolean NOT NULL,
    sugerencia          text    NOT NULL CHECK (sugerencia IN ('campana', 'revisar_precio', 'mantener')),
    leads_14            integer CHECK (leads_14 >= 0),
    leads_14_previos    integer CHECK (leads_14_previos >= 0),
    modelo_version      text    NOT NULL,
    n                   integer NOT NULL CHECK (n >= 0),
    CHECK (p_venta90_p10 <= p_venta90_p50 AND p_venta90_p50 <= p_venta90_p90),
    PRIMARY KEY (origen, dia, stock_id)""",

    # Modelo de supervivencia (hito 3): curva de cada grupo
    "panel_curva_grupo": """
    grupo           text      NOT NULL,
    descripcion     text      NOT NULL,
    t_dias          integer[] NOT NULL,
    s_p10           numeric[] NOT NULL,
    s_p50           numeric[] NOT NULL,
    s_p90           numeric[] NOT NULL,
    mediana_p10     numeric,                           -- NULL: la curva no baja del 50 %
    mediana_p50     numeric,
    mediana_p90     numeric,
    n               integer   NOT NULL CHECK (n >= 0), -- episodios
    n_ventas        integer   NOT NULL CHECK (n_ventas >= 0),
    modelo_version  text      NOT NULL,
    CHECK (cardinality(t_dias) = cardinality(s_p50) AND cardinality(s_p10) = cardinality(s_p50)
           AND cardinality(s_p90) = cardinality(s_p50)),
    PRIMARY KEY (origen, dia, grupo)""",

    # Walcu (hito 1; campaña cruzada con Meta en el hito 2): leads agregados
    "panel_leads_dia": """
    modelo      text    NOT NULL,
    campana_id  text,                                  -- NULL: sin campaña
    leads       integer NOT NULL CHECK (leads >= 0),
    citas       integer NOT NULL CHECK (citas >= 0),
    ventas      integer NOT NULL CHECK (ventas >= 0),
    UNIQUE NULLS NOT DISTINCT (origen, dia, modelo, campana_id)""",

    # Meta (hito 2): gasto por campaña y día
    "panel_campana_dia": """
    campana_id    text    NOT NULL,
    titulo        text    NOT NULL,
    tipo_campana  text    NOT NULL CHECK (tipo_campana IN ('modelo', 'general')),
    modelo        text,
    inicio        date    NOT NULL,
    gasto_cents   bigint  NOT NULL CHECK (gasto_cents >= 0),
    impresiones   integer NOT NULL CHECK (impresiones >= 0),
    clics         integer NOT NULL CHECK (clics >= 0),
    CHECK ((tipo_campana = 'modelo') = (modelo IS NOT NULL)),
    PRIMARY KEY (origen, dia, campana_id)""",

    # Modelo de salud de campañas (hito 4): cada eslabón frente a su referencia a los mismos días de vida
    "panel_campana_salud": """
    campana_id      text    NOT NULL,
    dias_vida       integer NOT NULL CHECK (dias_vida >= 0),
    eslabon         text    NOT NULL CHECK (eslabon IN ('entrega', 'anuncio', 'web', 'calidad_lead', 'stock')),
    medida          text    NOT NULL,
    unidad          text    NOT NULL,
    mejor           text    NOT NULL CHECK (mejor IN ('alto', 'bajo')),
    valor           numeric,
    ref_p10         numeric,
    ref_p50         numeric,
    ref_p90         numeric,
    prob_peor       numeric CHECK (prob_peor BETWEEN 0 AND 1),
    estado          text    NOT NULL CHECK (estado IN ('bien', 'vigilar', 'revisar', 'sin_datos')),
    modelo_version  text    NOT NULL,
    n               integer NOT NULL CHECK (n >= 0),
    CHECK (estado = 'sin_datos' OR (valor IS NOT NULL AND prob_peor IS NOT NULL AND ref_p50 IS NOT NULL)),
    PRIMARY KEY (origen, dia, campana_id, eslabon)""",

    # Reglas de recomendación (hito 4): una por modelo
    "panel_recomendacion_modelo": """
    modelo          text    NOT NULL,
    accion          text    NOT NULL CHECK (accion IN ('aumentar', 'mantener', 'reducir', 'cambiar')),
    motivo          text    NOT NULL,
    modelo_version  text    NOT NULL,
    n               integer NOT NULL CHECK (n >= 0),
    PRIMARY KEY (origen, dia, modelo)""",

    # Google Trends, gt_relativo (hito 2): atención frente a los rivales
    "panel_atencion_relativa": """
    modelo           text    NOT NULL,
    mes              date    NOT NULL,
    relativo_puntos  numeric NOT NULL,
    n_competidores   integer NOT NULL CHECK (n_competidores >= 2),
    PRIMARY KEY (origen, dia, modelo)""",

    # Modelo de estacionalidad y calendario (hito 5): cómo viene cada una de las seis semanas siguientes
    "panel_semana_campana": """
    semana          date    NOT NULL,                  -- lunes
    nivel           text    NOT NULL CHECK (nivel IN ('favorable', 'normal', 'poco_favorable')),
    indice_p10      numeric NOT NULL,
    indice_p50      numeric NOT NULL,
    indice_p90      numeric NOT NULL,
    motivos         jsonb   NOT NULL,                  -- [{"tipo": ..., "texto": ...}]
    modelo_version  text    NOT NULL,
    n               integer NOT NULL CHECK (n >= 0),
    CHECK (indice_p10 <= indice_p50 AND indice_p50 <= indice_p90),
    CHECK (jsonb_typeof(motivos) = 'array'),
    PRIMARY KEY (origen, dia, semana)""",

    # Modelo de referencia sin campaña (hito 5): qué dieron las campañas terminadas
    "panel_campana_resultado": """
    campana_id         text    NOT NULL,
    titulo             text    NOT NULL,
    inicio             date    NOT NULL,
    fin                date    NOT NULL,
    gasto_cents        bigint  NOT NULL CHECK (gasto_cents >= 0),
    leads              integer NOT NULL CHECK (leads >= 0),
    leads_sin_p10      numeric NOT NULL,
    leads_sin_p50      numeric NOT NULL,
    leads_sin_p90      numeric NOT NULL,
    ventas             integer NOT NULL CHECK (ventas >= 0),
    ventas_sin_p10     numeric NOT NULL,
    ventas_sin_p50     numeric NOT NULL,
    ventas_sin_p90     numeric NOT NULL,
    veredicto_leads    text    NOT NULL CHECK (veredicto_leads IN ('por_encima', 'normal', 'por_debajo')),
    veredicto_ventas   text    NOT NULL CHECK (veredicto_ventas IN ('por_encima', 'normal', 'por_debajo')),
    modelo_version     text    NOT NULL,
    n                  integer NOT NULL CHECK (n >= 0),
    CHECK (inicio <= fin),
    CHECK (leads_sin_p10 <= leads_sin_p50 AND leads_sin_p50 <= leads_sin_p90),
    CHECK (ventas_sin_p10 <= ventas_sin_p50 AND ventas_sin_p50 <= ventas_sin_p90),
    PRIMARY KEY (origen, dia, campana_id)""",
}


def upgrade() -> None:
    for nombre, columnas in TABLAS.items():
        op.execute(f"CREATE TABLE {nombre} ({COMUNES}{columnas}\n)")
        op.execute(f"GRANT SELECT ON {nombre} TO cdf_panel")


def downgrade() -> None:
    for nombre in reversed(list(TABLAS)):
        op.execute(f"DROP TABLE IF EXISTS {nombre}")
