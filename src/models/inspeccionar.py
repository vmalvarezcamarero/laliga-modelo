"""
Inspeccion rapida del ajuste de Dixon-Coles.

No forma parte del pipeline. Es una herramienta para mirar si los
numeros tienen sentido futbolistico antes de fiarse del modelo.
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import dixon_coles as dc

RAIZ = Path(__file__).resolve().parents[2]
RUTA_DB = RAIZ / "data" / "laliga.db"

TEMPORADA = "2025-26"


def main() -> None:
    with sqlite3.connect(RUTA_DB) as conexion:
        partidos = pd.read_sql(
            "SELECT * FROM matches WHERE temporada = ?",
            conexion,
            params=(TEMPORADA,),
        )

    print(f"Temporada {TEMPORADA}: {len(partidos)} partidos")

    # xi=0 -> todos los partidos pesan igual. Para inspeccionar una
    # temporada suelta es lo que queremos: el decaimiento se calibra
    # despues, en el backtest (P-02).
    p = dc.ajustar(partidos, xi=0.0)

    print(f"Ventaja de campo: {p.ventaja_campo:.3f}")
    print(f"Nivel de liga: {p.nivel_liga:.3f} xG")
    print(f"rho: {p.rho:.4f}")
    print()

    tabla = p.tabla()
    tabla.index = range(1, len(tabla) + 1)
    print(tabla.round(3).to_string())

    # Un partido de ejemplo para ver la prediccion completa
    local, visitante = "Barcelona", "Getafe"
    matriz = dc.matriz_marcadores(p, local, visitante)
    prob_l, prob_e, prob_v = dc.probabilidades_1x2(matriz)

    print(f"\n{local} - {visitante}")
    print(f"  1X2: {prob_l:.1%} / {prob_e:.1%} / {prob_v:.1%}")
    print(f"  Entropia: {dc.entropia(matriz):.3f} bits")

    # Marcador mas probable
    import numpy as np

    i, j = np.unravel_index(matriz.argmax(), matriz.shape)
    print(f"  Marcador mas probable: {i}-{j} ({matriz[i, j]:.1%})")


if __name__ == "__main__":
    main()