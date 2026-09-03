"""
Grafico del formato F5 — EGO PASA LA CRIBA.

La publicacion mas importante de la semana. Se publica siempre, gane o
pierda EGO (00_PROYECTO.md §3, D-12). Es el marcador de honestidad del
proyecto entero: sin el, esto es una cuenta mas que solo ensena sus
aciertos.

Que se dibuja
-------------
RPS acumulado a lo largo de la temporada. Tres series posibles:

  EGO           en ambar, la que se juzga
  modelo tonto  las frecuencias base de LaLiga, calculadas SOLO con
                datos de entrenamiento
  publico       la media de las encuestas, disponible a partir del mes 2
                (01_ARQUITECTURA.md §4). Es opcional a proposito: el
                grafico tiene que funcionar con dos lineas el primer
                lunes y con tres despues, sin rediseno.

Ademas, un punto por jornada con el RPS suelto de EGO. El acumulado
cuenta la temporada; el punto hace visible la semana concreta en que
EGO se pega el batacazo.

Decisiones de diseno
--------------------
1. Hacia abajo es mejor, y eso es antiintuitivo. No se invierte el eje
   (confunde mas): se etiqueta la esquina inferior con un "mejor"
   discreto y el texto del post lo dice.

2. El ambar aqui marca la linea de EGO, no una linea de corte. Sigue
   siendo un solo acento por grafico y sigue significando "lo que se
   esta juzgando", pero cambia de objeto respecto a F1.

3. Sin leyenda flotante. Cada linea lleva su nombre al final, a la
   derecha, donde termina. Se lee sin buscar.

El RPS viene de src/evaluate/rps.py. Una sola formula en todo el
proyecto: si algun dia se cambia la metrica, cambia en el backtest y en
el grafico a la vez, y no hay forma de que publiquemos un numero
distinto del que auditamos.

Uso:
    python -m src.content.grafico_ego
"""

import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.content import estilo
from src.evaluate.rps import rps
from src.models import dixon_coles
from src.models.criba import XI, asignar_jornadas

RAIZ = Path(__file__).resolve().parents[2]
BD = RAIZ / "data" / "laliga.db"

# Orden de rps.py: 0 = local, 1 = empate, 2 = visitante.
RESULTADO_A_INDICE = {"H": 0, "D": 1, "A": 2}


def frecuencias_base(entrenamiento: pd.DataFrame) -> np.ndarray:
    """
    El 'modelo tonto': con que frecuencia gana el local, se empata y
    gana el visitante en LaLiga.

    Se calcula SOLO con datos de entrenamiento. Calcularlas sobre toda
    la historia seria hacer trampa a favor del baseline
    (01_ARQUITECTURA.md §4).
    """
    cuenta = entrenamiento["resultado"].value_counts(normalize=True)
    return np.array([float(cuenta.get(r, 1 / 3)) for r in ["H", "D", "A"]])


def calcular_temporada(temporada: str) -> pd.DataFrame:
    """
    Una fila por jornada con el RPS medio de EGO y del modelo tonto.

    Frontera temporal estricta: cada jornada se predice entrenando solo
    con partidos anteriores.
    """
    con = sqlite3.connect(BD)
    partidos = pd.read_sql("SELECT * FROM matches", con, parse_dates=["fecha"])
    con.close()
    partidos = partidos.sort_values("fecha").reset_index(drop=True)

    de_la_temporada = asignar_jornadas(partidos[partidos["temporada"] == temporada])

    filas = []
    for jornada, bloque in de_la_temporada.groupby("jornada"):
        corte = bloque["fecha"].min()
        entrenamiento = partidos[partidos["fecha"] < corte]

        parametros = dixon_coles.ajustar(entrenamiento, xi=XI, referencia=corte)
        base = frecuencias_base(entrenamiento)

        probabilidades, observados = [], []
        for _, partido in bloque.iterrows():
            local, visitante = partido["local"], partido["visitante"]

            # Recien ascendidos sin historico: no se predicen a ciegas.
            if local not in parametros.equipos or visitante not in parametros.equipos:
                continue

            matriz = dixon_coles.matriz_marcadores(parametros, local, visitante)
            probabilidades.append(dixon_coles.probabilidades_1x2(matriz))
            observados.append(RESULTADO_A_INDICE[partido["resultado"]])

        if not observados:
            continue

        probabilidades = np.array(probabilidades)
        observados = np.array(observados)
        tontas = np.tile(base, (len(observados), 1))

        rps_ego = float(rps(probabilidades, observados).mean())
        rps_tonto = float(rps(tontas, observados).mean())

        filas.append(
            {
                "jornada": jornada,
                "rps_ego": rps_ego,
                "rps_tonto": rps_tonto,
                "partidos": len(observados),
            }
        )
        print(f"  J{jornada:>2}  EGO {rps_ego:.4f}  tonto {rps_tonto:.4f}  "
              f"({len(observados)} partidos)")

    df = pd.DataFrame(filas)

    # Acumulado ponderado por numero de partidos, no media de medias:
    # una jornada con 9 partidos evaluados no debe pesar igual que una
    # con 10.
    for columna in ["rps_ego", "rps_tonto"]:
        df[columna + "_acum"] = (
            (df[columna] * df["partidos"]).cumsum() / df["partidos"].cumsum()
        )

    return df


