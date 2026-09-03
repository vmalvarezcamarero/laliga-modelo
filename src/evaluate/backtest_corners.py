"""
Backtest del modelo de corners.

Mismo criterio que el resto del proyecto: el modelo no vale porque el
manual diga que la binomial negativa es lo correcto, sino porque bate a
alternativas mas tontas en datos que no ha visto.

Baselines
---------
1. Media de la liga. Todos los equipos igual, solo distingue local de
   visitante. Es el "no saber nada".
2. Media historica del equipo. Cuantos corners saca ese equipo de media
   en casa o fuera, sin mirar quien es el rival. Es el baseline que de
   verdad hay que batir: si el modelo no lo supera, toda la estructura
   equipo-contra-rival no aporta nada y sobra.

A los dos baselines se les da la MISMA dispersion que estima el modelo.
Asi lo unico que se compara es si la estructura aporta, y no salen
perdiendo por una razon ajena a lo que estamos midiendo. Es ser generoso
con el rival a proposito.

Metricas
--------
log-score: premia acertar la distribucion entera, no solo el numero
central. Mas alto es mejor. Es la metrica que decide.

MAE: error medio en corners. Mas bajo es mejor. No decide nada, pero es
la que se puede explicar en publico sin hablar de verosimilitudes.

Frontera temporal
-----------------
Se entrena con partidos ESTRICTAMENTE anteriores a la jornada evaluada.
Mismo criterio que backtest.py.

Uso:
    python -m src.evaluate.backtest_corners
"""

import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.models import corners
from src.models.criba import XI, asignar_jornadas

RAIZ = Path(__file__).resolve().parents[2]
BD = RAIZ / "data" / "laliga.db"

TEMPORADAS = ["2024-25", "2025-26"]
MIN_PARTIDOS_EQUIPO = 5  # por debajo, el baseline de equipo usa la liga


def cargar_partidos() -> pd.DataFrame:
    con = sqlite3.connect(BD)
    df = pd.read_sql("SELECT * FROM matches", con, parse_dates=["fecha"])
    con.close()
    return df.sort_values("fecha").reset_index(drop=True)


def media_por_equipo(entrenamiento: pd.DataFrame) -> tuple[dict, dict, float, float]:
    """
    Corners medios de cada equipo en casa y fuera, mas las medias de la
    liga como respaldo para equipos con pocos partidos.
    """
    media_liga_local = float(entrenamiento["corners_local"].mean())
    media_liga_visit = float(entrenamiento["corners_visitante"].mean())

    en_casa = entrenamiento.groupby("local")["corners_local"].agg(["mean", "size"])
    fuera = entrenamiento.groupby("visitante")["corners_visitante"].agg(["mean", "size"])

    casa = {
        equipo: (fila["mean"] if fila["size"] >= MIN_PARTIDOS_EQUIPO else media_liga_local)
        for equipo, fila in en_casa.iterrows()
    }
    visita = {
        equipo: (fila["mean"] if fila["size"] >= MIN_PARTIDOS_EQUIPO else media_liga_visit)
        for equipo, fila in fuera.iterrows()
    }

    return casa, visita, media_liga_local, media_liga_visit


def log_score(observado: int, mu: float, r: float) -> float:
    """Logaritmo de la probabilidad que el modelo daba a lo que paso."""
    dist = corners.distribucion(mu, r)
    indice = min(int(observado), len(dist) - 1)
    return float(np.log(max(dist[indice], 1e-12)))


