"""
La auditoria del lunes: EGO se somete a su propia criba.

Es la publicacion mas importante del proyecto (D-12). Se publica gane o
pierda, y es lo que sostiene la credibilidad de todo lo demas.

DOS REGLAS QUE GOBIERNAN ESTE MODULO:

1. Solo se audita lo que se publico (D-26). Este modulo LEE el JSON de
   prediccion, no recalcula nada. Recalcular con los datos de hoy daria
   otro numero, y auditar una prediccion que nunca publicaste es lo
   contrario de lo que hace esta cuenta.

2. El veredicto lo calcula el pipeline, no EGO (D-12). El lunes el
   redactor recibe un booleano. No opina sobre si mismo: es la unica
   forma de que el personaje no pueda escaquearse redactando.

EL LISTON (D-31): EGO pasa si bate a las frecuencias base de LaLiga, y
desde que haya encuestas tambien al publico. Es un AND, no una media:
si bastara con uno de los dos siempre habria una lectura favorable y el
lunes perderia tension en dos meses.

NOTA SOBRE rps(): devuelve el RPS de CADA partido, no la media. Se
agrega aqui con np.mean(). Es la firma correcta: asi el backtest puede
agregar como quiera.
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.evaluate.rps import brier, resultado_a_indice, rps
from src.ingest.encuestas import leer as leer_encuesta
from src.models.equipos import a_publicable
from src.models.semana import para_auditar

BASE = Path(__file__).resolve().parents[2]
DB = BASE / "data" / "laliga.db"
SALIDA = BASE / "outputs" / "predictions"

VERSION_ESQUEMA = 1


# --- Datos ----------------------------------------------------------


def _resultados(hasta: str | None = None) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql(
            "SELECT fecha, local, visitante, goles_local, goles_visitante "
            "FROM matches WHERE goles_local IS NOT NULL",
            con,
        )
    finally:
        con.close()
    df["fecha"] = pd.to_datetime(df["fecha"])
    if hasta:
        df = df[df["fecha"].dt.date < pd.Timestamp(hasta).date()]
    return df


def _prediccion_de_la_ventana(desde: str) -> tuple[dict, Path]:
    """
    El JSON cuya ventana coincide con la que se audita.

    Se busca por ventana y no por numero de jornada: en una semana
    mezclada la etiqueta editorial no identifica el fichero.
    """
    for ruta in sorted(SALIDA.glob("*_prediccion.json"), reverse=True):
        doc = json.loads(ruta.read_text(encoding="utf-8"))
        if doc.get("ventana", {}).get("desde") == desde:
            return doc, ruta
    raise FileNotFoundError(
        f"No hay JSON de prediccion para la ventana que empieza en {desde}. "
        f"Sin prediccion publicada no hay nada que auditar (D-26)."
    )


def _frecuencias_base(entrenamiento: pd.DataFrame) -> np.ndarray:
    """
    El liston. Se calcula con los MISMOS datos que uso la prediccion,
    para que la comparacion sea justa: si EGO entreno hasta el miercoles,
    el baseline tambien.
    """
    indices = [
        resultado_a_indice(g, v)
        for g, v in zip(
            entrenamiento["goles_local"], entrenamiento["goles_visitante"]
        )
    ]
    return np.bincount(indices, minlength=3) / len(indices)


# --- Cruce prediccion <-> resultado ----------------------------------


def _buscar_resultado(
    jugados: pd.DataFrame, local: str, visitante: str, fecha: str
) -> tuple[int, int] | None:
    """Tolerancia de +-1 dia: `matches.fecha` no trae hora."""
    dia = pd.Timestamp(fecha).date()
    for delta in (0, -1, 1):
        objetivo = dia + pd.Timedelta(days=delta)
        fila = jugados[
            (jugados["fecha"].dt.date == objetivo)
            & (jugados["local"] == local)
            & (jugados["visitante"] == visitante)
        ]
        if not fila.empty:
            f = fila.iloc[0]
            return int(f["goles_local"]), int(f["goles_visitante"])
    return None


def _canonico(publicable: str, jugados: pd.DataFrame) -> str | None:
    """
    El JSON guarda nombres publicables; `matches`, canonicos. Se invierte
    el diccionario en vez de guardar el canonico en el JSON: lo que sale
    del pipeline habla en publicable y punto (D-30).
    """
    for c in set(jugados["local"]) | set(jugados["visitante"]):
        if a_publicable(c) == publicable:
            return c
    return None


def _rps_de_uno(probabilidades: list[float], real: int) -> float:
    """RPS de un solo partido, ya agregado a escalar."""
    return float(np.mean(rps(np.array([probabilidades]), np.array([real]))))


# --- Auditoria -------------------------------------------------------


def generar(referencia: datetime | None = None, escribir: bool = True) -> dict:
    semana = para_auditar(referencia)
    prediccion, ruta = _prediccion_de_la_ventana(semana.desde.isoformat())

    temporada = prediccion["temporada"]
    entrenamiento = _resultados(hasta=prediccion["entrenamiento"]["hasta"])
    base = _frecuencias_base(entrenamiento)

    jugados = _resultados()

    filas, sin_resultado = [], []

    for p in prediccion["partidos"]:
        loc = _canonico(p["local"], jugados)
        vis = _canonico(p["visitante"], jugados)
        marcador = (
            _buscar_resultado(jugados, loc, vis, p["fecha"])
            if loc and vis
            else None
        )

        if marcador is None:
            sin_resultado.append(f"{p['local']} - {p['visitante']}")
            continue

        filas.append({
            "id": p["id"],
            "local": p["local"],
            "visitante": p["visitante"],
            "marcador": f"{marcador[0]}-{marcador[1]}",
            "real": resultado_a_indice(*marcador),
            "ego": [
                p["prob"]["local"] / 100,
                p["prob"]["empate"] / 100,
                p["prob"]["visitante"] / 100,
            ],
            "dio": p["prob"],
        })

    if not filas:
        raise RuntimeError(
            "Ningun partido de la ventana tiene resultado todavia. "
            "Comprueba que la ingesta del domingo ha corrido."
        )

    probs_ego = np.array([f["ego"] for f in filas])
    reales = np.array([f["real"] for f in filas])
    probs_base = np.tile(base, (len(filas), 1))

    rps_ego = float(np.mean(rps(probs_ego, reales)))
    rps_base = float(np.mean(rps(probs_base, reales)))

    # --- El desafio: EGO contra el publico, solo el partido de la encuesta
    desafio = None
    gancho = prediccion["ganchos"].get("F4_encuesta")
    if gancho:
        votos = leer_encuesta(temporada, gancho["partido"])
        objetivo = next((f for f in filas if f["id"] == gancho["partido"]), None)
        if votos and objetivo:
            rps_publico = _rps_de_uno(list(votos), objetivo["real"])
            rps_ego_uno = _rps_de_uno(objetivo["ego"], objetivo["real"])
            desafio = {
                "partido": gancho["partido"],
                "marcador": objetivo["marcador"],
                "rps_ego": round(rps_ego_uno, 4),
                "rps_publico": round(rps_publico, 4),
                "gana": "ego" if rps_ego_uno < rps_publico else "publico",
            }

    # --- El veredicto. Lo calcula el pipeline, no EGO (D-12).
    bate_base = rps_ego < rps_base
    bate_publico = desafio["gana"] == "ego" if desafio else None
    pasa = bate_base and (bate_publico is not False)

    mejor = min(filas, key=lambda f: _rps_de_uno(f["ego"], f["real"]))
    peor = max(filas, key=lambda f: _rps_de_uno(f["ego"], f["real"]))

    documento = {
        "version_esquema": VERSION_ESQUEMA,
        "generado": datetime.now().astimezone().isoformat(),
        "temporada": temporada,
        "jornada_etiqueta": prediccion["jornada_etiqueta"],
        "ventana": prediccion["ventana"],
        "prediccion_auditada": ruta.name,
        "evaluados": len(filas),
        "no_evaluados": len(sin_resultado),
        "rps": {
            "ego": round(rps_ego, 4),
            "frecuencias": round(rps_base, 4),
            "margen": round(rps_base - rps_ego, 4),
        },
        "brier": {
            "ego": round(float(np.mean(brier(probs_ego, reales))), 4),
            "frecuencias": round(float(np.mean(brier(probs_base, reales))), 4),
        },
        "veredicto": {
            "pasa": bool(pasa),
            "bate_frecuencias": bool(bate_base),
            "bate_publico": bate_publico,
        },
        "desafio": desafio,
        "mejor": {
            "partido": mejor["id"],
            "marcador": mejor["marcador"],
            "dio": mejor["dio"],
        },
        "peor": {
            "partido": peor["id"],
            "marcador": peor["marcador"],
            "dio": peor["dio"],
        },
        "advertencias": (
            [f"{len(sin_resultado)} partido(s) sin resultado, fuera de la "
             f"auditoria: {', '.join(sin_resultado)}"]
            if sin_resultado else []
        ),
    }

    if escribir:
        SALIDA.mkdir(parents=True, exist_ok=True)
                # Si la prediccion era una simulacion, la auditoria tambien lo
        # es: hereda la marca para que no acabe en el repo.
        marca = "_SIM" if "_SIM" in ruta.name else ""
        nombre = (f"{temporada}_J{prediccion['jornada_etiqueta']:02d}"
                  f"{marca}_auditoria.json")
        destino = SALIDA / nombre
        destino.write_text(
            json.dumps(documento, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Escrito: {destino}")

    return documento


if __name__ == "__main__":
    # Con un argumento se audita una ventana pasada, para probar:
    #   python -m src.evaluate.auditoria 2026-08-31
    ref = pd.Timestamp(sys.argv[1]).to_pydatetime() if len(sys.argv) > 1 else None

    d = generar(ref)

    v = d["veredicto"]
    print(f"\nJornada {d['jornada_etiqueta']}  ({d['evaluados']} partidos)")
    print(f"   EGO         {d['rps']['ego']}")
    print(f"   Frecuencias {d['rps']['frecuencias']}")
    print(f"   Margen      {d['rps']['margen']}")
    print()
    print("   EGO PASA SU CRIBA" if v["pasa"] else "   EGO NO PASA SU CRIBA")

    if d["desafio"]:
        de = d["desafio"]
        print(f"\n   Desafio: {de['partido']} ({de['marcador']})")
        print(f"   EGO {de['rps_ego']} vs publico {de['rps_publico']} "
              f"-> gana {de['gana']}")

    print(f"\n   Mejor: {d['mejor']['partido']} {d['mejor']['marcador']} "
          f"{d['mejor']['dio']}")
    print(f"   Peor:  {d['peor']['partido']} {d['peor']['marcador']} "
          f"{d['peor']['dio']}")

    for a in d["advertencias"]:
        print(f"\n   AVISO: {a}")