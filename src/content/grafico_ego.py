"""
Grafico del formato F5 — EGO PASA LA CRIBA.

La publicacion mas importante de la semana. Se publica siempre, gane o
pierda EGO (00_PROYECTO.md §3, D-12). Es el marcador de honestidad del
proyecto entero: sin el, esto es una cuenta mas que solo ensena sus
aciertos.

DE DONDE SALEN LOS NUMEROS (D-26)
---------------------------------
De los JSON de auditoria acumulados en outputs/predictions/, NO de un
recalculo.

La version anterior recalculaba el RPS desde cero cada vez que se
ejecutaba. Eso choca de frente con D-26: la auditoria del lunes lee lo
que se publico y no recalcula, precisamente porque recalcular con datos
de hoy da otro numero. Con dos fuentes distintas, el numero del texto y
el de la imagen pueden no coincidir, y publicariamos un post donde EGO
dice 0.198 sobre un grafico que dibuja 0.201. En una cuenta cuyo
producto es la credibilidad de sus cifras, ese es el peor fallo posible.

Que se dibuja
-------------
RPS acumulado a lo largo de la temporada. Tres series posibles:

  EGO           en ambar, la que se juzga
  modelo tonto  las frecuencias base de LaLiga, tal como las calculo la
                auditoria de cada semana
  publico       la media de las encuestas, disponible desde el mes 2
                (D-31). Es opcional a proposito: el grafico funciona con
                dos lineas el primer lunes y con tres despues, sin
                rediseno.

Ademas, un punto por jornada con el RPS suelto de EGO. El acumulado
cuenta la temporada; el punto hace visible la semana concreta en que
EGO se pega el batacazo.

El acumulado se compone aqui, ponderando por partidos evaluados. NO se
guarda en el JSON: un numero derivado que vive en dos sitios es un
numero que puede desincronizarse.

Decisiones de diseno
--------------------
1. Hacia abajo es mejor, y eso es antiintuitivo. No se invierte el eje
   (confunde mas): lo dice el pie del grafico y el texto del post.

2. El ambar aqui marca la linea de EGO, no una linea de corte. Sigue
   siendo un solo acento por grafico y sigue significando "lo que se
   esta juzgando", pero cambia de objeto respecto a F1.

3. Sin leyenda flotante. Cada linea lleva su nombre al final, a la
   derecha, donde termina. Se lee sin buscar.

Uso:
    python -m src.content.grafico_ego
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


def cargar_auditorias(temporada: str | None = None) -> pd.DataFrame:
    """
    Una fila por semana auditada, en orden cronologico.

    Se ordena por VENTANA, no por nombre de fichero ni por numero de
    jornada: en una semana mezclada la etiqueta editorial no ordena
    bien (D-28).
    """
    if not PREDICCIONES.exists():
        raise SystemExit(f"No existe {PREDICCIONES}.")

    filas = []
    for ruta in PREDICCIONES.glob("*_auditoria.json"):
        try:
            d = json.loads(ruta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  aviso: {ruta.name} no es un JSON valido. Se ignora.")
            continue

        if temporada and d.get("temporada") != temporada:
            continue

        fila = {
            "ventana": d["ventana"]["desde"],
            "jornada": d["jornada_etiqueta"],
            "partidos": d["evaluados"],
            "rps_ego": d["rps"]["ego"],
            "rps_tonto": d["rps"]["frecuencias"],
            "pasa": d["veredicto"]["pasa"],
        }

        # El publico solo existe desde que hay encuestas (D-31). Se
        # guarda como None cuando no lo hay: pandas lo maneja y la serie
        # aparece sola cuando empiece a haber datos.
        desafio = d.get("desafio")
        fila["rps_publico"] = desafio["rps_publico"] if desafio else None

        filas.append(fila)

    if not filas:
        raise SystemExit(
            "No hay ningun *_auditoria.json en outputs/predictions/.\n"
            "Ejecuta antes: python -m src.evaluate.auditoria"
        )

    df = pd.DataFrame(filas).sort_values("ventana").reset_index(drop=True)

    # Acumulado ponderado por partidos evaluados, no media de medias:
    # una semana con 9 partidos no debe pesar igual que una con 10.
    for columna in ["rps_ego", "rps_tonto"]:
        df[columna + "_acum"] = (
            (df[columna] * df["partidos"]).cumsum() / df["partidos"].cumsum()
        )

    if df["rps_publico"].notna().any():
        publico = df["rps_publico"]
        pesos = df["partidos"].where(publico.notna(), 0)
        df["rps_publico_acum"] = (
            (publico.fillna(0) * pesos).cumsum() / pesos.cumsum().replace(0, pd.NA)
        )
    else:
        df["rps_publico_acum"] = None

    return df


def dibujar(df: pd.DataFrame, subtitulo: str) -> plt.Figure:
    """df: salida de cargar_auditorias()."""
    fig, ax = plt.subplots(figsize=(estilo.ANCHO_PULGADAS, estilo.ALTO_PULGADAS))
    fig.subplots_adjust(left=0.09, right=0.79, top=0.80, bottom=0.14)

    x = df["jornada"]

    # El punto suelto de cada jornada: el acumulado cuenta la temporada,
    # el punto hace visible la semana del batacazo.
    ax.scatter(x, df["rps_ego"], s=24, color=estilo.SECUNDARIO, zorder=2)

    # Con una sola semana auditada no hay linea que trazar, solo puntos.
    # Pasa el primer lunes de cada temporada y no debe reventar.
    if len(df) > 1:
        ax.plot(
            x, df["rps_tonto_acum"], color=estilo.SECUNDARIO,
            linewidth=2, linestyle="--", zorder=3,
        )
        ax.plot(x, df["rps_ego_acum"], color=estilo.AMBAR, linewidth=3.2, zorder=5)
        if df["rps_publico_acum"].notna().any():
            ax.plot(
                x, df["rps_publico_acum"], color=estilo.TEXTO,
                linewidth=2.2, zorder=4,
            )
    else:
        ax.scatter(
            x, df["rps_tonto_acum"], s=60, color=estilo.SECUNDARIO,
            marker="_", zorder=3,
        )
        ax.scatter(x, df["rps_ego_acum"], s=90, color=estilo.AMBAR, zorder=5)

    # Nombre al final de cada linea, donde termina. Sin leyenda
    # flotante: se lee sin buscar.
    final = float(x.iloc[-1])
    etiquetas = [
        (df["rps_ego_acum"].iloc[-1], "EGO", estilo.AMBAR, "bold"),
        (df["rps_tonto_acum"].iloc[-1], "modelo tonto", estilo.SECUNDARIO, "normal"),
    ]
    if df["rps_publico_acum"].notna().any():
        etiquetas.append(
            (float(df["rps_publico_acum"].iloc[-1]), "vosotros",
             estilo.TEXTO, "normal")
        )

    # Las etiquetas se colocan en coordenadas del EJE (x=1.02 es justo
    # despues del borde derecho), no en coordenadas de datos. Con datos
    # se descolocan segun cuantas jornadas haya: con una sola, un
    # desplazamiento de 0.6 saca el texto fuera de la imagen.
    for valor, nombre, color, peso in etiquetas:
        ax.text(
            1.02, valor, f" {nombre}  {valor:.4f}",
            transform=ax.get_yaxis_transform(),
            va="center", fontsize=15, color=color, weight=peso,
        )

    primera = float(x.iloc[0])
    ax.set_xlim(primera - 0.5, final + 0.5)

    # Las jornadas son enteros. Sin esto, con una sola semana auditada
    # matplotlib inventa una escala decimal (2.6, 2.8, 3.0...) y el eje
    # queda absurdo. Pasa el primer lunes de cada temporada.
    ax.set_xticks(sorted(x.unique()))
    ax.xaxis.set_major_formatter(lambda v, _: f"{int(v)}")

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

    df = cargar_auditorias()
    temporada = "2026-27"

    print(f"{len(df)} semana(s) auditada(s):\n")
    for _, f in df.iterrows():
        veredicto = "pasa" if f["pasa"] else "NO pasa"
        print(f"  J{f['jornada']:>2}  EGO {f['rps_ego']:.4f}  "
              f"tonto {f['rps_tonto']:.4f}  ({f['partidos']} partidos)  {veredicto}")

    sub = (f"Jornada {df['jornada'].iloc[-1]}" if len(df) == 1
           else f"Jornadas {df['jornada'].iloc[0]} a {df['jornada'].iloc[-1]}")

    fig = dibujar(df, sub)
    destino = estilo.guardar(fig, f"ego_{temporada}.png")
    plt.close(fig)

    ego = df["rps_ego_acum"].iloc[-1]
    tonto = df["rps_tonto_acum"].iloc[-1]

    print(f"\nAcumulado: EGO {ego:.4f}  ·  modelo tonto {tonto:.4f}")
    print("EGO pasa la criba." if ego < tonto else "EGO NO pasa su propia criba.")
    print(f"Guardado en {destino}")


if __name__ == "__main__":
    main()