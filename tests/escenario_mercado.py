"""Escenario sintético de varios días para Mercado: huecos, una fuente que empieza más tarde,
altas, ausencias y retiradas. Se escribe con el código real de los scrapers (sinteticos.capturar).

Lo usan las pruebas, «make panel-dev» y las capturas de pantalla. Ningún dato real: concesionarios,
anuncios y precios inventados.
"""

from __future__ import annotations

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
                 ("Tiguan eHybrid", 47900), ("Golf eHybrid", 39900)],
}
TAMANO = {"vo": 260, "vn": 340, "vn_otros": 70}
OTRAS = ["28", "08", "46", "50", "48", "15", "33", "30"]


def nombre_dealer(dealer_id: str) -> str:
    return f"Concesionario prueba {dealer_id[-4:]}"


class Generador:
    def __init__(self, provincia: str, provincias_comunidad: tuple[str, ...], dealer_propio: str, semilla: int = 7):
        self.r = random.Random(semilla)
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
        precio = None if r.random() < 0.03 else int(base * r.uniform(0.85, 1.15)) * 100
        return coche(f"ESP{fuente.upper()}{self.n:07d}", fuente=fuente, dealer_id=dealer, dealer=nombre_dealer(dealer),
                     cp=cp, modelo=modelo, precio=precio, dia=dia.isoformat(), ciudad="Ciudad prueba")


def escenario(base: Path, fin: date, provincia: str, provincias_comunidad: tuple[str, ...], dealer_propio: str,
              dias: int = 40, semilla: int = 7) -> dict:
    """Capturas de `dias` días hasta `fin`. VO falla el día 12, VN no tiene pasada el día 25 y VN
    eléctricos e híbridos empieza el día 8 (todos contados desde el primero).

    En la provincia del concesionario propio, un concesionario (`calla`) no publica nada el
    antepenúltimo día y otro (`nuevo`) aparece cinco días antes del final."""
    g = Generador(provincia, provincias_comunidad, dealer_propio, semilla)
    inicio = fin - timedelta(days=dias - 1)
    stock: dict[str, list[dict]] = {}
    huecos = {"vo": {12}, "vn": {25}, "vn_otros": set(range(8))}
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
                stock[fuente] = [g.nuevo(fuente, dia) for _ in range(TAMANO[fuente])]
            else:
                vivos = [c for c in stock[fuente] if g.r.random() > 0.035]
                vivos += [g.nuevo(fuente, dia) for _ in range(int(TAMANO[fuente] * g.r.uniform(0.02, 0.05)))]
                stock[fuente] = vivos
            if i in huecos[fuente]:
                if fuente == "vo":
                    fallar(base, fuente, dia.isoformat())
                continue
            publicados = [dict(c) for c in stock[fuente]
                          if not (dia == dia_calla and c["business"]["dealer_id"] == calla)]
            capturar(base, fuente, dia.isoformat(), publicados)
    return {"inicio": inicio, "fin": fin, "calla": (calla, dia_calla), "nuevo": (nuevo, dia_nuevo),
            "huecos": {f: {inicio + timedelta(days=i) for i in h} for f, h in huecos.items()}}
