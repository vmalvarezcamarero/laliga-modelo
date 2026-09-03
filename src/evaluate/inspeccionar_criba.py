"""
Verificacion cualitativa de la criba (P-04, segunda mitad).

El barrido de umbrales dice cuantos casos de friccion hay. Este script
dice CUALES son, con nombres, fechas y marcadores. Sirve para dos cosas:

1. Comprobar con ojo de aficionado que la lista de equipos que pasan la
   criba tiene sentido. Un numero no detecta que el umbral es absurdo;
   una lista con el Getafe por delante del Atletico, si.

2. Sacar material. Estos casos son el contenido del formato F1 y del F7.

No reajusta el modelo: lee el CSV de fuerzas_historico.py y lo cruza con
los resultados. Tarda un segundo.

Uso:
    python -m src.evaluate.inspeccionar_criba

Para mirar otra temporada u otro umbral, cambia las constantes de abajo.
"""

import sqlite3
from pathlib import Path

import pandas as pd

from src.models.criba import UMBRAL, asignar_jornadas

RAIZ = Path(__file__).resolve().parents[2]
BD = RAIZ / "data" / "laliga.db"
FUERZAS = RAIZ / "outputs" / "predictions" / "fuerzas_historico.csv"

TEMPORADA = "2025-26"
XG_DE_SUERTE = 1.0  # ganar generando menos de esto es ganar sin merecerlo
CUANTOS = 15


def cargar():
    if not FUERZAS.exists():
        raise SystemExit(
            f"No encuentro {FUERZAS}.\n"
            "Ejecuta antes: python -m src.evaluate.fuerzas_historico"
        )

    fuerzas = pd.read_csv(FUERZAS)
    fuerzas = fuerzas[fuerzas["temporada"] == TEMPORADA]

    con = sqlite3.connect(BD)
    partidos = pd.read_sql(
        "SELECT * FROM matches WHERE temporada = ?",
        con,
        params=(TEMPORADA,),
        parse_dates=["fecha"],
    )
    con.close()

    partidos = asignar_jornadas(partidos)

    clave = ["temporada", "jornada", "equipo", "fuerza"]
    partidos = partidos.merge(
        fuerzas[clave].rename(columns={"equipo": "local", "fuerza": "f_local"}),
        on=["temporada", "jornada", "local"],
        how="inner",
    ).merge(
        fuerzas[clave].rename(columns={"equipo": "visitante", "fuerza": "f_visit"}),
        on=["temporada", "jornada", "visitante"],
        how="inner",
    )

    # Normalizamos a la vista "ganador / perdedor" para no repetir la
    # misma logica cuatro veces mas abajo.
    gana_local = partidos["resultado"] == "H"
    gana_visit = partidos["resultado"] == "A"
    jugados = partidos[gana_local | gana_visit].copy()
    es_local = jugados["resultado"] == "H"

    jugados["ganador"] = jugados["local"].where(es_local, jugados["visitante"])
    jugados["perdedor"] = jugados["visitante"].where(es_local, jugados["local"])
    jugados["f_ganador"] = jugados["f_local"].where(es_local, jugados["f_visit"])
    jugados["f_perdedor"] = jugados["f_visit"].where(es_local, jugados["f_local"])
    jugados["xg_ganador"] = jugados["xg_local"].where(es_local, jugados["xg_visitante"])
    jugados["xg_perdedor"] = jugados["xg_visitante"].where(es_local, jugados["xg_local"])
    jugados["marcador"] = (
        jugados["goles_local"].astype(str) + "-" + jugados["goles_visitante"].astype(str)
    )
    jugados["donde"] = jugados["local"] + " - " + jugados["visitante"]

    return fuerzas, jugados


