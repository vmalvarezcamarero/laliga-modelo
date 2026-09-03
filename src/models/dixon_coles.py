"""
Modelo Dixon-Coles ajustado sobre xG.

Estima para cada equipo una fuerza de ataque y una de defensa, mas una
ventaja de campo global y el nivel medio de la liga. De ahi sale una
matriz de probabilidad de marcadores, y de la matriz salen 1X2,
over/under y entropia.

Dos particularidades respecto a la implementacion de manual:

1. Se ajusta sobre xG, no sobre goles (decision D-02). Como el xG no es
   un numero entero, no se puede usar la verosimilitud de Poisson tal
   cual: se usa la version continua, que admite decimales y da los
   mismos estimadores.

2. Por eso el ajuste va en dos etapas. Las fuerzas se estiman con xG;
   la correccion de marcadores bajos (rho) se estima despues sobre los
   goles reales, porque esa correccion habla de marcadores enteros y no
   tiene sentido aplicarla al xG.

Ataque y defensa estan centrados: 1.00 es exactamente la media de la
liga. El nivel absoluto de goles vive en su propio parametro.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

MAX_GOLES = 8  # matriz de marcadores de 0-0 a 8-8


@dataclass
class ParametrosDC:
    """Resultado de un ajuste."""

    equipos: list[str]
    ataque: np.ndarray      # uno por equipo, 1.00 = media
    defensa: np.ndarray     # uno por equipo, 1.00 = media, menos es mejor
    ventaja_campo: float
    nivel_liga: float       # xG medio de referencia de la liga
    rho: float
    n_partidos: int

    def indice(self, equipo: str) -> int:
        return self.equipos.index(equipo)

    def tabla(self) -> pd.DataFrame:
        """Fuerzas por equipo, ordenadas por fuerza neta."""
        df = pd.DataFrame(
            {
                "equipo": self.equipos,
                "ataque": self.ataque,
                "defensa": self.defensa,
            }
        )
        # Fuerza neta: cuanto genera dividido entre cuanto concede.
        # 1.00 es un equipo exactamente medio.
        df["fuerza"] = df["ataque"] / df["defensa"]
        return df.sort_values("fuerza", ascending=False).reset_index(drop=True)


# --- Pesos temporales ----------------------------------------------


def pesos_temporales(fechas: pd.Series, referencia, xi: float) -> np.ndarray:
    """
    peso = exp(-xi * dias_transcurridos)

    xi = 0      -> todos los partidos pesan igual
    xi = 0.001  -> un partido de hace 2 anos pesa la mitad
    xi = 0.005  -> un partido de hace 5 meses pesa la mitad
    """
    dias = (pd.Timestamp(referencia) - pd.to_datetime(fechas)).dt.days.to_numpy()
    dias = np.clip(dias, 0, None)
    return np.exp(-xi * dias)


# --- Etapa 1: fuerzas sobre xG --------------------------------------


def _neg_log_verosimilitud(
    params: np.ndarray,
    idx_local: np.ndarray,
    idx_visitante: np.ndarray,
    xg_local: np.ndarray,
    xg_visitante: np.ndarray,
    pesos: np.ndarray,
    n_equipos: int,
) -> float:
    """
    Poisson continua, ponderada.

    Trabajamos en logaritmos para que ataque y defensa sean siempre
    positivos sin necesidad de restricciones.
    """
    log_ataque = params[:n_equipos]
    log_defensa = params[n_equipos : 2 * n_equipos]
    log_gamma = params[-2]
    mu = params[-1]  # nivel medio de xG de la liga

    # Identificabilidad: sin esto hay infinitas soluciones equivalentes
    # (subir todos los ataques y bajar todas las defensas da lo mismo).
    # Centrando ambos, un 1.00 significa "exactamente la media".
    log_ataque = log_ataque - log_ataque.mean()
    log_defensa = log_defensa - log_defensa.mean()

    log_lambda_local = mu + log_ataque[idx_local] + log_defensa[idx_visitante] + log_gamma
    log_lambda_visit = mu + log_ataque[idx_visitante] + log_defensa[idx_local]

    lambda_local = np.exp(log_lambda_local)
    lambda_visit = np.exp(log_lambda_visit)

    # log f(x) = x*log(lambda) - lambda  (se omite log Gamma(x+1),
    # constante respecto a los parametros)
    ll = (
        xg_local * log_lambda_local
        - lambda_local
        + xg_visitante * log_lambda_visit
        - lambda_visit
    )

    # Penalizacion minuscula sobre los parametros en bruto.
    # Centrar dentro de la funcion crea direcciones planas (sumar una
    # constante a todos los ataques no cambia nada), y el optimizador
    # se pasea por ellas sin converger. Este termino las elimina sin
    # mover la solucion de forma apreciable.
    penalizacion = 1e-4 * float(np.sum(params**2))

    return -float(np.sum(pesos * ll)) + penalizacion


# --- Etapa 2: rho sobre goles reales --------------------------------


def _tau(x, y, lambda_local, lambda_visit, rho):
    """
    Correccion de Dixon-Coles. Solo toca 0-0, 1-0, 0-1 y 1-1, que son
    los marcadores que Poisson subestima.
    """
    t = np.ones_like(lambda_local, dtype=float)
    t = np.where((x == 0) & (y == 0), 1 - lambda_local * lambda_visit * rho, t)
    t = np.where((x == 0) & (y == 1), 1 + lambda_local * rho, t)
    t = np.where((x == 1) & (y == 0), 1 + lambda_visit * rho, t)
    t = np.where((x == 1) & (y == 1), 1 - rho, t)
    return t


def _neg_ll_rho(rho_array, goles_local, goles_visit, lambda_local, lambda_visit, pesos):
    rho = float(rho_array[0])
    t = _tau(goles_local, goles_visit, lambda_local, lambda_visit, rho)
    if np.any(t <= 0):
        return 1e10  # rho invalido: probabilidades negativas
    return -float(np.sum(pesos * np.log(t)))


# --- Ajuste completo ------------------------------------------------


def ajustar(
    partidos: pd.DataFrame,
    xi: float = 0.0018,
    referencia=None,
) -> ParametrosDC:
    """
    Ajusta el modelo sobre un DataFrame de partidos.

    Columnas necesarias: fecha, local, visitante, xg_local, xg_visitante,
    goles_local, goles_visitante.
    """
    partidos = partidos.dropna(
        subset=["xg_local", "xg_visitante", "goles_local", "goles_visitante"]
    ).copy()

    if partidos.empty:
        raise ValueError("No hay partidos con xG para ajustar.")

    if referencia is None:
        referencia = pd.to_datetime(partidos["fecha"]).max()

    equipos = sorted(set(partidos["local"]) | set(partidos["visitante"]))
    n = len(equipos)
    pos = {e: i for i, e in enumerate(equipos)}

    idx_local = partidos["local"].map(pos).to_numpy()
    idx_visit = partidos["visitante"].map(pos).to_numpy()
    xg_local = partidos["xg_local"].to_numpy(dtype=float)
    xg_visit = partidos["xg_visitante"].to_numpy(dtype=float)
    pesos = pesos_temporales(partidos["fecha"], referencia, xi)

    # Punto de partida: todos los equipos iguales, ventaja de campo leve
    inicial = np.concatenate([np.zeros(n), np.zeros(n), [np.log(1.15), np.log(1.3)]])

    resultado = minimize(
        _neg_log_verosimilitud,
        inicial,
        args=(idx_local, idx_visit, xg_local, xg_visit, pesos, n),
        method="L-BFGS-B",
        options={"maxiter": 5000, "maxfun": 50000},,
    )

    if not resultado.success:
        print(f"  aviso: la optimizacion no convergio ({resultado.message})")

    log_ataque = resultado.x[:n]
    log_ataque = log_ataque - log_ataque.mean()
    log_defensa = resultado.x[n : 2 * n]
    log_defensa = log_defensa - log_defensa.mean()
    log_gamma = resultado.x[-2]
    mu = resultado.x[-1]

    # Etapa 2: rho sobre los goles reales
    lambda_local = np.exp(mu + log_ataque[idx_local] + log_defensa[idx_visit] + log_gamma)
    lambda_visit = np.exp(mu + log_ataque[idx_visit] + log_defensa[idx_local])

    res_rho = minimize(
        _neg_ll_rho,
        np.array([-0.05]),
        args=(
            partidos["goles_local"].to_numpy(),
            partidos["goles_visitante"].to_numpy(),
            lambda_local,
            lambda_visit,
            pesos,
        ),
        method="L-BFGS-B",
        bounds=[(-0.3, 0.3)],
    )

    return ParametrosDC(
        equipos=equipos,
        ataque=np.exp(log_ataque),
        defensa=np.exp(log_defensa),
        ventaja_campo=float(np.exp(log_gamma)),
        nivel_liga=float(np.exp(mu)),
        rho=float(res_rho.x[0]),
        n_partidos=len(partidos),
    )


# --- Prediccion -----------------------------------------------------


def matriz_marcadores(p: ParametrosDC, local: str, visitante: str) -> np.ndarray:
    """
    Matriz de probabilidad de marcadores. La celda [i, j] es la
    probabilidad de que acabe i-j.
    """
    i_local, i_visit = p.indice(local), p.indice(visitante)

    lam_local = p.nivel_liga * p.ataque[i_local] * p.defensa[i_visit] * p.ventaja_campo
    lam_visit = p.nivel_liga * p.ataque[i_visit] * p.defensa[i_local]

    goles = np.arange(MAX_GOLES + 1)
    prob_local = poisson.pmf(goles, lam_local)
    prob_visit = poisson.pmf(goles, lam_visit)

    matriz = np.outer(prob_local, prob_visit)

    # Correccion de las cuatro celdas bajas
    matriz[0, 0] *= 1 - lam_local * lam_visit * p.rho
    matriz[0, 1] *= 1 + lam_local * p.rho
    matriz[1, 0] *= 1 + lam_visit * p.rho
    matriz[1, 1] *= 1 - p.rho

    return matriz / matriz.sum()


def probabilidades_1x2(matriz: np.ndarray) -> tuple[float, float, float]:
    """Devuelve (local, empate, visitante)."""
    local = float(np.tril(matriz, -1).sum())
    empate = float(np.trace(matriz))
    visitante = float(np.triu(matriz, 1).sum())
    return local, empate, visitante


def entropia(matriz: np.ndarray) -> float:
    """
    Incertidumbre del 1X2, en bits. Maximo 1.585 (tres opciones
    equiprobables). Alimenta el formato del punto ciego.
    """
    probs = np.array(probabilidades_1x2(matriz))
    probs = probs[probs > 0]
    return float(-np.sum(probs * np.log2(probs)))