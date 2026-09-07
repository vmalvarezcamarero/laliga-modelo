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
desde que haya encuestas tambien al publico. Es un AND, no una media.

EL BLOQUE `rendimiento`. Ademas de auditar a EGO, este modulo calcula
quien gano sin merecerlo: goles reales menos xG generado. Alimenta el
formato del miercoles.

Va aqui y no en un modulo aparte porque el cruce prediccion <-> resultado
ya se hace en este fichero. Repetirlo en otro sitio serian dos modulos
emparejando partidos por fecha con tolerancia, y dos sitios que pueden
desincronizarse. Es el mismo motivo por el que los graficos leen el JSON
en vez de recalcular (D-47).

NOTA SOBRE rps(): devuelve el RPS de CADA partido, no la media. Se
agrega aqui con np.mean().
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

VERSION_ESQUEMA = 2  # 2: anade el bloque `rendimiento`


# --- Datos ----------------------------------------------------------


def _resultados(hasta: str | None = None) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql(
            "SELECT fecha, local, visitante, goles_local, goles_visitante, "
            "xg_local, xg_visitante "
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
    para que la comparacion sea justa.
    """
    indices = [
        resultado_a_indice(g, v)
        for g, v in zip(
            entrenamiento["goles_local"], entrenamiento["goles_visitante"]
        )
    ]
    return np.bincount(indices, minlength=3) / len(indices)


# --- Cruce prediccion <-> resultado ----------------------------------


def _buscar_partido(
    jugados: pd.DataFrame, local: str, visitante: str, fecha: str
) -> pd.Series | None:
    """La fila entera del partido. Tolerancia de +-1 dia: `matches.fecha`
    no trae hora."""
    dia = pd.Timestamp(fecha).date()
    for delta in (0, -1, 1):
        objetivo = dia + pd.Timedelta(days=delta)
        fila = jugados[
            (jugados["fecha"].dt.date == objetivo)
            & (jugados["local"] == local)
            & (jugados["visitante"] == visitante)
        ]
        if not fila.empty:
            return fila.iloc[0]
    return None


def _canonico(publicable: str, jugados: pd.DataFrame) -> str | None:
    """
    El JSON guarda nombres publicables; `matches`, canonicos. Se invierte
    el diccionario en vez de guardar el canonico en el JSON (D-30).
    """
    for c in set(jugados["local"]) | set(jugados["visitante"]):
        if a_publicable(c) == publicable:
            return c
    return None


def _rps_de_uno(probabilidades: list[float], real: int) -> float:
    """RPS de un solo partido, ya agregado a escalar."""
    return float(np.mean(rps(np.array([probabilidades]), np.array([real]))))


# --- Rendimiento: quien gano sin merecerlo ---------------------------


def _rendimiento(filas: list[dict]) -> dict:
    """
    Goles reales menos xG generado, por equipo y partido.

    Positivo = marco mas de lo que genero. Es la tesis de la cuenta
    aplicada al pasado: un equipo puede ganar sin merecerlo.

    Los partidos sin xG quedan fuera y se cuentan. Pasa con la temporada
    en curso cuando Understat va por detras de Football-Data. No se
    inventa nada.
    """
    partidos, sin_xg = [], []

    for f in filas:
        if f["xg_local"] is None or f["xg_visitante"] is None:
            sin_xg.append(f"{f['local']} - {f['visitante']}")
            continue

        gl, gv = f["goles"]
        xl, xv = f["xg_local"], f["xg_visitante"]

        partidos.append({
            "id": f["id"],
            "local": f["local"],
            "visitante": f["visitante"],
            "marcador": f["marcador"],
            "goles": {"local": gl, "visitante": gv},
            "xg": {"local": round(xl, 2), "visitante": round(xv, 2)},
            "diferencia": {
                "local": round(gl - xl, 2),
                "visitante": round(gv - xv, 2),
            },
        })

    if not partidos:
        return {
            "partidos": [],
            "mas_afortunado": None,
            "menos_afortunado": None,
            "sin_xg": sin_xg,
        }

    # Un equipo por cada lado de cada partido: 20 candidatos por jornada.
    candidatos = []
    for p in partidos:
        for lado in ("local", "visitante"):
            candidatos.append({
                "equipo": p[lado],
                "partido": p["id"],
                "marcador": p["marcador"],
                "goles": p["goles"][lado],
                "xg": p["xg"][lado],
                "diferencia": p["diferencia"][lado],
            })

    mas = max(candidatos, key=lambda c: c["diferencia"])
    menos = min(candidatos, key=lambda c: c["diferencia"])

    return {
        "partidos": partidos,
        "mas_afortunado": mas,
        "menos_afortunado": menos,
        "sin_xg": sin_xg,
    }


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
        fila = (
            _buscar_partido(jugados, loc, vis, p["fecha"])
            if loc and vis
            else None
        )

        if fila is None:
            sin_resultado.append(f"{p['local']} - {p['visitante']}")
            continue

        gl, gv = int(fila["goles_local"]), int(fila["goles_visitante"])
        xl, xv = fila["xg_local"], fila["xg_visitante"]

        filas.append({
            "id": p["id"],
            "local": p["local"],
            "visitante": p["visitante"],
            "marcador": f"{gl}-{gv}",
            "goles": (gl, gv),
            "xg_local": None if pd.isna(xl) else float(xl),
            "xg_visitante": None if pd.isna(xv) else float(xv),
            "real": resultado_a_indice(gl, gv),
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

    rendimiento = _rendimiento(filas)

    avisos = []
    if sin_resultado:
        avisos.append(
            f"{len(sin_resultado)} partido(s) sin resultado, fuera de la "
            f"auditoria: {', '.join(sin_resultado)}"
        )
    if rendimiento["sin_xg"]:
        avisos.append(
            f"{len(rendimiento['sin_xg'])} partido(s) sin xG, fuera del "
            f"analisis de rendimiento: {', '.join(rendimiento['sin_xg'])}"
        )

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
        "rendimiento": rendimiento,
        "advertencias": avisos,
    }

    if escribir:
        SALIDA.mkdir(parents=True, exist_ok=True)
        # Si la prediccion era una simulacion, la auditoria tambien lo
        # es: hereda la marca para que no acabe en el repo (D-48).
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

    r = d["rendimiento"]
    if r["mas_afortunado"]:
        m, n = r["mas_afortunado"], r["menos_afortunado"]
        print(f"\n   RENDIMIENTO ({len(r['partidos'])} partidos con xG)")
        print(f"   Mas afortunado:  {m['equipo']:<14} "
              f"{m['goles']} goles con {m['xg']:.2f} de xG  ({m['diferencia']:+.2f})")
        print(f"   Menos afortunado: {n['equipo']:<14} "
              f"{n['goles']} goles con {n['xg']:.2f} de xG  ({n['diferencia']:+.2f})")

    for a in d["advertencias"]:
        print(f"\n   AVISO: {a}")