"""API de Walcu: coches, leads de venta y citas (ingesta/walcu_api.py), y lo que se deriva de ellos.

Hechos de solo añadir (versionados por huella, como Meta y GA4: volver a descargar no duplica; las vistas
*_vigente dan la última versión de cada id de Walcu). Ningún nombre, teléfono, correo ni dirección: clientes,
comerciales, matrículas y bastidores van como HMAC-SHA256 (*_seudo).
- walcu_api_marca: marca de agua por recurso (hasta dónde se ha descargado) y, en la carga inicial, su primer día.
- walcu_api_vehiculo, walcu_api_vehiculo_estado (historia de estados), walcu_api_vehiculo_precio_dia (nuestra
  foto diaria del precio de los publicados: Walcu no fecha las bajadas).
- walcu_api_lead, walcu_api_lead_estado (historia de estados con el comercial seudonimizado), walcu_api_cita.

walcu_campana_semana gana la columna fuente (csv o api): ingesta/walcu_crm.py la genera desde la API cuando el
mapeo de estados y sedes está definido. La vista vigente se rehace para que la traiga.

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
    coches           integer     NOT NULL DEFAULT 0
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

-- Walcu por semanas: de dónde sale cada carga
DROP VIEW walcu_campana_semana_vigente;
ALTER TABLE walcu_campana_semana ADD COLUMN fuente text NOT NULL DEFAULT 'csv' CHECK (fuente IN ('csv', 'api'));
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
    PRIMARY KEY (origen, dia, mes, tipo, modelo)
);
ALTER TABLE panel_stock_vehiculo ADD COLUMN publicado boolean, ADD COLUMN fecha_entrada date;
ALTER TABLE panel_precio_vo ADD COLUMN fecha_entrada date,
    ADD COLUMN dias_stock integer CHECK (dias_stock >= 0);
GRANT SELECT ON panel_ventas_crm TO cdf_panel;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS panel_ventas_crm;
ALTER TABLE panel_stock_vehiculo DROP COLUMN IF EXISTS publicado, DROP COLUMN IF EXISTS fecha_entrada;
ALTER TABLE panel_precio_vo DROP COLUMN IF EXISTS fecha_entrada, DROP COLUMN IF EXISTS dias_stock;
DROP VIEW walcu_campana_semana_vigente;
ALTER TABLE walcu_campana_semana DROP COLUMN fuente;
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
