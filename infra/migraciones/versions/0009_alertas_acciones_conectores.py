"""Motor de alertas, botones de Ventas y conectores de Meta y GA4 (hito 1b, sesión 3).

Motor de alertas (alertas/motor.py, cron de las 09:15):
- alertas: una fila por día de evaluación, regla, rol y clave de lo que la dispara (modelo,
  segmento, campaña…), con su texto, sus cifras en JSON, su origen y su estado: «nueva» el primer
  día, «abierta» mientras se sigue cumpliendo y «cerrada» el día que deja de cumplirse. Es derivada:
  recalcular un día sustituye ese día. Una alerta calculada sobre filas de ejemplo solo puede ir al
  rol técnico (lo exige la base).
- alertas_evaluacion: qué reglas se evaluaron cada día y con qué origen. Con el formato de las
  tablas del contrato (dia, origen, calculado_at): el bloque de alertas de las vistas es real en
  cuanto una regla se ha evaluado con datos reales, aunque no salte ninguna.
- alertas_salida: qué se envió por Telegram, a quién y cuándo, y si falló. Solo se añaden filas.

Botones de Ventas:
- acciones: «Pedir campaña a marketing» y «Revisar precio» (vehículo, acción, usuario del panel,
  fecha y lo que el panel mostraba). Solo se añaden filas; el panel (cdf_panel) puede leer e
  insertar, nada más. El aviso por Telegram lo envía cdf_jobs (el panel no tiene el token).

Mercado:
- panel_mercado_competidor: anuncios y precio mediano de cada concesionario de la provincia propia
  por modelo y por modelo + acabado. Derivada (la rellena el precálculo); la usa la alerta de
  presión de un competidor.

Conectores (inactivos hasta tener credenciales):
- campanas_meta_dia y ga4_trafico_dia: hechos de solo añadir. Cada descarga recarga los últimos 7
  días; una fila solo se añade si su contenido cambia (huella), así que una corrección de Meta o de
  GA4 queda como versión nueva sin duplicar las que no cambian. Las vistas *_vigente dan la última.
- panel_campanas_sin_regla: campañas de Meta y GA4 sin regla en config/campanas.yaml (derivada).

fuentes_estado admite el tipo «alertas» y «descarga» y el estado «sin_credenciales».

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-21
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

ROLES = "'direccion', 'ventas', 'marketing', 'tecnico'"

UPGRADE = rf"""
ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_tipo_check;
ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_tipo_check
    CHECK (tipo IN ('carga', 'comprobacion', 'copia', 'aviso', 'precalculo', 'alertas', 'descarga'));
ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_estado_check;
ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_estado_check
    CHECK (estado IN ('ok', 'sin_captura', 'sin_cargar', 'error', 'sin_credenciales'));

-- ------------------------------------------------------------------ motor de alertas
CREATE TABLE alertas (
    id             bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fecha          date        NOT NULL,                -- día de evaluación
    regla          text        NOT NULL,                -- id de alertas/reglas.yaml
    rol            text        NOT NULL CHECK (rol IN ({ROLES})),
    clave          text        NOT NULL,                -- lo que la dispara: modelo, segmento, campaña…
    tema           text        NOT NULL CHECK (tema IN ('ventas', 'stock', 'campanas', 'mercado', 'datos')),
    nivel          text        NOT NULL CHECK (nivel IN ('aviso', 'info')),
    tipo           text        CHECK (tipo IN ('VN', 'VO')),   -- NULL: afecta a los dos
    texto          text        NOT NULL,                -- el motivo en una frase, con sus números
    cifras         jsonb       NOT NULL DEFAULT '{{}}'::jsonb CHECK (jsonb_typeof(cifras) = 'object'),
    origen         text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    estado         text        NOT NULL CHECK (estado IN ('nueva', 'abierta', 'cerrada')),
    abierta_desde  date        NOT NULL,
    calculado_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (fecha, regla, rol, clave, origen),
    CHECK (abierta_desde <= fecha),
    CHECK ((estado = 'nueva') = (abierta_desde = fecha) OR estado = 'cerrada'),
    CHECK (origen = 'real' OR rol = 'tecnico')      -- lo calculado sobre ejemplo, solo al técnico
);
CREATE INDEX alertas_por_fecha ON alertas (fecha, rol, estado);

