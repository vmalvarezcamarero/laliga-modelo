"""
Metricas de evaluacion de predicciones 1X2.

RPS (Ranked Probability Score) es la metrica principal. Penaliza mas
equivocarse por dos casillas que por una: si dices "gana el local" y
gana el visitante, el castigo es mayor que si empatan. Tiene sentido
porque el 1X2 tiene orden natural (local - empate - visitante).

Cuanto mas bajo, mejor. Un modelo perfecto daria 0.
"""

import numpy as np

# Referencia mental: el 33/33/33 da un RPS de ~0.2222 pase lo que pase.


def rps(probabilidades: np.ndarray, resultados: np.ndarray) -> np.ndarray:
    """
    RPS por partido.

    probabilidades: matriz (n, 3) con las probabilidades de local,
                    empate y visitante. Cada fila suma 1.
    resultados:     vector (n,) con 0 = local, 1 = empate, 2 = visitante.

    Devuelve un vector (n,) con el RPS de cada partido.
    """
    probabilidades = np.asarray(probabilidades, dtype=float)
    resultados = np.asarray(resultados, dtype=int)

    n = len(resultados)
    observado = np.zeros((n, 3))
    observado[np.arange(n), resultados] = 1.0

    # Acumuladas: la tercera columna siempre vale 1 en ambos lados,
    # asi que no aporta y se omite (de ahi el "r - 1" de la formula).
    acum_pred = np.cumsum(probabilidades, axis=1)[:, :2]
    acum_obs = np.cumsum(observado, axis=1)[:, :2]

    return np.sum((acum_pred - acum_obs) ** 2, axis=1) / 2.0


def brier(probabilidades: np.ndarray, resultados: np.ndarray) -> np.ndarray:
    """
    Brier score por partido. Metrica secundaria, mas facil de explicar
    en publico: es la distancia al cuadrado entre lo que dijiste y lo
    que paso, sin tener en cuenta el orden de los resultados.
    """
    probabilidades = np.asarray(probabilidades, dtype=float)
    resultados = np.asarray(resultados, dtype=int)

    n = len(resultados)
    observado = np.zeros((n, 3))
    observado[np.arange(n), resultados] = 1.0

    return np.sum((probabilidades - observado) ** 2, axis=1)


def resultado_a_indice(goles_local, goles_visitante) -> np.ndarray:
    """Convierte marcadores en 0 (local), 1 (empate) o 2 (visitante)."""
    gl = np.asarray(goles_local)
    gv = np.asarray(goles_visitante)
    return np.where(gl > gv, 0, np.where(gl == gv, 1, 2))


def cuotas_a_probabilidades(
    cuota_local, cuota_empate, cuota_visitante
) -> np.ndarray:
    """
    Convierte cuotas decimales en probabilidades, quitando el margen
    de la casa.

    Una cuota de 2.00 implica un 50%. Pero las tres implicitas de un
    partido suman mas de 1: ese exceso es el margen. Se reparte
    proporcionalmente dividiendo cada una entre la suma.

    USO INTERNO. Estas probabilidades son un baseline de evaluacion y
    no se publican nunca (decision D-14).
    """
    implicitas = np.column_stack(
        [
            1.0 / np.asarray(cuota_local, dtype=float),
            1.0 / np.asarray(cuota_empate, dtype=float),
            1.0 / np.asarray(cuota_visitante, dtype=float),
        ]
    )
    return implicitas / implicitas.sum(axis=1, keepdims=True)