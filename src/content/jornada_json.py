"""
El JSON de la jornada: el contrato entre el pipeline y la redaccion.

TRES PRINCIPIOS, en orden de importancia:

1. Ningun numero que no sea publicable tal cual. Aqui no hay 0.71342,
   hay 71. El redactor COPIA, nunca calcula. Un LLM que redondea acaba
   publicando "a 0.6 del umbral" cuando son 0.06.

2. La seleccion editorial la hace el pipeline. Cual es la prediccion mas
   atrevida o el partido mas abierto son preguntas con respuesta
   aritmetica. El redactor escribe prosa sobre un gancho ya elegido.

3. Lo que no esta aqui no se puede publicar. Es la garantia estructural
   de D-14: `market_odds` no es que no se mencione, es que el redactor
   nunca la ve. Este modulo NO abre esa tabla.

Frontera temporal: se ajusta con `fecha < primer dia de la ventana`.
Los partidos que se predicen no entran en su propio entrenamiento.
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.models import cards, corners
from src.models.criba import UMBRAL, XI
from src.models.dixon_coles import (
    ajustar,
    entropia,
    matriz_marcadores,
    probabilidades_1x2,
)
from src.models.equipos import a_publicable
from src.models.semana import Semana, para_predecir

BASE = Path(__file__).resolve().parents[2]
DB = BASE / "data" / "laliga.db"
SALIDA = BASE / "outputs" / "predictions"

VERSION_ESQUEMA = 1

# Partidos minimos para que un equipo tenga cifra publicable en la criba.
# El encogimiento (D-27) arregla las estimaciones absurdas por abajo, pero
# empuja a los equipos sin muestra hacia 1.00, que es mejor de lo que
# merecen. La exclusion visual resuelve eso (D-25).
MIN_PARTIDOS_CRIBA = 10


# --- Datos ----------------------------------------------------------


def _cargar_matches() -> pd.DataFrame:
    """Solo `matches`. Este modulo no abre `market_odds` (D-14)."""
    con = sqlite3.connect(DB)
    try:
        df = pd.read_sql("SELECT * FROM matches", con)
    finally:
        con.close()
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df


def _equipos_de_la_temporada(temporada: str) -> set[str]:
    con = sqlite3.connect(DB)
    try:
        filas = con.execute(
            "SELECT DISTINCT local FROM fixtures WHERE temporada = ?", (temporada,)
        ).fetchall()
    finally:
        con.close()
    return {f[0] for f in filas}


def _criba_anterior() -> set[str]:
    """Quien pasaba la criba en el JSON anterior. Vacio si no hay."""
    if not SALIDA.exists():
        return set()
    previos = sorted(SALIDA.glob("*_prediccion.json"))
    if not previos:
        return set()
    try:
        datos = json.loads(previos[-1].read_text(encoding="utf-8"))
        return {e["equipo"] for e in datos["criba"]["pasan"]}
    except (json.JSONDecodeError, KeyError):
        return set()


# --- Criba ----------------------------------------------------------


def _construir_criba(tabla: pd.DataFrame, de_la_liga: set[str]) -> dict:
    tabla = tabla[tabla["equipo"].isin(de_la_liga)].copy()
    antes = _criba_anterior()

    pasan, no_pasan, sin_datos = [], [], []

    for _, fila in tabla.iterrows():
        publicable = a_publicable(fila["equipo"])

        if fila["partidos"] < MIN_PARTIDOS_CRIBA:
            sin_datos.append({
                "equipo": publicable,
                "partidos": int(fila["partidos"]),
            })
            continue

        fuerza = round(float(fila["fuerza"]), 2)
        distancia = round(fuerza - UMBRAL, 2)
        dentro = fuerza >= UMBRAL
        estaba = publicable in antes

        if dentro:
            estado = "sigue_dentro" if estaba else "entra"
        else:
            estado = "cae" if estaba else "sigue_fuera"

        registro = {
            "equipo": publicable,
            "fuerza": fuerza,
            "distancia": distancia,
            "estado": estado,
            "partidos": int(fila["partidos"]),
        }
        (pasan if dentro else no_pasan).append(registro)

    return {
        "umbral": UMBRAL,
        "pasan": pasan,
        "no_pasan": no_pasan,
        "sin_datos": sin_datos,
        "resumen": {
            "n_pasan": len(pasan),
            "entran": [e["equipo"] for e in pasan if e["estado"] == "entra"],
            "salen": [e["equipo"] for e in no_pasan if e["estado"] == "cae"],
            "primera_criba": not antes,
        },
    }


# --- Partidos -------------------------------------------------------


def _marcador_probable(matriz: np.ndarray) -> str:
    i, j = np.unravel_index(int(np.argmax(matriz)), matriz.shape)
    return f"{i}-{j}"


def _prob_mas_de_25(matriz: np.ndarray) -> float:
    total = 0.0
    for i in range(matriz.shape[0]):
        for j in range(matriz.shape[1]):
            if i + j >= 3:
                total += matriz[i, j]
    return total


def _prob_ambos_marcan(matriz: np.ndarray) -> float:
    return float(matriz[1:, 1:].sum())


def _conoce(parametros, equipo: str) -> bool:
    return equipo in getattr(parametros, "equipos", [])


def _construir_partidos(
    semana: Semana, dc, corn, tarj, criba: dict
) -> tuple[list[dict], list[str]]:
    dentro = {e["equipo"] for e in criba["pasan"]}
    sin_criba = {e["equipo"] for e in criba["sin_datos"]}

    partidos, avisos = [], []

    for p in semana.partidos:
        loc_pub = a_publicable(p.local)
        vis_pub = a_publicable(p.visitante)
        etiqueta = f"{loc_pub} - {vis_pub}"

        if not (_conoce(dc, p.local) and _conoce(dc, p.visitante)):
            avisos.append(
                f"{etiqueta}: sin historico suficiente. EGO no dictamina (D-25)."
            )
            continue

        matriz = matriz_marcadores(dc, p.local, p.visitante)
        pl, pe, pv = probabilidades_1x2(matriz)

        registro = {
            "id": f"J{p.jornada}_{p.local}_{p.visitante}".replace(" ", ""),
            "jornada": p.jornada,
            "local": loc_pub,
            "visitante": vis_pub,
            "fecha": p.fecha_hora.isoformat(),
            "prob": {
                "local": round(pl * 100),
                "empate": round(pe * 100),
                "visitante": round(pv * 100),
            },
            "marcador_probable": _marcador_probable(matriz),
            "entropia": round(entropia(matriz), 3),
            "mas_25_goles": round(_prob_mas_de_25(matriz) * 100),
            "ambos_marcan": round(_prob_ambos_marcan(matriz) * 100),
            "criba": {
                "local": loc_pub in dentro,
                "visitante": vis_pub in dentro,
                "choque_directo": (loc_pub in dentro) != (vis_pub in dentro),
            },
            "fiabilidad": (
                "parcial"
                if (loc_pub in sin_criba or vis_pub in sin_criba)
                else "completa"
            ),
        }

        if _conoce(corn, p.local) and _conoce(corn, p.visitante):
            cl, cv = corners.corners_esperados(corn, p.local, p.visitante)
            registro["corners"] = {
                "local": round(cl, 1),
                "visitante": round(cv, 1),
            }

        if _conoce(tarj, p.local) and _conoce(tarj, p.visitante):
            tl, tv = cards.tarjetas_esperadas(tarj, p.local, p.visitante)
            registro["tarjetas"] = {
                "local": round(tl, 1),
                "visitante": round(tv, 1),
            }

        partidos.append(registro)

    return partidos, avisos


# --- Ganchos --------------------------------------------------------


def _ganchos(partidos: list[dict], criba: dict) -> dict:
    """Seleccion editorial por aritmetica, nunca por criterio del redactor."""
    g = {
        "F1_descarte": None,
        "F2_atrevida": None,
        "F3_punto_ciego": None,
        "F4_encuesta": None,
    }

    # F1: el equipo mas cerca del corte por debajo. Si alguno cae esta
    # semana, ese manda: es el titular.
    caen = [e for e in criba["no_pasan"] if e["estado"] == "cae"]
    candidatos = caen or criba["no_pasan"]
    if candidatos:
        elegido = max(candidatos, key=lambda e: e["distancia"])
        g["F1_descarte"] = {
            "equipo": elegido["equipo"],
            "cifra": elegido["distancia"],
            "motivo": "cae_esta_semana" if caen else "mas_cerca_del_corte",
        }

    # F2: la prediccion mas atrevida. Un visitante favorito es lo mas
    # contrario a la intuicion; si no hay, el mayor favoritismo local.
    visitantes = [p for p in partidos if p["prob"]["visitante"] > p["prob"]["local"]]
    if visitantes:
        elegido = max(visitantes, key=lambda p: p["prob"]["visitante"])
        g["F2_atrevida"] = {
            "partido": elegido["id"],
            "cifra": elegido["prob"]["visitante"],
            "motivo": "visitante_favorito",
        }
    elif partidos:
        elegido = max(partidos, key=lambda p: p["prob"]["local"])
        g["F2_atrevida"] = {
            "partido": elegido["id"],
            "cifra": elegido["prob"]["local"],
            "motivo": "favorito_mas_claro",
        }

    # F3: maxima entropia. Donde EGO admite que no sabe.
    if partidos:
        abierto = max(partidos, key=lambda p: p["entropia"])
        g["F3_punto_ciego"] = {
            "partido": abierto["id"],
            "cifra": abierto["entropia"],
        }
        # F4: la encuesta va al segundo mas abierto, para no repetir el
        # mismo partido en dos formatos de la misma semana.
        resto = [p for p in partidos if p["id"] != abierto["id"]]
        if resto:
            segundo = max(resto, key=lambda p: p["entropia"])
            g["F4_encuesta"] = {"partido": segundo["id"]}

    return g


# --- Ensamblado -----------------------------------------------------


def generar(referencia: datetime | None = None, escribir: bool = True) -> dict:
    semana = para_predecir(referencia)

    if not semana.partidos:
        raise RuntimeError("No hay partidos que predecir en esta ventana.")

    temporada = semana.partidos[0].temporada
    corte = semana.desde.date()

    # Frontera temporal estricta. `matches.fecha` no trae hora, asi que
    # se compara solo el dia. Es la misma forma que usan backtest.py y
    # los tres graficos.
    todos = _cargar_matches()
    entrenamiento = todos[todos["fecha"].dt.date < corte]

    if entrenamiento.empty:
        raise RuntimeError(f"Sin datos de entrenamiento anteriores a {corte}.")

    print(f"Ajustando con {len(entrenamiento)} partidos anteriores a {corte}...")
    dc = ajustar(entrenamiento, xi=XI)
    corn = corners.ajustar(entrenamiento, xi=XI)
    tarj = cards.ajustar(entrenamiento, xi=XI)

    criba = _construir_criba(dc.tabla(), _equipos_de_la_temporada(temporada))
    partidos, avisos = _construir_partidos(semana, dc, corn, tarj, criba)

    documento = {
        "version_esquema": VERSION_ESQUEMA,
        "generado": datetime.now().astimezone().isoformat(),
        "temporada": temporada,
        "jornada_etiqueta": semana.jornada_etiqueta,
        "semana_mezclada": semana.mezclada,
        "ventana": {
            "desde": semana.desde.isoformat(),
            "hasta": semana.hasta.isoformat(),
        },
        "entrenamiento": {
            "hasta": str(corte),
            "partidos": len(entrenamiento),
        },
        "criba": criba,
        "partidos": partidos,
        "ganchos": _ganchos(partidos, criba),
        "advertencias": semana.advertencias + avisos,
    }

    if escribir:
        SALIDA.mkdir(parents=True, exist_ok=True)
        nombre = f"{temporada}_J{semana.jornada_etiqueta:02d}_prediccion.json"
        ruta = SALIDA / nombre
        ruta.write_text(
            json.dumps(documento, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Escrito: {ruta}")

    return documento


if __name__ == "__main__":
    d = generar()

    c = d["criba"]
    print(f"\nCRIBA (umbral {c['umbral']}): pasan {c['resumen']['n_pasan']}")
    for e in c["pasan"]:
        print(f"   {e['equipo']:<14} {e['fuerza']:.2f}  ({e['estado']})")
    if c["sin_datos"]:
        print("   sin muestra: " +
              ", ".join(f"{e['equipo']} ({e['partidos']})" for e in c["sin_datos"]))

    print(f"\nPARTIDOS ({len(d['partidos'])})")
    for p in d["partidos"]:
        pr = p["prob"]
        print(f"   {p['local']:<14} {pr['local']:>3}  {pr['empate']:>3}  "
              f"{pr['visitante']:>3}  {p['visitante']:<14} "
              f"H={p['entropia']:.2f}")

    print("\nGANCHOS")
    for k, v in d["ganchos"].items():
        print(f"   {k}: {v}")

    for a in d["advertencias"]:
        print(f"\n   AVISO: {a}")