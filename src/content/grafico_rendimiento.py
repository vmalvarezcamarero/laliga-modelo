"""
Grafico del formato F6 — LO QUE DICEN LOS GOLES Y LO QUE DICE EL XG.

Analisis retrospectivo de la jornada: quien marco mas de lo que genero
y quien menos. Es la tesis de la cuenta mirando hacia atras. Los
resultados no dicen quien jugo mejor, y por eso la criba no los mira.

DE DONDE SALEN LOS NUMEROS (D-47)
---------------------------------
Del bloque `rendimiento` del JSON de auditoria. Ningun grafico
publicable recalcula: el numero del texto y el de la imagen tienen que
ser el mismo por construccion.

Decisiones de diseno
--------------------
1. Una barra por PARTIDO Y EQUIPO con la diferencia (goles menos xG),
   saliendo de un cero central. Se descartaron dos alternativas: barras
   enfrentadas de goles y xG (cuarenta datos, ilegible en un movil) y
   un grafico de dispersion con diagonal de referencia (elegante, pero
   exige entender que es una dispersion y el publico objetivo no tiene
   formacion estadistica, 00_PROYECTO.md §5).

2. Mismo lenguaje visual que F1: barras horizontales ordenadas, cifras
   en columna a la derecha, y una linea ambar de referencia. Quien ya
   conoce F1 entiende F6 al instante.

3. NADA DE ROJO Y VERDE. Ademas de ser la estetica de las casas de
   apuestas, aqui pintaria de verde "tener suerte", y tener suerte no
   es bueno ni malo: es un hecho. Todas las barras en gris apagado, y
   solo los dos protagonistas del post en gris claro.

4. El rival va SIEMPRE en la etiqueta. La primera version solo lo ponia
   cuando un equipo jugaba dos veces en la ventana (P-15), y el
   resultado era incoherente: `Barcelona (Rayo)` encima de `Levante` a
   secas parecia que faltase informacion en unas filas. Ademas, sin el
   rival queda ambiguo que cada barra es un PARTIDO y no el rendimiento
   del equipo en toda la jornada.

5. TODAS las cifras a la derecha, fuera del area de barras. En una
   version anterior el detalle iba a la izquierda y las barras
   negativas le pasaban por encima.

Uso:
    python -m src.content.grafico_rendimiento
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


def desde_json(ruta: Path | None = None) -> tuple[pd.DataFrame, dict]:
    """
    Lee el bloque `rendimiento` del JSON de auditoria mas reciente.

    Una fila por equipo y partido: veinte filas en una jornada normal.
    """
    if ruta is None:
        candidatos = sorted(
            p for p in PREDICCIONES.glob("*_auditoria.json")
            if "_SIM" not in p.name
        )
        if not candidatos:
            raise SystemExit(
                f"No hay ningun *_auditoria.json en {PREDICCIONES}.\n"
                f"Ejecuta antes: python -m src.evaluate.auditoria"
            )
        ruta = candidatos[-1]

    doc = json.loads(ruta.read_text(encoding="utf-8"))

    rendimiento = doc.get("rendimiento")
    if not rendimiento or not rendimiento["partidos"]:
        raise SystemExit(
            f"{ruta.name} no trae bloque `rendimiento` con partidos.\n"
            f"Los JSON anteriores a version_esquema 2 no lo tienen: "
            f"regenera la auditoria."
        )

    filas = []
    for p in rendimiento["partidos"]:
        for lado, rival in (("local", "visitante"), ("visitante", "local")):
            filas.append({
                "equipo": p[lado],
                "rival": p[rival],
                "goles": p["goles"][lado],
                "xg": p["xg"][lado],
                "diferencia": p["diferencia"][lado],
            })

    df = pd.DataFrame(filas).sort_values(
        "diferencia", ascending=False
    ).reset_index(drop=True)

    # El rival va SIEMPRE, no solo cuando un equipo se repite (P-15).
    # Ponerlo a veces si y a veces no parece que falte informacion en
    # unas filas, y ademas deja ambiguo que cada barra es un PARTIDO
    # concreto y no el rendimiento del equipo en la jornada.
    df["etiqueta"] = df["equipo"] + " - " + df["rival"]

    return df, doc


def dibujar(df: pd.DataFrame, subtitulo: str) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(estilo.ANCHO_PULGADAS, estilo.ALTO_PULGADAS))
    fig.subplots_adjust(left=0.27, right=0.66, top=0.80, bottom=0.14)

    posiciones = list(range(len(df)))
    diferencias = df["diferencia"].tolist()

    # Solo los dos protagonistas del post en gris claro. El resto
    # apagado: el color no juzga, solo dice donde mirar.
    destacados = {0, len(df) - 1}
    colores = [
        estilo.TEXTO if i in destacados else estilo.APAGADO
        for i in posiciones
    ]

    ax.barh(posiciones, diferencias, color=colores, height=0.66)

    # La linea del cero es el equivalente a la linea de corte de F1:
    # el unico ambar del grafico.
    ax.axvline(0, color=estilo.AMBAR, linewidth=2.2, zorder=3)

    # Las etiquetas llevan equipo y rival, asi que son largas:
    # `Real Sociedad  vs Real Madrid`. Se reduce la fuente en vez de
    # inventar nombres cortos, que serian una segunda fuente de verdad
    # (D-30).
    ax.set_yticks(posiciones)
    ax.set_yticklabels(df["etiqueta"], fontsize=12)
    ax.invert_yaxis()

    # Margen simetrico para que el cero quede centrado de verdad.
    tope = max(abs(min(diferencias)), abs(max(diferencias))) * 1.15
    ax.set_xlim(-tope, tope)
    ax.set_xticks([])
    ax.grid(False)
    ax.tick_params(axis="y", length=0, pad=12)

    for i, fila in df.iterrows():
        signo = "+" if fila["diferencia"] > 0 else ""
        destacada = i in destacados

        ax.text(
            tope * 1.10,
            i,
            f"{fila['goles']} gol{'es' if fila['goles'] != 1 else ''}"
            f"  ·  {fila['xg']:.2f} xG",
            va="center",
            ha="left",
            fontsize=12,
            color=estilo.TEXTO if destacada else estilo.SECUNDARIO,
        )
        ax.text(
            tope * 2.05,
            i,
            f"{signo}{fila['diferencia']:.2f}",
            va="center",
            ha="right",
            fontsize=14,
            color=estilo.TEXTO if destacada else estilo.SECUNDARIO,
            weight="bold" if destacada else "normal",
        )

    for t in ax.texts:
        t.set_clip_on(False)

    estilo.titular(fig, "GOLES CONTRA XG", subtitulo)
    estilo.pie(
        fig,
        "xG = las ocasiones que creó cada equipo   ·   "
        "a la derecha, los que marcaron más de lo que generaron",
    )

    return fig


def main() -> None:
    estilo.aplicar()

    df, doc = desde_json()

    temporada = doc["temporada"]
    jornada = doc["jornada_etiqueta"]
    r = doc["rendimiento"]

    subtitulo = f"Jornada {jornada}  ·  {temporada}"

    fig = dibujar(df, subtitulo)
    destino = estilo.guardar(fig, f"rendimiento_{temporada}_j{jornada:02d}.png")
    plt.close(fig)

    mas, menos = r["mas_afortunado"], r["menos_afortunado"]
    print(f"Jornada {jornada} de {temporada}  ({len(r['partidos'])} partidos)")
    print(f"\n   Marcó de más:   {mas['equipo']:<14} "
          f"{mas['goles']} con {mas['xg']:.2f} de xG  ({mas['diferencia']:+.2f})")
    print(f"   Marcó de menos: {menos['equipo']:<14} "
          f"{menos['goles']} con {menos['xg']:.2f} de xG  ({menos['diferencia']:+.2f})")

    if r["sin_xg"]:
        print(f"\n   Sin xG, fuera del gráfico: {', '.join(r['sin_xg'])}")

    print(f"\nGuardado en {destino}")


if __name__ == "__main__":
    main()