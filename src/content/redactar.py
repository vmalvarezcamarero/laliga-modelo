"""
Generacion de borradores: la Skill de voz.

Recibe el JSON de la jornada y devuelve borradores rankeados para un
formato concreto. El humano elige uno en Telegram (00_PROYECTO.md §12).

TRES PRINCIPIOS:

1. La voz vive en docs/02_VOZ_Y_FORMATOS.md, no aqui. Se lee en cada
   llamada. Ajustar como habla EGO es editar ese Markdown, sin tocar
   codigo ni desplegar nada. Es lo que hace sostenible el techo de 15
   minutos semanales.

2. Cada formato ve SOLO su parcela del JSON. Un F1 recibe la criba y
   nada mas. Si le das el documento entero, mete probabilidades de
   partidos en un post que va sobre la criba, y cualquier campo nuevo
   llegaria al redactor sin que nadie lo decidiera. La garantia no es
   la instruccion, es el recorte.

3. Los numeros del borrador se verifican contra el JSON (P-13). La
   instruccion de no calcular no se cumple sola: un borrador escribio
   "0.16 puntos" restando dos cifras del documento. Se marcan, NO se
   descartan: "7 de 20" es legitimo aunque el 20 no sea un campo, y un
   filtro automatico tiraria buenos borradores sin que nadie se entere.

COSTE. La guia son ~5.300 tokens de entrada por llamada, y eso es fijo.
Lo que se disparo en las pruebas fue la SALIDA: el razonamiento
extendido se comia los 4.000 tokens antes de escribir una palabra. Se
desactiva con `thinking`. Medido: ~2 centimos por llamada.
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

# Cinco y no ocho: en Telegram los lees en menos de cinco minutos, y
# ocho borradores casi identicos son peor que cinco distintos.
N_BORRADORES = 5

# Freno, no presupuesto: solo se paga lo generado. Se deja bajo para que
# un descontrol se corte con un error en vez de con una factura.
MAX_TOKENS = 4000


# --- Recorte por formato --------------------------------------------


def _recortar_f1(doc: dict) -> dict:
    """F1 - LA CRIBA. Solo la criba y su gancho."""
    return {
        "jornada": doc["jornada_etiqueta"],
        "criba": doc["criba"],
        "gancho": doc["ganchos"].get("F1_descarte"),
    }


RECORTES = {
    "F1": _recortar_f1,
}


# --- Instrucciones por formato --------------------------------------

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
}


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


def _usuario(formato: str, datos: dict) -> str:
    return f"""{INSTRUCCIONES[formato]}

DATOS DE LA JORNADA:

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
    legitimo. Por eso se senalan y las lee el humano, en vez de
    descartarse solas.
    """
    permitidos = _numeros_del_json(datos)
    fuera = []
    for m in _NUMERO.findall(texto):
        n = _normalizar(m)
        if n not in permitidos and n not in fuera:
            fuera.append(n)
    return fuera


# --- Llamada --------------------------------------------------------


def _ultimo_json(patron: str = "*_prediccion.json") -> dict:
    ficheros = sorted(PREDICCIONES.glob(patron))
    if not ficheros:
        raise FileNotFoundError(
            f"No hay ningun {patron} en {PREDICCIONES}. "
            f"Ejecuta antes: python -m src.content.jornada_json"
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
        doc = _ultimo_json()

    datos = RECORTES[formato](doc)

    cliente = Anthropic()
    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=MAX_TOKENS,
        # Sin razonamiento extendido. Escribir cinco tuits con la guia
        # delante y el gancho ya elegido por el pipeline no lo necesita,
        # y ese bloque se comia los 4000 tokens antes de escribir nada.
        thinking={"type": "disabled"},
        system=_sistema(),
        messages=[{"role": "user", "content": _usuario(formato, datos)}],
    )

    uso = respuesta.usage
    print(f"   tokens: {uso.input_tokens} entrada -> {uso.output_tokens} salida")

    if respuesta.stop_reason == "max_tokens":
        raise RuntimeError(
            f"La respuesta se corto por limite de tokens ({MAX_TOKENS}). "
            f"Antes de subirlo, comprueba que `thinking` sigue desactivado: "
            f"el razonamiento extendido es lo que disparaba la salida."
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
        