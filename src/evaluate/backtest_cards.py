"""
Backtest del modelo de tarjetas.

Este backtest existe para poder RETIRAR las tarjetas con un dato en la
mano, no con una intuicion. Las amarillas tienen ratio varianza/media de
1.13, casi Poisson puro, asi que hay motivos para sospechar que la
estructura equipo-contra-rival aportara poco. Si el modelo no bate a la
media historica del equipo, se retira del alcance de la temporada 1
(opcion 3 de 01_ARQUITECTURA.md §3.3) y queda registrado por que.

Baselines
---------
1. Media de la liga, distinguiendo local de visitante.
2. Media historica del equipo, sin mirar al rival. Este es el que hay
   que batir de verdad.

Metricas
--------
log-score (decide) y MAE (explicable en publico).

Frontera temporal
-----------------
Entrena solo con partidos anteriores a la jornada evaluada.

Uso:
    python -m src.evaluate.backtest_cards
"""

import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.models import cards
from src.models.criba import XI, asignar_jornadas

RAIZ = Path(__file__).resolve().parents[2]
BD = RAIZ / "data" / "laliga.db"

TEMPORADAS = ["2024-25", "2025-26"]
MIN_PARTIDOS_EQUIPO = 5


def cargar_partidos() -> pd.DataFrame:
    con = sqlite3.connect(BD)
    df = pd.read_sql("SELECT * FROM matches", con, parse_dates=["fecha"])
    con.close()
    return df.sort_values("fecha").reset_index(drop=True)


def media_por_equipo(entrenamiento: pd.DataFrame):
    media_liga_local = float(entrenamiento["amarillas_local"].mean())
    media_liga_visit = float(entrenamiento["amarillas_visitante"].mean())

    en_casa = entrenamiento.groupby("local")["amarillas_local"].agg(["mean", "size"])
    fuera = entrenamiento.groupby("visitante")["amarillas_visitante"].agg(
        ["mean", "size"]
    )

    casa = {
        equipo: (fila["mean"] if fila["size"] >= MIN_PARTIDOS_EQUIPO else media_liga_local)
        for equipo, fila in en_casa.iterrows()
    }
    visita = {
        equipo: (fila["mean"] if fila["size"] >= MIN_PARTIDOS_EQUIPO else media_liga_visit)
        for equipo, fila in fuera.iterrows()
    }

    return casa, visita, media_liga_local, media_liga_visit


def log_score(observado: int, mu: float) -> float:
    dist = cards.distribucion(mu)
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

            p = cards.ajustar(entrenamiento, xi=XI, referencia=corte)
            casa, visita, liga_local, liga_visit = media_por_equipo(entrenamiento)

            for _, partido in bloque.iterrows():
                local, visitante = partido["local"], partido["visitante"]

                if local not in p.equipos or visitante not in p.equipos:
                    continue

                mu_local, mu_visit = cards.tarjetas_esperadas(p, local, visitante)

                for lado, observado, mu_modelo, mu_liga, mu_equipo in [
                    ("local", partido["amarillas_local"], mu_local, liga_local,
                     casa.get(local, liga_local)),
                    ("visitante", partido["amarillas_visitante"], mu_visit, liga_visit,
                     visita.get(visitante, liga_visit)),
                ]:
                    registros.append(
                        {
                            "lado": lado,
                            "observado": observado,
                            "mu_modelo": mu_modelo,
                            "mu_liga": mu_liga,
                            "mu_equipo": mu_equipo,
                            "ls_modelo": log_score(observado, mu_modelo),
                            "ls_liga": log_score(observado, mu_liga),
                            "ls_equipo": log_score(observado, mu_equipo),
                            "factor_local": p.factor_local,
                        }
                    )

    df = pd.DataFrame(registros)
    print(f"Evaluadas {len(df)} predicciones de equipo "
          f"({len(df)//2} partidos) en {(time.time()-inicio)/60:.1f} min.\n")

    print("=== RESULTADO ===\n")
    print(f"{'Modelo':<28} {'log-score':>10} {'MAE':>8}")
    print("-" * 48)
    for etiqueta, columna_ls, columna_mu in [
        ("EGO (Poisson, sin arbitro)", "ls_modelo", "mu_modelo"),
        ("Media historica del equipo", "ls_equipo", "mu_equipo"),
        ("Media de la liga", "ls_liga", "mu_liga"),
    ]:
        ls = df[columna_ls].mean()
        mae = (df["observado"] - df[columna_mu]).abs().mean()
        print(f"{etiqueta:<28} {ls:>10.4f} {mae:>8.3f}")

    contra_equipo = df["ls_modelo"].mean() - df["ls_equipo"].mean()
    contra_liga = df["ls_equipo"].mean() - df["ls_liga"].mean()

    print()
    print(f"Saber QUIEN juega aporta:      {contra_liga:+.4f}")
    print(f"Ajustar por EL RIVAL aporta:   {contra_equipo:+.4f}")
    print()

    if contra_equipo > 0.005:
        print("El modelo aporta lo suficiente. Las tarjetas se quedan.")
    elif contra_equipo > 0:
        print("El modelo gana, pero por un margen despreciable.")
        print("Las tarjetas no dan para formato propio. Como mucho, F6 (ruido).")
    else:
        print("El modelo NO bate a la media del equipo.")
        print("Retirar las tarjetas del alcance (01_ARQUITECTURA.md §3.3, opcion 3)")
        print("o publicar simplemente la media historica de cada equipo.")

    print(f"\nFactor local medio: {df['factor_local'].mean():.3f}")
    print("(Por debajo de 1.00 significa que el local recibe menos tarjetas.)")


if __name__ == "__main__":
    main()
