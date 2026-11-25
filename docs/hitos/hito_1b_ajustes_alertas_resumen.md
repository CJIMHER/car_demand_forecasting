# Hito 1b · Ajustes: caída de ventas, reparto de alertas por rol y exclusiones de las negociaciones

Rama `hito-1b-ajustes-alertas` desde `origin/main` (`8a4ed67`). Sin push, sin conectarse al servidor. Un commit por
ajuste.

## 1. Campaña general: caída de ventas con 5 coches y sin las 2 últimas semanas

`config/campana_general.yaml`: `ventas.diferencia_minima: 5` y `ventas.semanas_recientes_excluidas: 2`. Las dos
ventanas de 8 semanas terminan 2 semanas antes de la última semana completa (Walcu cierra las ventas con retraso).
Hoy (25-11-2026) la ventana actual va del 14-9 al 2-11 (semanas) y la anterior, del 20-7 al 7-9.

Semanas en que habría saltado la regla con el histórico (`~/cdf_walcu/walcu_ventas_semana_historico.csv`), evaluando
cada lunes en que las dos ventanas ya tienen datos:

| Configuración | VN | VO |
| --- | --- | --- |
| Antes: diferencia 0, sin excluir semanas (76 lunes, del 16-6-2025 al 23-11-2026) | 28 (37 %) | 27 (36 %) |
| Diferencia 5, sin excluir semanas (76 lunes) | 22 (29 %) | 14 (18 %) |
| Diferencia 0, excluyendo 2 semanas (74 lunes, del 30-6-2025 al 23-11-2026) | 26 (35 %) | 26 (35 %) |
| **Ahora: diferencia 5, excluyendo 2 semanas (74 lunes)** | **20 (27 %)** | **14 (19 %)** |

Con el histórico, hoy saltaría para VN (5 ventas frente a 17, −71 %) y no para VO (9 frente a 8).

En el histórico ya cargado las semanas recientes están completas, así que excluirlas apenas cambia la frecuencia:
lo que evita es la caída falsa de cada lunes con los datos al día, cuando las 2 últimas semanas aún no tienen todas
sus ventas cerradas.
