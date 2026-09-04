"""
Modelo de corners: binomial negativa.

Por que binomial negativa y no Poisson
--------------------------------------
Poisson exige que la varianza sea igual a la media. En los datos de
LaLiga (12 temporadas, 4.590 partidos):

    corners del local      media 5.39   varianza 8.40   ratio 1.56
    corners del visitante  media 4.18   varianza 6.03   ratio 1.44

Con Poisson, un equipo sacando 12 corners seria casi imposible, y pasa
varias veces por jornada. La binomial negativa anade un parametro de
dispersion 'r' que absorbe ese exceso de varianza. Cuando r tiende a
infinito, la binomial negativa ES una Poisson: el modelo puede decidir
por si mismo que no hacia falta.

Parte del exceso de varianza no es dispersion real, sino que unos
equipos sacan mas corners que otros. El modelo explica esa parte con los
parametros de equipo, asi que la r ajustada saldra por encima de la que
sugieren las cifras de arriba. Es lo esperado.

Por que sobre conteos brutos y no sobre una metrica de expectativa
------------------------------------------------------------------
Resuelve P-09. Ajustamos el modelo principal sobre xG porque el gol es
un suceso raro y el xG es la senal detras del ruido. Un corner no es
raro: hay 9.6 por partido. La media ya hace ese trabajo de suavizado, y
no existe un "xCorners" con la solidez del xG. Buscarlo seria complicar
por gusto.

Encogimiento hacia la media (D-27)
----------------------------------
El Rayo salio con 13.7 corners esperados contra el Racing recien
ascendido: la concesion del Racing con 3 partidos estaba disparatada y
multiplicaba. Generacion y concesion se mezclan con el 1.00 de la liga
en proporcion a la muestra, usando la misma funcion que dixon_coles.py.

Estructura
----------
Igual que dixon_coles.py, para no aprender un patron nuevo: generacion y
concesion por equipo, ventaja de campo, nivel de liga, todo centrado en
1.00 = media. Sin rho (la correccion de marcadores bajos no tiene
equivalente aqui) y sin ajuste en dos etapas (los corners ya son
enteros, no hay Poisson continua que resolver).

Nota sobre publicacion
----------------------
Este modulo produce de forma natural cosas como "probabilidad de mas de
9.5 corners". Ese formato con decimal es una convencion de casas de
apuestas, no estadistica. No se usa en NADA publicable: en los posts se
habla con enteros y en castellano. Ver 00_PROYECTO.md §8.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import nbinom

from src.models.criba import XI
from src.models.dixon_coles import K_ENCOGIMIENTO, encoger, pesos_temporales

MAX_CORNERS = 25  # cola suficiente: el maximo historico por equipo esta lejos


@dataclass
class ParametrosCorners:
    """Resultado de un ajuste. Los parametros ya vienen encogidos."""

    equipos: list[str]
    generacion: np.ndarray   # 1.00 = saca los corners de un equipo medio
    concesion: np.ndarray    # 1.00 = concede los de un equipo medio
    ventaja_campo: float
    nivel_liga: float        # corners de referencia por equipo y partido
    dispersion: float        # r; cuanto mas alto, mas se parece a Poisson
    n_partidos: int
    partidos_equipo: dict[str, int] = field(default_factory=dict)

    def indice(self, equipo: str) -> int:
        return self.equipos.index(equipo)

    def muestra(self, equipo: str) -> int:
        return self.partidos_equipo.get(equipo, 0)

    def tabla(self) -> pd.DataFrame:
        """Generacion y concesion por equipo, de mas a menos corners."""
        df = pd.DataFrame(
            {
                "equipo": self.equipos,
                "generacion": self.generacion,
                "concesion": self.concesion,
            }
        )
        df["partidos"] = df["equipo"].map(self.partidos_equipo).fillna(0).astype(int)
        return df.sort_values("generacion", ascending=False).reset_index(drop=True)


# --- Verosimilitud --------------------------------------------------


def _log_pmf_nb(y: np.ndarray, mu: np.ndarray, r: float) -> np.ndarray:
    """
    Binomial negativa parametrizada por media y dispersion.

    Es la forma comoda: 'mu' es directamente el numero esperado de
    corners, y 'r' controla cuanta varianza extra hay por encima de
    Poisson. La varianza vale mu + mu^2 / r.
    """
    return (
        gammaln(y + r)
        - gammaln(r)
        - gammaln(y + 1)
        + r * (np.log(r) - np.log(r + mu))
        + y * (np.log(mu) - np.log(r + mu))
    )


def _neg_log_verosimilitud(
    params: np.ndarray,
    idx_local: np.ndarray,
    idx_visitante: np.ndarray,
    corners_local: np.ndarray,
    corners_visitante: np.ndarray,
    pesos: np.ndarray,
    n_equipos: int,
) -> float:
    log_generacion = params[:n_equipos]
    log_concesion = params[n_equipos : 2 * n_equipos]
    log_gamma = params[-3]
    mu_liga = params[-2]
    log_r = params[-1]

    # Identificabilidad: sin centrar hay infinitas soluciones
    # equivalentes. Centrando, 1.00 significa "exactamente la media".
    log_generacion = log_generacion - log_generacion.mean()
    log_concesion = log_concesion - log_concesion.mean()

    r = np.exp(log_r)

    mu_local = np.exp(
        mu_liga + log_generacion[idx_local] + log_concesion[idx_visitante] + log_gamma
    )
    mu_visit = np.exp(
        mu_liga + log_generacion[idx_visitante] + log_concesion[idx_local]
    )

    ll = _log_pmf_nb(corners_local, mu_local, r) + _log_pmf_nb(
        corners_visitante, mu_visit, r
    )

    # Misma penalizacion que en dixon_coles.py y por el mismo motivo:
    # centrar dentro de la funcion crea direcciones planas por las que
    # el optimizador se pasea sin converger.
    penalizacion = 1e-4 * float(np.sum(params**2))

    return -float(np.sum(pesos * ll)) + penalizacion


# --- Ajuste ---------------------------------------------------------


def ajustar(
    partidos: pd.DataFrame,
    xi: float = XI,
    referencia=None,
    k: int = K_ENCOGIMIENTO,
) -> ParametrosCorners:
    """
    Columnas necesarias: fecha, local, visitante, corners_local,
    corners_visitante.

    Los parametros devueltos ya vienen encogidos hacia la media (D-27).
    Con k = 0 se desactiva el encogimiento, util para comparar.
    """
    partidos = partidos.dropna(
        subset=["corners_local", "corners_visitante"]
    ).copy()

    if partidos.empty:
        raise ValueError("No hay partidos con corners para ajustar.")

    if referencia is None:
        referencia = pd.to_datetime(partidos["fecha"]).max()

    equipos = sorted(set(partidos["local"]) | set(partidos["visitante"]))
    n = len(equipos)
    pos = {e: i for i, e in enumerate(equipos)}

    idx_local = partidos["local"].map(pos).to_numpy()
    idx_visit = partidos["visitante"].map(pos).to_numpy()
    c_local = partidos["corners_local"].to_numpy(dtype=float)
    c_visit = partidos["corners_visitante"].to_numpy(dtype=float)
    pesos = pesos_temporales(partidos["fecha"], referencia, xi)

    # Partidos por equipo. Es lo que gobierna el encogimiento.
    conteo = (
        pd.concat([partidos["local"], partidos["visitante"]])
        .value_counts()
        .to_dict()
    )
    partidos_equipo = {e: int(conteo.get(e, 0)) for e in equipos}

    # Punto de partida: todos los equipos iguales, ventaja de campo leve,
    # nivel de liga en la media observada, dispersion moderada.
    media = float(np.mean(np.concatenate([c_local, c_visit])))
    inicial = np.concatenate(
        [np.zeros(n), np.zeros(n), [np.log(1.25), np.log(media), np.log(10.0)]]
    )

    resultado = minimize(
        _neg_log_verosimilitud,
        inicial,
        args=(idx_local, idx_visit, c_local, c_visit, pesos, n),
        method="L-BFGS-B",
        options={"maxiter": 5000, "maxfun": 50000},
    )

    if not resultado.success:
        print(f"  aviso: la optimizacion no convergio ({resultado.message})")

    log_generacion = resultado.x[:n]
    log_generacion = log_generacion - log_generacion.mean()
    log_concesion = resultado.x[n : 2 * n]
    log_concesion = log_concesion - log_concesion.mean()

    generacion = np.exp(log_generacion)
    concesion = np.exp(log_concesion)

    # Encogimiento (D-27). Una sola vez, aqui, para que todo lo que
    # salga de este objeto hable con los mismos numeros.
    if k > 0:
        muestras = np.array([partidos_equipo[e] for e in equipos], dtype=float)
        generacion = encoger(generacion, muestras, k)
        concesion = encoger(concesion, muestras, k)

    return ParametrosCorners(
        equipos=equipos,
        generacion=generacion,
        concesion=concesion,
        ventaja_campo=float(np.exp(resultado.x[-3])),
        nivel_liga=float(np.exp(resultado.x[-2])),
        dispersion=float(np.exp(resultado.x[-1])),
        n_partidos=len(partidos),
        partidos_equipo=partidos_equipo,
    )


# --- Prediccion -----------------------------------------------------


def corners_esperados(
    p: ParametrosCorners, local: str, visitante: str
) -> tuple[float, float]:
    """Numero esperado de corners de cada equipo."""
    i_local, i_visit = p.indice(local), p.indice(visitante)

    mu_local = p.nivel_liga * p.generacion[i_local] * p.concesion[i_visit] * p.ventaja_campo
    mu_visit = p.nivel_liga * p.generacion[i_visit] * p.concesion[i_local]

    return float(mu_local), float(mu_visit)


def distribucion(mu: float, r: float) -> np.ndarray:
    """Probabilidad de 0, 1, 2... MAX_CORNERS corners para un equipo."""
    valores = np.arange(MAX_CORNERS + 1)
    probs = nbinom.pmf(valores, r, r / (r + mu))
    return probs / probs.sum()


def distribucion_total(
    p: ParametrosCorners, local: str, visitante: str
) -> np.ndarray:
    """
    Distribucion del total del partido, sumando las dos.

    OJO: esto asume que los corners de los dos equipos son
    independientes, y en los datos no lo son del todo (si uno domina, el
    otro no saca corners). El backtest mide cuanto se desvia. Si el
    sesgo es grande, no uses esta funcion para publicar totales.
    """
    mu_local, mu_visit = corners_esperados(p, local, visitante)
    return np.convolve(
        distribucion(mu_local, p.dispersion),
        distribucion(mu_visit, p.dispersion),
    )


def prob_mas_de(dist: np.ndarray, n: int) -> float:
    """
    Probabilidad de superar n corners, con n ENTERO.

    Deliberadamente no se admiten medios corners: el 9.5 es una
    convencion de casas de apuestas y este proyecto no publica eso.
    'Mas de 12' significa 13 o mas.
    """
    if int(n) != n:
        raise ValueError(
            "El umbral debe ser un entero. Las lineas con .5 son de "
            "casas de apuestas y no se usan aqui (00_PROYECTO.md §8)."
        )
    return float(dist[int(n) + 1 :].sum())