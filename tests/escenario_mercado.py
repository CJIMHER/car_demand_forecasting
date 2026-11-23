"""Escenario sintético de varios días para Mercado: huecos, una fuente que empieza más tarde,
altas, ausencias y retiradas. Se escribe con el código real de los scrapers (sinteticos.capturar).

Lo usan las pruebas, «make panel-dev» y las capturas de pantalla. Ningún dato real: concesionarios,
anuncios y precios inventados.
"""

from __future__ import annotations

import gzip
import json
import random
from datetime import date, timedelta
from pathlib import Path

from sinteticos import capturar, coche, fallar

MODELOS = {
    "vo": [("T-Roc", 24000), ("T-Cross", 19500), ("Taigo", 21500), ("Golf", 21000), ("Tiguan", 29500),
           ("Polo", 15500), ("Touran", 22500), ("Passat", 27500), ("ID.3", 24000), ("ID.4", 31500)],
    "vn": [("T-Roc", 31900), ("T-Cross", 25400), ("Tiguan", 41200), ("Golf", 31500), ("Taigo", 26800),
           ("Polo", 21900), ("Tayron", 49900), ("Golf Variant", 33900), ("Passat", 47300)],
    "vn_otros": [("ID.3", 36900), ("ID.4", 44900), ("ID.5", 49900), ("ID.7", 56900), ("ID. Buzz", 64900),
                 ("Tiguan", 47900), ("Golf", 39900)],
}
# Versiones como las escribe la web: acabado (con su ortografía de VO o de VN) y motor. Hay acabados
# fuera del catálogo («Dark», «Classic», ninguno) para que «Sin clasificar» tenga anuncios.
ACABADOS = {
    "vo": [("Life", 1.0), ("R-Line", 1.1), ("``Más``", 1.04), ("Style", 1.06), ("Sport", 1.0), ("Dark Line", 1.05),
           ("Dark", 1.03), ("", 0.97)],
    "vn": [('"Más"', 1.0), ("R-Line", 1.1), ("Style", 1.06), ("{modelo}", 0.93), ("Match", 0.98),
           ("R-Line Classic", 1.08)],
    "vn_otros": [('"Más"', 1.0), ("R-Line", 1.1), ("Style", 1.06), ("Pro", 1.0), ("Pro S", 1.08), ("GTX", 1.15)],
}
MOTORES = {   # (cilindrada, tecnología, kW, CV, combustible, factor de precio)
    "termico": [("1.0", "TSI", 70, 95, "Gasolina", 0.92), ("1.0", "TSI", 85, 116, "Gasolina", 0.97),
                ("1.5", "eTSI", 110, 150, "Gasolina", 1.06), ("1.5", "TSI", 110, 150, "Gasolina", 1.04),
                ("2.0", "TDI", 110, 150, "Diésel", 1.08)],
    "electrico": [("", "", 150, 204, "Eléctrico", 1.0), ("", "", 210, 286, "Eléctrico", 1.12)],
    "hibrido": [("1.5", "TSI eHybrid", 150, 204, "Híbrido", 1.0), ("1.5", "TSI eHybrid", 200, 272, "Híbrido", 1.1)],
}


def version(fuente: str, modelo: str, acabado: str, motor: tuple, automatico: bool) -> str:
    """Texto de versión con el formato de VO («<marca> T-Roc ``Más`` 1.5 TSI 110 kW (150 CV) DSG») o
    de VN («T-Roc "Más"  1.5 eTSI 85 kW (116 CV)  Automático DSG 7 vel.»)."""
    cil, tec, kw, cv, combustible, _ = motor
    acabado = acabado.replace("{modelo}", modelo)
    if fuente == "vo":
        partes = ["Volkswagen", modelo, acabado, cil, tec, f"{kw} kW ({cv} CV)", "DSG" if automatico else ""]
        return " ".join(p for p in partes if p)
    if combustible == "Eléctrico":
        return f"{modelo} {acabado}   {kw} kW ({cv} CV) 77 kWh Automático 1 vel."
    cambio = "Automático DSG 7 vel." if automatico else "6 vel."
    return f"{modelo} {acabado}  {cil} {tec} {kw} kW ({cv} CV)  {cambio}"
