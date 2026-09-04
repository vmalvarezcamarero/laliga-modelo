"""Ingesta del calendario de LaLiga desde football-data.org.

Una llamada semanal. Reescribe la tabla `fixtures` entera.

La clave vive en la variable de entorno FOOTBALL_DATA_KEY.
Nunca se escribe en un fichero del repo.
"""

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.models.equipos import a_canonico, NombreDesconocido

BASE = Path(__file__).resolve().parents[2]
DB = BASE / "data" / "laliga.db"

URL = "https://api.football-data.org/v4/competitions/PD/matches"
MADRID = ZoneInfo("Europe/Madrid")

PARTIDOS_ESPERADOS = 380
JORNADAS_ESPERADAS = 38
EQUIPOS_ESPERADOS = 20

# Estados que football-data.org documenta.
ESTADOS_CONOCIDOS = {
    "SCHEDULED", "TIMED", "IN_PLAY", "PAUSED",
    "FINISHED", "SUSPENDED", "POSTPONED", "CANCELLED", "AWARDED",
}

# Lista blanca: SOLO esto cuenta como partido jugado.
# La fuente ya ha devuelto basura en el campo `status` (una fecha en vez de
# un estado). Nada que no esté aquí se da por terminado, pase lo que pase.
ESTADOS_TERMINADOS = {"FINISHED", "AWARDED"}

# Partidos que no se juegan en su jornada: no la bloquean.
ESTADOS_FUERA = {"POSTPONED", "CANCELLED"}


def descargar(temporada_inicio: int) -> dict:
    clave = os.environ.get("FOOTBALL_DATA_KEY")
    if not clave:
        raise RuntimeError(
            "Falta la variable de entorno FOOTBALL_DATA_KEY.\n"
            'En PowerShell:  $env:FOOTBALL_DATA_KEY = "tu_clave"'
        )
    r = requests.get(
        URL,
        headers={"X-Auth-Token": clave},
        params={"season": temporada_inicio},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def transformar(datos: dict) -> tuple[list[tuple], str]:
    """Convierte la respuesta en filas listas para insertar."""
    if not datos.get("matches"):
        raise RuntimeError("La respuesta no trae partidos.")

    inicio = datos["matches"][0]["season"]["startDate"]
    anyo = int(inicio[:4])
    temporada = f"{anyo}-{str(anyo + 1)[2:]}"

    filas = []
    for p in datos["matches"]:
        local = a_canonico(p["homeTeam"]["name"])
        visitante = a_canonico(p["awayTeam"]["name"])

        utc = datetime.fromisoformat(p["utcDate"].replace("Z", "+00:00"))
        madrid = utc.astimezone(MADRID)

        estado = p.get("status")
        if estado not in ESTADOS_CONOCIDOS:
            print(
                f"   AVISO: estado no reconocido en {local}-{visitante}: "
                f"{estado!r}. Se guarda como DESCONOCIDO."
            )
            estado = "DESCONOCIDO"

        arbitros = p.get("referees") or []
        arbitro = next(
            (a["name"] for a in arbitros if a.get("type") == "REFEREE"), None
        )

        filas.append((
            p["id"],
            temporada,
            p["matchday"],
            madrid.isoformat(),
            local,
            visitante,
            estado,
            arbitro,
        ))

    return filas, temporada


def verificar(filas: list[tuple]) -> None:
    """Si algo no cuadra, se detiene sin escribir."""
    if len(filas) != PARTIDOS_ESPERADOS:
        raise RuntimeError(
            f"{len(filas)} partidos, se esperaban {PARTIDOS_ESPERADOS}."
        )

    jornadas = {f[2] for f in filas}
    if len(jornadas) != JORNADAS_ESPERADAS:
        raise RuntimeError(
            f"{len(jornadas)} jornadas distintas, se esperaban {JORNADAS_ESPERADAS}."
        )

    equipos = {f[4] for f in filas} | {f[5] for f in filas}
    if len(equipos) != EQUIPOS_ESPERADOS:
        raise RuntimeError(
            f"{len(equipos)} equipos distintos, se esperaban {EQUIPOS_ESPERADOS}: "
            f"{sorted(equipos)}"
        )


def escribir(filas: list[tuple], temporada: str) -> None:
    con = sqlite3.connect(DB)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS fixtures (
                fuente_id   INTEGER PRIMARY KEY,
                temporada   TEXT    NOT NULL,
                jornada     INTEGER NOT NULL,
                fecha_hora  TEXT    NOT NULL,
                local       TEXT    NOT NULL,
                visitante   TEXT    NOT NULL,
                estado      TEXT    NOT NULL,
                arbitro     TEXT
            )
        """)
        con.execute("DELETE FROM fixtures WHERE temporada = ?", (temporada,))
        con.executemany(
            "INSERT INTO fixtures VALUES (?, ?, ?, ?, ?, ?, ?, ?)", filas
        )
        con.commit()
    finally:
        con.close()


def proxima_jornada(temporada: str) -> int | None:
    """La jornada más baja con partidos pendientes de jugar.

    Lista blanca: solo FINISHED y AWARDED cuentan como jugados. Un estado
    corrupto en la fuente deja la jornada abierta, que es el error seguro:
    peor sería dar por jugado un partido que no lo está y publicar la
    auditoría del lunes incompleta.

    Aplazados y cancelados no bloquean su jornada.
    """
    excluidos = sorted(ESTADOS_TERMINADOS | ESTADOS_FUERA)
    marcadores = ",".join("?" * len(excluidos))
    con = sqlite3.connect(DB)
    try:
        fila = con.execute(
            f"SELECT MIN(jornada) FROM fixtures "
            f"WHERE temporada = ? AND estado NOT IN ({marcadores})",
            (temporada, *excluidos),
        ).fetchone()
    finally:
        con.close()
    return fila[0] if fila else None


def main(temporada_inicio: int = 2026) -> None:
    print(f"Descargando calendario de {temporada_inicio}...")
    datos = descargar(temporada_inicio)

    try:
        filas, temporada = transformar(datos)
    except NombreDesconocido as e:
        print(f"\nPARADA: {e}")
        print("No se ha escrito nada en la base.")
        raise SystemExit(1)

    verificar(filas)

    escribir(filas, temporada)

    jugados = sum(1 for f in filas if f[6] in ESTADOS_TERMINADOS)
    raros = sum(1 for f in filas if f[6] == "DESCONOCIDO")
    con_arbitro = sum(1 for f in filas if f[7])

    print(f"OK. Temporada {temporada}: {len(filas)} partidos escritos.")
    print(f"   Jugados: {jugados}  |  Con árbitro: {con_arbitro}")
    if raros:
        print(f"   Estados corruptos en la fuente: {raros}")
    print(f"   Próxima jornada: {proxima_jornada(temporada)}")


if __name__ == "__main__":
    main()