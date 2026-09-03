"""
Grafico del formato F2 — EL DICTAMEN.

Los diez partidos de la jornada con sus probabilidades 1X2. Es la imagen
del jueves que acompana a F1 (02_VOZ_Y_FORMATOS §5).

Decisiones de diseno
--------------------
1. Barras apiladas, no tabla de numeros. Treinta porcentajes en una
   tabla no se leen en un movil. Una barra partida en tres deja ver de
   un vistazo si un partido esta decidido o abierto, y el numero va
   dentro para quien quiera el detalle.

2. Tres grises, no tres colores. Claro = gana el local, medio = empate,
   oscuro = gana el visitante. Siempre en ese orden y siempre igual: en
   cuanto alguien lo vea dos jueves seguidos deja de necesitar la
   leyenda. Nada de verde y rojo, que es estetica de casa de apuestas
   (00_PROYECTO.md §8).

3. El ambar pinta el segmento ganador de la prediccion mas atrevida,
   que es la que comenta el texto del tuit. Un solo acento por grafico,
   y sigue significando lo mismo que en F1 y F5: aqui es donde hay que
   mirar.

   Se probo antes una marca vertical al margen y no cabia: entre el
   nombre del equipo y el inicio de la barra no hay hueco. Pintar el
   segmento se ve mejor y no estorba a nada.

   "Atrevida" se define como la mayor probabilidad concedida a un
   visitante. En futbol, apostar por el que juega fuera es la
   afirmacion arriesgada por defecto. Es una heuristica: se puede
   forzar otra fila con el parametro 'destacar'.

4. Los porcentajes se escriben dentro de su segmento solo si cabe. Un
   numero encima de otro es peor que un numero ausente; el que falta se
   deduce, porque los tres suman 100.

Uso:
    python -m src.content.grafico_dictamen
"""

import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from src.content import estilo
from src.models import dixon_coles
from src.models.criba import XI, asignar_jornadas

RAIZ = Path(__file__).resolve().parents[2]
BD = RAIZ / "data" / "laliga.db"

# Ancho minimo de segmento para que quepa el porcentaje dentro.
MINIMO_PARA_ESCRIBIR = 0.12

COLOR_LOCAL = estilo.TEXTO
COLOR_EMPATE = "#4A5058"
COLOR_VISITANTE = "#8B939C"

# Sobre estos dos fondos, que son claros, el texto va oscuro.
FONDOS_CLAROS = {COLOR_LOCAL, estilo.AMBAR}


def dibujar(
    partidos: pd.DataFrame,
    subtitulo: str,
    destacar: int | None = None,
) -> plt.Figure:
    """
    partidos: DataFrame con columnas local, visitante, p_local,
              p_empate, p_visitante. Una fila por partido.
    destacar: indice de la fila a marcar en ambar. Si es None, se elige
              la de mayor probabilidad de victoria visitante.
    """
    if destacar is None:
        destacar = int(partidos["p_visitante"].idxmax())

    fig, ax = plt.subplots(figsize=(estilo.ANCHO_PULGADAS, estilo.ALTO_PULGADAS))
    fig.subplots_adjust(left=0.28, right=0.95, top=0.80, bottom=0.14)

    posiciones = list(range(len(partidos)))

    for i, (_, partido) in enumerate(partidos.iterrows()):
        valores = [partido["p_local"], partido["p_empate"], partido["p_visitante"]]
        colores = [COLOR_LOCAL, COLOR_EMPATE, COLOR_VISITANTE]
        es_destacada = i == destacar

        if es_destacada:
            # El segmento ganador de la fila destacada va en ambar.
            colores[valores.index(max(valores))] = estilo.AMBAR

        izquierda = 0.0
        for valor, color in zip(valores, colores):
            ax.barh(
                i, valor, left=izquierda, height=0.62,
                color=color, edgecolor=estilo.FONDO, linewidth=1.5,
            )
            if valor >= MINIMO_PARA_ESCRIBIR:
                sobre_claro = color in FONDOS_CLAROS
                ax.text(
                    izquierda + valor / 2, i,
                    f"{valor * 100:.0f}%",
                    ha="center", va="center", fontsize=14,
                    color=estilo.FONDO if sobre_claro else estilo.TEXTO,
                    weight="bold" if sobre_claro else "normal",
                )
            izquierda += valor

    etiquetas = [
        f"{fila['local']} - {fila['visitante']}"
        for _, fila in partidos.iterrows()
    ]
    ax.set_yticks(posiciones)
    ax.set_yticklabels(etiquetas, fontsize=14)
    ax.invert_yaxis()

    for i, etiqueta in enumerate(ax.get_yticklabels()):
        if i == destacar:
            etiqueta.set_color(estilo.AMBAR)

    ax.set_xlim(0, 1)
    ax.set_xticks([])
    ax.grid(False)
    ax.tick_params(axis="y", length=0, pad=14)

    estilo.titular(fig, "EL DICTAMEN", subtitulo)
    fig.text(
        0.95, 0.885,
        "gana el local  ·  empate  ·  gana el visitante",
        fontsize=14, color=estilo.SECUNDARIO, ha="right",
    )
    estilo.pie(
        fig,
        "probabilidades del modelo   ·   cada barra suma 100%   ·   "
        "en ambar, lo mas atrevido de la jornada",
    )

    return fig


