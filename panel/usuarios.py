"""Alta, baja, cambio de rol y restablecimiento de usuarios del panel (las lanza make, en cdf_panel).

    python -m panel.usuarios alta USUARIO --nombre "Nombre visible" --rol ventas
    python -m panel.usuarios baja USUARIO
    python -m panel.usuarios rol USUARIO marketing
    python -m panel.usuarios restablecer USUARIO
    python -m panel.usuarios lista

La contraseña temporal se pide por teclado y sin eco (dos veces), nunca como argumento. Tiene
12 caracteres como mínimo y obliga a cambiarla en el primer acceso. No se escribe en ningún sitio:
solo su hash argon2id. De cada persona se guardan usuario, nombre visible y rol.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys

import psycopg

from panel import seguridad as seg
from panel.permisos import ROLES

PATRON_USUARIO = re.compile(r"^[a-z0-9][a-z0-9._-]{1,39}$")


class ErrorUsuarios(Exception):
    pass


def pedir_clave(leer=getpass.getpass, usuario: str = "") -> str:
    if leer is getpass.getpass and not sys.stdin.isatty():
        raise ErrorUsuarios("Hace falta un terminal para escribir la contraseña (make ... desde una consola).")
    clave = leer("Contraseña temporal (12 caracteres como mínimo, no se ve al escribir): ")
    if leer("Repítela: ") != clave:
        raise ErrorUsuarios("Las dos contraseñas no coinciden.")
    try:
        seg.comprobar_clave_nueva(clave, usuario)
    except seg.ClaveNoValida as exc:
        raise ErrorUsuarios(str(exc)) from None
    return clave


def _usuario(valor: str) -> str:
    valor = (valor or "").strip().lower()
    if not PATRON_USUARIO.match(valor):
        raise ErrorUsuarios("Usuario no válido: 2–40 caracteres, minúsculas, cifras, punto, guion o guion bajo.")
    return valor


def _rol(valor: str) -> str:
    if valor not in ROLES:
        raise ErrorUsuarios(f"Rol no válido: {valor!r}. Roles: {', '.join(ROLES)}.")
    return valor


def alta(pg: psycopg.Connection, usuario: str, nombre: str, rol: str, clave: str) -> None:
    usuario, rol = _usuario(usuario), _rol(rol)
    nombre = (nombre or "").strip()
    if not 1 <= len(nombre) <= 80:
        raise ErrorUsuarios("El nombre visible tiene que tener entre 1 y 80 caracteres.")
    seg.comprobar_clave_nueva(clave, usuario)
    if pg.execute("SELECT 1 FROM panel_usuarios WHERE usuario = %s", (usuario,)).fetchone():
        raise ErrorUsuarios(f"Ya existe el usuario {usuario}.")
    pg.execute("INSERT INTO panel_usuarios (usuario, nombre, rol, clave_hash, cambiar_clave) VALUES (%s, %s, %s, %s, true)",
               (usuario, nombre, rol, seg.hash_clave(clave)))


def baja(pg: psycopg.Connection, usuario: str) -> None:
    if pg.execute("DELETE FROM panel_usuarios WHERE usuario = %s", (_usuario(usuario),)).rowcount == 0:
        raise ErrorUsuarios(f"No existe el usuario {usuario}.")


def cambiar_rol(pg: psycopg.Connection, usuario: str, rol: str) -> None:
    if pg.execute("UPDATE panel_usuarios SET rol = %s WHERE usuario = %s",
                  (_rol(rol), _usuario(usuario))).rowcount == 0:
        raise ErrorUsuarios(f"No existe el usuario {usuario}.")


def restablecer(pg: psycopg.Connection, usuario: str, clave: str) -> None:
    usuario = _usuario(usuario)
    seg.comprobar_clave_nueva(clave, usuario)
    with pg.transaction():
        if pg.execute("UPDATE panel_usuarios SET clave_hash = %s, cambiar_clave = true WHERE usuario = %s",
                      (seg.hash_clave(clave), usuario)).rowcount == 0:
            raise ErrorUsuarios(f"No existe el usuario {usuario}.")
        pg.execute("DELETE FROM panel_sesiones WHERE usuario = %s", (usuario,))


def lista(pg: psycopg.Connection) -> list[tuple]:
    return pg.execute("SELECT usuario, nombre, rol, cambiar_clave FROM panel_usuarios ORDER BY usuario").fetchall()


def main(argv=None, leer=getpass.getpass) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="orden", required=True)
    p = sub.add_parser("alta")
    p.add_argument("usuario")
    p.add_argument("--nombre", required=True)
    p.add_argument("--rol", required=True, choices=list(ROLES))
    sub.add_parser("baja").add_argument("usuario")
    p = sub.add_parser("rol")
    p.add_argument("usuario")
    p.add_argument("rol", choices=list(ROLES))
    sub.add_parser("restablecer").add_argument("usuario")
    sub.add_parser("lista")
    args = parser.parse_args(argv)
    try:
        with psycopg.connect(os.environ.get("PANEL_DATABASE_URL", ""), autocommit=True) as pg:
            if args.orden == "alta":
                alta(pg, args.usuario, args.nombre, args.rol, pedir_clave(leer, args.usuario))
                print(f"Alta de {args.usuario} ({args.rol}). Tendrá que cambiar la contraseña al entrar.")
            elif args.orden == "baja":
                baja(pg, args.usuario)
                print(f"Baja de {args.usuario}: sus sesiones se han cerrado.")
            elif args.orden == "rol":
                cambiar_rol(pg, args.usuario, args.rol)
                print(f"{args.usuario} pasa a {args.rol}.")
            elif args.orden == "restablecer":
                restablecer(pg, args.usuario, pedir_clave(leer, args.usuario))
                print(f"Contraseña temporal de {args.usuario} restablecida; sus sesiones se han cerrado.")
            else:
                for usuario, nombre, rol, cambiar in lista(pg):
                    print(f"{usuario:<20} {rol:<10} {nombre}{'  (contraseña temporal)' if cambiar else ''}")
    except (ErrorUsuarios, seg.ClaveNoValida) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
