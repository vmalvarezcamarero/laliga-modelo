"""
Generacion de borradores: la Skill de voz.

Recibe el JSON de la jornada o el de auditoria y devuelve borradores
rankeados para un formato. El humano elige uno en Telegram.

TRES PRINCIPIOS:

1. La voz vive en docs/02_VOZ_Y_FORMATOS.md, no aqui. Se lee en cada
   llamada. Ajustar como habla EGO es editar ese Markdown, sin tocar
   codigo ni desplegar nada.

2. Cada formato ve SOLO su parcela del JSON. Un F1 recibe la criba y
   nada mas; un F5, el veredicto y nada de la semana que viene; un F4,
   el partido de la encuesta SIN las probabilidades de EGO. La garantia
   no es la instruccion, es el recorte: lo que no esta delante no se
   puede publicar.

3. Los numeros del borrador se verifican contra el JSON (P-13). Se
   marcan, NO se descartan: "7 de 20" es legitimo aunque el 20 no sea
   un campo.

COSTE. La guia son ~5.300 tokens de entrada por llamada, y eso es fijo.
El razonamiento extendido se desactiva con `thinking`: sin eso se comia
los 4.000 tokens antes de escribir una palabra. Medido: ~2 centimos por
llamada.
"""

import json
import os
import re
import sys
from pathlib import Path

from anthropic import Anthropic

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

BASE = Path(__file__).resolve().parents[2]
GUIA = BASE / "docs" / "02_VOZ_Y_FORMATOS.md"
PREDICCIONES = BASE / "outputs" / "predictions"

MODELO = "claude-sonnet-5"

# Cinco y no ocho: en Telegram los lees en menos de cinco minutos.
N_BORRADORES = 5

# Freno, no presupuesto: solo se paga lo generado.
MAX_TOKENS = 4000

# Por debajo de este margen de RPS, EGO gana pero no para presumir.
MARGEN_RASPADO = 0.01


# --- De que fichero come cada formato --------------------------------

FUENTE = {
    "F1": "prediccion",
    "F2": "prediccion",
    "F3": "prediccion",
    "F4": "prediccion",
    "F5": "auditoria",
}


# --- Recorte por formato --------------------------------------------


def _partido(doc: dict, id_partido: str) -> dict | None:
    return next((p for p in doc["partidos"] if p["id"] == id_partido), None)


def _recortar_f1(doc: dict) -> dict:
    """F1 - LA CRIBA. Solo la criba y su gancho."""
    return {
        "jornada": doc["jornada_etiqueta"],
        "criba": doc["criba"],
        "gancho": doc["ganchos"].get("F1_descarte"),
    }


def _recortar_f2(doc: dict) -> dict:
    """
    F2 - EL DICTAMEN. Los partidos y el gancho de la prediccion mas
    atrevida. NO lleva la criba completa: eso es F1.
    """
    gancho = doc["ganchos"].get("F2_atrevida")
    elegido = _partido(doc, gancho["partido"]) if gancho else None

    return {
        "jornada": doc["jornada_etiqueta"],
        "n_partidos": len(doc["partidos"]),
        "gancho": gancho,
        "partido_del_gancho": elegido,
        "todos_los_partidos": [
            {
                "local": p["local"],
                "visitante": p["visitante"],
                "prob": p["prob"],
                "choque_directo": p["criba"]["choque_directo"],
            }
            for p in doc["partidos"]
        ],
    }


def _recortar_f3(doc: dict) -> dict:
    """
    F3 - EL PUNTO CIEGO. UN partido y nada mas.

    Es el formato mas pequeno del proyecto, y eso es deliberado: EGO
    admitiendo que no sabe necesita tres frases, no diez.
    """
    gancho = doc["ganchos"].get("F3_punto_ciego")
    elegido = _partido(doc, gancho["partido"]) if gancho else None

    return {
        "jornada": doc["jornada_etiqueta"],
        "entropia": gancho["cifra"] if gancho else None,
        "partido": (
            {
                "local": elegido["local"],
                "visitante": elegido["visitante"],
                "fecha": elegido["fecha"],
                "prob": elegido["prob"],
            }
            if elegido else None
        ),
    }


