"""Diccionario único de nombres de equipo.

Cuatro grafías conviven en el proyecto:
  - Football-Data.co.uk  -> CANÓNICO. Es lo que hay en la tabla `matches`.
  - Understat            -> se traduce a canónico en la ingesta.
  - football-data.org    -> se traduce a canónico en la ingesta.
  - Publicable           -> solo en el último metro: JSON de redacción y PNG.

REGLA: dentro del sistema todo habla en canónico.
Si un nombre no está aquí, el script que lo pida se detiene.
Traducir mal es peor que no traducir.
"""


# football-data.org -> canónico (Football-Data.co.uk)
FDORG_A_CANONICO = {
    "Athletic Club": "Ath Bilbao",
    "CA Osasuna": "Osasuna",
    "Club Atlético de Madrid": "Ath Madrid",
    "Deportivo Alavés": "Alaves",
    "Elche CF": "Elche",
    "FC Barcelona": "Barcelona",
    "Getafe CF": "Getafe",
    "Levante UD": "Levante",
    "Málaga CF": "Malaga",
    "Rayo Vallecano de Madrid": "Vallecano",
    "RC Celta de Vigo": "Celta",
    "RC Deportivo La Coruña": "La Coruna",
    "RCD Espanyol de Barcelona": "Espanol",
    "Real Betis Balompié": "Betis",
    "Real Madrid CF": "Real Madrid",
    "Real Racing Club de Santander": "Santander",
    "Real Sociedad de Fútbol": "Sociedad",
    "Sevilla FC": "Sevilla",
    "Valencia CF": "Valencia",
    "Villarreal CF": "Villarreal",
    # Equipos que pueden ascender en el futuro
    "Real Oviedo": "Oviedo",
    "Real Valladolid CF": "Valladolid",
    "Girona FC": "Girona",
    "UD Las Palmas": "Las Palmas",
    "RCD Mallorca": "Mallorca",
    "Cádiz CF": "Cadiz",
    "Granada CF": "Granada",
    "UD Almería": "Almeria",
    "CD Leganés": "Leganes",
    "SD Huesca": "Huesca",
    "SD Eibar": "Eibar",
    "Real Sporting de Gijón": "Sp Gijon",
    "Córdoba CF": "Cordoba",
}


# canónico -> publicable (el nombre que usa la afición)
CANONICO_A_PUBLICABLE = {
    "Alaves": "Alavés",
    "Almeria": "Almería",
    "Ath Bilbao": "Athletic",
    "Ath Madrid": "Atlético",
    "Barcelona": "Barcelona",
    "Betis": "Betis",
    "Cadiz": "Cádiz",
    "Celta": "Celta",
    "Cordoba": "Córdoba",
    "Eibar": "Eibar",
    "Elche": "Elche",
    "Espanol": "Espanyol",
    "Getafe": "Getafe",
    "Girona": "Girona",
    "Granada": "Granada",
    "Huesca": "Huesca",
    "La Coruna": "Dépor",
    "Las Palmas": "Las Palmas",
    "Leganes": "Leganés",
    "Levante": "Levante",
    "Malaga": "Málaga",
    "Mallorca": "Mallorca",
    "Osasuna": "Osasuna",
    "Oviedo": "Oviedo",
    "Real Madrid": "Real Madrid",
    "Santander": "Racing",
    "Sevilla": "Sevilla",
    "Sociedad": "Real Sociedad",
    "Sp Gijon": "Sporting",
    "Valencia": "Valencia",
    "Valladolid": "Valladolid",
    "Vallecano": "Rayo",
    "Villarreal": "Villarreal",
}


class NombreDesconocido(Exception):
    """Un nombre de equipo sin equivalencia definida."""


def a_canonico(nombre: str, fuente: str = "fdorg") -> str:
    """Traduce un nombre de una fuente externa al canónico interno."""
    tablas = {"fdorg": FDORG_A_CANONICO}
    if fuente not in tablas:
        raise ValueError(f"Fuente no reconocida: {fuente}")
    tabla = tablas[fuente]
    if nombre not in tabla:
        raise NombreDesconocido(
            f"'{nombre}' no está en el diccionario de {fuente}. "
            f"Añádelo a src/models/equipos.py antes de continuar."
        )
    return tabla[nombre]


def a_publicable(canonico: str) -> str:
    """Traduce el nombre interno al nombre que se publica. Último metro."""
    if canonico not in CANONICO_A_PUBLICABLE:
        raise NombreDesconocido(
            f"'{canonico}' no tiene nombre publicable definido. "
            f"Añádelo a src/models/equipos.py."
        )
    return CANONICO_A_PUBLICABLE[canonico]