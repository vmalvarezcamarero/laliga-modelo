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

# Cuantos equipos a cada lado del corte se incluyen en `apretados`.
EQUIPOS_APRETADOS = 3

# Frecuencias base de LaLiga. Sirven de referencia para medir cuanto se
# aleja EGO de lo que diria cualquiera, cuando no hay choque directo.
BASE_LIGA = (0.45, 0.25, 0.30)


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


def _criba_anterior(desde: str | None = None) -> set[str] | None:
    """
    Quien pasaba la criba en la ventana INMEDIATAMENTE anterior.

    Se busca por ventana, no por orden alfabetico de fichero: al
    reconstruir una semana pasada, el ultimo fichero del directorio es
    posterior y daria estados `entra`/`cae` invertidos.

    Devuelve None si no hay ventana previa: sin referencia no se puede
    decir que nadie "entra" ni "cae". Un set vacio significaria que la
    semana pasada no paso nadie, que es otra cosa.
    """
    if not SALIDA.exists():
        return None

    candidatos = []
    for ruta in SALIDA.glob("*_prediccion.json"):
        try:
            doc = json.loads(ruta.read_text(encoding="utf-8"))
            inicio = doc["ventana"]["desde"]
        except (json.JSONDecodeError, KeyError):
            continue
        if desde is None or inicio < desde:
            candidatos.append((inicio, doc))

    if not candidatos:
        return None

    _, previo = max(candidatos, key=lambda c: c[0])
    try:
        return {e["equipo"] for e in previo["criba"]["pasan"]}
    except KeyError:
        return None


# --- Criba ----------------------------------------------------------


def _construir_criba(
    tabla: pd.DataFrame, de_la_liga: set[str], desde: str | None = None
) -> dict:
    tabla = tabla[tabla["equipo"].isin(de_la_liga)].copy()
    antes = _criba_anterior(desde)
    primera = antes is None

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

        if primera:
            # Sin criba anterior no hay movimiento que narrar. Decir
            # "entra" haria que el redactor escribiese que el Barcelona
            # acaba de entrar en la criba.
            estado = "sin_referencia"
        else:
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
            "primera_criba": primera,
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

        loc_dentro = loc_pub in dentro
        vis_dentro = vis_pub in dentro

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
                "local": loc_dentro,
                "visitante": vis_dentro,
                "choque_directo": loc_dentro != vis_dentro,
            },
            "fiabilidad": (
                "parcial"
                if (loc_pub in sin_criba or vis_pub in sin_criba)
                else "completa"
            ),
        }

        if _conoce(corn, p.local) and _conoce(corn, p.visitante):
            cl, cv = corners.corners_esperados(corn, p.local, p.visitante)
            registro["corners"] = {"local": round(cl, 1), "visitante": round(cv, 1)}

        if _conoce(tarj, p.local) and _conoce(tarj, p.visitante):
            tl, tv = cards.tarjetas_esperadas(tarj, p.local, p.visitante)
            registro["tarjetas"] = {"local": round(tl, 1), "visitante": round(tv, 1)}

        partidos.append(registro)

    return partidos, avisos


# --- Ganchos --------------------------------------------------------


def _apretados(criba: dict) -> list[dict]:
    """
    Los equipos que rodean la linea de corte, con las distancias ENTRE
    ELLOS ya calculadas.

    Existe por P-13. Un borrador escribio "0.16 puntos" restando 0.05 de
    -0.11: el dato que queria no estaba en el JSON, asi que lo calculo.
    La prohibicion de calcular no se cumple sola; darle el numero hecho,
    si. Ademas el contraste entre quien pasa raspado y quien no llega es
    de lo mejor que da la criba como contenido.
    """
    ultimos = criba["pasan"][-EQUIPOS_APRETADOS:]
    primeros = criba["no_pasan"][:EQUIPOS_APRETADOS]
    zona = ultimos + primeros

    filas = []
    for i, e in enumerate(zona):
        fila = {
            "equipo": e["equipo"],
            "fuerza": e["fuerza"],
            "distancia_al_umbral": e["distancia"],
            "pasa": e in ultimos,
        }
        if i > 0:
            fila["distancia_al_anterior"] = round(
                zona[i - 1]["fuerza"] - e["fuerza"], 2
            )
        filas.append(fila)

    return filas


def _distancia_a_la_liga(p: dict) -> float:
    """Cuanto se aleja EGO del 45/25/30 de LaLiga, en puntos."""
    return (
        abs(p["prob"]["local"] - BASE_LIGA[0] * 100)
        + abs(p["prob"]["empate"] - BASE_LIGA[1] * 100)
        + abs(p["prob"]["visitante"] - BASE_LIGA[2] * 100)
    )