def _recortar_f4(doc: dict) -> dict:
    """
    F4 - EL DESAFIO. El partido de la encuesta, SIN las probabilidades
    de EGO.

    Esto es lo importante del recorte: el post pide al publico que vote
    antes de saber que dice EGO. Si el redactor tuviera las
    probabilidades delante las filtraria en el texto, por mucho que la
    instruccion lo prohiba. La unica garantia es no darselas.
    """
    gancho = doc["ganchos"].get("F4_encuesta")
    elegido = _partido(doc, gancho["partido"]) if gancho else None

    return {
        "jornada": doc["jornada_etiqueta"],
        "partido": (
            {
                "local": elegido["local"],
                "visitante": elegido["visitante"],
                "fecha": elegido["fecha"],
            }
            if elegido else None
        ),
    }


def _recortar_f5(doc: dict) -> dict:
    """
    F5 - EGO PASA LA CRIBA. El veredicto y las cifras que lo sostienen.

    NO lleva la criba de equipos ni predicciones de la semana que viene:
    este post va sobre EGO, no sobre los partidos.
    """
    return {
        "jornada": doc["jornada_etiqueta"],
        "partidos_evaluados": doc["evaluados"],
        "veredicto": doc["veredicto"],
        "rps": doc["rps"],
        "mejor": doc["mejor"],
        "peor": doc["peor"],
        "desafio": doc.get("desafio"),
    }


RECORTES = {
    "F1": _recortar_f1,
    "F2": _recortar_f2,
    "F3": _recortar_f3,
    "F4": _recortar_f4,
    "F5": _recortar_f5,
}


# --- Instrucciones por formato --------------------------------------

_F5_COMUN = """FORMATO OBJETIVO: F5 - EGO PASA LA CRIBA (lunes).

Es la publicacion mas importante de la semana y se publica gane o
pierda. Acompana a un grafico de RPS acumulado.

El RPS mide el error: MAS BAJO ES MEJOR. Traducelo siempre, nunca lo
sueltes sin explicar. "Modelo tonto" o "el listón" valen para las
frecuencias base.

Longitud: entre 180 y 260 caracteres.

El veredicto viene dado en `veredicto.pasa`. NO lo discutas ni lo
matices: EGO recibe un booleano y no opina sobre si mismo.

Usa `mejor` y `peor` si aportan, con el marcador y la probabilidad que
dio. Son las cifras del JSON: copialas, no las recalcules."""