def _dictamen_de_una_jornada(temporada: str, jornada: int) -> pd.DataFrame:
    """Ajusta con datos anteriores a la jornada y predice sus partidos."""
    con = sqlite3.connect(BD)
    todos = pd.read_sql("SELECT * FROM matches", con, parse_dates=["fecha"])
    con.close()
    todos = todos.sort_values("fecha").reset_index(drop=True)

    de_la_temporada = asignar_jornadas(todos[todos["temporada"] == temporada])
    bloque = de_la_temporada[de_la_temporada["jornada"] == jornada]
    if bloque.empty:
        raise SystemExit(f"No hay jornada {jornada} en {temporada}.")

    corte = bloque["fecha"].min()
    # Frontera temporal estricta, igual que en todo el proyecto.
    entrenamiento = todos[todos["fecha"] < corte]

    parametros = dixon_coles.ajustar(entrenamiento, xi=XI, referencia=corte)

    filas = []
    for _, partido in bloque.iterrows():
        local, visitante = partido["local"], partido["visitante"]

        # Recien ascendidos sin historico: no se predicen a ciegas.
        if local not in parametros.equipos or visitante not in parametros.equipos:
            print(f"  aviso: {local} - {visitante} sin datos suficientes, fuera")
            continue

        matriz = dixon_coles.matriz_marcadores(parametros, local, visitante)
        p_local, p_empate, p_visitante = dixon_coles.probabilidades_1x2(matriz)

        filas.append(
            {
                "local": local,
                "visitante": visitante,
                "p_local": p_local,
                "p_empate": p_empate,
                "p_visitante": p_visitante,
                "entropia": dixon_coles.entropia(matriz),
            }
        )

    # De mas decidido a mas abierto: la barra mas partida abajo. Da al
    # grafico una diagonal que se lee sola.
    return (
        pd.DataFrame(filas)
        .sort_values("entropia")
        .reset_index(drop=True)
    )


def main() -> None:
    estilo.aplicar()

    temporada, jornada = "2025-26", 38
    partidos = _dictamen_de_una_jornada(temporada, jornada)

    fig = dibujar(partidos, f"Jornada {jornada}  ·  {temporada}")
    destino = estilo.guardar(fig, f"dictamen_{temporada}_j{jornada}.png")
    plt.close(fig)

    atrevida = partidos.loc[partidos["p_visitante"].idxmax()]
    ciego = partidos.loc[partidos["entropia"].idxmax()]

    print(f"\nGuardado en {destino}")
    print(
        f"Mas atrevida: {atrevida['local']} - {atrevida['visitante']}, "
        f"{atrevida['p_visitante'] * 100:.0f}% al visitante"
    )
    print(
        f"Punto ciego (F3): {ciego['local']} - {ciego['visitante']}, "
        f"{ciego['p_local'] * 100:.0f}% / {ciego['p_empate'] * 100:.0f}% / "
        f"{ciego['p_visitante'] * 100:.0f}%"
    )


if __name__ == "__main__":
    main()
