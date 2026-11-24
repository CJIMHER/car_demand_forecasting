"""Histórico de Meta, cargas de Walcu agregadas y referencias de coste.

- walcu_campana_mes: hechos de solo añadir. Una fila por carga (run_id), mes, origen, campaña de Walcu y
  tipo (VN, VO), con leads, leads por zona, citas, ventas y números inexistentes, ya agregados en el Mac
  (scripts/walcu_agregar.py). Sin datos personales: el fichero se rechaza entero si trae otra columna o un
  valor con aspecto de correo, teléfono o DNI/NIE (ingesta/walcu.py). Recargar un mes lo sustituye sin
  borrar nada: walcu_campana_mes_vigente da, para cada mes, solo las filas de la última carga que lo trae.
- campanas_meta_estado: estado de cada campaña de Meta (ACTIVE, PAUSED, ARCHIVED, DELETED…) tal como lo da
  la API en cada descarga del histórico. Solo añadir; una fila nueva solo si cambia. La vista _vigente da
  el último.
- panel_campana_mes (contrato del panel): por mes y campaña de Meta, el gasto, las impresiones, los clics y
  los leads de Meta, y los leads, citas y ventas de Walcu de la misma campaña (unidas por nombre
  normalizado, config/campanas_walcu.yaml). campana_id NULL: campaña de Walcu con origen Facebook sin
  campaña de Meta con la que casar. La rehace ingesta/referencias_meta.py.
- panel_referencia_meta (contrato del panel): por año y tipo (VN, VO, todo), las sumas con que se calculan
  coste por lead, por cita y por venta, lead a cita, cita a venta, % fuera de zona y % de número
  inexistente. Las de 2025 son la «media» de las reglas c y d (alertas/criterios.py).
- panel_walcu_sin_cruce: el informe de lo que no casa entre Meta y Walcu (make referencias-meta).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-24
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE TABLE walcu_campana_mes (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fecha_carga      timestamptz NOT NULL,
    run_id           text        NOT NULL,
    fichero_origen   text        NOT NULL,              -- solo el nombre del fichero, sin la ruta
    huella_fichero   text        NOT NULL,
    mes              date        NOT NULL CHECK (extract(day FROM mes) = 1),
    origen           text        NOT NULL,
    campana          text        NOT NULL,
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    leads            integer     NOT NULL CHECK (leads >= 0),
    leads_cercana    integer     NOT NULL CHECK (leads_cercana >= 0),
    leads_fuera      integer     NOT NULL CHECK (leads_fuera >= 0),
    leads_sin_zona   integer     NOT NULL CHECK (leads_sin_zona >= 0),
    citas            integer     NOT NULL CHECK (citas >= 0),
    ventas           integer     NOT NULL CHECK (ventas >= 0),
    num_inexistente  integer     NOT NULL CHECK (num_inexistente >= 0),
    UNIQUE (run_id, mes, origen, campana, tipo)
);
CREATE INDEX walcu_campana_mes_mes ON walcu_campana_mes (mes, fecha_carga DESC, id DESC);
-- Para cada mes, las filas de la última carga que lo trae (recargar un mes lo sustituye)
CREATE VIEW walcu_campana_mes_vigente AS
    SELECT w.* FROM walcu_campana_mes w
    JOIN (SELECT DISTINCT ON (mes) mes, run_id FROM walcu_campana_mes ORDER BY mes, fecha_carga DESC, id DESC) u
      ON u.mes = w.mes AND u.run_id = w.run_id;

CREATE TABLE campanas_meta_estado (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    descargado_at    timestamptz NOT NULL,
    run_id           text        NOT NULL,
    cuenta_id        text        NOT NULL,
    campana_id       text        NOT NULL,
    campana_nombre   text,
    estado           text,                               -- status
    estado_efectivo  text,                               -- effective_status
    huella           text        NOT NULL
);
CREATE INDEX campanas_meta_estado_clave ON campanas_meta_estado (cuenta_id, campana_id, id DESC);
CREATE VIEW campanas_meta_estado_vigente AS
    SELECT DISTINCT ON (cuenta_id, campana_id) * FROM campanas_meta_estado ORDER BY cuenta_id, campana_id, id DESC;

CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON walcu_campana_mes FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON walcu_campana_mes FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON campanas_meta_estado FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON campanas_meta_estado FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();

CREATE TABLE panel_campana_mes (
    dia              date        NOT NULL,              -- último día con datos de Meta al calcular
    origen           text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at     timestamptz NOT NULL DEFAULT now(),
    mes              date        NOT NULL CHECK (extract(day FROM mes) = 1),
    campana_id       text,                               -- NULL: campaña de Walcu sin campaña de Meta
    titulo           text        NOT NULL,
    ambito           text        CHECK (ambito IN ('VN', 'VO', 'ambos')),   -- NULL: sin regla
    tipo_campana     text        CHECK (tipo_campana IN ('modelo', 'general')),
    modelo           text,
    gasto_cents      bigint      NOT NULL DEFAULT 0 CHECK (gasto_cents >= 0),
    impresiones      bigint      NOT NULL DEFAULT 0 CHECK (impresiones >= 0),
    clics            bigint      NOT NULL DEFAULT 0 CHECK (clics >= 0),
    leads_meta       integer     CHECK (leads_meta >= 0),
    walcu            boolean     NOT NULL,              -- hay carga de Walcu de ese mes
    cruce            text        CHECK (cruce IN ('nombre', 'equivalencia')),  -- NULL: sin leads de Walcu que casen
    leads            integer     CHECK (leads >= 0),
    citas            integer     CHECK (citas >= 0),
    ventas           integer     CHECK (ventas >= 0),
    leads_vn         integer     CHECK (leads_vn >= 0),
    leads_vo         integer     CHECK (leads_vo >= 0),
    citas_vn         integer     CHECK (citas_vn >= 0),
    citas_vo         integer     CHECK (citas_vo >= 0),
    ventas_vn        integer     CHECK (ventas_vn >= 0),
    ventas_vo        integer     CHECK (ventas_vo >= 0),
    leads_cercana    integer     CHECK (leads_cercana >= 0),
    leads_fuera      integer     CHECK (leads_fuera >= 0),
    leads_sin_zona   integer     CHECK (leads_sin_zona >= 0),
    num_inexistente  integer     CHECK (num_inexistente >= 0),
    CHECK ((cruce IS NULL) = (leads IS NULL)),
    CHECK (cruce IS NULL OR walcu),
    UNIQUE NULLS NOT DISTINCT (origen, dia, mes, campana_id, titulo)
);

CREATE TABLE panel_referencia_meta (
    dia              date        NOT NULL,
    origen           text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at     timestamptz NOT NULL DEFAULT now(),
    anio             integer     NOT NULL,
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO', 'todo')),
    desde_mes        date        NOT NULL,
    hasta_mes        date        NOT NULL,
    meses            integer     NOT NULL CHECK (meses >= 1),
    gasto_cents      bigint      NOT NULL CHECK (gasto_cents >= 0),
    leads_meta       integer     NOT NULL CHECK (leads_meta >= 0),
    leads            integer     NOT NULL CHECK (leads >= 0),     -- de Walcu (origen Facebook)
    citas            integer     NOT NULL CHECK (citas >= 0),
    ventas           integer     NOT NULL CHECK (ventas >= 0),
    leads_cercana    integer     NOT NULL CHECK (leads_cercana >= 0),
    leads_fuera      integer     NOT NULL CHECK (leads_fuera >= 0),
    leads_sin_zona   integer     NOT NULL CHECK (leads_sin_zona >= 0),
    num_inexistente  integer     NOT NULL CHECK (num_inexistente >= 0),
    PRIMARY KEY (origen, dia, anio, tipo)
);

CREATE TABLE panel_walcu_sin_cruce (
    fuente        text        NOT NULL CHECK (fuente IN ('meta', 'walcu')),
    campana       text        NOT NULL,
    primer_mes    date        NOT NULL,
    ultimo_mes    date        NOT NULL,
    gasto_cents   bigint,                               -- Meta
    leads         integer,                              -- Walcu
    motivo        text        NOT NULL,
    calculado_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fuente, campana)
);

GRANT SELECT ON panel_campana_mes, panel_referencia_meta, panel_walcu_sin_cruce TO cdf_panel;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS panel_walcu_sin_cruce;
DROP TABLE IF EXISTS panel_referencia_meta;
DROP TABLE IF EXISTS panel_campana_mes;
DROP VIEW IF EXISTS campanas_meta_estado_vigente;
DROP TABLE IF EXISTS campanas_meta_estado;
DROP VIEW IF EXISTS walcu_campana_mes_vigente;
DROP TABLE IF EXISTS walcu_campana_mes;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
