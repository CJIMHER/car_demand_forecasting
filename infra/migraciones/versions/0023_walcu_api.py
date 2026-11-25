"""API de Walcu: coches, leads de venta y citas (ingesta/walcu_api.py), y lo que se deriva de ellos.

Hechos de solo añadir (versionados por huella, como Meta y GA4: volver a descargar no duplica; las vistas
*_vigente dan la última versión de cada id de Walcu). Ningún nombre, teléfono, correo ni dirección: clientes,
comerciales, matrículas y bastidores van como HMAC-SHA256 (*_seudo).
- walcu_api_marca: marca de agua por recurso (hasta dónde se ha descargado) y, en la carga inicial, su primer día.
- walcu_api_vehiculo, walcu_api_vehiculo_estado (historia de estados), walcu_api_vehiculo_precio_dia (nuestra
  foto diaria del precio de los publicados: Walcu no fecha las bajadas).
- walcu_api_lead, walcu_api_lead_estado (historia de estados con el comercial seudonimizado), walcu_api_cita.

- walcu_api_lead lleva la zona (cercana, fuera o sin_dato) y la provincia (dos primeros dígitos) del código
  postal del cliente, nunca el código postal.
- walcu_api_cp_semana: leads, citas y ventas por código postal completo, semana del lead y tipo, como sumas de
  cambios (cada carga añade lo que cambia; la vista walcu_api_cp_semana_total da la suma). Sin el lead, sin el
  cliente: el código postal solo está aquí. walcu_api_cp_contado: lo ya sumado de cada lead (sin el código
  postal), para sumar solo la diferencia cuando el lead vuelve a descargarse.

walcu_campana_semana gana la columna fuente (csv o api): ingesta/walcu_crm.py la genera desde la API cuando el
mapeo de estados y sedes está definido. La vista vigente se rehace para que la traiga. También gana
ventas_con_cita (ventas de leads con una cita a la que el cliente asistió antes de la venta: la tasa de cita a
venta, comparable con el CSV, en el que toda venta tiene cita; en las filas del CSV queda vacía y vale ventas);
ventas son todas (coste por venta y Dirección). La misma pareja en panel_campana_semana, panel_referencia_meta y
panel_ventas_crm.

fuentes_estado admite el estado sin_configurar (la API de Walcu sin WALCU_API_KEY: no es un error).

Del contrato del panel:
- panel_ventas_crm (nueva): ventas del CRM por mes de venta, tipo y modelo (Dirección, «Ventas del mes»).
- panel_stock_vehiculo: + publicado y fecha_entrada (stock del CRM con su fecha de entrada real).
- panel_precio_vo: + fecha_entrada y dias_stock (el coche de la web enlazado con el CRM por el bastidor
  seudonimizado: días en stock reales en vez de días publicado).

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-24
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

COMUNES = """
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           text        NOT NULL,
    capturado_at     timestamptz NOT NULL,
    id_walcu         text        NOT NULL,              -- _id de Walcu (no es un dato personal)
    huella           text        NOT NULL,              -- de todo lo demás: solo se añade si cambia
    actualizado_at   timestamptz,                       -- updated_at de Walcu"""


def _solo_anadir(tabla: str) -> str:
    return (f"CREATE TRIGGER solo_anadir BEFORE UPDATE OR DELETE ON {tabla} FOR EACH ROW EXECUTE FUNCTION cdf_solo_anadir();\n"
            f"CREATE TRIGGER sin_truncate BEFORE TRUNCATE ON {tabla} FOR EACH STATEMENT EXECUTE FUNCTION cdf_solo_anadir();\n")


def _vigente(tabla: str) -> str:
    return (f"CREATE INDEX {tabla}_clave ON {tabla} (id_walcu, id DESC);\n"
            f"CREATE VIEW {tabla}_vigente AS SELECT DISTINCT ON (id_walcu) * FROM {tabla} ORDER BY id_walcu, id DESC;\n")


UPGRADE = f"""
CREATE TABLE walcu_api_marca (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recurso          text        NOT NULL CHECK (recurso IN ('cars', 'saleleads', 'dates')),
    marca            timestamptz NOT NULL,              -- lo siguiente se pide desde aquí (menos el solape)
    run_id           text        NOT NULL,
    cobertura_desde  date,                              -- carga inicial: su primer día
    anotado_at       timestamptz NOT NULL DEFAULT now()
);
{_solo_anadir("walcu_api_marca")}
CREATE TABLE walcu_api_vehiculo ({COMUNES}
    tipo             text,                              -- new, used, demo, km0
    marca            text,
    modelo           text,
    version          text,
    combustible      text,
    codigo_version   text,
    location         text,
    fecha_entrada    date,                              -- purchase_date o, sin ella, created_at (Madrid)
    fecha_entrada_origen text CHECK (fecha_entrada_origen IN ('purchase_date', 'created_at')),
    publicado        boolean,
    despublicado_at  timestamptz,
    precio_cents     bigint,
    km               integer,
    anio             integer,
    color            text,
    estado           text,
    estado_at        timestamptz,
    matricula_seudo  text,                              -- HMAC-SHA256
    bastidor_seudo   text,                              -- HMAC-SHA256
    creado_at        timestamptz
);
{_vigente("walcu_api_vehiculo")}{_solo_anadir("walcu_api_vehiculo")}
CREATE TABLE walcu_api_vehiculo_estado (
    vehiculo_id      text        NOT NULL,
    estado           text        NOT NULL,
    fecha            timestamptz NOT NULL,
    run_id           text        NOT NULL,
    PRIMARY KEY (vehiculo_id, estado, fecha)
);
{_solo_anadir("walcu_api_vehiculo_estado")}
CREATE TABLE walcu_api_vehiculo_precio_dia (
    vehiculo_id      text        NOT NULL,
    dia              date        NOT NULL,
    precio_cents     bigint      NOT NULL,
    run_id           text        NOT NULL,
    PRIMARY KEY (vehiculo_id, dia)
);
{_solo_anadir("walcu_api_vehiculo_precio_dia")}
CREATE TABLE walcu_api_lead ({COMUNES}
    cliente_seudo    text,                              -- HMAC-SHA256 de client_id
    alta_at          timestamptz,
    origen_source    text,
    origen_medium    text,
    origen_campaign  text,
    location         text,
    estado           text,
    estado_at        timestamptz,
    comercial_seudo  text,                              -- HMAC-SHA256 del comercial asignado
    vehiculo_id      text,                              -- car_list: el primero
    tipo_vehiculo    text,
    marca_vehiculo   text,
    modelo_vehiculo  text,
    coches           integer     NOT NULL DEFAULT 0,
    zona             text        CHECK (zona IN ('cercana', 'fuera', 'sin_dato')),  -- del código postal
    provincia        text        CHECK (provincia ~ '^[0-9]{{2}}$')                 -- sus dos primeros dígitos
);
{_vigente("walcu_api_lead")}{_solo_anadir("walcu_api_lead")}
CREATE TABLE walcu_api_lead_estado (
    lead_id          text        NOT NULL,
    estado           text        NOT NULL,
    fecha            timestamptz NOT NULL,
    usuario_seudo    text,                              -- HMAC-SHA256 del usuario de la transición
    run_id           text        NOT NULL,
    PRIMARY KEY (lead_id, estado, fecha)
);
{_solo_anadir("walcu_api_lead_estado")}
CREATE TABLE walcu_api_cita ({COMUNES}
    lead_id          text,
    lead_tipo        text,                              -- salelead, appraisallead, aftersalelead
    cliente_seudo    text,
    vehiculo_id      text,
    inicio_at        timestamptz,
    asistio          boolean,                           -- showed_up
    estado           text
);
{_vigente("walcu_api_cita")}{_solo_anadir("walcu_api_cita")}
CREATE INDEX walcu_api_cita_lead ON walcu_api_cita (lead_id);

