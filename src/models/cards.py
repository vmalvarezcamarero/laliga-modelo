"""
Modelo de tarjetas amarillas: Poisson.

Por que Poisson y no binomial negativa
--------------------------------------
En los datos (4.590 partidos): amarillas por partido, media 4.93,
varianza 5.56. Ratio 1.13, practicamente lo que exige Poisson. Los
corners estaban en 1.44-1.56 y por eso alli hacia falta un parametro de
dispersion. Aqui no lo hay que anadir.

Por que sin efecto arbitro (D-18, resuelve P-08)
------------------------------------------------
La arquitectura original planteaba un efecto arbitro con regularizacion.
Football-Data no publica el arbitro para LaLiga: solo para las ligas
inglesas. Sacarlo de FBref exige Selenium, un navegador automatizado y
una peticion cada 6-7 segundos, con el HTML de un tercero como
dependencia permanente.

El exceso de varianza sobre Poisson es de 0.63, y se reparte entre
equipos agresivos, tension del partido y arbitro. Lo que le toca al
arbitro es una fraccion de una cifra ya pequena. Pagar por ella un cron
que se rompera solo va contra el techo de 15 minutos semanales
(00_PROYECTO.md §9). Si algun dia las tarjetas dan juego de verdad, se
anade entonces con datos sobre la mesa.

Nota (D-24): football-data.org SI trae arbitro en el calendario, y se
guarda en `fixtures` sin usarlo. Viene incompleto (14 de 30 partidos en
la primera prueba), asi que la decision se mantiene hasta tener dos
temporadas acumuladas.

Solo amarillas
--------------
Las rojas van a 0.241 por partido: una cada cuatro. Estimar una tasa por
equipo con eso es estimar ruido. Se tratan como tasa global de liga.

Los dos parametros por equipo
-----------------------------
propension  cuantas amarillas RECIBE ese equipo
provocacion cuantas amarillas PROVOCA en su rival

La segunda es la interesante: un equipo que provoca faltas juega
distinto de uno que las comete. Da contenido aunque el modelo prediga
regular.

Encogimiento hacia la media (D-27)
----------------------------------
Igual que en los otros dos modelos, y con la misma funcion. Un equipo
con pocos partidos recibe estimaciones extremas que se multiplican al
cruzarse con las del rival.

Nota sobre la ventaja de campo
------------------------------
Aqui se invierte respecto a xG y corners: el local suele recibir MENOS
tarjetas. Si el parametro sale por debajo de 1.00, no es un error.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

from src.models.criba import XI
from src.models.dixon_coles import K_ENCOGIMIENTO, encoger, pesos_temporales

MAX_TARJETAS = 15


@dataclass
class ParametrosTarjetas:
    """Resultado de un ajuste. Los parametros ya vienen encogidos."""

    equipos: list[str]
    propension: np.ndarray    # 1.00 = recibe las de un equipo medio
    provocacion: np.ndarray   # 1.00 = provoca las de un equipo medio
    factor_local: float       # por debajo de 1.00 es lo normal
    nivel_liga: float         # amarillas de referencia por equipo y partido
    tasa_rojas: float         # rojas por partido en toda la liga
    n_partidos: int
    partidos_equipo: dict[str, int] = field(default_factory=dict)

    def indice(self, equipo: str) -> int:
        return self.equipos.index(equipo)

    def muestra(self, equipo: str) -> int:
        return self.partidos_equipo.get(equipo, 0)

    def tabla(self) -> pd.DataFrame:
        """Propension y provocacion por equipo, de mas a menos tarjetero."""
        df = pd.DataFrame(
            {
                "equipo": self.equipos,
                "propension": self.propension,
                "provocacion": self.provocacion,
            }
        )
        df["partidos"] = df["equipo"].map(self.partidos_equipo).fillna(0).astype(int)
        return df.sort_values("propension", ascending=False).reset_index(drop=True)


# --- Verosimilitud --------------------------------------------------


def _neg_log_verosimilitud(
    params: np.ndarray,
    idx_local: np.ndarray,
    idx_visitante: np.ndarray,
    tarjetas_local: np.ndarray,
    tarjetas_visitante: np.ndarray,
    pesos: np.ndarray,
    n_equipos: int,
) -> float:
    log_propension = params[:n_equipos]
    log_provocacion = params[n_equipos : 2 * n_equipos]
    log_factor_local = params[-2]
    mu_liga = params[-1]

    # Identificabilidad: igual que en los otros modelos. Centrando,
    # 1.00 significa "exactamente la media de la liga".
    log_propension = log_propension - log_propension.mean()
    log_provocacion = log_provocacion - log_provocacion.mean()

    # Las que recibe el local dependen de su propension y de cuanto
    # provoca el visitante.
    log_mu_local = (
        mu_liga + log_propension[idx_local] + log_provocacion[idx_visitante]
        + log_factor_local
    )
    log_mu_visit = mu_liga + log_propension[idx_visitante] + log_provocacion[idx_local]

    mu_local = np.exp(log_mu_local)
    mu_visit = np.exp(log_mu_visit)

    # log f(y) = y*log(mu) - mu  (se omite log Gamma(y+1), constante
    # respecto a los parametros)
    ll = (
        tarjetas_local * log_mu_local
        - mu_local
        + tarjetas_visitante * log_mu_visit
        - mu_visit
    )

    penalizacion = 1e-4 * float(np.sum(params**2))

    return -float(np.sum(pesos * ll)) + penalizacion


# --- Ajuste ---------------------------------------------------------


def ajustar(
    partidos: pd.DataFrame,
    xi: float = XI,
    referencia=None,
    k: int = K_ENCOGIMIENTO,
) -> ParametrosTarjetas:
    """
    Columnas necesarias: fecha, local, visitante, amarillas_local,
    amarillas_visitante. Usa rojas_local y rojas_visitante si estan,
    solo para la tasa global.

    Los parametros devueltos ya vienen encogidos hacia la media (D-27).
    Con k = 0 se desactiva el encogimiento, util para comparar.
    """
    partidos = partidos.dropna(
        subset=["amarillas_local", "amarillas_visitante"]
    ).copy()

    if partidos.empty:
        raise ValueError("No hay partidos con tarjetas para ajustar.")

    if referencia is None:
        referencia = pd.to_datetime(partidos["fecha"]).max()

    equipos = sorted(set(partidos["local"]) | set(partidos["visitante"]))
    n = len(equipos)
    pos = {e: i for i, e in enumerate(equipos)}

    idx_local = partidos["local"].map(pos).to_numpy()
    idx_visit = partidos["visitante"].map(pos).to_numpy()
    t_local = partidos["amarillas_local"].to_numpy(dtype=float)
    t_visit = partidos["amarillas_visitante"].to_numpy(dtype=float)
    pesos = pesos_temporales(partidos["fecha"], referencia, xi)

    # Partidos por equipo. Es lo que gobierna el encogimiento.
    conteo = (
        pd.concat([partidos["local"], partidos["visitante"]])
        .value_counts()
        .to_dict()
    )
    partidos_equipo = {e: int(conteo.get(e, 0)) for e in equipos}

    media = float(np.mean(np.concatenate([t_local, t_visit])))
    # Factor local por debajo de 1: el local suele recibir menos.
    inicial = np.concatenate([np.zeros(n), np.zeros(n), [np.log(0.9), np.log(media)]])

    resultado = minimize(
        _neg_log_verosimilitud,
        inicial,
        args=(idx_local, idx_visit, t_local, t_visit, pesos, n),
        method="L-BFGS-B",
        options={"maxiter": 5000, "maxfun": 50000},
    )

    if not resultado.success:
        print(f"  aviso: la optimizacion no convergio ({resultado.message})")

    log_propension = resultado.x[:n]
    log_propension = log_propension - log_propension.mean()
    log_provocacion = resultado.x[n : 2 * n]
    log_provocacion = log_provocacion - log_provocacion.mean()

    if {"rojas_local", "rojas_visitante"}.issubset(partidos.columns):
        tasa_rojas = float(
            (partidos["rojas_local"] + partidos["rojas_visitante"]).mean()
        )
    else:
        tasa_rojas = float("nan")

    propension = np.exp(log_propension)
    provocacion = np.exp(log_provocacion)

    # Encogimiento (D-27). Una sola vez, aqui.
    if k > 0:
        muestras = np.array([partidos_equipo[e] for e in equipos], dtype=float)
        propension = encoger(propension, muestras, k)
        provocacion = encoger(provocacion, muestras, k)

    return ParametrosTarjetas(
        equipos=equipos,
        propension=propension,
        provocacion=provocacion,
        factor_local=float(np.exp(resultado.x[-2])),
        nivel_liga=float(np.exp(resultado.x[-1])),
        tasa_rojas=tasa_rojas,
        n_partidos=len(partidos),
        partidos_equipo=partidos_equipo,
    )


# --- Prediccion -----------------------------------------------------


def tarjetas_esperadas(
    p: ParametrosTarjetas, local: str, visitante: str
) -> tuple[float, float]:
    """Amarillas esperadas para cada equipo."""
    i_local, i_visit = p.indice(local), p.indice(visitante)

    mu_local = (
        p.nivel_liga * p.propension[i_local] * p.provocacion[i_visit] * p.factor_local
    )
    mu_visit = p.nivel_liga * p.propension[i_visit] * p.provocacion[i_local]

    return float(mu_local), float(mu_visit)


def distribucion(mu: float) -> np.ndarray:
    """Probabilidad de 0, 1, 2... MAX_TARJETAS amarillas para un equipo."""
    valores = np.arange(MAX_TARJETAS + 1)
    probs = poisson.pmf(valores, mu)
    return probs / probs.sum()


def distribucion_total(
    p: ParametrosTarjetas, local: str, visitante: str
) -> np.ndarray:
    """
    Total del partido. A diferencia de los corners, aqui sumar las dos
    distribuciones es defendible: la suma de dos Poisson es otra
    Poisson. El backtest comprueba si la independencia se sostiene.
    """
    mu_local, mu_visit = tarjetas_esperadas(p, local, visitante)
    return np.convolve(distribucion(mu_local), distribucion(mu_visit))


def prob_mas_de(dist: np.ndarray, n: int) -> float:
    """
    Probabilidad de superar n amarillas, con n ENTERO.

    Nada de lineas con .5: son convencion de casas de apuestas y este
    proyecto no publica eso (00_PROYECTO.md §8).
    """
    if int(n) != n:
        raise ValueError(
            "El umbral debe ser un entero. Las lineas con .5 son de "
            "casas de apuestas y no se usan aqui (00_PROYECTO.md §8)."
        )
    return float(dist[int(n) + 1 :].sum())