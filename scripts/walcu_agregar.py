"""Agrega la exportación de leads de Walcu por mes, origen, campaña y tipo (en el Mac).

Uso:
  .venv/bin/python scripts/walcu_agregar.py ENTRADA.xlsx|ENTRADA.csv SALIDA.csv [--excluidos FICHERO]
  make walcu-agregar ENTRADA=… SALIDA=… [EXCLUIDOS=…]

Lee la exportación tal como la saca el CRM y escribe el CSV que carga  make walcu-cargar  en el servidor,
con exactamente estas columnas:
  mes, origen, campana, tipo, leads, leads_cercana, leads_fuera, leads_sin_zona, citas, ventas, num_inexistente

Solo se leen las columnas de config/walcu_export.yaml (Origen, Detalle campaña, Tipo de vehículo solicitado,
Fecha Lead, Id negociación creada, Resultado última actividad, Estado, Motivo fracaso y, solo para la zona,
Código postal; Vendedor, solo si hay comerciales excluidos); las demás (nombre, teléfono, correo…) no se
leen. En un .xlsx, las celdas de las otras columnas se saltan sin convertirlas.

- Comerciales excluidos: fichero local opcional (por defecto ~/cdf_walcu/comerciales_excluidos.txt), un
  nombre por línea. Sus leads se descartan antes de agregar. Lleva nombres: no se versiona ni se copia al
  repositorio ni al servidor, y el registro solo dice cuántos leads se han excluido. Si el fichero por
  defecto no existe, no se excluye nada; si se pasa uno con --excluidos y no existe, no se escribe nada.
- Cita: el lead tiene negociación creada, salvo que su «Resultado última actividad» sea una de
  actividades_no_cita (PRESUPUESTO POR TELÉFONO). Venta: además, su estado es de venta (estados_venta).
- Zona: por los dos primeros dígitos del código postal, con la zona cercana de config/concesionario.yaml
  (la provincia propia y provincias_cercanas): cercana, fuera o sin zona (sin código postal válido).
- Número inexistente: el motivo de fracaso contiene alguno de motivos_num_inexistente.
- Origen: los de config/walcu_export.yaml y la web propia (propio.web de config/concesionario.yaml); los
  demás, «otros».

Nunca escribe datos personales, ni en el CSV ni en lo que muestra: solo recuentos y, para ajustar la
configuración, los valores de Estado y Tipo y los motivos de fracaso que se repiten al menos 5 veces (los
que parezcan un correo, un teléfono o un DNI no se muestran nunca). Una campaña cuyo nombre parezca un dato
personal se escribe como «(campaña omitida)». Antes de escribir, el CSV pasa la misma validación que en el
servidor (ingesta/walcu.py): si no la pasa, no se escribe nada.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import yaml  # noqa: E402

from ingesta import concesionario as cc  # noqa: E402
from ingesta import walcu  # noqa: E402
from ingesta.campanas import plegar  # noqa: E402

SIN_CAMPANA = "(sin campaña)"
OMITIDA = "(campaña omitida)"
OTROS = "otros"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MIN_MOSTRAR = 5
EXCLUIDOS = Path.home() / "cdf_walcu" / "comerciales_excluidos.txt"


class ErrorExportacion(Exception):
    pass


def cargar_config(fichero: Path | None = None) -> dict:
    return yaml.safe_load((fichero or RAIZ / "config" / "walcu_export.yaml").read_text(encoding="utf-8")) or {}


# --------------------------------------------------------------------------- lectura (solo las columnas necesarias)

def _indices(cabecera: list[str], columnas: dict[str, str]) -> dict[str, int]:
    plegada = [plegar(c) for c in cabecera]
    indices, faltan = {}, []
    for clave, nombre in columnas.items():
        if plegar(nombre) in plegada:
            indices[clave] = plegada.index(plegar(nombre))
        else:
            faltan.append(nombre)
    if faltan:
        raise ErrorExportacion("faltan columnas en la exportación: " + ", ".join(faltan))
    return indices


def leer_csv(ruta: Path, columnas: dict[str, str]):
    bruto = ruta.read_bytes()
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = bruto.decode("cp1252")
    primera = texto.split("\n", 1)[0]
    sep = ";" if primera.count(";") > primera.count(",") else ("\t" if "\t" in primera else ",")
    lector = csv.reader(io.StringIO(texto, newline=""), delimiter=sep)
    indices = _indices(next(lector, []), columnas)
    for valores in lector:
        yield {k: (valores[i] if i < len(valores) else "") for k, i in indices.items()}


def _columna(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def leer_xlsx(ruta: Path, columnas: dict[str, str]):
    """Primera hoja de un .xlsx con la biblioteca estándar: solo las celdas de las columnas pedidas."""
    with zipfile.ZipFile(ruta) as z:
        libro = ET.fromstring(z.read("xl/workbook.xml"))
        hoja = libro.find(f"{NS}sheets/{NS}sheet")
        rid = hoja.get(f"{NS_REL}id")
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        destino = next(r.get("Target") for r in rels if r.get("Id") == rid)
        destino = destino.lstrip("/")
        destino = destino if destino.startswith("xl/") else f"xl/{destino}"
        compartidas = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{NS}si"):
                compartidas.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
        indices, buscadas = None, None
        with z.open(destino) as f:
            for _, fila in ET.iterparse(f):
                if fila.tag != f"{NS}row":
                    continue
                valores = {}
                for c in fila.iter(f"{NS}c"):
                    col = _columna(c.get("r", "A"))
                    if buscadas is not None and col not in buscadas:
                        continue                           # columna que no hace falta: no se convierte
                    t = c.get("t")
                    if t == "inlineStr":
                        v = "".join(x.text or "" for x in c.iter(f"{NS}t"))
                    else:
                        el = c.find(f"{NS}v")
                        v = "" if el is None or el.text is None else el.text
                        if t == "s" and v:
                            v = compartidas[int(v)]
                    valores[col] = v
                fila.clear()
                if indices is None:
                    ancho = max(valores) + 1 if valores else 0
                    indices = _indices([valores.get(i, "") for i in range(ancho)], columnas)
                    buscadas = set(indices.values())
                    continue
                yield {k: valores.get(i, "") for k, i in indices.items()}
    if indices is None:
        raise ErrorExportacion("la hoja está vacía")


def leer_excluidos(ruta: Path | None) -> frozenset[str]:
    """Nombres de comerciales excluidos (plegados), uno por línea; vacías y # se ignoran. Sin fichero por
    defecto, ninguno. Los nombres no salen nunca de aquí: solo sirven para comparar."""
    if ruta is None:
        if not EXCLUIDOS.is_file():
            return frozenset()
        ruta = EXCLUIDOS
    if not ruta.is_file():
        raise ErrorExportacion("no existe el fichero de comerciales excluidos que se ha indicado")
    return frozenset(plegar(n) for n in ruta.read_text(encoding="utf-8-sig").splitlines()
                     if plegar(n) and not n.lstrip().startswith("#"))


def leer(ruta: Path, columnas: dict[str, str]):
    if ruta.suffix.lower() in (".xlsx", ".xlsm"):
        return leer_xlsx(ruta, columnas)
    if ruta.suffix.lower() in (".csv", ".txt"):
        return leer_csv(ruta, columnas)
    raise ErrorExportacion("la entrada tiene que ser .xlsx o .csv")


# --------------------------------------------------------------------------- agregación

def fecha(valor: str) -> date | None:
    v = str(valor or "").strip()
    if not v:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", v):                    # número de serie de Excel
        n = float(v)
        return (datetime(1899, 12, 30) + timedelta(days=n)).date() if 20000 < n < 80000 else None
    for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%m-%Y %H:%M", "%d-%m-%Y", "%d/%m/%y %H:%M", "%d/%m/%y"):
        try:
            return datetime.strptime(v[:19] if "T" in v else v, formato).date()
        except ValueError:
            continue
    return None


def zona(cp: str, cercanas: tuple[str, ...]) -> str:
    v = re.sub(r"\.0$", "", str(cp or "").strip())
    if not re.fullmatch(r"\d{4,5}", v):
        return "sin_zona"
    v = v.zfill(5)
    if not ("01" <= v[:2] <= "52"):
        return "sin_zona"
    return "cercana" if v[:2] in cercanas else "fuera"


class Agregador:
    def __init__(self, cfg: dict, conc: cc.Concesionario, excluidos: frozenset[str] = frozenset()):
        self.cercanas = conc.zona_cercana
        self.excluidos = excluidos
        self.no_cita = {plegar(x) for x in cfg.get("actividades_no_cita") or []}
        self.ventas = {plegar(x) for x in cfg.get("estados_venta") or []}
        self.inexistente = [plegar(x) for x in cfg.get("motivos_num_inexistente") or [] if plegar(x)]
        self.tipos = {plegar(a): t for t, alias in (cfg.get("tipos") or {}).items() for a in alias or []}
        self.tipo_defecto = str(cfg.get("tipo_por_defecto") or "VN")
        self.origenes = {plegar(a): nombre for nombre, alias in (cfg.get("origenes") or {}).items()
                         for a in [nombre, *(alias or [])]}
        if conc.web:
            self.origenes[plegar(conc.web)] = conc.web
        self.filas: dict[tuple, Counter] = defaultdict(Counter)
        self.cuentas = Counter()
        self.estados, self.tipos_vistos, self.motivos = Counter(), Counter(), Counter()

    def anadir(self, lead: dict) -> None:
        self.cuentas["leidos"] += 1
        if self.excluidos and plegar(lead.get("comercial")) in self.excluidos:
            self.cuentas["excluidos"] += 1
            return
        d = fecha(lead["fecha"])
        if d is None:
            self.cuentas["sin_fecha"] += 1
            return
        origen = self.origenes.get(plegar(lead["origen"]), OTROS)
        campana = re.sub(r"\s+", " ", str(lead["campana"] or "")).strip() or SIN_CAMPANA
        if walcu.personal(campana):
            campana = OMITIDA
            self.cuentas["campanas_omitidas"] += 1
        tipo_crudo = plegar(lead["tipo"])
        tipo = self.tipos.get(tipo_crudo)
        if tipo is None:
            tipo = self.tipo_defecto
            self.cuentas["tipo_por_defecto"] += 1
        estado = plegar(lead["estado"])
        motivo = plegar(lead["motivo_fracaso"])
        cita = bool(str(lead["negociacion"] or "").strip())
        if cita and plegar(lead["resultado_actividad"]) in self.no_cita:
            cita = False
            self.cuentas["no_cita"] += 1
        a = self.filas[(f"{d:%Y-%m}", origen, campana, tipo)]
        a["leads"] += 1
        a[f"leads_{zona(lead['codigo_postal'], self.cercanas)}"] += 1
        a["citas"] += cita
        a["ventas"] += cita and estado in self.ventas
        a["num_inexistente"] += any(m in motivo for m in self.inexistente)
        self.estados[estado or "(vacío)"] += 1
        self.tipos_vistos[tipo_crudo or "(vacío)"] += 1
        if motivo:
            self.motivos[motivo] += 1

    def csv(self) -> bytes:
        salida = io.StringIO()
        w = csv.writer(salida, lineterminator="\n")
        w.writerow(walcu.COLUMNAS)
        for (mes, origen, campana, tipo), a in sorted(self.filas.items()):
            w.writerow([mes, origen, campana, tipo] + [a[c] for c in walcu.NUMEROS])
        return salida.getvalue().encode("utf-8")

    def registro(self) -> str:
        """Solo recuentos y valores de listas del CRM que se repiten; nada que parezca un dato personal."""
        def lista(cont: Counter, minimo: int) -> str:
            vis = [(v, n) for v, n in cont.most_common() if n >= minimo and not walcu.personal(v)]
            return ", ".join(f"«{v}»: {n}" for v, n in vis) or "—"
        c = self.cuentas
        return "\n".join([
            f"Leads leídos: {c['leidos']}; de comerciales excluidos (no cuentan): {c['excluidos']}; "
            f"sin fecha válida (no cuentan): {c['sin_fecha']}; "
            f"con negociación que no cuenta como cita por su última actividad: {c['no_cita']}; "
            f"con tipo no reconocido (cuentan como {self.tipo_defecto}): {c['tipo_por_defecto']}; "
            f"campañas omitidas por parecer un dato personal: {c['campanas_omitidas']}.",
            f"Estados: {lista(self.estados, 1)}",
            f"Tipos de vehículo: {lista(self.tipos_vistos, 1)}",
            f"Motivos de fracaso que se repiten al menos {MIN_MOSTRAR} veces: {lista(self.motivos, MIN_MOSTRAR)}",
        ])


def agregar(entrada: Path, cfg: dict | None = None, conc: cc.Concesionario | None = None,
            excluidos: frozenset[str] = frozenset()) -> Agregador:
    cfg = cfg or cargar_config()
    ag = Agregador(cfg, conc or cc.cargar(), excluidos)
    columnas = dict(cfg["columnas"])
    if not excluidos:
        columnas.pop("comercial", None)          # sin excluidos, la columna del comercial no se lee
    for lead in leer(entrada, columnas):
        ag.anadir(lead)
    return ag


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entrada", type=Path)
    parser.add_argument("salida", type=Path)
    parser.add_argument("--excluidos", type=Path, default=None,
                        help=f"comerciales excluidos, uno por línea (por defecto {EXCLUIDOS}, si existe)")
    args = parser.parse_args(argv)
    try:
        ag = agregar(args.entrada, excluidos=leer_excluidos(args.excluidos))
        contenido = ag.csv()
        v = walcu.validar(contenido)              # la misma validación que en el servidor
    except (ErrorExportacion, walcu.ErrorWalcu, OSError, zipfile.BadZipFile, KeyError) as exc:
        print(f"No se ha escrito nada: {type(exc).__name__ if isinstance(exc, KeyError) else exc}", file=sys.stderr)
        return 1
    args.salida.write_bytes(contenido)
    print(ag.registro())
    print(f"Escrito {args.salida.name}: {len(v.filas)} filas." + "".join(f"\nAviso: {a}." for a in v.avisos))
    print(f"Para cargarlo en el servidor: make walcu-cargar FICHERO=<ruta en el servidor>/{args.salida.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
