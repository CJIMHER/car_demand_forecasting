# Hito 1b · Walcu: tipo VN/VO, exclusiones y equivalencias: resumen

Rama `hito-1b-walcu-tipo`, desde `origin/main` en `6febb55` (precio provisional). Sin push y sin conectarse
al servidor.

## Qué cambia

1. **Tipo VN/VO** (`scripts/walcu_agregar.py`, `config/walcu_export.yaml`), por este orden:
   1. El nombre de la campaña, con las reglas de ámbito de `config/campanas.yaml`: `[VN]` o `[VO]`; si no, VN
      o VO al principio del nombre; si no, una palabra de `palabras_vo` (ocasión, boutique, seminuevo,
      seminuevos) → VO.
   2. Si el nombre no lo dice: sede en `sedes_vo` (Carpa VO) o equipo en `equipos_vo` (VO) → VO.
   3. El campo «Tipo de vehículo solicitado» (`tipos`: VO y Km0 → VO).
   4. `tipo_por_defecto` (VN).

   Sede y Equipo se leen solo para esto y para las exclusiones; nunca se escriben ni se muestran. El
   registro dice cuántos leads salen de cada paso.
2. **Exclusiones** (`config/concesionario.yaml`).
   - PV en `propio.palabras_excluidas` (posventa: «PV [2025] …», «VW [PV] [2025] …»).
   - `propio.empresas_excluidas: [Covey]`. Al agregar, los leads con ese texto en sede, equipo u origen se
     descartan, se cuentan y se avisa. En `make referencias-meta`, las campañas de Meta o de Walcu cuyo
     nombre lo lleva quedan fuera, con el mismo recuento y aviso que las otras marcas.
3. **Equivalencias Walcu → Meta** (`config/campanas_walcu.yaml`).
   - Por regla:
     - sufijos « Campaña», « - leadgen» y « - landing», además de -copy, copia y -OK;
     - sin espacios entre corchetes («[VO] [2025]» = «[VO][2025]»).
   - Así casan sin equivalencia: Boutique ocasión, T-ROC febrero, Caddy eHybrid y Test Drive ID3-ID4.
   - A mano, las otras seis: Gama ahorro (2025 → 2026), los dos Avisa Days, ID Polo B, ABRIL 2 y «Sin título
     22/1/25, 15:18».
   - «Lanzamiento» y «Lanzamiento Julio» (generales de VN de Walcu, sin modelo) van en `sin_equivalencia`:
     salen en el informe como «campaña de Walcu sin campaña de Meta», sin pedir una equivalencia.
4. **Histórico**: `~/cdf_walcu/walcu_semanal_historico.csv`, validado desde allí sin copiarlo: 1.280 filas,
   100 semanas, sin avisos.

## Decisiones tomadas sin preguntar

1. **« B» y « 2» van a mano, no por regla.**
   - Quitar una letra o un número sueltos al final rompería nombres legítimos («ID 3», «Golf R»).
   - La de « B» además necesita quitar «Test - » del nombre de Meta.
2. **Espacios entre corchetes por regla**: «[VO] [2025]» y «[VO][2025]» son la misma campaña. Se aplica a
   los dos lados, así que no cambia ningún cruce que ya funcionara.
3. **«Lanzamiento» no fuerza el tipo VN de los leads.**
   - El histórico corregido trae 3 filas de «Lanzamiento» como VO, que salen de la sede o el equipo de VO.
   - He seguido el CSV: el nombre no dice el tipo, así que deciden la sede o el equipo y luego el campo.
   - No las he añadido como asignaciones en `config/campanas.yaml`: una prueba exige que el repositorio no
     traiga campañas de ningún concesionario (regla Replicable), y esas asignaciones solo valdrían para una
     campaña de Meta con ese nombre, que no existe.
   - Si se quiere que todos sus leads sean VN, basta una lista de nombres con tipo fijo en
     `walcu_export.yaml`.
4. **VN o VO al principio**: palabra entera; «Volkswagen Days» no es VO.
5. **Dos etiquetas** (`[VN]` y `[VO]`): el nombre no dice el tipo y se pasa a la sede o el equipo, como en
   `config/campanas.yaml`.
6. **Sede y equipo de VO**: coincidencia exacta, sin mayúsculas ni tildes. **Empresas excluidas**: el texto
   contenido en cualquier parte, como pide el encargo («Covey Autocasión»).
7. **Dónde va cada lista.**
   - `empresas_excluidas` va en `config/concesionario.yaml`, junto a las otras marcas, porque la usan el Mac
     (agregar) y el servidor (referencias).
   - `sedes_vo`, `equipos_vo` y `palabras_vo` van en `config/walcu_export.yaml`, como sugiere el encargo.
8. **Sede y Equipo pasan a ser columnas obligatorias** de la exportación. Sin ellas, el script no escribe nada
   y dice qué columnas faltan.
9. **PV como palabra entera**: «PVP» no cae.

## Para revisar

- La primera exportación con estas reglas: el registro dice cuántos leads van a VO por el nombre, por la sede
  o el equipo y por el campo.
- `make referencias-meta` en el servidor después de cargar el histórico: lo que quede en «sin cruce».

## Pruebas

`tests/test_walcu_meta_hist.py`, de 85 a 115:
- el tipo en cada paso, en el orden del encargo;
- la configuración, sin «Carpa» ni «Covey» en el código;
- Covey por sede, equipo y origen: contado, con aviso y sin escribir sede ni equipo;
- PV;
- los diez pares del encargo, diciendo cuáles casan por regla y cuáles a mano;
- los sufijos nuevos;
- Lanzamiento en el informe;
- Covey fuera de las referencias.

Las pruebas que ya había se adaptan a las columnas Sede y Equipo, a PV y a que el nombre de la campaña manda
sobre el campo. Suite completa en verde.
