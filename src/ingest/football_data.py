"""
Ingesta de Football-Data.co.uk para LaLiga (SP1).

Descarga los CSV historicos, los normaliza y los guarda en SQLite.
Los CSV crudos se conservan en data/raw/ para no depender de la red
si hay que reprocesar.
"""

import sqlite3
import time
from pathlib import Path

import pandas as pd
import requests

# --- Configuracion -------------------------------------------------

PRIMERA_TEMPORADA = 2014   # 2014-15, inicio de cobertura de Understat
ULTIMA_TEMPORADA = 2026    # 2026-27, temporada en curso

URL_BASE = "https://www.football-data.co.uk/mmz4281/{codigo}/SP1.csv"

# La raiz del proyecto son dos niveles por encima de este archivo:
# src/ingest/football_data.py -> src/ingest -> src -> laliga-modelo
RAIZ = Path(__file__).resolve().parents[2]
DIR_RAW = RAIZ / "data" / "raw"
RUTA_DB = RAIZ / "data" / "laliga.db"

# Nombres cripticos de Football-Data -> nombres legibles
COLUMNAS = {
    "Date": "fecha",
    "HomeTeam": "local",
    "AwayTeam": "visitante",
    "FTHG": "goles_local",
    "FTAG": "goles_visitante",
    "FTR": "resultado",
    "Referee": "arbitro",
    "HS": "tiros_local",
    "AS": "tiros_visitante",
    "HST": "tiros_puerta_local",
    "AST": "tiros_puerta_visitante",
    "HC": "corners_local",
    "AC": "corners_visitante",
    "HF": "faltas_local",
    "AF": "faltas_visitante",
    "HY": "amarillas_local",
    "AY": "amarillas_visitante",
    "HR": "rojas_local",
    "AR": "rojas_visitante",
}


def codigo_temporada(anio: int) -> str:
    """2014 -> '1415'. Es el formato que usa Football-Data en la URL."""
    return f"{anio % 100:02d}{(anio + 1) % 100:02d}"


def etiqueta_temporada(anio: int) -> str:
    """2014 -> '2014-15'. Formato legible para guardar en la base."""
    return f"{anio}-{(anio + 1) % 100:02d}"


def descargar(anio: int) -> Path | None:
    """
    Descarga el CSV de una temporada si no lo tenemos ya.
    Devuelve la ruta al archivo, o None si no se pudo descargar.
    """
    codigo = codigo_temporada(anio)
    destino = DIR_RAW / f"SP1_{codigo}.csv"

    if destino.exists():
        print(f"  {etiqueta_temporada(anio)}: ya descargado")
        return destino

    url = URL_BASE.format(codigo=codigo)
    try:
        respuesta = requests.get(url, timeout=30)
        respuesta.raise_for_status()
    except requests.RequestException as error:
        print(f"  {etiqueta_temporada(anio)}: NO disponible ({error})")
        return None

    destino.write_bytes(respuesta.content)
    print(f"  {etiqueta_temporada(anio)}: descargado ({len(respuesta.content) // 1024} KB)")
    time.sleep(1)  # cortesia con el servidor
    return destino


def leer_y_normalizar(ruta: Path, anio: int) -> pd.DataFrame | None:
    """Lee un CSV crudo y devuelve un DataFrame con columnas normalizadas."""
    # Football-Data usa codificacion latin-1 en algunas temporadas
    try:
        df = pd.read_csv(ruta, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(ruta, encoding="latin-1")

    # Quedarnos solo con las columnas que existen en esta temporada
    disponibles = {k: v for k, v in COLUMNAS.items() if k in df.columns}
    faltantes = set(COLUMNAS) - set(disponibles)
    if faltantes:
        print(f"    aviso: faltan columnas {sorted(faltantes)}")

    df = df[list(disponibles)].rename(columns=disponibles)

    # Filas basura: al final de algunos CSV hay lineas vacias
    df = df.dropna(subset=["local", "visitante"])

    # Las fechas vienen como dd/mm/yy o dd/mm/yyyy segun la temporada
    df["fecha"] = pd.to_datetime(df["fecha"], dayfirst=True, format="mixed")

    df.insert(0, "temporada", etiqueta_temporada(anio))

    return df


def main() -> None:
    DIR_RAW.mkdir(parents=True, exist_ok=True)

    print("Descargando temporadas...")
    tablas = []
    for anio in range(PRIMERA_TEMPORADA, ULTIMA_TEMPORADA + 1):
        ruta = descargar(anio)
        if ruta is None:
            continue
        df = leer_y_normalizar(ruta, anio)
        if df is not None and not df.empty:
            tablas.append(df)

    if not tablas:
        print("\nNo se ha podido descargar ninguna temporada. Abortando.")
        return

    partidos = pd.concat(tablas, ignore_index=True)
    partidos = partidos.sort_values("fecha").reset_index(drop=True)

    RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(RUTA_DB) as conexion:
        partidos.to_sql("matches", conexion, if_exists="replace", index=False)

    # --- Comprobacion: LaLiga son 380 partidos por temporada ---------
    print("\nPartidos por temporada:")
    conteo = partidos.groupby("temporada").size()
    for temporada, n in conteo.items():
        marca = "" if n == 380 else "  <-- revisar"
        print(f"  {temporada}: {n}{marca}")

    print(f"\nTotal: {len(partidos)} partidos guardados en {RUTA_DB}")
    print(f"Rango de fechas: {partidos['fecha'].min().date()} a {partidos['fecha'].max().date()}")
    print(f"Equipos distintos: {partidos['local'].nunique()}")


if __name__ == "__main__":
    main()