TAMANO = {"vo": 260, "vn": 340, "vn_otros": 70}
# Colores tal como los publican las fichas (nombres reales de la web; ninguno identifica a nadie)
COLORES = [("Blanco Puro", 14), ("Blanco puro", 3), ("Blanco, y punto", 2), ("Negro Noche (metalizado)", 10),
           ("Negro Profundo efecto perla", 6), ("Plata Réflex metalizado", 8), ("Gris Ceniza metalizado", 7),
           ("Gris Grisáceo (metalizado)", 4), ("Gris Delfín (metalizado)", 5), ("Gris Indy metalizado", 6),
           ("Azul Arrecife metalizado", 6), ("Rojo Kings metalizado", 4), ("Verde Kiwi", 1), ("Naranja Energía", 1),
           ("Pintura exterior metalizada", 3), ("Bicolor especial", 1)]


def exportar_colores(base: Path, coches: dict[str, tuple[str, dict, date]], fin: date, semilla: int = 7,
                     fraccion: float = 0.6) -> Path:
    """Exportación del componente de fichas (vw_details/exports/colors_latest.jsonl.gz) para los anuncios
    vistos: la recuperación va de los más antiguos a los más recientes, así que tienen ficha leída
    (status ok) los primeros `fraccion`; del resto, unos pendientes y otros sin ficha en la exportación.
    Algún anuncio trae un VIN distinto. fetched_at: dos días antes de `fin`."""
    r = random.Random(semilla * 31 + 5)
    orden = sorted(coches.items(), key=lambda kv: (kv[1][2], kv[0]))
    corte = int(len(orden) * fraccion)
    fetched = f"{(fin - timedelta(days=2)).isoformat()}T05:00:00+00:00"
    lineas = []
    for i, (vid, (fuente, coche, _)) in enumerate(orden):
        vin = coche["business"]["vin"]
        if i < corte:
            estado = r.choices(["ok", "listing_color", "empty", "error"], [90, 4, 4, 2])[0]
        else:
            estado = r.choices(["pending", None], [70, 30])[0]
            if estado is None:
                continue                                  # aún no está en la exportación
        color = r.choices([c for c, _ in COLORES], [w for _, w in COLORES])[0] if estado in ("ok", "listing_color") else None
        if color and r.random() < 0.01:
            vin = "WVWZZZ" + "9" * 11                    # la ficha es de otro coche: el color no vale
        lineas.append({"scope": "x", "source_name": fuente, "vehicle_id": vid, "vin": vin, "status": estado,
                       "exterior_color": color, "interior_color": "Negro Titanio" if color else None,
                       "interior_color_json": None, "color_json": None,
                       "fetched_at": fetched if estado != "pending" else None,
                       "source_snapshot_day": fin.isoformat(), "present": True})
    ruta = base / "vw_details" / "exports" / "colors_latest.jsonl.gz"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(gzip.compress("\n".join(json.dumps(x, ensure_ascii=False) for x in lineas).encode()))
    return ruta
OTRAS = ["28", "08", "46", "50", "48", "15", "33", "30"]


def nombre_dealer(dealer_id: str) -> str:
    return f"Concesionario prueba {dealer_id[-4:]}"


class Generador:
    def __init__(self, provincia: str, provincias_comunidad: tuple[str, ...], dealer_propio: str, semilla: int = 7):
        self.r = random.Random(semilla)
        self.v = random.Random(semilla * 1000 + 1)   # versiones, matriculación y km, aparte de lo demás
        cercanas = [p for p in provincias_comunidad if p != provincia][:4]
        lejanas = [p for p in OTRAS if p not in provincias_comunidad and p != provincia]
        self.provincias = [(provincia, 0.3)] + [(p, 0.2 / max(len(cercanas), 1)) for p in cercanas] + \
                          [(p, 0.48 / len(lejanas)) for p in lejanas]
        self.dealers = {p: [f"ESP9{p}{i}" for i in range(3)] for p, _ in self.provincias}
        self.provincia, self.propio = provincia, dealer_propio
        self.n = 0

    def nuevo(self, fuente: str, dia: date, dealer: str | None = None) -> dict:
        self.n += 1
        r = self.r
        modelo, base = r.choice(MODELOS[fuente])
        if dealer:
            cp = f"{self.provincia}{r.randint(1, 999):03d}"
        elif r.random() < 0.02:
            cp, dealer = None, "ESP9SINCP"
        else:
            prov = r.choices([p for p, _ in self.provincias], [w for _, w in self.provincias])[0]
            cp = f"{prov}{r.randint(1, 999):03d}"
            dealer = self.propio if prov == self.provincia and r.random() < 0.25 else r.choice(self.dealers[prov])
        precio = None if r.random() < 0.03 else base * r.uniform(0.85, 1.15)
        v = self.v
        acabado, f_acabado = v.choice(ACABADOS[fuente])
        clase = "electrico" if modelo.startswith("ID.") else ("hibrido" if fuente == "vn_otros" else "termico")
        if fuente == "vo" and clase == "termico" and v.random() < 0.12:   # VO con los cuatro combustibles
            clase = "hibrido"
        motor = v.choice(MOTORES[clase])
        automatico = clase != "termico" or v.random() < 0.6
        factor = f_acabado * motor[5] * (1.03 if automatico else 1.0)
        extra = {}
        if fuente == "vo":
            edad = v.randint(30, 9 * 365)
            km = int(edad / 365 * v.uniform(6000, 24000))
            factor *= max(0.4, 1 - 0.075 * edad / 365) * max(0.7, 1 - km / 500_000)
            extra = {"matriculacion": (dia - timedelta(days=edad)).isoformat(), "km": km}
        if precio is not None:
            precio = int(precio * factor) * 100
        return coche(f"ESP{fuente.upper()}{self.n:07d}", fuente=fuente, dealer_id=dealer, dealer=nombre_dealer(dealer),
                     cp=cp, modelo=modelo, precio=precio, dia=dia.isoformat(), ciudad="Ciudad prueba",
                     version=version(fuente, modelo, acabado, motor, automatico), cv=motor[3],
                     combustible=motor[4], **extra)


