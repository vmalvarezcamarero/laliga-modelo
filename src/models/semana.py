"""La semana como unidad de publicación.

LaLiga no juega las jornadas en orden: un partido de la J6 puede jugarse
antes que toda la J4. Predecir "los 10 partidos de la jornada N" produciría
dictámenes con partidos ya jugados y auditorías descuadradas.

La unidad es la ventana miércoles 09:00 -> miércoles 09:00, que es la que
marca el cron de predicciones. La jornada queda como etiqueta editorial.

AUTORIDAD SOBRE SI UN PARTIDO SE JUGÓ: la tabla `matches`, nunca `fixtures`.
El campo `status` de football-data.org ya ha devuelto basura (una fecha en
vez de un estado). `matches` no puede equivocarse: un partido solo entra
ahí cuando tiene resultado.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parents[2]
DB = BASE / "data" / "laliga.db"

MADRID = ZoneInfo("Europe/Madrid")

# El cron de predicciones corre los miércoles a las 09:00 (ARQUITECTURA §8).
DIA_CORTE = 2      # 0=lunes ... 2=miércoles
HORA_CORTE = 9

# Tolerancia del cruce fixtures <-> matches, en días.
# `matches.fecha` no trae hora (viene a medianoche desde Football-Data.co.uk)
# y `fixtures.fecha_hora` sí. Misma tolerancia que funcionó con Understat.
TOLERANCIA_DIAS = 1

# Margen tras el inicio a partir del cual un partido se da por terminado,
# sepamos o no el resultado. Un partido que empezó hace 3 horas ha acabado:
# eso es cierto sin consultar ninguna fuente. Protege contra el peor fallo
# posible, que es dictaminar sobre un partido ya jugado.
MARGEN_FIN_HORAS = 3

ESTADOS_FUERA = {"POSTPONED", "CANCELLED"}


@dataclass
class Partido:
    fuente_id: int
    temporada: str
    jornada: int
    fecha_hora: datetime
    local: str
    visitante: str
    estado: str


@dataclass
class Semana:
    desde: datetime
    hasta: datetime
    partidos: list[Partido]
    jornadas_presentes: dict[int, int]
    advertencias: list[str] = field(default_factory=list)

    @property
    def jornada_etiqueta(self) -> int | None:
        """La jornada mayoritaria. Es solo una etiqueta editorial."""
        if not self.jornadas_presentes:
            return None
        return max(self.jornadas_presentes, key=self.jornadas_presentes.get)

    @property
    def mezclada(self) -> bool:
        return len(self.jornadas_presentes) > 1


def _normalizar(referencia: datetime | None) -> datetime:
    if referencia is None:
        referencia = datetime.now(MADRID)
    if referencia.tzinfo is None:
        referencia = referencia.replace(tzinfo=MADRID)
    return referencia


def ventana(referencia: datetime | None = None) -> tuple[datetime, datetime]:
    """Miércoles 09:00 anterior o igual a la referencia, y el siguiente.

    Definición única de 'semana'. Predicción y auditoría la comparten para
    que no puedan discrepar.
    """
    referencia = _normalizar(referencia)

    corte = referencia.replace(hour=HORA_CORTE, minute=0, second=0, microsecond=0)
    atras = (corte.weekday() - DIA_CORTE) % 7
    corte -= timedelta(days=atras)
    if corte > referencia:
        corte -= timedelta(days=7)

    return corte, corte + timedelta(days=7)


def _leer_fixtures(desde: datetime, hasta: datetime) -> list[Partido]:
    con = sqlite3.connect(DB)
    try:
        filas = con.execute(
            "SELECT fuente_id, temporada, jornada, fecha_hora, local, "
            "visitante, estado FROM fixtures ORDER BY fecha_hora"
        ).fetchall()
    finally:
        con.close()

    partidos = []
    for fid, temp, jor, fh, loc, vis, est in filas:
        cuando = datetime.fromisoformat(fh)
        if desde <= cuando < hasta and est not in ESTADOS_FUERA:
            partidos.append(Partido(fid, temp, jor, cuando, loc, vis, est))
    return partidos


def _resultados_conocidos() -> set[tuple[str, str, str]]:
    """(dia, local, visitante) de todo lo que tiene resultado en `matches`."""
    con = sqlite3.connect(DB)
    try:
        filas = con.execute(
            "SELECT fecha, local, visitante FROM matches "
            "WHERE goles_local IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return {(str(f)[:10], l, v) for f, l, v in filas}


def esta_jugado(p: Partido, conocidos: set[tuple[str, str, str]]) -> bool:
    """¿Tiene resultado en `matches`? Con tolerancia de +-1 día.

    No se consulta el estado de `fixtures`: esa fuente ya ha mentido.
    """
    for delta in range(-TOLERANCIA_DIAS, TOLERANCIA_DIAS + 1):
        dia = (p.fecha_hora + timedelta(days=delta)).strftime("%Y-%m-%d")
        if (dia, p.local, p.visitante) in conocidos:
            return True
    return False


def ya_empezo(p: Partido, referencia: datetime) -> bool:
    """¿Ha pasado ya la hora de inicio más el margen de fin?"""
    return referencia >= p.fecha_hora + timedelta(hours=MARGEN_FIN_HORAS)


def _contar_jornadas(partidos: list[Partido]) -> dict[int, int]:
    cuenta: dict[int, int] = {}
    for p in partidos:
        cuenta[p.jornada] = cuenta.get(p.jornada, 0) + 1
    return cuenta


def para_predecir(
    referencia: datetime | None = None, simular: bool = False
) -> Semana:
    """Partidos de la ventana que viene, aún sin jugar.

    Dos filtros independientes, y basta con que uno diga que no:
      - tiene resultado en `matches`  -> fuera
      - su hora de inicio ya pasó     -> fuera, aunque no sepamos el resultado

    El segundo existe porque `matches` puede ir retrasada respecto a la
    realidad. No saber el resultado no es lo mismo que no haberse jugado,
    y dictaminar sobre un partido terminado es el peor fallo posible (D-26).

    `simular=True` salta los dos filtros y devuelve la ventana como
    estaba antes de jugarse. Sirve para reconstruir semanas pasadas y
    probar el bucle completo de publicación y auditoría. NUNCA se usa en
    producción: un JSON generado así diría que predice partidos que ya
    han terminado.
    """
    referencia = _normalizar(referencia)
    desde, hasta = ventana(referencia)

    todos = _leer_fixtures(desde, hasta)

    if simular:
        jornadas = _contar_jornadas(todos)
        avisos = ["SIMULACION: la ventana se ha reconstruido ignorando los "
                  "resultados ya conocidos. No publicar."]
        if len(jornadas) > 1:
            detalle = ", ".join(f"J{j}: {n}" for j, n in sorted(jornadas.items()))
            avisos.append(f"Semana con partidos de varias jornadas ({detalle}).")
        return Semana(desde, hasta, todos, jornadas, avisos)

    conocidos = _resultados_conocidos()

    pendientes, con_resultado, limbo = [], [], []
    for p in todos:
        if esta_jugado(p, conocidos):
            con_resultado.append(p)
        elif ya_empezo(p, referencia):
            limbo.append(p)
        else:
            pendientes.append(p)

    avisos = []
    if con_resultado:
        avisos.append(
            f"{len(con_resultado)} partido(s) ya jugados en esta ventana: "
            f"fuera del dictamen (D-26)."
        )
    if limbo:
        detalle = ", ".join(f"{p.local}-{p.visitante}" for p in limbo)
        avisos.append(
            f"{len(limbo)} partido(s) ya disputados pero sin resultado en "
            f"`matches` ({detalle}). Ni se predicen ni se auditan: falta ingesta."
        )

    jornadas = _contar_jornadas(pendientes)
    if len(jornadas) > 1:
        detalle = ", ".join(f"J{j}: {n}" for j, n in sorted(jornadas.items()))
        avisos.append(f"Semana con partidos de varias jornadas ({detalle}).")

    return Semana(desde, hasta, pendientes, jornadas, avisos)


def para_auditar(referencia: datetime | None = None) -> Semana:
    """Partidos de la ventana que se cierra y que ya tienen resultado.

    Se llama el lunes, dentro de la ventana abierta el miércoles anterior.
    """
    referencia = _normalizar(referencia)
    desde, hasta = ventana(referencia)
    conocidos = _resultados_conocidos()

    todos = _leer_fixtures(desde, hasta)
    jugados = [p for p in todos if esta_jugado(p, conocidos)]

    avisos = []
    faltan = len(todos) - len(jugados)
    if faltan:
        avisos.append(
            f"{faltan} partido(s) de la ventana sin resultado en `matches`. "
            f"Puede ser ingesta pendiente o aplazamiento."
        )

    return Semana(desde, hasta, jugados, _contar_jornadas(jugados), avisos)


if __name__ == "__main__":
    from src.models.equipos import a_publicable

    s = para_predecir()
    print(f"VENTANA  {s.desde:%d/%m %H:%M}  ->  {s.hasta:%d/%m %H:%M}")
    print(f"Etiqueta: jornada {s.jornada_etiqueta}"
          f"{'  (MEZCLADA)' if s.mezclada else ''}")
    print(f"Partidos a predecir: {len(s.partidos)}\n")
    for p in s.partidos:
        print(f"  J{p.jornada}  {p.fecha_hora:%a %d/%m %H:%M}  "
              f"{a_publicable(p.local)} - {a_publicable(p.visitante)}")
    for a in s.advertencias:
        print(f"\n  AVISO: {a}")