def criba_de_una_jornada(fuerzas: pd.DataFrame, jornada: int) -> None:
    print(f"=== LA CRIBA — {TEMPORADA}, jornada {jornada} (umbral {UMBRAL}) ===\n")

    tabla = fuerzas[fuerzas["jornada"] == jornada].sort_values(
        "fuerza", ascending=False
    )
    corte_dibujado = False

    for _, fila in tabla.iterrows():
        if not corte_dibujado and fila["fuerza"] < UMBRAL:
            print("  " + "-" * 46 + f"  linea de corte: {UMBRAL}")
            corte_dibujado = True
        marca = "PASA" if fila["fuerza"] >= UMBRAL else "    "
        distancia = fila["fuerza"] - UMBRAL
        print(
            f"  {marca}  {fila['equipo']:<16} "
            f"fuerza {fila['fuerza']:.2f}   "
            f"({distancia:+.2f} del umbral)"
        )
    print()


def choques(jugados: pd.DataFrame) -> None:
    print(f"=== CHOQUES: gana quien no pasa a quien si pasa ({TEMPORADA}) ===\n")

    casos = jugados[
        (jugados["f_ganador"] < UMBRAL) & (jugados["f_perdedor"] >= UMBRAL)
    ].copy()
    casos["brecha"] = casos["f_perdedor"] - casos["f_ganador"]
    casos = casos.sort_values("brecha", ascending=False)

    print(f"Total en la temporada: {len(casos)}. Los {CUANTOS} de mayor brecha:\n")
    for _, f in casos.head(CUANTOS).iterrows():
        print(
            f"  J{f['jornada']:>2}  {f['fecha'].date()}  {f['donde']:<32} {f['marcador']}"
        )
        print(
            f"        gana {f['ganador']} ({f['f_ganador']:.2f}) "
            f"a {f['perdedor']} ({f['f_perdedor']:.2f})   "
            f"brecha {f['brecha']:.2f}   "
            f"xG {f['xg_ganador']:.2f} - {f['xg_perdedor']:.2f}"
        )
    print()


def ganar_sin_merecerlo(jugados: pd.DataFrame) -> None:
    print(f"=== GANAR SIN MERECERLO: victorias con menos de {XG_DE_SUERTE} xG ===\n")

    casos = jugados[
        (jugados["f_ganador"] < UMBRAL) & (jugados["xg_ganador"] < XG_DE_SUERTE)
    ].copy()
    casos["diferencia_xg"] = casos["xg_perdedor"] - casos["xg_ganador"]
    casos = casos.sort_values("diferencia_xg", ascending=False)

    if casos.empty:
        print("  Ninguno. Revisa el umbral: el argumento central del")
        print("  proyecto se queda sin material.\n")
        return

    print(f"Total en la temporada: {len(casos)}. Los {CUANTOS} mas sangrantes:\n")
    for _, f in casos.head(CUANTOS).iterrows():
        print(
            f"  J{f['jornada']:>2}  {f['fecha'].date()}  {f['donde']:<32} {f['marcador']}"
        )
        print(
            f"        {f['ganador']} gana generando {f['xg_ganador']:.2f} xG "
            f"frente a {f['xg_perdedor']:.2f} del rival"
        )
    print()


def reparto_por_jornada(jugados: pd.DataFrame) -> None:
    print("=== REPARTO DE CHOQUES POR JORNADA ===\n")

    casos = jugados[(jugados["f_ganador"] < UMBRAL) & (jugados["f_perdedor"] >= UMBRAL)]
    por_jornada = casos.groupby("jornada").size().reindex(range(1, 39), fill_value=0)

    for jornada, n in por_jornada.items():
        print(f"  J{jornada:>2}  {'#' * int(n)}{'' if n else '(ninguno)'}")

    sin_choque = int((por_jornada == 0).sum())
    print()
    print(f"  Media: {por_jornada.mean():.2f} por jornada")
    print(f"  Jornadas sin ningun choque: {sin_choque} de 38")
    if sin_choque > 20:
        print("  Aviso: demasiadas semanas sin material para el formato F1.")
    print()


def main() -> None:
    fuerzas, jugados = cargar()
    ultima = int(fuerzas["jornada"].max())

    criba_de_una_jornada(fuerzas, ultima)
    choques(jugados)
    ganar_sin_merecerlo(jugados)
    reparto_por_jornada(jugados)


if __name__ == "__main__":
    main()
