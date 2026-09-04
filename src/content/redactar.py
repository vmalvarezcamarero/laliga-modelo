"""
Generacion de borradores: la Skill de voz.

Recibe el JSON de la jornada y devuelve borradores rankeados para un
formato concreto. El humano elige uno en Telegram (00_PROYECTO.md §12).

DOS PRINCIPIOS:

1. La voz vive en docs/02_VOZ_Y_FORMATOS.md, no aqui. Se lee en cada
   llamada. Ajustar como habla EGO es editar ese Markdown, sin tocar
   codigo ni desplegar nada. Es lo que hace sostenible el techo de 15
   minutos semanales.

2. Cada formato ve SOLO su parcela del JSON. Un F1 recibe la criba y
   nada mas. Si le das el documento entero, mete probabilidades de
   partidos en un post que va sobre la criba, y cualquier campo nuevo
   llegaria al redactor sin que nadie lo decidiera. La garantia no es
   la instruccion, es el recorte.

COSTE. La guia de voz son ~5.300 tokens de entrada por llamada, y eso
es fijo. Lo que se disparo en las pruebas fue la SALIDA: el modelo trae
razonamiento extendido por defecto y ese bloque se comia los 4000
tokens antes de escribir una palabra. Se desactiva con thinking. Para
escribir cinco tuits con la guia delante y el gancho ya elegido por el
pipeline no aporta nada.

MAX_TOKENS no es un presupuesto que se gaste: es un freno. Solo se paga
lo generado. Se deja bajo a proposito para que un descontrol se corte
con un error claro en vez de con una factura silenciosa.
"""

import json
import os
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

# Freno, no presupuesto. Ver la nota de coste arriba.
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

- Cada borrador usa SOLO cifras que aparezcan en los datos que recibes.
  No calcules, no redondees, no deduzcas numeros nuevos. Si un numero no
  esta en los datos, no existe.
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

    # La respuesta puede traer bloques de razonamiento antes del texto.
    # Solo nos quedamos con los de tipo `text`.
    bruto = "".join(
        b.text for b in respuesta.content if getattr(b, "type", None) == "text"
    ).strip()

    if not bruto:
        tipos = [b.type for b in respuesta.content]
        raise RuntimeError(
            f"La respuesta no traia texto. Bloques recibidos: {tipos}"
        )

    # Red de seguridad: si el modelo envuelve el JSON en backticks pese
    # a la instruccion, se limpia en vez de reventar.
    if bruto.startswith("```"):
        bruto = bruto.split("```")[1]
        if bruto.startswith("json"):
            bruto = bruto[4:]
        bruto = bruto.strip()

    try:
        return json.loads(bruto)["borradores"]
    except (json.JSONDecodeError, KeyError) as e:
        print("La respuesta no venia en el formato esperado:\n")
        print(bruto)
        raise RuntimeError("Respuesta mal formada.") from e


if __name__ == "__main__":
    formato = sys.argv[1] if len(sys.argv) > 1 else "F1"

    print(f"Generando {N_BORRADORES} borradores de {formato}...\n")
    borradores = generar(formato)

    print()
    for i, b in enumerate(borradores, 1):
        texto = b["texto"]
        print(f"--- {i} ---  ({len(texto)} caracteres)")
        print(texto)
        print(f"    [{b['por_que']}]\n")