CREATE TABLE alertas_evaluacion (
    dia           date        NOT NULL,
    origen        text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at  timestamptz NOT NULL DEFAULT now(),
    regla         text        NOT NULL,
    estado        text        NOT NULL CHECK (estado IN ('evaluada', 'sin_evaluar')),
    motivo        text,                                 -- por qué no se evaluó (falta la captura de hace 7 días…)
    disparadas    integer     NOT NULL CHECK (disparadas >= 0),
    PRIMARY KEY (origen, dia, regla)
);

-- ------------------------------------------------------------------ botones de Ventas
CREATE TABLE acciones (
    id                bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pedido_at         timestamptz NOT NULL DEFAULT now(),
    stock_id          text        NOT NULL,             -- número de stock (panel_riesgo_vehiculo)
    accion            text        NOT NULL CHECK (accion IN ('campana', 'revisar_precio')),
    usuario           text        NOT NULL,             -- usuario del panel que la pide
    origen            text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    tipo              text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    vehiculo          text        NOT NULL,             -- modelo y versión, como los mostraba el panel
    dias_exposicion   integer     NOT NULL CHECK (dias_exposicion >= 0),
    p_venta90_p10     numeric     NOT NULL CHECK (p_venta90_p10 BETWEEN 0 AND 1),
    p_venta90_p50     numeric     NOT NULL CHECK (p_venta90_p50 BETWEEN 0 AND 1),
    p_venta90_p90     numeric     NOT NULL CHECK (p_venta90_p90 BETWEEN 0 AND 1)
);
CREATE INDEX acciones_vehiculo ON acciones (stock_id, accion, pedido_at DESC);

CREATE TABLE alertas_salida (
    id             bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fecha          date        NOT NULL,
    que            text        NOT NULL CHECK (que IN ('alertas', 'accion')),
    rol            text        NOT NULL CHECK (rol IN ({ROLES})),   -- a quién va dirigido
    destino        text        NOT NULL CHECK (destino IN ({ROLES})), -- chat usado (técnico si falta el del rol)
    texto          text        NOT NULL,
    alertas        jsonb       NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(alertas) = 'array'),
    accion_id      bigint      REFERENCES acciones (id),
    estado         text        NOT NULL CHECK (estado IN ('enviado', 'error')),
    error          text,
    intentos       integer,
    registrado_at  timestamptz NOT NULL DEFAULT now(),
    CHECK ((que = 'accion') = (accion_id IS NOT NULL))
);
CREATE INDEX alertas_salida_fecha ON alertas_salida (fecha, rol);
CREATE INDEX alertas_salida_accion ON alertas_salida (accion_id);

CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON acciones FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON acciones FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON alertas_salida FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON alertas_salida FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();

-- ------------------------------------------------------------------ mercado: por concesionario y modelo
CREATE TABLE panel_mercado_competidor (
    dia                   date    NOT NULL,
    tipo                  text    NOT NULL CHECK (tipo IN ('VN', 'VO')),
    dealer_id             text    NOT NULL,
    concesionario         text    NOT NULL,
    es_propio             boolean NOT NULL,
    modelo                text    NOT NULL,
    acabado               text,                         -- NULL: todo el modelo
    anuncios              integer NOT NULL CHECK (anuncios > 0),
    n_precio              integer NOT NULL CHECK (n_precio >= 0),
    precio_mediano_cents  bigint,
    calculado_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (dia, tipo, dealer_id, modelo, acabado),
    CHECK ((precio_mediano_cents IS NULL) = (n_precio = 0))
);