def _atrevida(partidos: list[dict]) -> dict | None:
    """
    La prediccion mas atrevida: el equipo que NO pasa la criba con mas
    probabilidad de ganar a uno que SI la pasa.

    Es el choque directo, que es el conflicto que define la cuenta: la
    criba dice una cosa y la prediccion del partido dice otra. Que gane
    el visitante no es atrevido por si mismo (el Barcelona en Mestalla
    es lo que diria cualquiera); lo atrevido es que EGO le de opciones a
    quien acaba de descartar.

    Si ninguna jornada enfrenta a dentro contra fuera, cae al segundo
    criterio: el partido donde EGO mas se aleja de las frecuencias base.
    """
    candidatos = []
    for p in partidos:
        if not p["criba"]["choque_directo"]:
            continue
        if p["criba"]["local"]:
            candidatos.append((p, p["prob"]["visitante"], p["visitante"]))
        else:
            candidatos.append((p, p["prob"]["local"], p["local"]))

    if candidatos:
        elegido, cifra, equipo = max(candidatos, key=lambda c: c[1])
        return {
            "partido": elegido["id"],
            "equipo": equipo,
            "cifra": cifra,
            "motivo": "descartado_con_opciones",
        }

    if not partidos:
        return None

    elegido = max(partidos, key=_distancia_a_la_liga)
    favorito = max(
        ("local", "empate", "visitante"), key=lambda k: elegido["prob"][k]
    )
    return {
        "partido": elegido["id"],
        "equipo": elegido.get(favorito, "empate"),
        "cifra": elegido["prob"][favorito],
        "motivo": "lejos_de_la_media_de_la_liga",
    }


def _ganchos(partidos: list[dict], criba: dict) -> dict:
    """Seleccion editorial por aritmetica, nunca por criterio del redactor."""
    g = {
        "F1_descarte": None,
        "F2_atrevida": None,
        "F3_punto_ciego": None,
        "F4_encuesta": None,
    }

    # F1: si alguien cae esta semana, ese es el titular. Si no, el que
    # se ha quedado mas cerca del corte.
    caen = [e for e in criba["no_pasan"] if e["estado"] == "cae"]
    candidatos = caen or criba["no_pasan"]
    if candidatos:
        elegido = max(candidatos, key=lambda e: e["distancia"])
        g["F1_descarte"] = {
            "equipo": elegido["equipo"],
            "cifra": elegido["distancia"],
            "motivo": "cae_esta_semana" if caen else "mas_cerca_del_corte",
            "apretados": _apretados(criba),
        }

    g["F2_atrevida"] = _atrevida(partidos)

    # F3: maxima entropia. Donde EGO admite que no sabe.
    if partidos:
        abierto = max(partidos, key=lambda p: p["entropia"])
        g["F3_punto_ciego"] = {"partido": abierto["id"], "cifra": abierto["entropia"]}

        # F4: la encuesta va al segundo mas abierto, para no repetir el
        # mismo partido en dos formatos de la misma semana.
        ocupados = {abierto["id"]}
        if g["F2_atrevida"]:
            ocupados.add(g["F2_atrevida"]["partido"])
        resto = [p for p in partidos if p["id"] not in ocupados]
        if resto:
            segundo = max(resto, key=lambda p: p["entropia"])
            g["F4_encuesta"] = {"partido": segundo["id"]}

    return g


# --- Ensamblado -----------------------------------------------------


def generar(
    referencia: datetime | None = None,
    escribir: bool = True,
    simular: bool = False,
) -> dict:
    """
    `simular=True` reconstruye una ventana pasada ignorando que sus
    partidos ya se jugaron. Sirve para probar el bucle completo de
    prediccion y auditoria. NUNCA en produccion.
    """
    semana = para_predecir(referencia, simular=simular)

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

    criba = _construir_criba(
        dc.tabla(),
        _equipos_de_la_temporada(temporada),
        desde=semana.desde.isoformat(),
    )
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
        "entrenamiento": {"hasta": str(corte), "partidos": len(entrenamiento)},
        "criba": criba,
        "partidos": partidos,
        "ganchos": _ganchos(partidos, criba),
        "advertencias": semana.advertencias + avisos,
    }

    if escribir:
        SALIDA.mkdir(parents=True, exist_ok=True)
        # Los ficheros de simulacion llevan _SIM y estan en .gitignore.
        # `outputs/predictions/` es el registro publico de lo que EGO
        # dictamino de verdad: un JSON reconstruido a posteriori no
        # puede acabar ahi por descuido (D-26).
        marca = "_SIM" if simular else ""
        nombre = f"{temporada}_J{semana.jornada_etiqueta:02d}{marca}_prediccion.json"
        ruta = SALIDA / nombre
        ruta.write_text(
            json.dumps(documento, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Escrito: {ruta}")

    return documento


if __name__ == "__main__":
    # Con una fecha se reconstruye una ventana pasada, para probar:
    #   python -m src.content.jornada_json 2026-08-31
    if len(sys.argv) > 1:
        ref = pd.Timestamp(sys.argv[1]).to_pydatetime()
        d = generar(ref, simular=True)
    else:
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
        marca = " *" if p["criba"]["choque_directo"] else "  "
        print(f"  {marca} {p['local']:<14} {pr['local']:>3}  {pr['empate']:>3}  "
              f"{pr['visitante']:>3}  {p['visitante']:<14} H={p['entropia']:.2f}")

    if d["ganchos"]["F1_descarte"]:
        print("\nZONA DE CORTE")
        for e in d["ganchos"]["F1_descarte"]["apretados"]:
            sep = e.get("distancia_al_anterior", "")
            marca = "PASA" if e["pasa"] else "    "
            print(f"   {marca}  {e['equipo']:<14} {e['fuerza']:.2f}   "
                  f"al anterior: {sep}")

    print("\nGANCHOS")
    for k, v in d["ganchos"].items():
        if k == "F1_descarte" and v:
            v = {x: y for x, y in v.items() if x != "apretados"}
        print(f"   {k}: {v}")

    for a in d["advertencias"]:
        print(f"\n   AVISO: {a}")