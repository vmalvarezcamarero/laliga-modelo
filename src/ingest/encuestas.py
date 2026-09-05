"""
Tabla de encuestas: los votos del publico.

El publico es el tercer baseline del lunes (D-31) y el rival narrativo
de EGO en F4 y F7. Los votos se meten a mano el domingo desde el movil:
tres numeros leidos de la encuesta de X, treinta segundos. Automatizarlo
exigiria leer X por API, que es Fase 2 y de pago.

La encuesta es de UN SOLO partido por semana (el gancho F4). El publico
se evalua solo sobre ese, acumulado a lo largo de la temporada: a la
jornada 10 llevas 10 partidos comparados. Preguntar los 10 cada semana
muere en la semana 3 porque nadie vota diez encuestas.
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DB = BASE / "data" / "laliga.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS encuestas (
    temporada        TEXT    NOT NULL,
    jornada          INTEGER NOT NULL,
    partido_id       TEXT    NOT NULL,
    votos_local      INTEGER NOT NULL,
    votos_empate     INTEGER NOT NULL,
    votos_visitante  INTEGER NOT NULL,
    cerrada_en       TEXT,
    PRIMARY KEY (temporada, partido_id)
)
"""


def crear() -> None:
    con = sqlite3.connect(DB)
    try:
        con.execute(ESQUEMA)
        con.commit()
    finally:
        con.close()


def registrar(
    temporada: str,
    jornada: int,
    partido_id: str,
    local: int,
    empate: int,
    visitante: int,
) -> None:
    """
    Guarda los votos de una encuesta. Si ya existe, la reemplaza.

    Los tres numeros son votos absolutos, no porcentajes. X los da en
    porcentaje y en total: se apunta el total y los tres porcentajes
    salen solos. Si solo tienes porcentajes, mete los porcentajes; la
    proporcion es lo unico que se usa.
    """
    if local + empate + visitante == 0:
        raise ValueError("Una encuesta sin votos no se guarda.")

    con = sqlite3.connect(DB)
    try:
        con.execute(
            "INSERT OR REPLACE INTO encuestas VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                temporada, jornada, partido_id,
                local, empate, visitante,
                datetime.now().astimezone().isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()


def leer(temporada: str, partido_id: str) -> tuple[float, float, float] | None:
    """
    Probabilidades del publico para un partido, o None si no hay
    encuesta. Se devuelven normalizadas a 1.
    """
    con = sqlite3.connect(DB)
    try:
        fila = con.execute(
            "SELECT votos_local, votos_empate, votos_visitante FROM encuestas "
            "WHERE temporada = ? AND partido_id = ?",
            (temporada, partido_id),
        ).fetchone()
    finally:
        con.close()

    if not fila:
        return None

    total = sum(fila)
    if total == 0:
        return None
    return tuple(v / total for v in fila)


if __name__ == "__main__":
    crear()

    if len(sys.argv) == 7:
        temporada, jornada, partido_id, vl, ve, vv = sys.argv[1:]
        registrar(temporada, int(jornada), partido_id, int(vl), int(ve), int(vv))
        print(f"Guardada: {partido_id}  {vl} / {ve} / {vv}")
    else:
        con = sqlite3.connect(DB)
        try:
            tablas = [
                r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ]
            n = con.execute("SELECT COUNT(*) FROM encuestas").fetchone()[0]
        finally:
            con.close()

        print(f"Tablas en la base: {', '.join(tablas)}")
        print(f"Encuestas registradas: {n}")
        print()
        print("Para meter una encuesta el domingo:")
        print("  python -m src.ingest.encuestas 2026-27 4 "
              "J4_Elche_Sociedad 320 145 210")