"""
Ingesta de xG desde Understat y cruce con los partidos de Football-Data.

El modelo se ajusta sobre xG, no sobre goles (decision D-02), asi que
esta es la fuente critica del proyecto.

El cruce entre fuentes se hace por fecha + equipos. No hay identificador
comun, solo el nombre, y cada fuente escribe los nombres a su manera.
Por eso el script VALIDA el cruce y se detiene si no cuadra, en lugar
de perder partidos en silencio.

IDEMPOTENCIA. Este script se ejecuta desde un cron cada martes y cada
domingo, asi que tiene que dar el mismo resultado la vez 1 y la vez 50.
La primera version no lo era: al volver a ejecutarse sobre una tabla
`matches` que YA tenia columnas de xG, el merge las renombraba a
`xg_local_x` y `xg_local_y` y todo lo posterior fallaba con
`KeyError: 'xg_local'`. Se descartan antes de cruzar: el xG siempre
viene de Understat, las que hubiera en la base son de una ejecucion
anterior.

El diccionario de nombres vive en `src/models/equipos.py` (D-30).
"""

import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
import soccerdata as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.models.equipos import UNDERSTAT_A_CANONICO

# --- Configuracion -------------------------------------------------

PRIMERA_TEMPORADA = 2014
ULTIMA_TEMPORADA = 2026

# Si cruza menos de este porcentaje, algo va mal y no escribimos nada
UMBRAL_CRUCE = 0.99

RAIZ = Path(__file__).resolve().parents[2]
RUTA_DB = RAIZ / "data" / "laliga.db"

COLUMNAS_XG = ["xg_local", "xg_visitante"]


def codigo_temporada(anio: int) -> str:
    """2014 -> '1415', el formato que espera soccerdata."""
    return f"{anio % 100:02d}{(anio + 1) % 100:02d}"


def descargar_temporadas() -> pd.DataFrame:
    """Descarga el calendario con xG de todas las temporadas."""
    tablas = []
    for anio in range(PRIMERA_TEMPORADA, ULTIMA_TEMPORADA + 1):
        codigo = codigo_temporada(anio)
        etiqueta = f"{anio}-{(anio + 1) % 100:02d}"
        try:
            us = sd.Understat(leagues="ESP-La Liga", seasons=codigo)
            df = us.read_schedule().reset_index()
        except Exception as error:
            print(f"  {etiqueta}: NO disponible ({type(error).__name__})")
            continue

        # Partidos aun no jugados no traen xG
        df = df[df["has_data"] == True]  # noqa: E712
        df = df.dropna(subset=["home_xg", "away_xg"])

        print(f"  {etiqueta}: {len(df)} partidos con xG")
        tablas.append(df)
        time.sleep(2)  # Understat es un sitio pequeno, sin prisa

    if not tablas:
        raise SystemExit("No se ha descargado ninguna temporada de Understat.")

    return pd.concat(tablas, ignore_index=True)


def traducir_nombres(df: pd.DataFrame) -> pd.DataFrame:
    """
    Traduce los nombres de Understat al canonico interno.
    Si aparece un nombre que no esta en el diccionario, se detiene:
    traducirlo mal es peor que no traducirlo.
    """
    nombres = set(df["home_team"]) | set(df["away_team"])
    desconocidos = sorted(nombres - set(UNDERSTAT_A_CANONICO))

    if desconocidos:
        print("\nNombres de Understat sin equivalencia definida:")
        for nombre in desconocidos:
            print(f"  - {nombre}")
        raise SystemExit(
            "\nAnade estos nombres a UNDERSTAT_A_CANONICO en "
            "src/models/equipos.py y vuelve a ejecutar."
        )

    df = df.copy()
    df["local"] = df["home_team"].map(UNDERSTAT_A_CANONICO)
    df["visitante"] = df["away_team"].map(UNDERSTAT_A_CANONICO)
    return df


def preparar_xg(df: pd.DataFrame) -> pd.DataFrame:
    """Se queda con las columnas del cruce y normaliza la fecha a dia."""
    df = df.copy()
    df["fecha"] = pd.to_datetime(df["date"]).dt.normalize()
    return df[["fecha", "local", "visitante", "home_xg", "away_xg"]].rename(
        columns={"home_xg": "xg_local", "away_xg": "xg_visitante"}
    )


