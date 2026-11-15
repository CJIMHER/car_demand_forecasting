"""Panel (hito 1b): tablas derivadas de Mercado, usuarios, sesiones y accesos, y el rol cdf_panel.

- panel_mercado_*: derivadas de las tablas de hechos por analitica/mercado.py. Recalcular un día
  sustituye ese día (la regla «nada se sobrescribe» es para las tablas de hechos).
- panel_usuarios, panel_sesiones, panel_accesos: de las personas solo se guardan usuario, nombre
  visible y rol (más el hash argon2id de su contraseña).
- Rol cdf_panel: lee panel_mercado_* y fuentes_estado y escribe solo en sus tablas de usuarios,
  sesiones y accesos. Sin acceso a las tablas de hechos. Se crea sin LOGIN y sin contraseña: la
  contraseña la pone «make panel-rol» desde el .env (PANEL_DB_PASSWORD), nunca una migración.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE = r"""
ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_tipo_check;
ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_tipo_check
    CHECK (tipo IN ('carga', 'comprobacion', 'copia', 'aviso', 'precalculo'));

-- Una fila por día con captura completa, tipo de stock y zona.
CREATE TABLE panel_mercado_dia (
    dia                date        NOT NULL,
    tipo               text        NOT NULL CHECK (tipo IN ('VN', 'VO')),
    zona               text        NOT NULL CHECK (zona IN ('provincia', 'comunidad', 'espana')),
    zona_nombre        text        NOT NULL,
    anuncios           integer     NOT NULL,
    propios            integer     NOT NULL,
    concesionarios     integer     NOT NULL,
    altas_hoy          integer,    -- NULL si falta la captura completa de ayer
    ausencias_hoy      integer,    -- NULL si falta la captura completa de ayer
    bajas_7            integer,    -- ausencias de 7 días ya retiradas por el scraper; NULL sin ventana completa
    ayer_completo      boolean     NOT NULL,
    hace7_completo     boolean     NOT NULL,
    ventana_completa   boolean     NOT NULL,  -- capturas completas de D-7 a D
    calculado_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dia, tipo, zona)
);

-- Por modelo (el del scraper, normalizado).
CREATE TABLE panel_mercado_modelo (
    dia                   date    NOT NULL,
    tipo                  text    NOT NULL CHECK (tipo IN ('VN', 'VO')),
    zona                  text    NOT NULL CHECK (zona IN ('provincia', 'comunidad', 'espana')),
    modelo                text    NOT NULL,
    anuncios              integer NOT NULL,
    anuncios_hace7        integer,  -- NULL si falta la captura completa de hace 7 días
    altas_7               integer,  -- NULL sin ventana completa
    ausencias_7           integer,  -- NULL sin ventana completa
    precio_mediano_cents  bigint,
    n_precio              integer NOT NULL,
    dias_mediana          integer,
    dias_cota             boolean NOT NULL DEFAULT false,  -- la mediana es una cota inferior («≥ N»)
    PRIMARY KEY (dia, tipo, zona, modelo)
);

-- Un día por fuente desde su primera captura: si tuvo captura completa, cuándo terminó y cuántos
-- anuncios trajo. Para la cabecera de Mercado y «Estado de las fuentes».
CREATE TABLE panel_mercado_capturas (
    fuente         text        NOT NULL,
    dia            date        NOT NULL,
    completa       boolean     NOT NULL,
    fin_at         timestamptz,
    anuncios       integer,
    sin_provincia  integer,
    calculado_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fuente, dia)
);

CREATE TABLE panel_usuarios (
    usuario            text        PRIMARY KEY CHECK (usuario ~ '^[a-z0-9][a-z0-9._-]{1,39}$'),
    nombre             text        NOT NULL CHECK (length(nombre) BETWEEN 1 AND 80),
    rol                text        NOT NULL CHECK (rol IN ('direccion', 'ventas', 'marketing', 'tecnico')),
    clave_hash         text        NOT NULL CHECK (clave_hash LIKE '$argon2id$%'),
    cambiar_clave      boolean     NOT NULL DEFAULT true,
    creado_at          timestamptz NOT NULL DEFAULT now(),
    clave_cambiada_at  timestamptz
);

-- La cookie lleva un testigo aleatorio; aquí solo su SHA-256.
CREATE TABLE panel_sesiones (
    id_hash        text        PRIMARY KEY,
    usuario        text        NOT NULL REFERENCES panel_usuarios ON DELETE CASCADE ON UPDATE CASCADE,
    creada_at      timestamptz NOT NULL DEFAULT now(),
    ultimo_uso_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX panel_sesiones_usuario ON panel_sesiones (usuario);

-- Registro de accesos (sin contraseñas). usuario solo si existe; se borra a los 90 días.
CREATE TABLE panel_accesos (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    registrado_at  timestamptz NOT NULL DEFAULT now(),
    usuario        text,
    resultado      text        NOT NULL CHECK (resultado IN ('ok', 'fallo', 'bloqueado', 'salida', 'cambio_clave')),
    ip             text
);
CREATE INDEX panel_accesos_fecha ON panel_accesos (registrado_at);
CREATE INDEX panel_accesos_usuario ON panel_accesos (usuario, registrado_at);
CREATE INDEX panel_accesos_ip ON panel_accesos (ip, registrado_at);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cdf_panel') THEN
        CREATE ROLE cdf_panel NOLOGIN;
    END IF;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM cdf_panel;
GRANT USAGE ON SCHEMA public TO cdf_panel;
GRANT SELECT ON panel_mercado_dia, panel_mercado_modelo, panel_mercado_capturas, fuentes_estado TO cdf_panel;
GRANT SELECT, INSERT, UPDATE, DELETE ON panel_usuarios, panel_sesiones, panel_accesos TO cdf_panel;
"""

DOWNGRADE = r"""
DROP TABLE IF EXISTS panel_accesos;
DROP TABLE IF EXISTS panel_sesiones;
DROP TABLE IF EXISTS panel_usuarios;
DROP TABLE IF EXISTS panel_mercado_capturas;
DROP TABLE IF EXISTS panel_mercado_modelo;
DROP TABLE IF EXISTS panel_mercado_dia;
REVOKE ALL ON fuentes_estado FROM cdf_panel;
-- El rol cdf_panel es de todo el servidor de Postgres: no se borra aquí.
ALTER TABLE fuentes_estado DROP CONSTRAINT fuentes_estado_tipo_check;
ALTER TABLE fuentes_estado ADD CONSTRAINT fuentes_estado_tipo_check
    CHECK (tipo IN ('carga', 'comprobacion', 'copia', 'aviso'));
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
