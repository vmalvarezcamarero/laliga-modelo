"""
Grafico del formato F1 — LA CRIBA.

Los 20 equipos ordenados por fuerza, con la linea de corte marcada.
Quien pasa y quien no. Es el formato insignia de los jueves
(02_VOZ_Y_FORMATOS §5).

Decisiones de diseno
--------------------
1. Dos lineas de referencia, no una. El ambar marca el umbral. Una linea
   gris casi invisible marca el 1.00, la media de la liga. Esa segunda
   es la que hace el grafico legible para alguien sin formacion
   estadistica: ve de un vistazo quien esta por encima de la media y
   quien ni eso (00_PROYECTO.md §5).

2. Las cifras van en columna alineada a la derecha, no al final de cada
   barra. Al final de la barra chocaban con la linea de corte en los
   equipos que rondan el umbral, que son justo los interesantes. En
   columna, ademas, la monoespaciada alinea los decimales sola.

3. Barras horizontales y no verticales. Con 20 nombres de equipo,
   verticales obligarian a girar el texto y no se leeria en un movil.

4. Tipografia grande. Esto se ve en el timeline de X con el pulgar, no
   en un monitor. Si dudas entre dos tamanos, el mayor.

5. Los equipos sin datos suficientes aparecen, pero sin barra y sin
   numero. Un recien ascendido con tres partidos recibe estimaciones
   ridiculas (el Oviedo salio con fuerza 0.47 en la J3 de 2025-26).
   EGO no dice tonterias: dice que no tiene informacion.

Uso:
    python -m src.content.grafico_criba
"""

import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from src.content import estilo
from src.models import dixon_coles
from src.models.criba import UMBRAL, XI, asignar_jornadas

RAIZ = Path(__file__).resolve().parents[2]
BD = RAIZ / "data" / "laliga.db"

# Por debajo de esto, un equipo no recibe numero: recibe "sin datos".
MIN_PARTIDOS = 10


def dibujar(
    tabla: pd.DataFrame,
    subtitulo: str,
    umbral: float = UMBRAL,
    sin_datos: list[str] | None = None,
) -> plt.Figure:
    """
    tabla: DataFrame con columnas 'equipo' y 'fuerza', de mayor a menor.
    sin_datos: equipos que se muestran al final, sin barra.
    """
    sin_datos = sin_datos or []

    fig, ax = plt.subplots(figsize=(estilo.ANCHO_PULGADAS, estilo.ALTO_PULGADAS))
    fig.subplots_adjust(left=0.20, right=0.88, top=0.80, bottom=0.14)

    equipos = list(tabla["equipo"]) + sin_datos
    fuerzas = list(tabla["fuerza"]) + [0.0] * len(sin_datos)
    posiciones = list(range(len(equipos)))

    colores = [
        estilo.TEXTO if f >= umbral else estilo.APAGADO for f in tabla["fuerza"]
    ] + [estilo.FONDO] * len(sin_datos)

    ax.barh(posiciones, fuerzas, color=colores, height=0.66)

    # Linea de la media de la liga: discreta, solo para orientarse.
    ax.axvline(1.0, color=estilo.REJILLA, linewidth=1.5, zorder=0)
    # Linea de corte: el unico ambar del grafico.
    ax.axvline(umbral, color=estilo.AMBAR, linewidth=2.2, linestyle="--", zorder=3)

    ax.set_yticks(posiciones)
    ax.set_yticklabels(equipos, fontsize=15)
    ax.invert_yaxis()

    # Espacio a la derecha para la columna de cifras, que vive fuera de
    # la zona de barras y por eso nunca choca con nada.
    maximo = max(list(tabla["fuerza"]) + [umbral])
    tope = maximo * 1.16
    columna_cifras = maximo * 1.08

    ax.set_xlim(0, tope)
    ax.set_xticks([])
    ax.grid(False)
    ax.tick_params(axis="y", length=0, pad=12)

    for i, fuerza in enumerate(tabla["fuerza"]):
        pasa = fuerza >= umbral
        ax.text(
            columna_cifras,
            i,
            f"{fuerza:.2f}",
            va="center",
            ha="right",
            fontsize=15,
            color=estilo.TEXTO if pasa else estilo.SECUNDARIO,
            weight="bold" if pasa else "normal",
        )

    for j, _ in enumerate(sin_datos, start=len(tabla)):
        ax.text(
            0.02,
            j,
            "sin datos suficientes",
            va="center",
            fontsize=13,
            color=estilo.APAGADO,
            style="italic",
        )
        ax.get_yticklabels()[j].set_color(estilo.APAGADO)

    # Etiqueta de la linea de corte, por encima de la primera barra.
    ax.text(
        umbral,
        -1.3,
        f" corte {umbral:.2f}",
        color=estilo.AMBAR,
        fontsize=14,
        va="bottom",
    )

    cuantos = int((tabla["fuerza"] >= umbral).sum())
    estilo.titular(fig, "LA CRIBA", subtitulo)
    fig.text(
        0.955,
        0.885,
        f"pasan {cuantos} de {len(equipos)}",
        fontsize=17,
        color=estilo.TEXTO,
        ha="right",
    )
    estilo.pie(
        fig,
        "fuerza = ataque / defensa estimados sobre xG   ·   1.00 = media de la liga",
    )

    return fig


def _tabla_de_una_jornada(temporada: str, jornada: int):
    """Ajusta el modelo con datos anteriores a esa jornada y devuelve la tabla."""
    con = sqlite3.connect(BD)
    partidos = pd.read_sql("SELECT * FROM matches", con, parse_dates=["fecha"])
    con.close()

    de_la_temporada = asignar_jornadas(partidos[partidos["temporada"] == temporada])
    bloque = de_la_temporada[de_la_temporada["jornada"] == jornada]
    if bloque.empty:
        raise SystemExit(f"No hay jornada {jornada} en {temporada}.")

    corte = bloque["fecha"].min()
    # Frontera temporal estricta, igual que en todo el proyecto.
    entrenamiento = partidos[partidos["fecha"] < corte]

    parametros = dixon_coles.ajustar(entrenamiento, xi=XI, referencia=corte)
    tabla = parametros.tabla()

    equipos_temporada = set(de_la_temporada["local"]) | set(de_la_temporada["visitante"])

    jugados = pd.concat(
        [entrenamiento["local"], entrenamiento["visitante"]]
    ).value_counts()

    sin_datos = sorted(
        e for e in equipos_temporada if jugados.get(e, 0) < MIN_PARTIDOS
    )

    tabla = tabla[
        tabla["equipo"].isin(equipos_temporada - set(sin_datos))
    ].reset_index(drop=True)

    return tabla, sin_datos


def main() -> None:
    estilo.aplicar()

    temporada, jornada = "2025-26", 38
    tabla, sin_datos = _tabla_de_una_jornada(temporada, jornada)

    fig = dibujar(
        tabla,
        f"Jornada {jornada}  ·  {temporada}",
        sin_datos=sin_datos,
    )

    destino = estilo.guardar(fig, f"criba_{temporada}_j{jornada}.png")
    plt.close(fig)

    print(f"Guardado en {destino}")
    if sin_datos:
        print(f"Sin datos suficientes: {', '.join(sin_datos)}")


if __name__ == "__main__":
    main()