def dibujar(
    df: pd.DataFrame,
    subtitulo: str,
    publico: pd.Series | None = None,
) -> plt.Figure:
    """
    df: salida de calcular_temporada().
    publico: RPS acumulado del publico, si ya existe. Opcional.
    """
    fig, ax = plt.subplots(figsize=(estilo.ANCHO_PULGADAS, estilo.ALTO_PULGADAS))
    fig.subplots_adjust(left=0.09, right=0.82, top=0.80, bottom=0.14)

    x = df["jornada"]

    # El punto suelto de cada jornada: el acumulado cuenta la temporada,
    # el punto hace visible la semana del batacazo.
    ax.scatter(x, df["rps_ego"], s=24, color=estilo.SECUNDARIO, zorder=2)

    ax.plot(
        x, df["rps_tonto_acum"], color=estilo.SECUNDARIO,
        linewidth=2, linestyle="--", zorder=3,
    )
    ax.plot(x, df["rps_ego_acum"], color=estilo.AMBAR, linewidth=3.2, zorder=5)

    if publico is not None:
        ax.plot(x, publico, color=estilo.TEXTO, linewidth=2.2, zorder=4)

    # Nombre al final de cada linea, donde termina. Sin leyenda
    # flotante: se lee sin buscar.
    final = float(x.iloc[-1])
    etiquetas = [
        (df["rps_ego_acum"].iloc[-1], "EGO", estilo.AMBAR, "bold"),
        (df["rps_tonto_acum"].iloc[-1], "modelo tonto", estilo.SECUNDARIO, "normal"),
    ]
    if publico is not None:
        etiquetas.append((float(publico.iloc[-1]), "vosotros", estilo.TEXTO, "normal"))

    for valor, nombre, color, peso in etiquetas:
        ax.text(
            final + 0.6, valor, f" {nombre}  {valor:.4f}",
            va="center", fontsize=15, color=color, weight=peso,
        )

    ax.set_xlim(0.5, final + 0.5)
    ax.set_xlabel("jornada", fontsize=13, color=estilo.SECUNDARIO, labelpad=10)
    ax.tick_params(labelsize=13)
    ax.grid(axis="y", linewidth=1)
    ax.set_axisbelow(True)

  

    estilo.titular(fig, "EGO PASA LA CRIBA", subtitulo)
    estilo.pie(
        fig,
        "RPS acumulado: cuanto se equivoca cada uno   ·   "
        "mas abajo es mejor   ·   los puntos son cada jornada suelta",
    )

    return fig


def main() -> None:
    estilo.aplicar()

    temporada = "2025-26"
    print(f"Calculando {temporada}. Un ajuste por jornada, aguanta.\n")
    df = calcular_temporada(temporada)

    fig = dibujar(df, f"Temporada {temporada} completa")
    destino = estilo.guardar(fig, f"ego_{temporada}.png")
    plt.close(fig)

    ego = df["rps_ego_acum"].iloc[-1]
    tonto = df["rps_tonto_acum"].iloc[-1]

    print(f"\nEGO {ego:.4f}  ·  modelo tonto {tonto:.4f}")
    print("EGO pasa la criba." if ego < tonto else "EGO NO pasa su propia criba.")
    print(f"Guardado en {destino}")


if __name__ == "__main__":
    main()