def main() -> None:
    partidos = cargar_partidos()
    print(f"Cargados {len(partidos)} partidos.\n")

    registros = []
    inicio = time.time()

    for temporada in TEMPORADAS:
        de_la_temporada = asignar_jornadas(partidos[partidos["temporada"] == temporada])

        for jornada, bloque in de_la_temporada.groupby("jornada"):
            corte = bloque["fecha"].min()

            # Toda la validacion temporal esta aqui. El '<' es estricto.
            entrenamiento = partidos[partidos["fecha"] < corte]

            p = corners.ajustar(entrenamiento, xi=XI, referencia=corte)
            casa, visita, liga_local, liga_visit = media_por_equipo(entrenamiento)
            r = p.dispersion

            for _, partido in bloque.iterrows():
                local, visitante = partido["local"], partido["visitante"]

                # Equipos sin historico: no se predicen a ciegas.
                if local not in p.equipos or visitante not in p.equipos:
                    continue

                mu_local, mu_visit = corners.corners_esperados(p, local, visitante)

                for lado, observado, mu_modelo, mu_liga, mu_equipo in [
                    ("local", partido["corners_local"], mu_local, liga_local,
                     casa.get(local, liga_local)),
                    ("visitante", partido["corners_visitante"], mu_visit, liga_visit,
                     visita.get(visitante, liga_visit)),
                ]:
                    registros.append(
                        {
                            "temporada": temporada,
                            "jornada": jornada,
                            "lado": lado,
                            "observado": observado,
                            "mu_modelo": mu_modelo,
                            "mu_liga": mu_liga,
                            "mu_equipo": mu_equipo,
                            "ls_modelo": log_score(observado, mu_modelo, r),
                            "ls_liga": log_score(observado, mu_liga, r),
                            "ls_equipo": log_score(observado, mu_equipo, r),
                            "dispersion": r,
                        }
                    )

                registros[-1]["total_observado"] = (
                    partido["corners_local"] + partido["corners_visitante"]
                )
                registros[-1]["total_esperado"] = mu_local + mu_visit

    df = pd.DataFrame(registros)
    print(f"Evaluadas {len(df)} predicciones de equipo "
          f"({len(df)//2} partidos) en {(time.time()-inicio)/60:.1f} min.\n")

    print("=== RESULTADO ===\n")
    print(f"{'Modelo':<28} {'log-score':>10} {'MAE':>8}")
    print("-" * 48)
    for etiqueta, columna_ls, columna_mu in [
        ("EGO (binomial negativa)", "ls_modelo", "mu_modelo"),
        ("Media historica del equipo", "ls_equipo", "mu_equipo"),
        ("Media de la liga", "ls_liga", "mu_liga"),
    ]:
        ls = df[columna_ls].mean()
        mae = (df["observado"] - df[columna_mu]).abs().mean()
        print(f"{etiqueta:<28} {ls:>10.4f} {mae:>8.3f}")

    mejora = df["ls_modelo"].mean() - df["ls_equipo"].mean()
    print()
    if mejora > 0:
        print(f"El modelo bate a la media del equipo por {mejora:.4f} de log-score.")
        print("La estructura equipo-contra-rival aporta. Se queda.")
    else:
        print(f"El modelo NO bate a la media del equipo ({mejora:.4f}).")
        print("La estructura no aporta nada. Simplifica: usa la media del equipo.")

    print(f"\nDispersion media estimada: {df['dispersion'].mean():.1f}")
    print("(Cuanto mas alta, mas se parece a una Poisson.)")

    # --- Independencia entre los dos equipos -------------------------
    totales = df.dropna(subset=["total_observado"])
    print("\n=== INDEPENDENCIA DE LOS DOS EQUIPOS ===\n")

    residuo_local = (
        df[df["lado"] == "local"]["observado"].to_numpy()
        - df[df["lado"] == "local"]["mu_modelo"].to_numpy()
    )
    residuo_visit = (
        df[df["lado"] == "visitante"]["observado"].to_numpy()
        - df[df["lado"] == "visitante"]["mu_modelo"].to_numpy()
    )
    correlacion = float(np.corrcoef(residuo_local, residuo_visit)[0, 1])

    print(f"Correlacion entre los errores de los dos equipos: {correlacion:+.3f}")
    if abs(correlacion) < 0.10:
        print("Practicamente independientes. Se puede sumar las dos")
        print("distribuciones para predecir el total del partido.")
    else:
        print("NO son independientes. Sumar las dos distribuciones")
        print("exagera la varianza del total. No publiques totales")
        print("calculados asi sin corregirlo antes.")

    sesgo = (totales["total_observado"] - totales["total_esperado"]).mean()
    print(f"\nSesgo del total: {sesgo:+.2f} corners por partido")


if __name__ == "__main__":
    main()
