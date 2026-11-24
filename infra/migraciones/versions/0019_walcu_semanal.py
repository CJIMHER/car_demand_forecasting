"""Walcu por semanas: walcu_campana_semana sustituye a walcu_campana_mes (vacía en el servidor).

- walcu_campana_semana: hechos de solo añadir. Una fila por carga (run_id), semana (el lunes de la semana
  ISO del lead), origen, campaña de Walcu y tipo (VN, VO), con los mismos recuentos que la mensual. Recargar
  una semana la sustituye sin borrar nada: walcu_campana_semana_vigente da, para cada semana, solo las filas
  de la última carga que la trae.
- panel_campana_semana (contrato del panel) sustituye a panel_campana_mes: por semana y campaña de Meta, sus
  cifras de Meta y las de Walcu de la misma campaña. citas_completas / ventas_completas: la semana tenía, al
  cargarse, al menos lead_a_cita_dias / cita_a_venta_dias de antigüedad (config/campanas_walcu.yaml); si no,
  sus citas o sus ventas todavía pueden crecer (cohorte: se atribuyen a la semana del lead).
- panel_referencia_meta: por año ISO y tipo, sumas de semanas; las tasas y costes de cita y de venta, con
  sus propias sumas de solo las semanas completas para citas y para ventas.
- panel_walcu_sin_cruce: primera y última semana en vez de mes.

Las tres tablas del panel se rehacen enteras (ingesta/referencias_meta.py): se borran y se crean de nuevo.
El downgrade recrea las mensuales de la 0017, vacías.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-24
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

RECUENTOS = """
    leads            integer     NOT NULL CHECK (leads >= 0),
    leads_cercana    integer     NOT NULL CHECK (leads_cercana >= 0),
    leads_fuera      integer     NOT NULL CHECK (leads_fuera >= 0),
    leads_sin_zona   integer     NOT NULL CHECK (leads_sin_zona >= 0),
    citas            integer     NOT NULL CHECK (citas >= 0),
    ventas           integer     NOT NULL CHECK (ventas >= 0),
    num_inexistente  integer     NOT NULL CHECK (num_inexistente >= 0),"""

# Columnas de panel_campana_semana / panel_campana_mes después de la clave de tiempo
CAMPANA = """
    campana_id       text,                               -- NULL: campaña de Walcu sin campaña de Meta
    titulo           text        NOT NULL,
    ambito           text        CHECK (ambito IN ('VN', 'VO', 'ambos')),   -- NULL: sin regla
    tipo_campana     text        CHECK (tipo_campana IN ('modelo', 'general')),
    modelo           text,
    gasto_cents      bigint      NOT NULL DEFAULT 0 CHECK (gasto_cents >= 0),
    impresiones      bigint      NOT NULL DEFAULT 0 CHECK (impresiones >= 0),
    clics            bigint      NOT NULL DEFAULT 0 CHECK (clics >= 0),
    leads_meta       integer     CHECK (leads_meta >= 0),
    walcu            boolean     NOT NULL,              -- hay carga de Walcu de ese periodo
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
    CHECK (cruce IS NULL OR walcu),"""

REFERENCIA_SUMAS = """
    gasto_cents      bigint      NOT NULL CHECK (gasto_cents >= 0),
    leads_meta       integer     NOT NULL CHECK (leads_meta >= 0),
    leads            integer     NOT NULL CHECK (leads >= 0),     -- de Walcu (origen Facebook)
    citas            integer     NOT NULL CHECK (citas >= 0),
    ventas           integer     NOT NULL CHECK (ventas >= 0),
    leads_cercana    integer     NOT NULL CHECK (leads_cercana >= 0),
    leads_fuera      integer     NOT NULL CHECK (leads_fuera >= 0),
    leads_sin_zona   integer     NOT NULL CHECK (leads_sin_zona >= 0),
    num_inexistente  integer     NOT NULL CHECK (num_inexistente >= 0),"""

UPGRADE = f"""
DROP VIEW walcu_campana_mes_vigente;
DROP TABLE walcu_campana_mes;               -- vacía: la carga mensual no llegó a usarse en el servidor

CREATE TABLE walcu_campana_semana (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fecha_carga      timestamptz NOT NULL,
    run_id           text        NOT NULL,
    fichero_origen   text        NOT NULL,              -- solo el nombre del fichero, sin la ruta
    huella_fichero   text        NOT NULL,
    semana           date        NOT NULL CHECK (extract(isodow FROM semana) = 1),   -- lunes (semana ISO)
    origen           text        NOT NULL,
    campana          text        NOT NULL,
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO')),{RECUENTOS}
    UNIQUE (run_id, semana, origen, campana, tipo)
);
CREATE INDEX walcu_campana_semana_semana ON walcu_campana_semana (semana, fecha_carga DESC, id DESC);
CREATE INDEX walcu_campana_semana_huella ON walcu_campana_semana (huella_fichero);
-- Para cada semana, las filas de la última carga que la trae (recargar una semana la sustituye)
CREATE VIEW walcu_campana_semana_vigente AS
    SELECT w.* FROM walcu_campana_semana w
    JOIN (SELECT DISTINCT ON (semana) semana, run_id FROM walcu_campana_semana
          ORDER BY semana, fecha_carga DESC, id DESC) u
      ON u.semana = w.semana AND u.run_id = w.run_id;
CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON walcu_campana_semana FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON walcu_campana_semana FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();

DROP TABLE panel_campana_mes;
CREATE TABLE panel_campana_semana (
    dia              date        NOT NULL,              -- último día con datos de Meta al calcular
    origen           text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at     timestamptz NOT NULL DEFAULT now(),
    semana           date        NOT NULL CHECK (extract(isodow FROM semana) = 1),{CAMPANA}
    citas_completas  boolean,                            -- NULL sin carga de Walcu de la semana
    ventas_completas boolean,
    CHECK ((citas_completas IS NULL) = NOT walcu AND (ventas_completas IS NULL) = NOT walcu),
    UNIQUE NULLS NOT DISTINCT (origen, dia, semana, campana_id, titulo)
);

DROP TABLE panel_referencia_meta;
CREATE TABLE panel_referencia_meta (
    dia              date        NOT NULL,
    origen           text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at     timestamptz NOT NULL DEFAULT now(),
    anio             integer     NOT NULL,              -- año ISO de las semanas
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO', 'todo')),
    desde_semana     date        NOT NULL,
    hasta_semana     date        NOT NULL,
    semanas          integer     NOT NULL CHECK (semanas >= 1),{REFERENCIA_SUMAS}
    -- Solo las semanas completas para citas (lead a cita, coste por cita) …
    semanas_citas    integer     NOT NULL CHECK (semanas_citas >= 0),
    gasto_citas_cents bigint     NOT NULL CHECK (gasto_citas_cents >= 0),
    leads_citas      integer     NOT NULL CHECK (leads_citas >= 0),
    citas_citas      integer     NOT NULL CHECK (citas_citas >= 0),
    -- … y para ventas (cita a venta, coste por venta)
    semanas_ventas   integer     NOT NULL CHECK (semanas_ventas >= 0),
    gasto_ventas_cents bigint    NOT NULL CHECK (gasto_ventas_cents >= 0),
    citas_ventas     integer     NOT NULL CHECK (citas_ventas >= 0),
    ventas_ventas    integer     NOT NULL CHECK (ventas_ventas >= 0),
    PRIMARY KEY (origen, dia, anio, tipo)
);

DROP TABLE panel_walcu_sin_cruce;
CREATE TABLE panel_walcu_sin_cruce (
    fuente          text        NOT NULL CHECK (fuente IN ('meta', 'walcu')),
    campana         text        NOT NULL,
    primera_semana  date        NOT NULL,
    ultima_semana   date        NOT NULL,
    gasto_cents     bigint,                               -- Meta
    leads           integer,                              -- Walcu
    motivo          text        NOT NULL,
    calculado_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fuente, campana)
);

GRANT SELECT ON panel_campana_semana, panel_referencia_meta, panel_walcu_sin_cruce TO cdf_panel;
"""

DOWNGRADE = f"""
DROP TABLE IF EXISTS panel_walcu_sin_cruce;
DROP TABLE IF EXISTS panel_referencia_meta;
DROP TABLE IF EXISTS panel_campana_semana;
DROP VIEW IF EXISTS walcu_campana_semana_vigente;
DROP TABLE IF EXISTS walcu_campana_semana;

CREATE TABLE walcu_campana_mes (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fecha_carga      timestamptz NOT NULL,
    run_id           text        NOT NULL,
    fichero_origen   text        NOT NULL,
    huella_fichero   text        NOT NULL,
    mes              date        NOT NULL CHECK (extract(day FROM mes) = 1),
    origen           text        NOT NULL,
    campana          text        NOT NULL,
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO')),{RECUENTOS}
    UNIQUE (run_id, mes, origen, campana, tipo)
);
CREATE INDEX walcu_campana_mes_mes ON walcu_campana_mes (mes, fecha_carga DESC, id DESC);
CREATE VIEW walcu_campana_mes_vigente AS
    SELECT w.* FROM walcu_campana_mes w
    JOIN (SELECT DISTINCT ON (mes) mes, run_id FROM walcu_campana_mes ORDER BY mes, fecha_carga DESC, id DESC) u
      ON u.mes = w.mes AND u.run_id = w.run_id;
CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON walcu_campana_mes FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();
CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON walcu_campana_mes FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();

CREATE TABLE panel_campana_mes (
    dia              date        NOT NULL,
    origen           text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at     timestamptz NOT NULL DEFAULT now(),
    mes              date        NOT NULL CHECK (extract(day FROM mes) = 1),{CAMPANA}
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
    meses            integer     NOT NULL CHECK (meses >= 1),{REFERENCIA_SUMAS}
    PRIMARY KEY (origen, dia, anio, tipo)
);

CREATE TABLE panel_walcu_sin_cruce (
    fuente        text        NOT NULL CHECK (fuente IN ('meta', 'walcu')),
    campana       text        NOT NULL,
    primer_mes    date        NOT NULL,
    ultimo_mes    date        NOT NULL,
    gasto_cents   bigint,
    leads         integer,
    motivo        text        NOT NULL,
    calculado_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fuente, campana)
);

GRANT SELECT ON panel_campana_mes, panel_referencia_meta, panel_walcu_sin_cruce TO cdf_panel;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
