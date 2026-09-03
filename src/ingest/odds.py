"""
Ingesta de cuotas de cierre de mercado (Bet365) desde los CSV ya
descargados de Football-Data.

USO EXCLUSIVAMENTE INTERNO (decision D-14).

Estas cuotas son un baseline de evaluacion: el mercado es el mejor
predictor publico que existe y sirve para saber cuanto le falta a EGO.

NUNCA se publican. Ni el numero, ni la comparacion, ni la mencion.
Por eso viven en una tabla propia, separada de `matches`: el JSON que
alimenta la redaccion se construye desde `matches`, asi que desde ahi
estas cuotas son fisicamente inalcanzables.
"""

import sqlite3
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[2]
DIR_RAW = RAIZ / "data" / "raw"
RUTA_DB = RAIZ / "data" / "laliga.db"

# Cuotas de cierre de Bet365: mejor cobertura historica de la fuente
COLUMNAS = {
    "Date": "fecha",
    "HomeTeam": "local",
    "AwayTeam": "visitante",
    "B365H": "cuota_local",
    "B365D": "cuota_empate",
    "B365A": "cuota_visitante",
}


def leer_csv(ruta: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(ruta, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(ruta, encoding="latin-1")

    faltantes = set(COLUMNAS) - set(df.columns)
    if faltantes:
        print(f"  {ruta.name}: faltan {sorted(faltantes)}, se omite")
        return None

    df = df[list(COLUMNAS)].rename(columns=COLUMNAS)
    df = df.dropna(subset=["local", "visitante"])
    df["fecha"] = pd.to_datetime(df["fecha"], dayfirst=True, format="mixed").dt.normalize()

    # Sin las tres cuotas la fila no sirve como baseline
    df = df.dropna(subset=["cuota_local", "cuota_empate", "cuota_visitante"])

    print(f"  {ruta.name}: {len(df)} partidos con cuotas")
    return df


def main() -> None:
    archivos = sorted(DIR_RAW.glob("SP1_*.csv"))
    if not archivos:
        raise SystemExit(
            "No hay CSV en data/raw/. Ejecuta antes src/ingest/football_data.py"
        )

    print("Leyendo cuotas de los CSV descargados...")
    tablas = [df for ruta in archivos if (df := leer_csv(ruta)) is not None]

    if not tablas:
        raise SystemExit("Ningun CSV tenia las columnas de cuotas.")

    odds = pd.concat(tablas, ignore_index=True)
    odds = odds.sort_values("fecha").reset_index(drop=True)

    with sqlite3.connect(RUTA_DB) as conexion:
        odds.to_sql("market_odds", conexion, if_exists="replace", index=False)
        partidos = pd.read_sql(
            "SELECT fecha, local, visitante FROM matches", conexion
        )

    # --- Cobertura frente a los partidos de la base ------------------
    partidos["fecha"] = pd.to_datetime(partidos["fecha"]).dt.normalize()
    cruce = partidos.merge(odds, on=["fecha", "local", "visitante"], how="left")
    con_cuota = cruce["cuota_local"].notna().sum()

    print(f"\nGuardadas {len(odds)} filas en la tabla market_odds")
    print(f"Cobertura: {con_cuota}/{len(partidos)} partidos ({con_cuota / len(partidos):.1%})")

    # Margen medio de la casa: cuanto suman las probabilidades implicitas
    implicita = (
        1 / odds["cuota_local"] + 1 / odds["cuota_empate"] + 1 / odds["cuota_visitante"]
    )
    print(f"Margen medio de la casa: {(implicita.mean() - 1) * 100:.1f}%")

    print("\nTablas en la base:")
    with sqlite3.connect(RUTA_DB) as conexion:
        tablas_db = pd.read_sql(
            "SELECT name FROM sqlite_master WHERE type='table'", conexion
        )
    print(tablas_db.to_string(index=False))


if __name__ == "__main__":
    main()