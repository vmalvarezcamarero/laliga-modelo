"""
La criba: umbral sobre la fuerza estimada por el modelo.

Un equipo pasa la criba si su fuerza neta (ataque / defensa) supera un
umbral. Como ataque y defensa estan centrados (D-16), 1.00 es un equipo
exactamente medio y el umbral se lee directamente: 1.20 significa "un
20% mejor que la media de la liga".

La criba es independiente de los resultados. Un equipo puede ganar y no
pasarla, y puede perder pasandola. Eso no es un defecto: es el producto.

Este modulo es solo definicion. No toca la base de datos ni ajusta nada,
para que lo puedan usar igual el calibrador, el generador de graficos y
el de borradores.
"""

import pandas as pd

# Valor resuelto en el Sprint 1 (P-02). Se pasa explicitamente en cada
# llamada a ajustar() para no depender del valor por defecto del modelo.
XI = 0.001

# Umbral provisional. Se sustituye por el resultado de calibrar_criba.py.
UMBRAL = 1.20  # calibrado en Sprint 2 (P-04)

PARTIDOS_POR_JORNADA = 10


def asignar_jornadas(partidos: pd.DataFrame) -> pd.DataFrame:
    """
    Reconstruye el numero de jornada a partir de la fecha.

    La base no guarda la jornada, solo la fecha. Como una jornada son 10
    partidos, se ordena la temporada por fecha y se parte en bloques de
    10. Con partidos aplazados un encuentro puede caer en el bloque
    contiguo; para calibrar un umbral es irrelevante, pero no uses esto
    como calendario oficial.

    Espera partidos de UNA sola temporada.
    """
    df = partidos.sort_values("fecha").reset_index(drop=True).copy()
    df["jornada"] = (df.index // PARTIDOS_POR_JORNADA) + 1
    return df


def marcar_criba(tabla: pd.DataFrame, umbral: float = UMBRAL) -> pd.DataFrame:
    """
    Anade la columna 'pasa' a la tabla de fuerzas de ParametrosDC.tabla().

    Anade tambien 'distancia': cuanto le falta (o le sobra) a cada equipo
    respecto al umbral. Es el numero que EGO usa para hablar de un equipo
    sin adjetivos: "el Girona esta a 0.31 del umbral".
    """
    df = tabla.copy()
    df["pasa"] = df["fuerza"] >= umbral
    df["distancia"] = (df["fuerza"] - umbral).round(3)
    return df


def equipos_que_pasan(tabla: pd.DataFrame, umbral: float = UMBRAL) -> list[str]:
    """Lista de equipos que superan el umbral, de mayor a menor fuerza."""
    df = marcar_criba(tabla, umbral)
    return df.loc[df["pasa"], "equipo"].tolist()