-- Código postal: solo agregado (nunca junto al lead ni al cliente)
CREATE TABLE walcu_api_cp_semana (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           text        NOT NULL,
    registrado_at    timestamptz NOT NULL DEFAULT now(),
    codigo_postal    text        CHECK (codigo_postal ~ '^[0-9]{{5}}$'),   -- vacío: sin código postal válido
    semana           date        NOT NULL CHECK (extract(isodow FROM semana) = 1),
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    leads            integer     NOT NULL,              -- cambios: pueden ser negativos
    citas            integer     NOT NULL,
    ventas           integer     NOT NULL,
    ventas_con_cita  integer     NOT NULL
);
{_solo_anadir("walcu_api_cp_semana")}
CREATE VIEW walcu_api_cp_semana_total AS
    SELECT codigo_postal, semana, tipo, sum(leads)::integer AS leads, sum(citas)::integer AS citas,
           sum(ventas)::integer AS ventas, sum(ventas_con_cita)::integer AS ventas_con_cita
    FROM walcu_api_cp_semana GROUP BY 1, 2, 3
    HAVING sum(leads) <> 0 OR sum(citas) <> 0 OR sum(ventas) <> 0 OR sum(ventas_con_cita) <> 0;
CREATE TABLE walcu_api_cp_contado (
    id               bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           text        NOT NULL,
    lead_id          text        NOT NULL,
    semana           date,                              -- vacía: el lead ya no cuenta (excluido)
    tipo             text        CHECK (tipo IN ('VN', 'VO')),
    con_cp           boolean     NOT NULL,              -- se sumó con código postal o sin él (nunca cuál)
    citas            integer     NOT NULL,
    ventas           integer     NOT NULL,
    ventas_con_cita  integer     NOT NULL
);
{_solo_anadir("walcu_api_cp_contado")}
CREATE INDEX walcu_api_cp_contado_clave ON walcu_api_cp_contado (lead_id, id DESC);
CREATE VIEW walcu_api_cp_contado_vigente AS
    SELECT DISTINCT ON (lead_id) * FROM walcu_api_cp_contado ORDER BY lead_id, id DESC;

ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_estado_check;
ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_estado_check
    CHECK (estado IN ('ok', 'sin_captura', 'sin_cargar', 'error', 'sin_credenciales', 'sin_configurar'));