INSTRUCCIONES = {
    "F1": """FORMATO OBJETIVO: F1 - LA CRIBA (jueves, formato insignia).

El post acompana a una imagen con los 20 equipos y la linea de corte.
La imagen ya ensena quien pasa y quien no: el texto NO los enumera.

Estructura: 2 o 3 frases sobre el descarte mas relevante, que viene
dado en el campo `gancho`. No elijas tu el protagonista.

Longitud: entre 180 y 260 caracteres. Es un tuit, no un parrafo.

El campo `gancho.apretados` trae los equipos que rodean la linea de
corte con las distancias ENTRE ELLOS ya calculadas, en
`distancia_al_anterior`. Si quieres contrastar a quien pasa raspado con
quien no llega, COPIA ese numero. No lo calcules restando dos fuerzas.

Si `criba.resumen.primera_criba` es true, NO digas que nadie "entra"
ni "cae": no hay semana anterior con la que comparar. Presenta la criba
como un estado, no como un cambio.

Si es false, el movimiento es la noticia: quien entra y quien cae.

Cada borrador ataca el post desde un angulo distinto: el descarte en
seco, cuantos pasan y que significa el umbral, el contraste entre dos
equipos separados por poco, lo que la criba ignora, o la voz de la
cuenta traduciendo a EGO. No la misma frase reordenada.""",

    "F2": """FORMATO OBJETIVO: F2 - EL DICTAMEN (jueves).

El post acompana a una imagen con la tabla de los partidos de la
jornada y sus probabilidades 1X2. La imagen ya ensena todos los
partidos: el texto NO los enumera ni hace un repaso.

Estructura: 2 o 3 frases sobre UNA sola prediccion, la que viene en
`gancho`. Sus datos completos estan en `partido_del_gancho`.

Longitud: entre 180 y 260 caracteres.

QUE HACE ATREVIDA ESA PREDICCION. El gancho senala al equipo que NO
pasa la criba y al que EGO le da opciones contra uno que SI la pasa. Ese
es el conflicto: la criba lo descarta y la prediccion no lo entierra.
Si `gancho.motivo` es "descartado_con_opciones", ese es el angulo.

Si el motivo es "lejos_de_la_media_de_la_liga", no hubo ningun choque
directo esta jornada y el angulo es otro: EGO se aleja de lo que diria
cualquiera.

Las probabilidades son porcentajes enteros y suman 100 (o 99 o 101 por
redondeo, y eso no se menciona). `todos_los_partidos` esta ahi solo
para que sepas el contexto de la jornada: NO lo enumeres.

Cada borrador ataca desde un angulo distinto: la cifra en seco, el
contraste entre lo que dice la criba y lo que dice la prediccion, lo
que el resultado significaria, o la voz de la cuenta senalando que EGO
se esta mojando.""",

    "F3": """FORMATO OBJETIVO: F3 - EL PUNTO CIEGO (jueves o viernes).

El partido de la jornada donde EGO tiene mas incertidumbre. Es el
formato mas pequeno del proyecto y eso es deliberado.

Estructura: 2 o 3 frases. Va sin imagen o con una muy simple, asi que
el texto lleva las tres probabilidades.

Longitud: entre 140 y 220 caracteres. MAS CORTO que los demas.

EL ANGULO ES LA HUMILDAD, no el partido. Lo interesante no es que
juegue el Getafe: es que EGO, que dictamina sin pestanear el resto de
la semana, aqui no sabe. Es lo mas cerca que va a estar de encogerse de
hombros.

La entropia es una medida de incertidumbre: cuanto mas alta, menos sabe
el modelo. El maximo es 1.585, tres opciones exactamente iguales. NO
uses la palabra "entropia" en el post sin traducirla, y en general es
mejor no usarla: di que las tres probabilidades estan casi igualadas.

EGO no se disculpa por no saber. Lo constata. La voz de la cuenta puede
disfrutar del momento.

Cada borrador ataca desde un angulo distinto: las tres cifras en seco,
lo que significa que esten tan juntas, el contraste con la seguridad
del resto de la semana, o la segunda voz senalando que EGO ha admitido
algo por una vez.""",

    "F4": """FORMATO OBJETIVO: F4 - EL DESAFIO (viernes).

Una encuesta nativa de X sobre UN partido. El publico vota y el lunes
se compara su criterio con el de EGO.

Estructura: 1 o 2 frases que presenten el partido y pidan el voto. La
encuesta va aparte, con las tres opciones (local / empate / visitante).

Longitud: entre 100 y 180 caracteres. Es el formato mas corto.

NO TIENES LAS PROBABILIDADES DE EGO, y es a proposito: el publico vota
antes de saber que dice el modelo. No especules sobre quien es favorito,
no insinues un resultado, no digas que EGO "lo tiene claro" ni que
"duda". No lo sabes.

El tono es de desafio, no de consulta. No preguntas una opinion:
propones un duelo. El lunes uno de los dos queda retratado.

Cada borrador ataca desde un angulo distinto: el reto directo, la
apuesta de que el publico acertara mas, la advertencia de que el lunes
hay comparacion, o la voz de la cuenta poniendose del lado del publico
contra EGO.""",

    "F5_pasa": _F5_COMUN + """

ESTA SEMANA EGO HA PASADO SU PROPIA CRIBA, y con margen.

Registro: EGO no celebra, constata. La segunda voz puede reconocerlo a
reganadientes: es la sonrisa que da rabia. Nada de triunfalismo ni de
superlativos; el numero habla solo.

Evita "acertamos" y "hemos ganado". EGO no gana: cumple.""",

    "F5_raspado": _F5_COMUN + """

EGO PASA, PERO POR MUY POCO. El margen esta en `rps.margen` y es
minusculo.

Registro: una victoria tecnica que no da para presumir. La segunda voz
lo senala sin piedad. EGO no se defiende, porque el margen es un dato
como cualquier otro y no le avergüenza.

No lo cuentes como un exito. Cuentalo como lo que es: ha pasado, y por
los pelos. Esa honestidad es el producto.""",

    "F5_no_pasa": _F5_COMUN + """

ESTA SEMANA EGO NO HA PASADO SU PROPIA CRIBA.

Registro: se dice sin adornos y sin excusas. Esta es la unica concesion
del personaje y es la que lo salva. Nada de "casi", nada de "aun asi",
nada de reencuadrar el fallo como aprendizaje.

La segunda voz disfruta abiertamente. Puede reirse de EGO, decir que
lleva callado desde ayer, o que se lo ha ganado.

Prohibido: justificar el error con la varianza, con la mala suerte o
con que "el 29% tambien existe". Eso lo dice EGO otras semanas, no la
semana que falla.""",
}