-- ------------------------------------------------------------------ conectores (hechos)
CREATE TABLE campanas_meta_dia (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    descargado_at    timestamptz NOT NULL,
    run_id           text        NOT NULL,
    origen           text        NOT NULL,              -- API y versión de la que sale la fila
    cuenta_id        text        NOT NULL,
    dia              date        NOT NULL,
    nivel            text        NOT NULL CHECK (nivel IN ('campana', 'conjunto', 'anuncio')),
    campana_id       text        NOT NULL,
    campana_nombre   text,
    conjunto_id      text,
    conjunto_nombre  text,
    anuncio_id       text,
    anuncio_nombre   text,
    moneda           text,
    gasto_cents      bigint      NOT NULL CHECK (gasto_cents >= 0),
    impresiones      bigint      NOT NULL CHECK (impresiones >= 0),
    alcance          bigint      CHECK (alcance >= 0),
    frecuencia       numeric,
    clics            bigint      NOT NULL CHECK (clics >= 0),
    cpm_cents        numeric,
    leads            integer     CHECK (leads >= 0),    -- NULL: la campaña no tiene acciones de lead
    acciones_lead    jsonb       NOT NULL DEFAULT '{{}}'::jsonb CHECK (jsonb_typeof(acciones_lead) = 'object'),
    huella           text        NOT NULL,              -- contenido de la fila: una corrección es otra huella
    CHECK ((nivel = 'campana') = (conjunto_id IS NULL)),
    CHECK ((nivel = 'anuncio') = (anuncio_id IS NOT NULL))
);
CREATE INDEX campanas_meta_dia_clave ON campanas_meta_dia (nivel, dia, campana_id, conjunto_id, anuncio_id, id DESC);
CREATE VIEW campanas_meta_dia_vigente AS
    SELECT DISTINCT ON (nivel, dia, campana_id, conjunto_id, anuncio_id) *
    FROM campanas_meta_dia ORDER BY nivel, dia, campana_id, conjunto_id, anuncio_id, id DESC;

CREATE TABLE ga4_trafico_dia (
    id                    bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    descargado_at         timestamptz NOT NULL,
    run_id                text        NOT NULL,
    origen                text        NOT NULL,
    propiedad_id          text        NOT NULL,
    dia                   date        NOT NULL,
    fuente                text        NOT NULL,         -- sessionSource
    medio                 text        NOT NULL,         -- sessionMedium
    campana               text        NOT NULL,         -- sessionCampaignName (utm_campaign)
    pagina_destino        text        NOT NULL,         -- landingPage: ruta, sin parámetros
    sesiones              bigint      NOT NULL CHECK (sesiones >= 0),
    sesiones_interaccion  bigint      NOT NULL CHECK (sesiones_interaccion >= 0),
    eventos_clave         numeric     NOT NULL CHECK (eventos_clave >= 0),
    huella                text        NOT NULL
);
CREATE INDEX ga4_trafico_dia_clave ON ga4_trafico_dia (dia, fuente, medio, campana, pagina_destino, id DESC);
CREATE VIEW ga4_trafico_dia_vigente AS
    SELECT DISTINCT ON (propiedad_id, dia, fuente, medio, campana, pagina_destino) *
    FROM ga4_trafico_dia ORDER BY propiedad_id, dia, fuente, medio, campana, pagina_destino, id DESC;

CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON campanas_meta_dia FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON campanas_meta_dia FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON ga4_trafico_dia FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON ga4_trafico_dia FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();

CREATE TABLE panel_campanas_sin_regla (
    fuente        text        NOT NULL CHECK (fuente IN ('meta', 'ga4')),
    campana       text        NOT NULL,
    primer_dia    date        NOT NULL,
    ultimo_dia    date        NOT NULL,
    gasto_cents   bigint,                               -- Meta
    sesiones      bigint,                               -- GA4
    calculado_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fuente, campana)
);

-- ------------------------------------------------------------------ permisos del panel
GRANT SELECT ON alertas, alertas_evaluacion, alertas_salida, panel_mercado_competidor,
    panel_campanas_sin_regla TO cdf_panel;
GRANT SELECT, INSERT ON acciones TO cdf_panel;
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS panel_campanas_sin_regla;
DROP VIEW IF EXISTS ga4_trafico_dia_vigente;
DROP TABLE IF EXISTS ga4_trafico_dia;
DROP VIEW IF EXISTS campanas_meta_dia_vigente;
DROP TABLE IF EXISTS campanas_meta_dia;
DROP TABLE IF EXISTS panel_mercado_competidor;
DROP TABLE IF EXISTS alertas_salida;
DROP TABLE IF EXISTS acciones;
DROP TABLE IF EXISTS alertas_evaluacion;
DROP TABLE IF EXISTS alertas;
-- fuentes_estado es de solo añadir: si ya tiene filas de tipo «alertas» o «descarga», o en estado
-- «sin_credenciales», las restricciones de 0008 no se pueden volver a poner y la bajada se detiene.
ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_estado_check;
ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_estado_check
    CHECK (estado IN ('ok', 'sin_captura', 'sin_cargar', 'error'));
ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_tipo_check;
ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_tipo_check
    CHECK (tipo IN ('carga', 'comprobacion', 'copia', 'aviso', 'precalculo'));
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
