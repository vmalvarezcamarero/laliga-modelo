"""
Grafico del formato F2 — EL DICTAMEN.

Los partidos de la jornada con sus probabilidades 1X2. Es la imagen del
jueves que acompana a F1 (02_VOZ_Y_FORMATOS §5).

DE DONDE SALEN LOS NUMEROS (D-47)
---------------------------------
Del JSON de la semana. Ningun grafico publicable recalcula.

La version anterior ajustaba el modelo por su cuenta con una temporada
y jornada fijas en `main()`: generaba la J38 de 2025-26 mientras el
texto hablaba de la J5 de 2026-27. Ademas elegia la prediccion
"atrevida" con un criterio propio (la mayor probabilidad al visitante)
que NO es el del JSON, asi que el ambar marcaba un partido y el tuit
hablaba de otro.

El gancho `F2_atrevida` lo elige el pipeline: el equipo que NO pasa la
criba con mas probabilidad de ganar a uno que SI. Ese es el criterio, y
el grafico lo copia.

Decisiones de diseno
--------------------
1. Barras apiladas, no tabla de numeros. Treinta porcentajes en una
   tabla no se leen en un movil. Una barra partida en tres deja ver de
   un vistazo si un partido esta decidido o abierto.

2. Tres grises, no tres colores. Claro = gana el local, medio = empate,
   oscuro = gana el visitante. Siempre igual: en cuanto alguien lo vea
   dos jueves seguidos deja de necesitar la leyenda. Nada de verde y
   rojo, que es estetica de casa de apuestas (00_PROYECTO.md §8).

3. El ambar marca la prediccion mas atrevida, que es la que comenta el
   texto del tuit. Un solo acento por grafico.

4. Los porcentajes se escriben dentro de su segmento solo si cabe. Un
   numero encima de otro es peor que un numero ausente; el que falta se
   deduce, porque los tres suman 100.

Uso:
    python -m src.content.grafico_dictamen
"""

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.content import estilo

RAIZ = Path(__file__).resolve().parents[2]
PREDICCIONES = RAIZ / "outputs" / "predictions"

# Ancho minimo de segmento para que quepa el porcentaje dentro.
MINIMO_PARA_ESCRIBIR = 0.12

COLOR_LOCAL = estilo.TEXTO
COLOR_EMPATE = "#4A5058"
COLOR_VISITANTE = "#8B939C"


def desde_json(ruta: Path | None = None) -> tuple[pd.DataFrame, int | None, dict]:
    """
    Lee los partidos del JSON de la semana.

    Devuelve tambien el indice de la fila que hay que destacar, que es
    la del gancho `F2_atrevida`: la eleccion editorial la hace el
    pipeline, no el grafico.
    """
    if ruta is None:
        candidatos = sorted(
            p for p in PREDICCIONES.glob("*_prediccion.json")
            if "_SIM" not in p.name
        )
        if not candidatos:
            raise SystemExit(
                f"No hay ningun *_prediccion.json en {PREDICCIONES}.\n"
                f"Ejecuta antes: python -m src.content.jornada_json"
            )
        ruta = candidatos[-1]

    doc = json.loads(ruta.read_text(encoding="utf-8"))

    filas = [
        {
            "id": p["id"],
            "local": p["local"],
            "visitante": p["visitante"],
            "p_local": p["prob"]["local"] / 100,
            "p_empate": p["prob"]["empate"] / 100,
            "p_visitante": p["prob"]["visitante"] / 100,
            "entropia": p["entropia"],
        }
        for p in doc["partidos"]
    ]

    # De mas decidido a mas abierto: la barra mas partida abajo. Da al
    # grafico una diagonal que se lee sola.
    df = pd.DataFrame(filas).sort_values("entropia").reset_index(drop=True)

    destacar = None
    gancho = doc["ganchos"].get("F2_atrevida")
    if gancho:
        coincide = df.index[df["id"] == gancho["partido"]]
        if len(coincide):
            destacar = int(coincide[0])

    return df, destacar, doc


def dibujar(
    partidos: pd.DataFrame,
    subtitulo: str,
    destacar: int | None = None,
) -> plt.Figure:
    """
    partidos: DataFrame con columnas local, visitante, p_local,
              p_empate, p_visitante. Una fila por partido.
    destacar: indice de la fila a marcar en ambar. Viene del gancho
              F2_atrevida del JSON.
    """
    fig, ax = plt.subplots(figsize=(estilo.ANCHO_PULGADAS, estilo.ALTO_PULGADAS))
    fig.subplots_adjust(left=0.28, right=0.95, top=0.80, bottom=0.14)

    posiciones = list(range(len(partidos)))

    for i, (_, partido) in enumerate(partidos.iterrows()):
        valores = [partido["p_local"], partido["p_empate"], partido["p_visitante"]]
        colores = [COLOR_LOCAL, COLOR_EMPATE, COLOR_VISITANTE]

        izquierda = 0.0
        for valor, color in zip(valores, colores):
            ax.barh(
                i, valor, left=izquierda, height=0.62,
                color=color, edgecolor=estilo.FONDO, linewidth=1.5,
            )
            if valor >= MINIMO_PARA_ESCRIBIR:
                # Sobre gris claro el texto va oscuro, y al reves.
                sobre_claro = color == COLOR_LOCAL
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

    if destacar is not None:
        ax.get_yticklabels()[destacar].set_color(estilo.AMBAR)

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


def main() -> None:
    estilo.aplicar()

    partidos, destacar, doc = desde_json()

    temporada = doc["temporada"]
    jornada = doc["jornada_etiqueta"]

    subtitulo = f"Jornada {jornada}  ·  {temporada}"
    if doc.get("semana_mezclada"):
        subtitulo += "  ·  semana mezclada"

    fig = dibujar(partidos, subtitulo, destacar=destacar)
    destino = estilo.guardar(fig, f"dictamen_{temporada}_j{jornada:02d}.png")
    plt.close(fig)

    print(f"Jornada {jornada} de {temporada}  ({len(partidos)} partidos)")

    gancho = doc["ganchos"].get("F2_atrevida")
    if gancho:
        print(f"\n   Mas atrevida: {gancho['equipo']} con {gancho['cifra']}%")
        print(f"   Motivo: {gancho['motivo']}")

    ciego = doc["ganchos"].get("F3_punto_ciego")
    if ciego:
        p = next(x for x in doc["partidos"] if x["id"] == ciego["partido"])
        print(f"\n   Punto ciego (F3): {p['local']} - {p['visitante']}, "
              f"{p['prob']['local']}% / {p['prob']['empate']}% / "
              f"{p['prob']['visitante']}%")

    for a in doc["advertencias"]:
        print(f"\n   AVISO: {a}")

    print(f"\nGuardado en {destino}")


if __name__ == "__main__":
        main()