def cruzar(partidos: pd.DataFrame, xg: pd.DataFrame) -> pd.DataFrame:
    """
    Une los partidos de Football-Data con el xG de Understat.

    Primero intenta cruzar por fecha exacta. Los que fallan se reintentan
    permitiendo un dia de diferencia: Understat guarda la hora en su propia
    zona horaria y un partido nocturno puede quedar en el dia siguiente.
    """
    partidos = partidos.copy()
    partidos["fecha"] = pd.to_datetime(partidos["fecha"]).dt.normalize()

    # Idempotencia: fuera el xG de ejecuciones anteriores. Sin esto, el
    # merge renombra las columnas a _x/_y y el script solo funciona la
    # primera vez que se ejecuta en su vida.
    sobrantes = [c for c in COLUMNAS_XG if c in partidos.columns]
    if sobrantes:
        print(f"  Descartando xG de una ejecucion anterior: {sobrantes}")
        partidos = partidos.drop(columns=sobrantes)

    unido = partidos.merge(
        xg, on=["fecha", "local", "visitante"], how="left", validate="one_to_one"
    )

    sin_xg = unido["xg_local"].isna()
    if sin_xg.any():
        print(f"\n  {sin_xg.sum()} partidos sin cruce por fecha exacta. Reintentando +/- 1 dia...")
        recuperados = 0
        for idx in unido.index[sin_xg]:
            fila = unido.loc[idx]
            candidatos = xg[
                (xg["local"] == fila["local"])
                & (xg["visitante"] == fila["visitante"])
                & ((xg["fecha"] - fila["fecha"]).abs() <= pd.Timedelta(days=1))
            ]
            if len(candidatos) == 1:
                unido.loc[idx, "xg_local"] = candidatos.iloc[0]["xg_local"]
                unido.loc[idx, "xg_visitante"] = candidatos.iloc[0]["xg_visitante"]
                recuperados += 1
        print(f"  Recuperados {recuperados} por tolerancia de fecha.")

    return unido


def main() -> None:
    print("Descargando Understat...")
    crudo = descargar_temporadas()

    print(f"\nTotal descargado: {len(crudo)} partidos con xG")

    traducido = traducir_nombres(crudo)
    xg = preparar_xg(traducido)

    with sqlite3.connect(RUTA_DB) as conexion:
        partidos = pd.read_sql("SELECT * FROM matches", conexion)

    print(f"Partidos en la base: {len(partidos)}")

    # Understat suele ir por delante: publica el xG de un partido antes
    # de que Football-Data lo incluya en su CSV semanal. Esos partidos
    # entraran en la base en la siguiente ingesta.
    sobran = len(crudo) - len(partidos)
    if sobran > 0:
        print(f"  ({sobran} partidos con xG que Football-Data aun no publica)")

    unido = cruzar(partidos, xg)

    # --- Validacion del cruce ---------------------------------------
    # Los partidos de la temporada en curso pueden no tener xG todavia
    con_xg = unido["xg_local"].notna()
    total = len(unido)
    cruzados = int(con_xg.sum())
    tasa = cruzados / total

    print(f"\nCruce: {cruzados}/{total} ({tasa:.1%})")

    if not con_xg.all():
        fallidos = unido[~con_xg]
        print("\nPartidos sin xG, por temporada:")
        print(fallidos.groupby("temporada").size().to_string())
        print("\nPrimeros 10 casos:")
        print(fallidos[["temporada", "fecha", "local", "visitante"]].head(10).to_string(index=False))

    if tasa < UMBRAL_CRUCE:
        raise SystemExit(
            f"\nCruce por debajo del umbral ({UMBRAL_CRUCE:.0%}). "
            "No se escribe nada en la base. Revisa los casos de arriba."
        )

    with sqlite3.connect(RUTA_DB) as conexion:
        unido.to_sql("matches", conexion, if_exists="replace", index=False)

    print(f"\nGuardado. {cruzados} partidos con xG en {RUTA_DB}")
    print("\nMuestra:")
    muestra = unido[con_xg][
        ["fecha", "local", "visitante", "goles_local", "goles_visitante", "xg_local", "xg_visitante"]
    ].tail(5)
    print(muestra.to_string(index=False))


if __name__ == "__main__":
    main()