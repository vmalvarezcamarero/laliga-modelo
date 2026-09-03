"""
Backtest con validacion temporal estricta.

Para cada jornada, el modelo se reajusta usando UNICAMENTE partidos
anteriores a esa fecha, predice, y se guarda la prediccion. Nunca ve
datos del futuro.

Este es el error mas comun en modelado deportivo y el que produce
modelos que parecen geniales en el papel y luego no funcionan. Aqui la
frontera temporal se aplica en un solo sitio (la variable `corte`) para
que sea facil de auditar.

Baselines de comparacion (arquitectura, seccion 4):
  1. Uniforme 33/33/33
  2. Frecuencias base de LaLiga, calculadas SOLO con datos de
     entrenamiento
  3. Mercado (cuotas de cierre sin margen) - uso interno, D-14
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "src" / "models"))
sys.path.insert(0, str(RAIZ / "src" / "evaluate"))

import dixon_coles as dc  # noqa: E402
from rps import (  # noqa: E402
    brier,
    cuotas_a_probabilidades,
    resultado_a_indice,
    rps,
)

RUTA_DB = RAIZ / "data" / "laliga.db"
DIR_SALIDA = RAIZ / "outputs" / "predictions"

# Partidos minimos antes de empezar a predecir. Dos temporadas.
MIN_ENTRENAMIENTO = 760


def cargar(temporadas: list[str] | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    with sqlite3.connect(RUTA_DB) as conexion:
        partidos = pd.read_sql("SELECT * FROM matches", conexion)
        odds = pd.read_sql("SELECT * FROM market_odds", conexion)

    partidos["fecha"] = pd.to_datetime(partidos["fecha"])
    odds["fecha"] = pd.to_datetime(odds["fecha"])

    partidos = partidos.dropna(subset=["xg_local", "xg_visitante"])
    partidos = partidos.sort_values("fecha").reset_index(drop=True)

    if temporadas:
        # Se conservan las temporadas anteriores para entrenar, pero
        # solo se EVALUA sobre las pedidas.
        partidos["evaluar"] = partidos["temporada"].isin(temporadas)
    else:
        partidos["evaluar"] = True

    return partidos, odds


def ejecutar(partidos: pd.DataFrame, odds: pd.DataFrame, xi: float) -> pd.DataFrame:
    # Agrupamos por semana natural: es una aproximacion buena a la
    # jornada y no depende de que la fuente publique el numero de
    # jornada, que no lo hace.
    partidos = partidos.copy()
    partidos["bloque"] = partidos["fecha"].dt.to_period("W")

    bloques = sorted(partidos.loc[partidos["evaluar"], "bloque"].unique())

    filas = []
    saltados_pocos_datos = 0
    saltados_equipo_nuevo = 0
    inicio = time.time()

    for i, bloque in enumerate(bloques, start=1):
        objetivo = partidos[(partidos["bloque"] == bloque) & partidos["evaluar"]]
        if objetivo.empty:
            continue

        # ---- LA FRONTERA TEMPORAL ----------------------------------
        corte = objetivo["fecha"].min()
        entrenamiento = partidos[partidos["fecha"] < corte]
        # ------------------------------------------------------------

        if len(entrenamiento) < MIN_ENTRENAMIENTO:
            saltados_pocos_datos += len(objetivo)
            continue

        parametros = dc.ajustar(entrenamiento, xi=xi, referencia=corte)

        # Frecuencias base: tambien solo con datos de entrenamiento
        res_entr = resultado_a_indice(
            entrenamiento["goles_local"], entrenamiento["goles_visitante"]
        )
        frecuencias = np.bincount(res_entr, minlength=3) / len(res_entr)

        for _, partido in objetivo.iterrows():
            local, visitante = partido["local"], partido["visitante"]
            if local not in parametros.equipos or visitante not in parametros.equipos:
                # Recien ascendido sin historico: no se puede predecir
                saltados_equipo_nuevo += 1
                continue

            matriz = dc.matriz_marcadores(parametros, local, visitante)
            p_local, p_empate, p_visit = dc.probabilidades_1x2(matriz)

            filas.append(
                {
                    "fecha": partido["fecha"],
                    "temporada": partido["temporada"],
                    "local": local,
                    "visitante": visitante,
                    "goles_local": partido["goles_local"],
                    "goles_visitante": partido["goles_visitante"],
                    "ego_local": p_local,
                    "ego_empate": p_empate,
                    "ego_visitante": p_visit,
                    "base_local": frecuencias[0],
                    "base_empate": frecuencias[1],
                    "base_visitante": frecuencias[2],
                    "entropia": dc.entropia(matriz),
                    "n_entrenamiento": len(entrenamiento),
                }
            )

        if i % 20 == 0 or i == len(bloques):
            transcurrido = time.time() - inicio
            print(
                f"  bloque {i}/{len(bloques)} - {len(filas)} predicciones "
                f"- {transcurrido:.0f}s"
            )

    if saltados_pocos_datos:
        print(f"  {saltados_pocos_datos} partidos sin evaluar: entrenamiento insuficiente")
    if saltados_equipo_nuevo:
        print(f"  {saltados_equipo_nuevo} partidos sin evaluar: equipo sin historico")

    if not filas:
        raise SystemExit("No se ha podido evaluar ningun partido.")

    resultado = pd.DataFrame(filas)

    # Mercado: se une al final, solo para los partidos evaluados
    resultado = resultado.merge(
        odds, on=["fecha", "local", "visitante"], how="left"
    )

    return resultado


def resumir(pred: pd.DataFrame, xi: float) -> None:
    real = resultado_a_indice(pred["goles_local"], pred["goles_visitante"])

    ego = pred[["ego_local", "ego_empate", "ego_visitante"]].to_numpy()
    base = pred[["base_local", "base_empate", "base_visitante"]].to_numpy()
    uniforme = np.full((len(pred), 3), 1 / 3)

    modelos = {
        "EGO (Dixon-Coles sobre xG)": ego,
        "Frecuencias base de LaLiga": base,
        "Uniforme 33/33/33": uniforme,
    }

    tiene_cuota = pred["cuota_local"].notna()
    if tiene_cuota.any():
        mercado = cuotas_a_probabilidades(
            pred.loc[tiene_cuota, "cuota_local"],
            pred.loc[tiene_cuota, "cuota_empate"],
            pred.loc[tiene_cuota, "cuota_visitante"],
        )

    print(f"\n{'=' * 62}")
    print(f"RESULTADO DEL BACKTEST  (xi = {xi}, {len(pred)} partidos)")
    print(f"{'=' * 62}")
    print(f"{'Modelo':<32}{'RPS':>10}{'Brier':>10}")
    print("-" * 62)

    rps_ego = rps(ego, real).mean()
    for nombre, probs in modelos.items():
        r = rps(probs, real).mean()
        b = brier(probs, real).mean()
        print(f"{nombre:<32}{r:>10.4f}{b:>10.4f}")

    if tiene_cuota.any():
        real_m = real[tiene_cuota.to_numpy()]
        r = rps(mercado, real_m).mean()
        b = brier(mercado, real_m).mean()
        print(f"{'Mercado (interno, D-14)':<32}{r:>10.4f}{b:>10.4f}")

    print("-" * 62)

    rps_uniforme = rps(uniforme, real).mean()
    mejora = (rps_uniforme - rps_ego) / rps_uniforme * 100

    print(f"\nEGO mejora al 33/33/33 en un {mejora:.1f}%")
    if rps_ego < rps_uniforme:
        print("CRITERIO DE EXITO DEL SPRINT 1: CUMPLIDO")
    else:
        print("CRITERIO DE EXITO DEL SPRINT 1: NO CUMPLIDO")

    print("\nRPS por temporada:")
    pred = pred.assign(rps_ego=rps(ego, real), rps_unif=rps(uniforme, real))
    por_temporada = pred.groupby("temporada").agg(
        partidos=("rps_ego", "size"),
        ego=("rps_ego", "mean"),
        uniforme=("rps_unif", "mean"),
    )
    print(por_temporada.round(4).to_string())


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest de EGO")
    parser.add_argument(
        "--xi",
        type=float,
        default=0.0018,
        help="decaimiento temporal (0 = todos los partidos pesan igual)",
    )
    parser.add_argument(
        "--temporadas",
        nargs="*",
        default=None,
        help="temporadas a evaluar; el resto se usa solo para entrenar",
    )
    parser.add_argument(
        "--guardar",
        action="store_true",
        help="guardar las predicciones en outputs/predictions/",
    )
    args = parser.parse_args()

    partidos, odds = cargar(args.temporadas)
    n_eval = int(partidos["evaluar"].sum())
    print(f"Partidos en la base: {len(partidos)}  |  a evaluar: {n_eval}")
    print(f"xi = {args.xi}\n")

    pred = ejecutar(partidos, odds, xi=args.xi)
    resumir(pred, args.xi)

    if args.guardar:
        DIR_SALIDA.mkdir(parents=True, exist_ok=True)
        destino = DIR_SALIDA / f"backtest_xi{args.xi}.csv"
        pred.to_csv(destino, index=False)
        print(f"\nGuardado en {destino}")


if __name__ == "__main__":
    main()