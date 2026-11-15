"""Contraseñas (argon2id), sesiones en el servidor, CSRF, bloqueo de intentos y IP del cliente.

Nada de aquí escribe contraseñas ni hashes en logs o respuestas.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
import time
from datetime import timedelta

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

COOKIE_SESION = "__Host-cdf_sesion"   # __Host-: solo este host, Secure, Path=/ y sin Domain
COOKIE_CSRF = "__Host-cdf_csrf"
LONGITUD_MINIMA = 12
LONGITUD_MAXIMA = 256
INTENTOS = 5
BLOQUEO = timedelta(minutes=15)
RETENCION_ACCESOS_DIAS = 90

_hasher = PasswordHasher()  # argon2id con los parámetros recomendados de argon2-cffi
_HASH_FALSO = _hasher.hash(secrets.token_urlsafe(16))  # para que un usuario inexistente tarde lo mismo


class ClaveNoValida(ValueError):
    pass


# --------------------------------------------------------------------------- contraseñas

def comprobar_clave_nueva(clave: str, usuario: str = "", anterior: str | None = None) -> None:
    if len(clave) < LONGITUD_MINIMA:
        raise ClaveNoValida(f"La contraseña debe tener al menos {LONGITUD_MINIMA} caracteres.")
    if len(clave) > LONGITUD_MAXIMA:
        raise ClaveNoValida(f"La contraseña no puede pasar de {LONGITUD_MAXIMA} caracteres.")
    if usuario and clave.casefold() == usuario.casefold():
        raise ClaveNoValida("La contraseña no puede ser el nombre de usuario.")
    if anterior is not None and clave == anterior:
        raise ClaveNoValida("La contraseña nueva tiene que ser distinta de la actual.")


def hash_clave(clave: str) -> str:
    return _hasher.hash(clave)


def verificar(hash_guardado: str | None, clave: str) -> bool:
    try:
        return _hasher.verify(hash_guardado or _HASH_FALSO, clave) and hash_guardado is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def necesita_rehash(hash_guardado: str) -> bool:
    return _hasher.check_needs_rehash(hash_guardado)


# --------------------------------------------------------------------------- sesiones

def _huella(testigo: str) -> str:
    return hashlib.sha256(testigo.encode()).hexdigest()


def crear_sesion(pg: psycopg.Connection, usuario: str) -> str:
    testigo = secrets.token_urlsafe(32)
    pg.execute("INSERT INTO panel_sesiones (id_hash, usuario) VALUES (%s, %s)", (_huella(testigo), usuario))
    return testigo


def leer_sesion(pg: psycopg.Connection, testigo: str | None, dias: int) -> dict | None:
    """Usuario de la sesión si sigue viva (inactividad < `dias`); renueva su último uso."""
    if not testigo or len(testigo) > 100:
        return None
    fila = pg.execute(
        """UPDATE panel_sesiones s SET ultimo_uso_at = now()
           FROM panel_usuarios u
           WHERE s.id_hash = %s AND u.usuario = s.usuario
             AND s.ultimo_uso_at > now() - make_interval(days => %s)
           RETURNING u.usuario, u.nombre, u.rol, u.cambiar_clave""", (_huella(testigo), dias)).fetchone()
    if fila is None:
        return None
    return {"usuario": fila[0], "nombre": fila[1], "rol": fila[2], "cambiar_clave": fila[3]}


def cerrar_sesion(pg: psycopg.Connection, testigo: str | None) -> None:
    if testigo:
        pg.execute("DELETE FROM panel_sesiones WHERE id_hash = %s", (_huella(testigo),))


def purgar(pg: psycopg.Connection, dias_sesion: int) -> None:
    pg.execute("DELETE FROM panel_sesiones WHERE ultimo_uso_at < now() - make_interval(days => %s)", (dias_sesion,))
    pg.execute("DELETE FROM panel_accesos WHERE registrado_at < now() - make_interval(days => %s)",
               (RETENCION_ACCESOS_DIAS,))


# --------------------------------------------------------------------------- CSRF (doble envío con cookie __Host-)

def nuevo_csrf() -> str:
    return secrets.token_urlsafe(32)


def csrf_valido(cookie: str | None, formulario: str | None) -> bool:
    return bool(cookie) and bool(formulario) and hmac.compare_digest(cookie, formulario)


# --------------------------------------------------------------------------- bloqueo y registro de accesos

def registrar(pg: psycopg.Connection, usuario: str | None, resultado: str, ip: str | None) -> None:
    pg.execute("INSERT INTO panel_accesos (usuario, resultado, ip) VALUES (%s, %s, %s)", (usuario, resultado, ip))


def _bloqueado_hasta(fallos: list) -> object | None:
    """Fin del bloqueo: 15 minutos después de cualquier quinto fallo en 15 minutos."""
    hasta = None
    for j in range(INTENTOS - 1, len(fallos)):
        if fallos[j] - fallos[j - INTENTOS + 1] <= BLOQUEO:
            hasta = fallos[j] + BLOQUEO
    return hasta


SQL_FALLOS_IP = """SELECT registrado_at FROM panel_accesos
                   WHERE ip = %(v)s AND resultado = 'fallo' AND registrado_at > %(desde)s ORDER BY registrado_at"""
# Por usuario, el contador vuelve a cero con cada acceso correcto.
SQL_FALLOS_USUARIO = """SELECT registrado_at FROM panel_accesos
                        WHERE usuario = %(v)s AND resultado = 'fallo' AND registrado_at > %(desde)s
                          AND registrado_at > coalesce((SELECT max(registrado_at) FROM panel_accesos
                                                        WHERE usuario = %(v)s AND resultado = 'ok'), '-infinity')
                        ORDER BY registrado_at"""


def bloqueado(pg: psycopg.Connection, usuario: str | None, ip: str | None) -> bool:
    """¿Bloqueado el usuario o la IP? (5 fallos en 15 minutos bloquean 15 minutos tras el quinto)."""
    ahora = pg.execute("SELECT now()").fetchone()[0]
    for sql, valor in ((SQL_FALLOS_IP, ip), (SQL_FALLOS_USUARIO, usuario)):
        if not valor:
            continue
        fallos = [r[0] for r in pg.execute(sql, {"v": valor, "desde": ahora - 2 * BLOQUEO})]
        hasta = _bloqueado_hasta(fallos)
        if hasta is not None and ahora < hasta:
            return True
    return False


# --------------------------------------------------------------------------- IP del cliente

class Proxy:
    """Solo se fía de X-Forwarded-For si la conexión llega del proxy (Caddy, por su nombre en Docker)."""

    def __init__(self, nombre: str | None, ttl: float = 60):
        self.nombre, self.ttl = (nombre or "").strip(), ttl
        self._ips: set[str] = set()
        self._hasta = 0.0

    def ips(self) -> set[str]:
        if not self.nombre:
            return set()
        if time.monotonic() > self._hasta:
            try:
                self._ips = {i[4][0] for i in socket.getaddrinfo(self.nombre, None)}
            except OSError:
                self._ips = set()
            self._hasta = time.monotonic() + self.ttl
        return self._ips

    def ip_cliente(self, par: str | None, cabecera: str | None) -> str | None:
        if par and cabecera and par in self.ips():
            ultimo = cabecera.split(",")[-1].strip()  # lo que ha añadido Caddy
            try:
                return str(ipaddress.ip_address(ultimo))
            except ValueError:
                return par
        return par
