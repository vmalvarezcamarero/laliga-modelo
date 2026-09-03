# CLAUDE.md — Reglas permanentes del repositorio

Proyecto **LA CRIBA** (`@PasaLaCriba`): cuenta de X que publica predicciones
estadísticas de LaLiga con un modelo propio llamado EGO, y que audita
públicamente sus propios errores cada lunes.

La fuente de verdad son los documentos del proyecto: `00_PROYECTO.md`,
`01_ARQUITECTURA.md`, `02_VOZ_Y_FORMATOS.md`, `03_ESTADO.md`.
Si algo de este archivo contradice a `00_PROYECTO.md`, gana `00_PROYECTO.md`.

---

## 1. Cómo trabajar conmigo

- **Responde en español.** Código y nombres de variables, también en español.
- Soy nuevo en desarrollo. **Explica qué vas a hacer y por qué antes de
  hacerlo**, y avísame de lo que puede salir mal antes de que salga mal.
- **No ejecutes comandos destructivos sin pedírmelo**: `git push --force`,
  `git reset --hard`, borrar archivos de `data/`, reescribir `laliga.db`.
- Si detectas que estoy complicando algo por diversión técnica en lugar de
  por necesidad, dímelo en lugar de seguirme.
- Prioriza que el sistema sobreviva 6 meses sobre que sea elegante.

---

## 2. Invariantes que no se rompen nunca

### D-14 — Las cuotas no llegan a la redacción

Las cuotas de cierre viven **solo** en la tabla `market_odds` y **solo** se
usan como baseline de evaluación interna.

- `market_odds` se lee únicamente desde `src/evaluate/`.
- **Nunca** se importa, cruza ni menciona desde `src/content/`,
  `src/models/` ni `src/deliver/`.
- El JSON que alimenta la generación de borradores se construye
  exclusivamente desde `matches`.
- Ningún texto publicable menciona cuotas, casas de apuestas, "value" ni
  recomendaciones de jugada. Nunca, en ningún formato, ni de pasada.

La garantía es estructural, no de disciplina: si una tarea necesita cruzar
`market_odds` con contenido publicable, la tarea está mal planteada. Párate y
dímelo.

### Validación temporal estricta

En `src/evaluate/backtest.py` la frontera es:

```python
corte = objetivo["fecha"].min()
entrenamiento = partidos[partidos["fecha"] < corte]
```

Ese `<` estricto es toda la validación temporal, y vive en un solo sitio a
propósito. **No lo dupliques, no lo relajes, no lo muevas.** Cualquier
modelo nuevo (córners, tarjetas) entrena con la misma frontera.

Los hiperparámetros se calibran sobre temporadas **distintas** de las de
validación (D-17). `xi` se calibró sobre 2019-2023 y se validó sobre
2024-25 y 2025-26.

### La ingesta falla ruidosamente

- Si aparece un nombre de equipo sin equivalencia en el diccionario de
  `src/ingest/understat.py`, **el script se detiene**. No inventes
  traducciones ni uses coincidencia difusa.
- Si el cruce Football-Data ↔ Understat baja del 99%, **no se escribe nada**
  en la base.
- Un fallo de cruce no se manifiesta como error, sino como un modelo
  entrenado con menos datos de los que crees. Por eso se comprueba explícito.

### El modelo

- Se ajusta sobre **xG**, no sobre goles (D-02).
- Ajuste en dos etapas: fuerzas sobre xG con Poisson continua, `rho` sobre
  goles reales (D-15). No mezclar.
- Ataque y defensa centrados: **1.00 = media exacta de la liga** (D-16).
  Penalización L2 de `1e-4` sobre los parámetros en bruto para que converja.
- `xi = 0.001`, fijado en Sprint 1. No se toca sin rehacer la calibración.

### FBref

Máximo 10 peticiones por minuto; ritmo objetivo 1 cada 6-7 segundos.
Superarlo bloquea la IP hasta un día. Es fuente **secundaria**: contexto
narrativo, nunca entrada del modelo principal.

Si aparecen errores sobre Chrome o chromedriver, es señal de que se está
tocando FBref sin querer. Párate.

### X

- Prohibido scrapear X o automatizar el navegador. Suspensión permanente.
- Prohibido responder automáticamente a tuits ajenos.
- En Fase 1 (sprints 1-4) **la publicación es manual**. Nada de código que
  publique.

---

## 3. Entorno

- Windows. Python 3.12.10 en entorno virtual `.venv`.
- pandas 3.0.5 · numpy 2.5.2 · scipy 1.18.1 · soccerdata 1.9.1 ·
  matplotlib 3.11.1.
- Repositorio: `C:\dev\laliga-modelo`.
- **Antes de instalar cualquier dependencia nueva, pregúntame.** Cada
  librería añadida es una cosa más que se puede romper en seis meses.

Comandos habituales (desde la raíz del repo, con `.venv` activado):

```
python -m src.ingest.football_data      # descarga histórico
python -m src.ingest.understat          # xG + cruce validado
python -m src.evaluate.backtest         # validación temporal
python -m src.models.inspeccionar       # inspección manual del ajuste
```

---

## 4. Estilo de código

- **Legible por encima de elegante.** Este código lo tiene que entender
  alguien que empezó a programar hace un mes.
- Funciones cortas con nombres descriptivos en español.
- Sin abstracciones prematuras: nada de clases base, factories ni capas de
  configuración hasta que haya tres casos reales que lo justifiquen.
- Comentarios que expliquen **por qué**, no qué. El qué ya lo dice el código.
- Sin dependencias nuevas para algo que `pandas` o la librería estándar ya
  hacen.
- Un archivo por responsabilidad, siguiendo la estructura de
  `01_ARQUITECTURA.md` §6.

---

## 5. Qué se versiona

| Sí | No |
|---|---|
| `data/laliga.db` (D-07) | `data/raw/` (CSVs redescargables) |
| `outputs/predictions/` (auditoría) | `outputs/charts/` (PNG regenerables) |
| Todo `src/`, `tests/` | `.venv/` |

`outputs/predictions/` se versiona porque es el registro histórico de lo que
EGO dictaminó. Sin él no puedes auditarte, y auditarse es el producto.

---

## 6. Antes de cada commit

1. El backtest sigue corriendo y el RPS no ha empeorado sin explicación.
   Referencia Sprint 1: **0.1974** sobre 759 partidos de 2024-25 y 2025-26.
2. Ningún archivo fuera de `src/evaluate/` importa o consulta `market_odds`.
3. Mensaje de commit en español, imperativo, una línea.
   Ejemplo: `Añade calibración del umbral de la criba`.
