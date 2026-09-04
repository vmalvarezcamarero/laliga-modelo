"""Temporal. Ver que devuelve la API en la llamada real de F1."""

from anthropic import Anthropic

from src.content.redactar import RECORTES, _sistema, _usuario, _ultimo_json

doc = _ultimo_json()
datos = RECORTES["F1"](doc)

sistema = _sistema()
usuario = _usuario("F1", datos)

print(f"Sistema: {len(sistema)} caracteres")
print(f"Usuario: {len(usuario)} caracteres\n")

r = Anthropic().messages.create(
    model="claude-sonnet-5",
    max_tokens=4000,
    system=sistema,
    messages=[{"role": "user", "content": usuario}],
)

print("STOP:", r.stop_reason)
print("TOKENS:", r.usage.input_tokens, "->", r.usage.output_tokens)
print("TIPOS DE BLOQUE:", [b.type for b in r.content])
print("\n--- CONTENIDO ---")
for b in r.content:
    print(f"[{b.type}]")
    print(getattr(b, "text", "(sin texto)"))