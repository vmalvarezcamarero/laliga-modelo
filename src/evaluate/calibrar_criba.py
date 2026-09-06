"""
Elige el umbral de la criba (P-04).

No se elige a ojo. Se elige mirando dos columnas a la vez:

1. Cuantos equipos pasan de media. Objetivo: entre 6 y 9 (00_PROYECTO
   §3). Ni tan pocos que resulte absurdo, ni tantos que no signifique
   nada.

2. Cuanta friccion produce. Si la lista de EGO coincide casi siempre con
   la clasificacion, la criba deja de ser un diferenciador y pasa a ser
   una tabla mas. La friccion es el producto, no un efecto secundario.

Se miden tres tipos de friccion:

  gana_sin_pasar    victorias de equipos por debajo del umbral
  pierde_pasando    derrotas de equipos por encima del umbral
  choque            un equipo por debajo gana a uno por encima
                    (el mas jugoso: da un post entero por si solo)

Uso:
    python -m src.evaluate.calibrar_criba

Tarda un segundo. Lee el CSV que genera fuerzas_historico.py.
"""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from src.models.criba import asignar_jornadas

RAIZ = Path(__file__).resolve().parents[2]
BD = RAIZ / "data" / "laliga.db"
FUERZAS = RAIZ / "outputs" / "calibracion" / "fuerzas_historico.csv"

UMBRALES = np.round(np.arange(1.00, 1.62, 0.02), 2)


def cargar() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not FUERZAS.exists():
        raise SystemExit(
            f"No encuentro {FUERZAS}.\n"
            "Ejecuta antes: python -m src.evaluate.fuerzas_historico"
        )

    fuerzas = pd.read_csv(FUERZAS)

    con = sqlite3.connect(BD)
    partidos = pd.read_sql("SELECT * FROM matches", con, parse_dates=["fecha"])
    con.close()

    temporadas = sorted(fuerzas["temporada"].unique())
    trozos = [
        asignar_jornadas(partidos[partidos["temporada"] == t]) for t in temporadas
    ]
    partidos = pd.concat(trozos, ignore_index=True)

    # Cada partido con la fuerza que tenian sus dos equipos ESA jornada.
    clave = ["temporada", "jornada", "equipo", "fuerza"]
    partidos = partidos.merge(
        fuerzas[clave].rename(columns={"equipo": "local", "fuerza": "fuerza_local"}),
        on=["temporada", "jornada", "local"],
        how="inner",
    ).merge(
        fuerzas[clave].rename(
            columns={"equipo": "visitante", "fuerza": "fuerza_visitante"}
        ),
        on=["temporada", "jornada", "visitante"],
        how="inner",
    )

    return fuerzas, partidos


def barrer(fuerzas: pd.DataFrame, partidos: pd.DataFrame) -> pd.DataFrame:
    n_jornadas = fuerzas.groupby(["temporada", "jornada"]).ngroups
    n_temporadas = fuerzas["temporada"].nunique()

    local_gana = partidos["resultado"] == "H"
    visita_gana = partidos["resultado"] == "A"

    filas = []
    for u in UMBRALES:
        pasan = (
            fuerzas.assign(pasa=fuerzas["fuerza"] >= u)
            .groupby(["temporada", "jornada"])["pasa"]
            .sum()
        )

        local_pasa = partidos["fuerza_local"] >= u
        visita_pasa = partidos["fuerza_visitante"] >= u

        gana_sin_pasar = int(
            (local_gana & ~local_pasa).sum() + (visita_gana & ~visita_pasa).sum()
        )
        pierde_pasando = int(
            (visita_gana & local_pasa).sum() + (local_gana & visita_pasa).sum()
        )
        choque = int(
            (local_gana & ~local_pasa & visita_pasa).sum()
            + (visita_gana & ~visita_pasa & local_pasa).sum()
        )

        filas.append(
            {
                "umbral": u,
                "pasan_media": round(pasan.mean(), 1),
                "pasan_min": int(pasan.min()),
                "pasan_max": int(pasan.max()),
                "gana_sin_pasar": round(gana_sin_pasar / n_temporadas, 1),
                "pierde_pasando": round(pierde_pasando / n_temporadas, 1),
                "choque": round(choque / n_temporadas, 1),
                "en_rango": "si" if 6 <= pasan.mean() <= 9 else "",
            }
        )

    print(f"Barrido sobre {n_jornadas} jornadas de {n_temporadas} temporadas.")
    print("Las columnas de friccion son casos POR TEMPORADA.\n")
    return pd.DataFrame(filas)


def main() -> None:
    fuerzas, partidos = cargar()
    print(f"{len(partidos)} partidos cruzados con su tabla de fuerzas.\n")

    tabla = barrer(fuerzas, partidos)
    print(tabla.to_string(index=False))

    validos = tabla[tabla["en_rango"] == "si"]
    print()
    if validos.empty:
        print("Ningun umbral deja pasar entre 6 y 9 equipos de media.")
        print("Revisa la escala de fuerza antes de seguir.")
        return

    mejor = validos.loc[validos["choque"].idxmax()]
    print(f"Umbrales dentro del objetivo 6-9: {validos['umbral'].min():.2f} "
          f"a {validos['umbral'].max():.2f}")
    print(f"El que mas choques directos genera: {mejor['umbral']:.2f} "
          f"({mejor['pasan_media']} equipos de media, "
          f"{mejor['choque']} choques por temporada)")
    print()
    print("Elige tu con la tabla delante. Luego fija UMBRAL en src/models/criba.py.")


if __name__ == "__main__":
    main()