def _clave_instruccion(formato: str, datos: dict) -> str:
    """F5 tiene tres registros distintos segun como haya ido la semana."""
    if formato != "F5":
        return formato

    if not datos["veredicto"]["pasa"]:
        return "F5_no_pasa"
    if datos["rps"]["margen"] < MARGEN_RASPADO:
        return "F5_raspado"
    return "F5_pasa"


# --- Prompt ---------------------------------------------------------


def _sistema() -> str:
    guia = GUIA.read_text(encoding="utf-8")
    return f"""Escribes los borradores de una cuenta de X llamada LA CRIBA,
que publica predicciones estadisticas de LaLiga generadas por un modelo
propio llamado EGO.

A continuacion va la guia editorial completa del proyecto. Es la fuente
de verdad sobre como habla EGO y como habla la cuenta. Siguela al pie de
la letra, incluidas las prohibiciones absolutas.

<guia_editorial>
{guia}
</guia_editorial>

REGLAS DE SALIDA, ademas de todo lo anterior:

- Cada borrador usa SOLO cifras que aparezcan literalmente en los datos
  que recibes. No calcules, no restes, no redondees, no deduzcas numeros
  nuevos. Si un numero no esta en los datos, no existe.
- Nada de cuotas, casas de apuestas, "value" ni recomendaciones de
  jugada, en ninguna forma ni como broma.
- Sin emojis. Sin hashtags. Sin signos de exclamacion.
- Los borradores tienen que ser distintos entre si: distinta apertura de
  frase y distinto angulo, no la misma idea reformulada.

Responde UNICAMENTE con un objeto JSON valido, sin texto antes ni
despues y sin bloques de codigo, con esta forma exacta:

{{"borradores": [{{"texto": "...", "por_que": "..."}}]}}

Ordenados del mejor al peor. `por_que` es una linea de menos de quince
palabras justificando la posicion."""


def _usuario(clave: str, datos: dict) -> str:
    return f"""{INSTRUCCIONES[clave]}

DATOS:

{json.dumps(datos, ensure_ascii=False, indent=2)}

Genera {N_BORRADORES} borradores."""


# --- Verificacion de numeros (P-13) ---------------------------------

# Captura enteros y decimales, con punto o coma. El signo se ignora a
# proposito: el JSON guarda -0.11 y el borrador puede escribir "0.11"
# de forma perfectamente correcta ("se queda a 0.11 del corte").
_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")


def _normalizar(texto: str) -> str:
    """'0,16' y '0.160' son el mismo numero. '1.20' y '1.2' tambien."""
    valor = float(texto.replace(",", "."))
    return f"{valor:g}"


def _numeros_del_json(datos) -> set[str]:
    """Todos los numeros que aparecen en el recorte, normalizados."""
    encontrados = set()

    def recorrer(nodo):
        if isinstance(nodo, dict):
            for v in nodo.values():
                recorrer(v)
        elif isinstance(nodo, list):
            for v in nodo:
                recorrer(v)
        elif isinstance(nodo, bool):
            pass  # True/False no son cifras publicables
        elif isinstance(nodo, (int, float)):
            encontrados.add(f"{abs(nodo):g}")
        elif isinstance(nodo, str):
            for m in _NUMERO.findall(nodo):
                encontrados.add(_normalizar(m))

    recorrer(datos)
    return encontrados


def numeros_sospechosos(texto: str, datos: dict) -> list[str]:
    """
    Cifras del borrador que no aparecen en el recorte del JSON.

    NO son necesariamente errores: "7 de 20 equipos" marca el 20, que es
    legitimo. Por eso se senalan y las lee el humano.
    """
    permitidos = _numeros_del_json(datos)
    fuera = []
    for m in _NUMERO.findall(texto):
        n = _normalizar(m)
        if n not in permitidos and n not in fuera:
            fuera.append(n)
    return fuera


# --- Llamada --------------------------------------------------------


