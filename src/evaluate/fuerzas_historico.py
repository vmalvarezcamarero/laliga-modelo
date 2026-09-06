"""
Calcula la fuerza que EGO daba a cada equipo en cada jornada de las
temporadas de validacion, y la guarda en un CSV.

Por que un CSV intermedio
-------------------------
Saber quien pasaba la criba en la jornada 14 exige ajustar el modelo
solo con datos anteriores a esa jornada. Son 76 ajustes completos. Si el
barrido de umbrales repitiera esos ajustes para cada umbral candidato,
serian horas.

Separando las dos cosas, la parte cara se paga una vez y el barrido pasa
a costar un segundo. Ademas queda un subproducto util: el registro de
que fuerza tenia cada equipo en cada jornada de las dos ultimas
temporadas, que es material publicable por si solo.

Frontera temporal
-----------------
Se entrena con partidos de fecha ESTRICTAMENTE anterior a la primera
fecha de la jornada evaluada. Mismo criterio que backtest.py. Nunca
datos del futuro.

Uso:
    python -m src.evaluate.fuerzas_historico

Tarda entre 5 y 20 minutos. Se ejecuta una vez.
"""

import sqlite3
import time
from pathlib import Path

import pandas as pd

from src.models import dixon_coles
from src.models.criba import XI, asignar_jornadas

RAIZ = Path(__file__).resolve().parents[2]
BD = RAIZ / "data" / "laliga.db"
# Artefacto de calibracion, no registro de lo dictaminado.
# `outputs/predictions/` solo contiene predicciones y auditorias (D-26).
SALIDA = RAIZ / "outputs" / "calibracion" / "fuerzas_historico.csv"

# Las mismas temporadas de validacion del Sprint 1. Calibrar el umbral
# sobre ellas es legitimo: el umbral no se elige por acierto predictivo,
# sino por cuantos equipos deja pasar y cuanta discusion genera.
TEMPORADAS = ["2024-25", "2025-26"]


def cargar_partidos() -> pd.DataFrame:
    con = sqlite3.connect(BD)
    df = pd.read_sql("SELECT * FROM matches", con, parse_dates=["fecha"])
    con.close()
    return df.sort_values("fecha").reset_index(drop=True)


def main() -> None:
    if not BD.exists():
        raise SystemExit(f"No encuentro la base de datos en {BD}")

    partidos = cargar_partidos()
    print(f"Cargados {len(partidos)} partidos.")

    filas = []
    sin_datos = set()
    inicio = time.time()
    hechas = 0
    total = 0

    for temporada in TEMPORADAS:
        de_la_temporada = asignar_jornadas(partidos[partidos["temporada"] == temporada])
        total += de_la_temporada["jornada"].nunique()

    for temporada in TEMPORADAS:
        de_la_temporada = asignar_jornadas(partidos[partidos["temporada"] == temporada])
        equipos_temporada = set(de_la_temporada["local"]) | set(de_la_temporada["visitante"])

        for jornada, bloque in de_la_temporada.groupby("jornada"):
            corte = bloque["fecha"].min()

            # Esta es toda la validacion temporal. El '<' es estricto.
            entrenamiento = partidos[partidos["fecha"] < corte]

            parametros = dixon_coles.ajustar(entrenamiento, xi=XI, referencia=corte)
            tabla = parametros.tabla()

            # Equipos de la temporada sin historico en la ventana de
            # entrenamiento: recien ascendidos que debutan. No se
            # inventan; se anotan y se reportan.
            sin_datos |= equipos_temporada - set(parametros.equipos)

            tabla = tabla[tabla["equipo"].isin(equipos_temporada)].copy()
            tabla["temporada"] = temporada
            tabla["jornada"] = jornada
            tabla["fecha_corte"] = corte
            tabla["n_entrenamiento"] = parametros.n_partidos
            filas.append(tabla)

            hechas += 1
            transcurrido = time.time() - inicio
            restante = transcurrido / hechas * (total - hechas)
            print(
                f"  {temporada} J{jornada:>2}  "
                f"{len(tabla)} equipos  "
                f"entrenado con {parametros.n_partidos} partidos  "
                f"(quedan ~{restante/60:.1f} min)"
            )

    resultado = pd.concat(filas, ignore_index=True)
    resultado = resultado[
        [
            "temporada",
            "jornada",
            "fecha_corte",
            "equipo",
            "ataque",
            "defensa",
            "fuerza",
            "n_entrenamiento",
        ]
    ]

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    resultado.to_csv(SALIDA, index=False, encoding="utf-8")

    print()
    print(f"Escritas {len(resultado)} filas en {SALIDA}")
    print(f"Tiempo total: {(time.time() - inicio)/60:.1f} min")

    if sin_datos:
        print()
        print("Equipos sin historico en alguna ventana de entrenamiento:")
        for e in sorted(sin_datos):
            print(f"  - {e}")
        print("Quedan fuera de la criba en esas jornadas. Ver decision pendiente.")


if __name__ == "__main__":
    main()
