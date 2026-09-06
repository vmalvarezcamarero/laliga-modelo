"""
Ingesta de Football-Data.co.uk para LaLiga (SP1).

Descarga los CSV historicos, los normaliza y los guarda en SQLite.
Los CSV crudos se conservan en data/raw/ para no depender de la red
si hay que reprocesar. `data/raw/` NO se versiona, asi que en un runner
de GitHub Actions siempre se descarga todo desde cero.

TRES COSAS APRENDIDAS EN EL PRIMER CRON:

1. La temporada EN CURSO se vuelve a descargar siempre. La cache por
   "si el fichero existe, no lo bajo" es correcta para el historico,
   que no cambia, y desastrosa para la temporada viva: el cron del
   martes no traeria nunca un resultado nuevo.

2. Sin cabecera de navegador, football-data.co.uk puede rechazar la
   peticion. Es una web pequena con proteccion basica y `requests` sin
   User-Agent se identifica como un script. El CSV es publico y de
   descarga libre; esto no evade nada, solo se presenta.

3. Si no se descarga nada, el script MUERE con codigo de error. Antes
   hacia `return` y terminaba limpiamente: GitHub marcaba el paso como
   correcto y el fallo aparecia tres pasos despues, en otro modulo.
   Un cron que "termina bien" sin hacer nada es peor que uno que falla.
"""

import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
import requests

# --- Configuracion -------------------------------------------------

PRIMERA_TEMPORADA = 2014   # 2014-15, inicio de cobertura de Understat
ULTIMA_TEMPORADA = 2026    # 2026-27, temporada en curso

URL_BASE = "https://www.football-data.co.uk/mmz4281/{codigo}/SP1.csv"

CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# Minimo de temporadas para dar la ingesta por buena. Si se descargan
# menos, algo va mal aunque tecnicamente haya datos.
MINIMO_TEMPORADAS = 10

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
    Descarga el CSV de una temporada. Devuelve la ruta, o None si falla.

    Las temporadas cerradas se cachean; la temporada EN CURSO se intenta
    descargar siempre, porque cambia cada fin de semana.

    SI LA DESCARGA FALLA Y HAY COPIA LOCAL, SE USA LA COPIA. Un 503 del
    servidor no puede hacer que una temporada entera desaparezca de la
    base: es un fallo temporal de un tercero y el dato de la semana
    pasada sigue siendo valido.
    """
    codigo = codigo_temporada(anio)
    destino = DIR_RAW / f"SP1_{codigo}.csv"
    en_curso = anio == ULTIMA_TEMPORADA

    if destino.exists() and not en_curso:
        print(f"  {etiqueta_temporada(anio)}: ya descargado")
        return destino

    url = URL_BASE.format(codigo=codigo)
    fallo = None
    try:
        respuesta = requests.get(url, headers=CABECERAS, timeout=30)
        respuesta.raise_for_status()
        # Una web puede devolver una pagina de error con codigo 200.
        # Sin esto se guardaria HTML como si fueran datos.
        if not respuesta.content.startswith(b"Div,"):
            fallo = f"la respuesta no parece un CSV ({len(respuesta.content)} bytes)"
        else:
            destino.write_bytes(respuesta.content)
            sufijo = " (en curso)" if en_curso else ""
            print(
                f"  {etiqueta_temporada(anio)}: descargado "
                f"({len(respuesta.content) // 1024} KB){sufijo}"
            )
            time.sleep(1)  # cortesia con el servidor
            return destino
    except requests.RequestException as error:
        fallo = str(error)

    if destino.exists():
        print(f"  {etiqueta_temporada(anio)}: fallo la descarga ({fallo}). "
              f"Se usa la copia local.")
        return destino

    print(f"  {etiqueta_temporada(anio)}: NO disponible ({fallo})")
    return None


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

    # Muere con codigo de error, no con un return limpio. Un cron que
    # termina bien sin hacer nada es un fallo invisible.
    if len(tablas) < MINIMO_TEMPORADAS:
        raise SystemExit(
            f"\nSolo se han obtenido {len(tablas)} temporadas de "
            f"{ULTIMA_TEMPORADA - PRIMERA_TEMPORADA + 1}. Se esperaban al "
            f"menos {MINIMO_TEMPORADAS}. No se escribe nada en la base."
        )

    partidos = pd.concat(tablas, ignore_index=True)
    partidos = partidos.sort_values("fecha").reset_index(drop=True)

    RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(RUTA_DB) as conexion:
        partidos.to_sql("matches", conexion, if_exists="replace", index=False)

    # --- Comprobacion: LaLiga son 380 partidos por temporada ---------
    print("\nPartidos por temporada:")
    conteo = partidos.groupby("temporada").size()
    for temporada, n in conteo.items():
        # La temporada en curso tiene menos, y eso es correcto.
        esperado = n == 380 or temporada == etiqueta_temporada(ULTIMA_TEMPORADA)
        marca = "" if esperado else "  <-- revisar"
        print(f"  {temporada}: {n}{marca}")

    print(f"\nTotal: {len(partidos)} partidos guardados en {RUTA_DB}")
    print(f"Rango de fechas: {partidos['fecha'].min().date()} a {partidos['fecha'].max().date()}")
    print(f"Equipos distintos: {partidos['local'].nunique()}")


if __name__ == "__main__":
    main()