-- Walcu por semanas: de dónde sale cada carga
DROP VIEW walcu_campana_semana_vigente;
ALTER TABLE walcu_campana_semana ADD COLUMN fuente text NOT NULL DEFAULT 'csv' CHECK (fuente IN ('csv', 'api')),
    ADD COLUMN ventas_con_cita integer CHECK (ventas_con_cita >= 0);
CREATE VIEW walcu_campana_semana_vigente AS
    SELECT w.* FROM walcu_campana_semana w
    JOIN (SELECT DISTINCT ON (semana) semana, run_id FROM walcu_campana_semana
          ORDER BY semana, fecha_carga DESC, id DESC) u
      ON u.semana = w.semana AND u.run_id = w.run_id;

-- Contrato del panel
CREATE TABLE panel_ventas_crm (
    dia              date        NOT NULL,              -- día del cálculo
    origen           text        NOT NULL CHECK (origen IN ('real', 'ejemplo')),
    calculado_at     timestamptz NOT NULL DEFAULT now(),
    mes              date        NOT NULL CHECK (extract(day FROM mes) = 1),
    tipo             text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    modelo           text        NOT NULL,
    ventas           integer     NOT NULL CHECK (ventas >= 0),
    ventas_con_cita  integer     NOT NULL DEFAULT 0 CHECK (ventas_con_cita >= 0),
    PRIMARY KEY (origen, dia, mes, tipo, modelo)
);
ALTER TABLE panel_campana_semana ADD COLUMN ventas_con_cita integer CHECK (ventas_con_cita >= 0),
    ADD COLUMN ventas_con_cita_vn integer CHECK (ventas_con_cita_vn >= 0),
    ADD COLUMN ventas_con_cita_vo integer CHECK (ventas_con_cita_vo >= 0);
