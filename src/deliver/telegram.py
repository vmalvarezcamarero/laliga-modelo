"""
Entrega de borradores por Telegram.

El humano elige uno y lo copia. Es el unico punto de contacto semanal
con el sistema, asi que tiene que bastarse solo: si hay que ir a buscar
el texto a una carpeta, se van los 15 minutos.

POLLING, NO WEBHOOK. Un webhook exige un servidor accesible desde
internet, con dominio y certificado, y mantenerlo es el tipo de trabajo
que hace que un proyecto se abandone en la semana 6. Con polling el
script pregunta un rato y se muere. Cero infraestructura.

Consecuencia asumida: el bot no escucha siempre. Si no respondes en
ESPERA_MINUTOS, los borradores quedan guardados en outputs/borradores/
y los recuperas cuando puedas.

DOS COSAS APRENDIDAS EN EL PRIMER CRON:

1. Las credenciales se limpian con .strip(). Un salto de linea invisible
   al final de un secreto de GitHub rompia la comparacion de chat_id sin
   dar ningun error: el bot leia los mensajes, los descartaba por "no
   autorizados" y seguia esperando en silencio. Un espacio no puede
   tumbar el sistema.

2. Un bot con polling solo admite UN consumidor a la vez. Telegram
   entrega cada actualizacion una sola vez, al primero que la pide. Si
   el cron esta esperando y ademas lanzas el script en local, se roban
   los mensajes entre si. No se lanzan los dos a la vez.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.content.redactar import FUENTE, N_BORRADORES, generar

BASE = Path(__file__).resolve().parents[2]
GUARDADOS = BASE / "outputs" / "borradores"

API = "https://api.telegram.org/bot"

ESPERA_MINUTOS = 20
INTERVALO_SEGUNDOS = 3


def _credenciales() -> tuple[str, str]:
    """
    Token y chat autorizado, SIN espacios ni saltos de linea.

    El .strip() no es cosmetico: un secreto de GitHub con un salto de
    linea al final produce un 404 en el token y un filtro de chat que
    no coincide nunca. Los dos fallos son silenciosos.
    """
    token = (os.environ.get("TELEGRAM_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        raise RuntimeError(
            "Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.\n"
            'En PowerShell:  $env:TELEGRAM_TOKEN = "..."'
        )
    return token, chat


def enviar(texto: str) -> None:
    token, chat = _credenciales()
    r = requests.post(
        API + token + "/sendMessage",
        json={"chat_id": chat, "text": texto},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram no acepto el mensaje: {r.text}")


def _ultimo_update_id(token: str) -> int:
    """
    Marca como leidos los mensajes anteriores.

    Sin esto, el bot leeria como respuesta cualquier mensaje viejo que
    Telegram tenga en cola. Es el fallo clasico de los bots de polling.
    """
    r = requests.get(API + token + "/getUpdates", timeout=30).json()
    resultados = r.get("result", [])
    return resultados[-1]["update_id"] + 1 if resultados else 0


def esperar_respuesta(
    validas: set[str], minutos: int = ESPERA_MINUTOS
) -> str | None:
    """
    Espera un mensaje del chat autorizado que este en `validas`.

    Devuelve la respuesta en minusculas, o None si se agota el tiempo.
    """
    token, chat = _credenciales()
    offset = _ultimo_update_id(token)
    limite = time.time() + minutos * 60

    ajenos = 0

    while time.time() < limite:
        try:
            r = requests.get(
                API + token + "/getUpdates",
                params={"offset": offset, "timeout": 10},
                timeout=30,
            ).json()
        except requests.RequestException:
            time.sleep(INTERVALO_SEGUNDOS)
            continue

        for u in r.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message", {})
            emisor = str(msg.get("chat", {}).get("id", ""))
            texto = (msg.get("text") or "").strip().lower()

            # Solo se atiende al chat autorizado. Si alguien encuentra
            # el bot y le escribe, se ignora. Se avisa por consola: un
            # descarte silencioso aqui es indistinguible de "no has
            # respondido", y eso cuesta media hora de diagnostico.
            if emisor != chat:
                ajenos += 1
                print(
                    f"   mensaje de un chat no autorizado ({emisor!r}, "
                    f"esperado {chat!r}). Ignorado."
                )
                continue

            if texto in validas:
                return texto

            print(f"   respuesta no reconocida: {texto!r}. Se esperaba "
                  f"{sorted(validas)}.")

        time.sleep(INTERVALO_SEGUNDOS)

    if ajenos:
        print(f"\n   {ajenos} mensaje(s) descartados por chat no autorizado. "
              f"Comprueba TELEGRAM_CHAT_ID.")

    return None


def _guardar(formato: str, borradores: list[dict]) -> Path:
    """Se guarda SIEMPRE, antes de mandar nada. Si Telegram falla, el
    trabajo no se pierde."""
    GUARDADOS.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M")
    ruta = GUARDADOS / f"{sello}_{formato}.json"
    ruta.write_text(
        json.dumps(borradores, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return ruta


def _mensaje(formato: str, borradores: list[dict]) -> str:
    lineas = [f"{formato} — {len(borradores)} borradores", ""]

    for i, b in enumerate(borradores, 1):
        lineas.append(f"[{i}]  {len(b['texto'])} caracteres")
        lineas.append(b["texto"])
        if b.get("sospechosos"):
            lineas.append(
                "!! cifras que no estan en el JSON: "
                + ", ".join(b["sospechosos"])
            )
        lineas.append("")

    lineas.append(f"Responde 1-{len(borradores)}, o NO para descartar.")
    return "\n".join(lineas)


def repartir(formato: str) -> dict | None:
    """
    Genera, envia, espera y devuelve el borrador elegido.
    """
    token, chat = _credenciales()
    print(f"Bot listo. Chat autorizado: {chat}")

    print(f"Generando borradores de {formato}...")
    borradores = generar(formato)

    ruta = _guardar(formato, borradores)
    print(f"Guardados en {ruta}")

    enviar(_mensaje(formato, borradores))
    print(f"Enviados. Esperando respuesta ({ESPERA_MINUTOS} min)...")

    validas = {str(i) for i in range(1, len(borradores) + 1)} | {"no"}
    respuesta = esperar_respuesta(validas)

    if respuesta is None:
        enviar(
            "Se acabo el tiempo de espera. Los borradores estan guardados "
            "en el repo, no se ha perdido nada."
        )
        print("Sin respuesta.")
        return None

    if respuesta == "no":
        enviar("Descartados los cinco. Nada que publicar.")
        print("Descartados.")
        return None

    elegido = borradores[int(respuesta) - 1]

    # Se manda el texto SOLO, para que se copie de un toque en el movil.
    enviar(elegido["texto"])

    if elegido.get("sospechosos"):
        enviar(
            "Ojo: este llevaba cifras sin respaldo en el JSON ("
            + ", ".join(elegido["sospechosos"])
            + "). Comprueba antes de publicar."
        )

    print(f"Elegido el {respuesta}.")
    return elegido


if __name__ == "__main__":
    formatos = sys.argv[1:] or ["F1"]

    desconocidos = [f for f in formatos if f not in FUENTE]
    if desconocidos:
        raise SystemExit(
            f"Formato(s) desconocido(s): {', '.join(desconocidos)}. "
            f"Disponibles: {sorted(FUENTE)}"
        )

    # Varios formatos en una sola ejecucion: el jueves van F1 y F2, el
    # viernes F3 y F4. Se reparten en serie porque un bot con polling
    # solo admite un consumidor (P-16): dos procesos a la vez se roban
    # los mensajes.
    for i, formato in enumerate(formatos):
        if i:
            print()
        try:
            repartir(formato)
        except Exception as e:
            # Que falle uno no puede impedir que se entreguen los
            # demas. Un jueves sin F2 es peor que un jueves sin nada.
            print(f"ERROR en {formato}: {e}")
            enviar(f"No se ha podido generar {formato}: {e}")