def _ultimo_json(tipo: str) -> dict:
    """
    El JSON mas reciente de ese tipo. Los de simulacion (_SIM) se
    descartan: no son predicciones reales y no se publican.
    """
    patron = f"*_{tipo}.json"
    ficheros = sorted(
        p for p in PREDICCIONES.glob(patron) if "_SIM" not in p.name
    )
    if not ficheros:
        modulo = (
            "src.content.jornada_json" if tipo == "prediccion"
            else "src.evaluate.auditoria"
        )
        raise FileNotFoundError(
            f"No hay ningun {patron} en {PREDICCIONES}. "
            f"Ejecuta antes: python -m {modulo}"
        )
    return json.loads(ficheros[-1].read_text(encoding="utf-8"))


def generar(formato: str = "F1", doc: dict | None = None) -> list[dict]:
    """
    Devuelve los borradores rankeados. Cada uno con:
      texto, por_que, sospechosos[]
    """
    if formato not in RECORTES:
        raise ValueError(
            f"Formato {formato} sin recorte definido. "
            f"Disponibles: {sorted(RECORTES)}"
        )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "Falta la variable de entorno ANTHROPIC_API_KEY.\n"
            'En PowerShell:  $env:ANTHROPIC_API_KEY = "tu_clave"'
        )

    if doc is None:
        doc = _ultimo_json(FUENTE[formato])

    datos = RECORTES[formato](doc)

    # Un formato sin su gancho no se puede escribir. Pasa si la jornada
    # no tenia candidatos: mejor parar que inventar.
    if formato in ("F2", "F3", "F4") and not datos.get("partido"):
        if formato == "F2" and datos.get("partido_del_gancho"):
            pass
        elif formato != "F2":
            raise SystemExit(
                f"El JSON no trae gancho para {formato}. Nada que escribir."
            )

    clave = _clave_instruccion(formato, datos)

    if clave != formato:
        print(f"   registro: {clave}")

    cliente = Anthropic()
    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=MAX_TOKENS,
        # Sin razonamiento extendido. Escribir cinco tuits con la guia
        # delante y el gancho ya elegido por el pipeline no lo necesita.
        thinking={"type": "disabled"},
        system=_sistema(),
        messages=[{"role": "user", "content": _usuario(clave, datos)}],
    )

    uso = respuesta.usage
    print(f"   tokens: {uso.input_tokens} entrada -> {uso.output_tokens} salida")

    if respuesta.stop_reason == "max_tokens":
        raise RuntimeError(
            f"La respuesta se corto por limite de tokens ({MAX_TOKENS}). "
            f"Comprueba que `thinking` sigue desactivado."
        )

    bruto = "".join(
        b.text for b in respuesta.content if getattr(b, "type", None) == "text"
    ).strip()

    if not bruto:
        tipos = [b.type for b in respuesta.content]
        raise RuntimeError(f"La respuesta no traia texto. Bloques: {tipos}")

    # Red de seguridad: si el modelo envuelve el JSON en backticks pese
    # a la instruccion, se limpia en vez de reventar.
    if bruto.startswith("```"):
        bruto = bruto.split("```")[1]
        if bruto.startswith("json"):
            bruto = bruto[4:]
        bruto = bruto.strip()

    try:
        borradores = json.loads(bruto)["borradores"]
    except (json.JSONDecodeError, KeyError) as e:
        print("La respuesta no venia en el formato esperado:\n")
        print(bruto)
        raise RuntimeError("Respuesta mal formada.") from e

    for b in borradores:
        b["sospechosos"] = numeros_sospechosos(b["texto"], datos)

    return borradores


if __name__ == "__main__":
    formato = sys.argv[1] if len(sys.argv) > 1 else "F1"

    print(f"Generando {N_BORRADORES} borradores de {formato}...\n")
    borradores = generar(formato)

    print()
    con_avisos = 0
    for i, b in enumerate(borradores, 1):
        texto = b["texto"]
        print(f"--- {i} ---  ({len(texto)} caracteres)")
        print(texto)
        print(f"    [{b['por_que']}]")
        if b["sospechosos"]:
            con_avisos += 1
            print(f"    !! cifras que no estan en el JSON: "
                  f"{', '.join(b['sospechosos'])}")
        print()

    if con_avisos:
        print(f"{con_avisos} de {len(borradores)} borradores llevan cifras "
              f"sin respaldo. Comprueba antes de publicar.")
    else:
        print("Todas las cifras estan respaldadas por el JSON.")