ALTER TABLE panel_referencia_meta ADD COLUMN ventas_con_cita integer NOT NULL DEFAULT 0,
    ADD COLUMN ventas_con_cita_ventas integer NOT NULL DEFAULT 0;
-- Lo ya calculado viene del CSV, donde toda venta tiene cita
UPDATE panel_campana_semana SET ventas_con_cita = ventas, ventas_con_cita_vn = ventas_vn,
    ventas_con_cita_vo = ventas_vo;
UPDATE panel_referencia_meta SET ventas_con_cita = ventas, ventas_con_cita_ventas = ventas_ventas;
ALTER TABLE panel_stock_vehiculo ADD COLUMN publicado boolean, ADD COLUMN fecha_entrada date;
ALTER TABLE panel_precio_vo ADD COLUMN fecha_entrada date,
    ADD COLUMN dias_stock integer CHECK (dias_stock >= 0);
GRANT SELECT ON panel_ventas_crm TO cdf_panel;
"""

DOWNGRADE = """
DELETE FROM fuentes_estado WHERE estado = 'sin_configurar';
ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_estado_check;
ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_estado_check
    CHECK (estado IN ('ok', 'sin_captura', 'sin_cargar', 'error', 'sin_credenciales'));
DROP VIEW IF EXISTS walcu_api_cp_contado_vigente;
DROP TABLE IF EXISTS walcu_api_cp_contado;
DROP VIEW IF EXISTS walcu_api_cp_semana_total;
DROP TABLE IF EXISTS walcu_api_cp_semana;
ALTER TABLE panel_campana_semana DROP COLUMN IF EXISTS ventas_con_cita, DROP COLUMN IF EXISTS ventas_con_cita_vn,
    DROP COLUMN IF EXISTS ventas_con_cita_vo;
ALTER TABLE panel_referencia_meta DROP COLUMN IF EXISTS ventas_con_cita,
    DROP COLUMN IF EXISTS ventas_con_cita_ventas;
DROP TABLE IF EXISTS panel_ventas_crm;
ALTER TABLE panel_stock_vehiculo DROP COLUMN IF EXISTS publicado, DROP COLUMN IF EXISTS fecha_entrada;
ALTER TABLE panel_precio_vo DROP COLUMN IF EXISTS fecha_entrada, DROP COLUMN IF EXISTS dias_stock;
DROP VIEW walcu_campana_semana_vigente;
ALTER TABLE walcu_campana_semana DROP COLUMN fuente, DROP COLUMN ventas_con_cita;
CREATE VIEW walcu_campana_semana_vigente AS
    SELECT w.* FROM walcu_campana_semana w
    JOIN (SELECT DISTINCT ON (semana) semana, run_id FROM walcu_campana_semana
          ORDER BY semana, fecha_carga DESC, id DESC) u
      ON u.semana = w.semana AND u.run_id = w.run_id;
DROP VIEW IF EXISTS walcu_api_cita_vigente;
DROP TABLE IF EXISTS walcu_api_cita;
DROP TABLE IF EXISTS walcu_api_lead_estado;
DROP VIEW IF EXISTS walcu_api_lead_vigente;
DROP TABLE IF EXISTS walcu_api_lead;
DROP TABLE IF EXISTS walcu_api_vehiculo_precio_dia;
DROP TABLE IF EXISTS walcu_api_vehiculo_estado;
DROP VIEW IF EXISTS walcu_api_vehiculo_vigente;
DROP TABLE IF EXISTS walcu_api_vehiculo;
DROP TABLE IF EXISTS walcu_api_marca;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
