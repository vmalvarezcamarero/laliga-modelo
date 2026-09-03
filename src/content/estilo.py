"""
Identidad visual de LA CRIBA.

Todo grafico del proyecto importa de aqui. El motivo es practico: si
cada grafico define sus propios colores, en tres meses hay cinco tonos
de gris distintos y la cuenta parece amateur. Cambiando una linea de
este archivo cambian los cinco formatos a la vez.

La regla del acento
-------------------
El ambar aparece UNA VEZ por grafico, y siempre significa lo mismo: la
linea de corte. Si se usa tambien para destacar equipos, y para el
titulo, y para lo que sea, deja de significar nada.

Nada de rojo y verde. Es la estetica de las casas de apuestas y el
proyecto se juega su identidad en no parecerse a eso
(00_PROYECTO.md §8).

Tipografia
----------
JetBrains Mono en todo el grafico, no solo en los numeros. EGO es una
maquina y una tipografia de maquina es coherente. Ademas los numeros
quedan alineados en columna sin esfuerzo.

Si la fuente no esta instalada, el modulo avisa y usa la monoespaciada
del sistema. El grafico sale, pero se nota.
"""

from pathlib import Path

import matplotlib
from matplotlib import font_manager

RAIZ = Path(__file__).resolve().parents[2]
CARPETA_GRAFICOS = RAIZ / "outputs" / "charts"

# --- Paleta ---------------------------------------------------------

FONDO = "#0E1013"        # casi negro, con un punto de azul
TEXTO = "#E8EAED"        # gris muy claro; el blanco puro deslumbra
SECUNDARIO = "#7C848D"   # lo que no debe llamar la atencion
REJILLA = "#1C1F24"      # apenas visible
AMBAR = "#FFB020"        # SOLO la linea de corte
APAGADO = "#3A4048"      # barras de quien no pasa la criba

# --- Formato --------------------------------------------------------

# 16:9. X recorta las imagenes en el timeline y esta proporcion se ve
# entera sin que nadie tenga que pulsar.
ANCHO_PULGADAS = 16
ALTO_PULGADAS = 9
DPI = 100  # da 1600x900 exactos

FUENTE = "JetBrains Mono"

CREDITO = "@PasaLaCriba"


def _hay_fuente() -> bool:
    disponibles = {f.name for f in font_manager.fontManager.ttflist}
    return FUENTE in disponibles


def aplicar() -> None:
    """
    Deja matplotlib configurado con la identidad del proyecto.

    Llamar UNA vez al principio de cada script de grafico, antes de
    crear ninguna figura.
    """
    if _hay_fuente():
        familia = [FUENTE]
    else:
        print(
            f"  aviso: '{FUENTE}' no esta instalada. Uso la monoespaciada "
            "del sistema; el grafico sale peor."
        )
        familia = ["DejaVu Sans Mono", "Consolas", "monospace"]

    matplotlib.rcParams.update(
        {
            "font.family": "monospace",
            "font.monospace": familia,
            "figure.facecolor": FONDO,
            "axes.facecolor": FONDO,
            "savefig.facecolor": FONDO,
            "text.color": TEXTO,
            "axes.labelcolor": TEXTO,
            "xtick.color": SECUNDARIO,
            "ytick.color": TEXTO,
            "axes.edgecolor": REJILLA,
            "grid.color": REJILLA,
            "figure.dpi": DPI,
            "savefig.dpi": DPI,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
            "axes.spines.bottom": False,
        }
    )


def titular(fig, titulo: str, subtitulo: str = "") -> None:
    """
    Titulo arriba a la izquierda, alineado con el resto del grafico.

    Se usa fig.text y no set_title porque el titulo pertenece a la
    imagen entera, no a los ejes.
    """
    fig.text(0.045, 0.945, titulo, fontsize=30, weight="bold", color=TEXTO)
    if subtitulo:
        fig.text(0.045, 0.898, subtitulo, fontsize=15, color=SECUNDARIO)


def pie(fig, texto: str) -> None:
    """Pie de foto: la explicacion y el credito de la cuenta."""
    fig.text(0.045, 0.045, texto, fontsize=12, color=SECUNDARIO)
    fig.text(0.955, 0.045, CREDITO, fontsize=12, color=SECUNDARIO, ha="right")


def guardar(fig, nombre: str) -> Path:
    """
    Guarda el PNG en outputs/charts/ y devuelve la ruta.

    Esa carpeta NO se versiona (01_ARQUITECTURA.md §6): los graficos se
    regeneran en un segundo y ocuparian el repo para nada.
    """
    CARPETA_GRAFICOS.mkdir(parents=True, exist_ok=True)
    destino = CARPETA_GRAFICOS / nombre
    fig.savefig(destino, facecolor=FONDO)
    return destino