def escenario(base: Path, fin: date, provincia: str, provincias_comunidad: tuple[str, ...], dealer_propio: str,
              dias: int = 40, semilla: int = 7, escala: float = 1.0, colores: float = 0.6) -> dict:
    """Capturas de `dias` días hasta `fin`. VO falla el día 12, VN no tiene pasada el día 25 y VN
    eléctricos e híbridos empieza el día 8 (todos contados desde el primero). `escala` multiplica el
    tamaño de cada flota (make panel-dev usa más anuncios para que los segmentos tengan muestra).

    En la provincia del concesionario propio, un concesionario (`calla`) no publica nada el
    antepenúltimo día y otro (`nuevo`) aparece cinco días antes del final.

    `colores`: fracción de los anuncios vistos (los más antiguos) con ficha leída en la exportación de
    colores (exportar_colores); 0, sin exportación."""
    g = Generador(provincia, provincias_comunidad, dealer_propio, semilla)
    inicio = fin - timedelta(days=dias - 1)
    stock: dict[str, list[dict]] = {}
    huecos = {"vo": {12}, "vn": {25}, "vn_otros": set(range(8))}
    vistos: dict[str, tuple[str, dict, date]] = {}
    calla, nuevo = g.dealers[provincia][0], f"ESP9{provincia}N"
    dia_calla, dia_nuevo = fin - timedelta(days=2), fin - timedelta(days=4)
    for i in range(dias):
        dia = inicio + timedelta(days=i)
        if dia == dia_nuevo:
            g.dealers[provincia].append(nuevo)
            for fuente in stock:  # llega con unos cuantos anuncios de golpe
                stock[fuente] += [g.nuevo(fuente, dia, dealer=nuevo) for _ in range(4)]
        for fuente in ("vo", "vn", "vn_otros"):
            if fuente not in stock:
                if i in huecos[fuente]:
                    continue
                stock[fuente] = [g.nuevo(fuente, dia) for _ in range(int(TAMANO[fuente] * escala))]
            else:
                vivos = [c for c in stock[fuente] if g.r.random() > 0.035]
                vivos += [g.nuevo(fuente, dia) for _ in range(int(TAMANO[fuente] * escala * g.r.uniform(0.02, 0.05)))]
                stock[fuente] = vivos
            if i in huecos[fuente]:
                if fuente == "vo":
                    fallar(base, fuente, dia.isoformat())
                continue
            publicados = [dict(c) for c in stock[fuente]
                          if not (dia == dia_calla and c["business"]["dealer_id"] == calla)]
            capturar(base, fuente, dia.isoformat(), publicados)
            for c in publicados:
                vistos.setdefault(c["vehicle_id"], (fuente, c, dia))
    if colores:
        exportar_colores(base, vistos, fin, semilla, colores)
    return {"inicio": inicio, "fin": fin, "calla": (calla, dia_calla), "nuevo": (nuevo, dia_nuevo),
            "huecos": {f: {inicio + timedelta(days=i) for i in h} for f, h in huecos.items()}}
