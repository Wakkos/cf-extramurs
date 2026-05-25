#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extramurs Calendar Automation - FFCV API client

Consume la API JSON pública de ffcv.es/competiciones/ (heredera del antiguo
portal resultadosffcv.isquad.es) para generar el calendario, clasificación y
plantilla de cada equipo configurado.
"""

import base64
import json
import logging
import re
import time
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

from ics import Calendar, Event
from jinja2 import Environment, FileSystemLoader
import requests


# Base de la API pública de la FFCV. Los IDs antiguos del portal isquad
# (id_temp, id_modalidad, id_competicion, id_torneo, id_equipo) siguen siendo
# válidos como cod_temporada, cod_competicion, cod_grupo y codequipo en la
# nueva API JSON.
FFCV_API_BASE = "https://ffcv.es/competiciones/api"

# El servidor bloquea User-Agents con patrón de scraping (curl/python-requests/etc.)
# y devuelve {"error":"blocked","reason_code":"UA_BLOCKED"}. Hace falta UA real.
FFCV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://ffcv.es/competiciones/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


class FFCVAPIError(RuntimeError):
    """Error devuelto por la API FFCV o respuesta inesperada."""

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
IMAGES_DIR = BASE_DIR / "Images"
PLANTILLA_IMAGES_DIR = IMAGES_DIR / "plantilla"
OUTPUT_ICS = BASE_DIR / "partidos.ics"
OUTPUT_JSON = DATA_DIR / "partidos.json"
OUTPUT_INDEX = BASE_DIR / "index.html"
OUTPUT_DASHBOARD = BASE_DIR / "dashboard.html"
OUTPUT_PLANTILLA = BASE_DIR / "plantilla.html"


def load_club_config() -> Optional[Dict]:
    """
    Carga `configs/_club.yaml` si existe. Si no, devuelve None (modo legacy:
    sólo se procesan los equipos definidos en configs/equipo*.yaml).
    """
    path = BASE_DIR / "configs" / "_club.yaml"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    logger.info(f"✓ Configuración de club cargada: {config['club']['nombre']}")
    return config


# NOTA: Variables globales - se inicializan dinámicamente por equipo en setup_globals()
CONFIG = None
TEAM_NAME = None
TEAM_SHORT_NAME = None
GRUPO = None
COD_GRUPO = None
COD_EQUIPO = None
PLANTILLA_IMAGES_DIR = None
OUTPUT_ICS = None
OUTPUT_JSON = None
OUTPUT_INDEX = None
OUTPUT_PLANTILLA = None


def setup_globals(config: Dict):
    """
    Inicializa variables globales para un equipo específico
    """
    global CONFIG, TEAM_NAME, TEAM_SHORT_NAME, GRUPO, COD_GRUPO, COD_EQUIPO
    global PLANTILLA_IMAGES_DIR, OUTPUT_ICS, OUTPUT_JSON, OUTPUT_INDEX, OUTPUT_PLANTILLA

    CONFIG = config
    TEAM_NAME = config['equipo']['nombre']
    TEAM_SHORT_NAME = config['equipo']['nombre_corto']
    GRUPO = config['equipo']['grupo']

    # Los antiguos id_torneo / id_equipo son los nuevos cod_grupo / codequipo
    # de la API JSON. Mantenemos los nombres de campo del YAML para compat.
    ids = config['ids_ffcv']
    COD_GRUPO = str(ids['torneo'])
    COD_EQUIPO = str(ids['equipo'])

    # Directorios de salida
    output_dir = BASE_DIR / config['sitio']['output_dir']
    images_dir = BASE_DIR / config['sitio']['images_dir']

    # Crear directorios si no existen
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    PLANTILLA_IMAGES_DIR = images_dir
    OUTPUT_ICS = output_dir / "partidos.ics"
    OUTPUT_JSON = DATA_DIR / f"{config['equipo']['nombre_corto'].lower().replace(' ', '')}.json"
    OUTPUT_INDEX = output_dir / "index.html"
    OUTPUT_PLANTILLA = output_dir / "plantilla.html"


_SESSION: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """requests.Session compartida, con headers FFCV preconfigurados."""
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update(FFCV_HEADERS)
    return _SESSION


def _es_respuesta_transitoria(data) -> Optional[str]:
    """
    Detecta respuestas del proxy FFCV que indican un fallo transitorio del
    backend isquad (sesión upstream caducada, caché en refresco, 503...).
    Devuelve un string descriptivo o None si la respuesta es sana.
    """
    if not isinstance(data, dict):
        return None

    # El proxy degrada respuestas vacías con esta marca explícita.
    if data.get("_source") == "degraded_empty":
        upstream = data.get("_upstream") or {}
        return f"degraded_empty upstream={upstream.get('code')}"

    # Errores de sesión del upstream que el proxy reenvía.
    estado = data.get("estado")
    if estado is not None and str(estado) == "0":
        err = data.get("error") or ""
        if "Sesión" in err or "sesión" in err or "sesion" in err.lower():
            return f"sesion_invalida: {err}"

    return None


def fetch_json(path: str, params: Optional[Dict] = None, max_retries: int = 5) -> Dict:
    """
    Hace GET a un endpoint de la API FFCV y devuelve el JSON parseado.

    Reintenta con backoff incremental si el proxy degrada la respuesta o si el
    upstream isquad pierde la sesión — estos fallos son transitorios y suelen
    resolverse en pocos segundos.

    Args:
        path: ruta relativa al endpoint (p.ej. "filtros/jornadas_fetch.php").
              Si empieza por "http" se trata como URL absoluta.
        params: parámetros de query string.
        max_retries: número total de intentos.

    Raises:
        FFCVAPIError: si la API devuelve un error permanente, o tras agotar
            los reintentos en errores transitorios.
        requests.RequestException: errores de red persistentes.
    """
    url = path if path.startswith("http") else f"{FFCV_API_BASE}/{path.lstrip('/')}"
    session = _get_session()

    last_motivo: Optional[str] = None
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.debug(f"GET {url} params={params} (intento {attempt}/{max_retries})")
            response = session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            # Errores permanentes del backend: nombre raro, parámetro inválido, etc.
            # Distinguir de errores transitorios (manejados aparte abajo).
            if isinstance(data, dict) and data.get("error") and data.get("estado") != "0":
                raise FFCVAPIError(f"API FFCV {url}: {data}")

            motivo = _es_respuesta_transitoria(data)
            if motivo:
                last_motivo = motivo
                backoff = min(2 ** attempt, 20)  # 2, 4, 8, 16, 20s
                logger.warning(
                    f"Respuesta transitoria en {url} ({motivo}); "
                    f"reintento {attempt}/{max_retries} en {backoff}s"
                )
                if attempt < max_retries:
                    time.sleep(backoff)
                    continue

            return data

        except FFCVAPIError:
            raise
        except requests.HTTPError as e:
            last_exc = e
            status = getattr(e.response, "status_code", None)
            # 429 = rate limit: backoff agresivo (30s+).
            if status == 429:
                espera = min(30 * attempt, 120)
                logger.warning(f"429 Too Many Requests; durmiendo {espera}s antes de reintentar")
                if attempt < max_retries:
                    time.sleep(espera)
                    continue
            logger.warning(f"Error HTTP {status} en intento {attempt}: {e}")
            if attempt < max_retries:
                time.sleep(5)
        except (requests.RequestException, ValueError) as e:
            last_exc = e
            logger.warning(f"Error en intento {attempt}: {e}")
            if attempt < max_retries:
                time.sleep(5)

    if last_motivo:
        raise FFCVAPIError(
            f"API FFCV agotó {max_retries} reintentos en {url}: {last_motivo}"
        )
    raise FFCVAPIError(
        f"No se pudo obtener {url} tras {max_retries} intentos"
    ) from last_exc


# ---------------------------------------------------------------------------
# Descubrimiento de equipos del club
# ---------------------------------------------------------------------------

# Mapeo de "codigo_categoria" → raíz del slug. Para categorías que no estén
# aquí caemos a una versión normalizada del nombre.
_CATEGORIA_SLUG_RAIZ = {
    "Prebenjamín": "prebenjamin",
    "Benjamín": "benjamin",
    "Alevín": "alevin",
    # Infantiles del club se llaman "2ª Regional Infantil"; el sufijo "Regional"
    # no aporta a nivel de URL así que nos quedamos con la base.
    "Infantil": "infantil",
    "Querubines": "querubines",
}


def _slugify(texto: str) -> str:
    """Lower-case, sin tildes y con guiones."""
    import unicodedata
    norm = unicodedata.normalize("NFD", texto)
    norm = "".join(c for c in norm if unicodedata.category(c) != "Mn")
    norm = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return norm


def _categoria_raiz(nombre_categoria: str) -> str:
    """Devuelve la raíz canónica para el slug a partir del nombre de categoría."""
    for clave, raiz in _CATEGORIA_SLUG_RAIZ.items():
        if clave.lower() in nombre_categoria.lower():
            return raiz
    return _slugify(nombre_categoria)


def _letra_equipo(nombre_equipo: str) -> str:
    """
    Extrae la letra del equipo: 'C.F. Extramurs Valencia 'A'' → 'a'.
    Si no hay letra entre comillas, devuelve '' y el llamador genera un
    sufijo alternativo.
    """
    match = re.search(r"'([A-Z])'\s*$", nombre_equipo)
    if match:
        return match.group(1).lower()
    return ""


def generar_slug(equipo_api: Dict) -> str:
    """
    Genera un slug determinístico a partir de la respuesta de
    ajax_club_equipos.php para un equipo (`categoria` + letra).

    Ejemplos:
        Prebenjamín 2º. Año 'A' → "prebenjamin-a"
        Alevín 1er. Año 'D'    → "alevin-d"
        Querubines 'B'         → "querubines-b"
    """
    categoria = equipo_api.get("categoria") or ""
    nombre_equipo = equipo_api.get("nombre_equipo") or ""
    raiz = _categoria_raiz(categoria)
    letra = _letra_equipo(nombre_equipo)
    if letra:
        return f"{raiz}-{letra}"
    # Sin letra reconocible: usar codequipo como sufijo para garantizar unicidad
    return f"{raiz}-{equipo_api.get('codequipo')}"


def _anyo_categoria(nombre_categoria: str) -> Optional[int]:
    """Devuelve 1 o 2 según el año dentro de la categoría, o None si no aplica."""
    txt = nombre_categoria.lower()
    if "2º" in txt or "2o." in txt or "2.º" in txt or "2do" in txt:
        return 2
    if "1er" in txt or "1º" in txt or "1.º" in txt or "primer" in txt:
        return 1
    return None


def descubrir_equipos_del_club(clave_acceso: str, cod_temporada: str) -> List[Dict]:
    """
    Lista los equipos en competición del club para la temporada indicada.
    Devuelve la lista cruda tal como la entrega la API.
    """
    logger.info(
        f"Descubriendo equipos del club (clave={clave_acceso}, temporada={cod_temporada})..."
    )
    data = fetch_json(
        "clubes/ajax_club_equipos.php",
        {"clave": clave_acceso, "cod_temporada": cod_temporada},
    )
    equipos = data.get("equipos") or []
    if not equipos:
        raise FFCVAPIError(
            f"ajax_club_equipos.php devolvió 0 equipos (clave={clave_acceso})"
        )
    logger.info(f"✓ {len(equipos)} equipos descubiertos")
    return equipos


def _competicion_relevante_para_provincia(nombre_comp: str) -> bool:
    """
    Heurística para descartar competiciones de provincias ajenas. Para el
    club Extramurs (Valencia) excluimos Alacant y Castelló; nos quedamos con
    las "València" y las multi-provinciales (Copa, Lliga Comunitat).
    """
    n = (nombre_comp or "").lower()
    if "alacant" in n or "alicante" in n:
        return False
    if "castell" in n:
        return False
    return True


def _es_competicion_liga(nombre_comp: str) -> bool:
    """
    True si parece una liga regular (vs copa/torneo/fase final). Las ligas
    tienen muchas más jornadas y son las que queremos como "competición
    principal" del equipo.
    """
    n = (nombre_comp or "").lower()
    palabras_cup = ("copa", "fase final", "torneo", "tornem", "playoff", "play-off")
    return not any(w in n for w in palabras_cup)


def _resolver_competiciones_por_categoria(cod_temporada: str) -> Dict[str, List[Dict]]:
    """
    Mapea codigo_categoria → lista de competiciones (con codigo y nombre) que
    pertenecen a esa categoría en la temporada indicada.
    """
    logger.info("Cargando competiciones de la temporada...")
    data = fetch_json("filtros/competiciones_fetch.php", {"cod_temporada": cod_temporada})
    competiciones = data.get("competiciones") or []
    if not competiciones:
        raise FFCVAPIError(
            f"competiciones_fetch.php devolvió 0 competiciones (temporada={cod_temporada})"
        )

    out: Dict[str, List[Dict]] = {}
    for comp in competiciones:
        cat = comp.get("CodigoCategoria")
        cod = comp.get("codigo")
        if cat and cod:
            out.setdefault(str(cat), []).append({
                "codigo": str(cod),
                "nombre": comp.get("nombre") or "",
            })
    logger.info(f"✓ {len(competiciones)} competiciones, {len(out)} categorías indexadas")
    return out


def _equipo_esta_en_grupo(codequipo: str, cod_grupo: str) -> bool:
    """
    Comprueba si un equipo está en un grupo usando clasificaciones_ajax.php
    (~10 equipos por grupo en respuestas pequeñas). Devuelve False también si
    el endpoint falla — el llamador puede seguir con otro grupo.
    """
    try:
        data = fetch_json(
            "clasificaciones/clasificaciones_ajax.php",
            {"cod_grupo": cod_grupo, "cod_jornada": "1"},
        )
    except FFCVAPIError:
        return False
    for fila in data.get("clasificacion") or []:
        if str(fila.get("codequipo")) == str(codequipo):
            return True
    return False


def _resolver_grupos_de_categoria(
    cod_categoria: str,
    codequipos_pendientes: set,
    competiciones_por_categoria: Dict[str, List[Dict]],
) -> Dict[str, Dict]:
    """
    Para una categoría dada y un conjunto de codequipos del club que pertenecen
    a esa categoría, descubre el `cod_grupo` de cada uno usando el endpoint de
    clasificaciones (10 equipos por grupo, respuesta pequeña).

    Itera las competiciones de la categoría (filtradas por provincia) y, dentro
    de cada competición, recorre los grupos. Sale cuanto se han resuelto todos
    los equipos pendientes para esa categoría.

    Devuelve {codequipo: {cod_competicion, cod_grupo, nombre_grupo, total_jornadas}}.
    """
    resueltos: Dict[str, Dict] = {}
    posibles = competiciones_por_categoria.get(str(cod_categoria), [])
    if not posibles:
        return resueltos

    # Estrategia de orden:
    #   1) Provincia local + es liga regular (Lliga/Preferent/Primera/Segona/...)
    #   2) Liga regular sin filtro de provincia (Lliga Comunitat, etc.)
    #   3) Provincia local + copa/torneo
    #   4) Resto (otras provincias, copa nacional...)
    # Así cogemos primero la categoría principal (más jornadas) y no la copa.
    cubos: Dict[int, List[Dict]] = {0: [], 1: [], 2: [], 3: []}
    for c in posibles:
        es_local = _competicion_relevante_para_provincia(c["nombre"])
        es_liga = _es_competicion_liga(c["nombre"])
        if es_local and es_liga:
            cubos[0].append(c)
        elif es_liga:
            cubos[1].append(c)
        elif es_local:
            cubos[2].append(c)
        else:
            cubos[3].append(c)
    orden = cubos[0] + cubos[1] + cubos[2] + cubos[3]

    for comp in orden:
        if not codequipos_pendientes:
            break
        try:
            grupos_data = fetch_json(
                "filtros/grupos_fetch.php", {"cod_competicion": comp["codigo"]}
            )
        except FFCVAPIError as e:
            logger.warning(f"Saltando competición {comp['codigo']} ({comp['nombre']}): {e}")
            continue

        for grupo in grupos_data.get("grupos") or []:
            if not codequipos_pendientes:
                break
            cod_grupo = str(grupo.get("codigo") or "")
            if not cod_grupo:
                continue

            try:
                clasif_data = fetch_json(
                    "clasificaciones/clasificaciones_ajax.php",
                    {"cod_grupo": cod_grupo, "cod_jornada": "1"},
                )
            except FFCVAPIError as e:
                logger.warning(f"Saltando grupo {cod_grupo}: {e}")
                continue

            for fila in clasif_data.get("clasificacion") or []:
                codeq = str(fila.get("codequipo") or "")
                if codeq in codequipos_pendientes:
                    resueltos[codeq] = {
                        "cod_competicion": comp["codigo"],
                        "cod_grupo": cod_grupo,
                        "nombre_grupo": grupo.get("nombre"),
                        "total_jornadas": _try_int(grupo.get("total_jornadas")),
                    }
                    codequipos_pendientes.discard(codeq)
                    logger.info(
                        f"  ✓ resuelto codequipo={codeq} → cod_grupo={cod_grupo} "
                        f"({grupo.get('nombre')})"
                    )

            # Pequeño respiro para no agitar al rate limiter
            time.sleep(0.1)

    return resueltos


def cargar_o_descubrir_club_map(
    clave_acceso: str,
    cod_temporada: str,
    cache_path: Path,
) -> Dict:
    """
    Carga el club_map de caché. Si no existe o la temporada cambió, lo
    regenera desde la API. Si existe, sólo resuelve `cod_grupo` para equipos
    que hayan aparecido nuevos desde la última actualización.

    El club_map tiene esta forma:
        {
          "cod_temporada": "21",
          "clave_acceso_club": "4189",
          "ultima_actualizacion": "2026-05-24T...",
          "equipos": [
            {codequipo, slug, letra, categoria, codigo_categoria,
             cod_grupo_categoria, nombre_grupo_categoria, anyo_categoria,
             cod_competicion, cod_grupo, nombre_grupo, total_jornadas,
             nombre_equipo, escudo, campo_juego, jugar_dia, jugar_horario,
             codigo_campo},
            ...
          ]
        }
    """
    cache: Dict = {"equipos": []}
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Cache club_map inválida ({e}); regenerando")
            cache = {"equipos": []}

    # Si cambió la temporada, invalidamos
    if str(cache.get("cod_temporada") or "") != str(cod_temporada):
        cache = {"equipos": []}

    # Estado actual del club según la API
    equipos_actuales = descubrir_equipos_del_club(clave_acceso, cod_temporada)
    actuales_codequipos = {str(e["codequipo"]) for e in equipos_actuales}

    # Eliminar de la caché los equipos que ya no están en competición
    cache["equipos"] = [
        e for e in cache["equipos"] if str(e.get("codequipo")) in actuales_codequipos
    ]
    cacheados_codequipos = {str(e["codequipo"]) for e in cache["equipos"]}

    # Refrescar campos volátiles (campo, horario, escudo, slug) de los que ya tenemos
    actuales_por_code = {str(e["codequipo"]): e for e in equipos_actuales}
    for entrada in cache["equipos"]:
        api_entry = actuales_por_code.get(str(entrada["codequipo"]))
        if api_entry:
            entrada.update(_extraer_campos_volatiles(api_entry))

    # Equipos nuevos: resolver cod_grupo y añadir
    nuevos = [
        e for e in equipos_actuales
        if str(e["codequipo"]) not in cacheados_codequipos
    ]

    if nuevos:
        logger.info(f"Resolviendo cod_grupo para {len(nuevos)} equipo(s) nuevo(s)...")
        comp_index = _resolver_competiciones_por_categoria(cod_temporada)

        # Agrupar los pendientes por categoría para resolver en bulk: con un
        # único barrido de los grupos de la categoría cubrimos a todos los
        # equipos del club que comparten esa categoría.
        por_categoria: Dict[str, List[Dict]] = {}
        for equipo_api in nuevos:
            cod_cat = str(equipo_api.get("codigo_categoria") or "")
            por_categoria.setdefault(cod_cat, []).append(equipo_api)

        for cod_categoria, equipos_de_cat in por_categoria.items():
            codequipos_pendientes = {str(e["codequipo"]) for e in equipos_de_cat}
            logger.info(
                f"Categoría {cod_categoria} ({equipos_de_cat[0].get('categoria')}): "
                f"{len(codequipos_pendientes)} equipo(s) a resolver"
            )
            resoluciones = _resolver_grupos_de_categoria(
                cod_categoria, codequipos_pendientes, comp_index
            )

            for equipo_api in equipos_de_cat:
                codequipo = str(equipo_api["codequipo"])
                resolucion = resoluciones.get(codequipo)
                if resolucion is None:
                    logger.warning(
                        f"No se pudo resolver grupo para {equipo_api.get('nombre_equipo')} "
                        f"(codequipo={codequipo}, categoria={equipo_api.get('categoria')})"
                    )
                    continue

                entrada = {
                    "codequipo": codequipo,
                    "slug": generar_slug(equipo_api),
                    "letra": _letra_equipo(equipo_api.get("nombre_equipo") or ""),
                    "anyo_categoria": _anyo_categoria(equipo_api.get("categoria") or ""),
                    "codigo_categoria": cod_categoria,
                    "cod_grupo_categoria": str(equipo_api.get("cod_grupo_categoria") or ""),
                    "nombre_grupo_categoria": equipo_api.get("nombre_grupo_categoria"),
                    **resolucion,
                    **_extraer_campos_volatiles(equipo_api),
                }
                cache["equipos"].append(entrada)

    # Detectar colisiones de slug
    slugs_vistos: Dict[str, str] = {}
    for entrada in cache["equipos"]:
        slug = entrada.get("slug")
        if not slug:
            continue
        if slug in slugs_vistos and slugs_vistos[slug] != entrada["codequipo"]:
            logger.warning(
                f"⚠️  Colisión de slug '{slug}' entre codequipo={slugs_vistos[slug]} "
                f"y codequipo={entrada['codequipo']}"
            )
        slugs_vistos[slug] = entrada["codequipo"]

    # Orden estable para que el JSON cacheado no oscile entre runs
    cache["equipos"].sort(key=lambda e: e.get("slug") or "")

    cache["cod_temporada"] = str(cod_temporada)
    cache["clave_acceso_club"] = str(clave_acceso)
    cache["ultima_actualizacion"] = datetime.now().isoformat()

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    logger.info(f"✓ club_map.json escrito con {len(cache['equipos'])} equipos")
    return cache


# ---------------------------------------------------------------------------
# Coordenadas de campos (vía API FFCV + caché en disco)
# ---------------------------------------------------------------------------
#
# La API FFCV expone `api/instalaciones/datos_campo.php?codcampo=...` con
# `latitud`/`longitud` ya calculadas. Para llegar al `codigo_campo` partiendo
# de un partido usamos `api/partidos/ficha_partido_ajax.php?cod_partido=<codacta>`
# que devuelve también esa referencia.

def _cargar_cache_campos(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _guardar_cache_campos(path: Path, cache: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


def _resolver_codcampo(codacta: str) -> Optional[str]:
    """Llama a ficha_partido_ajax.php y devuelve `codigo_campo` o None."""
    try:
        ficha = fetch_json(
            "partidos/ficha_partido_ajax.php", {"cod_partido": codacta}
        )
    except FFCVAPIError as e:
        logger.warning(f"ficha_partido_ajax codacta={codacta}: {e}")
        return None
    cod = ficha.get("codigo_campo")
    return str(cod) if cod else None


def _coords_de_campo_ffcv(cod_campo: str) -> Optional[Dict]:
    """Llama a datos_campo.php y devuelve {lat, lon, direccion, localidad}."""
    try:
        data = fetch_json(
            "instalaciones/datos_campo.php", {"codcampo": cod_campo}
        )
    except FFCVAPIError as e:
        logger.warning(f"datos_campo codcampo={cod_campo}: {e}")
        return None
    try:
        return {
            "lat": float(data["latitud"]),
            "lon": float(data["longitud"]),
            "direccion": data.get("direccion") or "",
            "localidad": data.get("localidad") or "",
            "codigo_campo_ffcv": str(cod_campo),
        }
    except (KeyError, TypeError, ValueError):
        return None


def resolver_coordenadas_campos(
    partidos: List[Dict],
    cache_path: Path,
) -> Dict[str, Dict]:
    """
    Para cada partido cuyo `campo` aún no esté cacheado, sigue la cadena
    codacta → codigo_campo → lat/lon y guarda el resultado. Los campos ya
    cacheados se reutilizan tal cual; basta con borrar `data/campos.json`
    para forzar resolución de nuevo.
    """
    cache = _cargar_cache_campos(cache_path)
    pendientes: List[Dict] = []
    vistos: set = set()

    for p in partidos:
        nombre = p.get("campo") or ""
        if not nombre or nombre in cache or nombre in vistos:
            continue
        vistos.add(nombre)
        pendientes.append(p)

    if not pendientes:
        return cache

    logger.info(f"Resolviendo coordenadas de {len(pendientes)} campo(s) nuevo(s) vía API FFCV...")
    for idx, partido in enumerate(pendientes, 1):
        nombre = partido["campo"]
        codacta = partido.get("id_partido")
        if not codacta:
            cache[nombre] = {"lat": None, "lon": None, "motivo": "sin_codacta"}
            logger.warning(f"  ⚠ [{idx}/{len(pendientes)}] {nombre} → sin codacta del partido")
            continue
        cod_campo = _resolver_codcampo(str(codacta))
        if not cod_campo:
            cache[nombre] = {"lat": None, "lon": None, "motivo": "sin_codcampo"}
            logger.warning(f"  ⚠ [{idx}/{len(pendientes)}] {nombre} → sin codcampo")
            continue
        coords = _coords_de_campo_ffcv(cod_campo)
        if not coords:
            cache[nombre] = {"lat": None, "lon": None, "motivo": "sin_coords", "codigo_campo_ffcv": cod_campo}
            logger.warning(f"  ⚠ [{idx}/{len(pendientes)}] {nombre} → sin coords en datos_campo")
            continue
        cache[nombre] = coords
        logger.info(
            f"  ✓ [{idx}/{len(pendientes)}] {nombre} → ({coords['lat']:.5f}, {coords['lon']:.5f})"
        )

    _guardar_cache_campos(cache_path, cache)
    return cache


def _extraer_campos_volatiles(equipo_api: Dict) -> Dict:
    """Campos del equipo que pueden cambiar entre temporadas o renovaciones."""
    return {
        "nombre_equipo": equipo_api.get("nombre_equipo"),
        "categoria": equipo_api.get("categoria"),
        "escudo": equipo_api.get("escudo"),
        "campo_juego": equipo_api.get("campo_juego"),
        "codigo_campo": equipo_api.get("codigo_campo"),
        "jugar_dia": _try_int(equipo_api.get("jugar_dia")),
        "jugar_horario": equipo_api.get("jugar_horario"),
        "total_jugadores": _try_int(equipo_api.get("total_jugadores")),
    }


def parse_spanish_date(date_str: str) -> Optional[datetime]:
    """
    Parsea fechas en formato español a datetime
    Ejemplos: "14-11-2025", "09/11/2025", "Sábado, 09 De Noviembre"
    """
    try:
        # Limpiar la cadena
        date_str = date_str.strip()

        # Intentar formato "14-11-2025" o "14-11-25"
        match = re.search(r'(\d{1,2})-(\d{1,2})-(\d{2,4})', date_str)
        if match:
            dia = match.group(1).zfill(2)
            mes = match.group(2).zfill(2)
            year = match.group(3)
            # Si el año es de 2 dígitos, añadir "20"
            if len(year) == 2:
                year = "20" + year
            fecha_str = f"{year}-{mes}-{dia}"
            return datetime.strptime(fecha_str, "%Y-%m-%d")

        # Diccionario de meses en español
        meses = {
            'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
            'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
            'septiembre': '09', 'octubre': '10', 'noviembre': '11', 'diciembre': '12'
        }

        # Intentar parsear formato largo "Sábado, 09 De Noviembre"
        match = re.search(r'(\d{1,2})\s+[Dd]e\s+(\w+)', date_str, re.IGNORECASE)
        if match:
            dia = match.group(1).zfill(2)
            mes_nombre = match.group(2).lower()
            mes = meses.get(mes_nombre)
            if mes:
                # Asumir año 2025 para la temporada actual
                year = 2025
                fecha_str = f"{year}-{mes}-{dia}"
                return datetime.strptime(fecha_str, "%Y-%m-%d")

        # Intentar formato corto "09/11/2025" o "09/11"
        match = re.search(r'(\d{1,2})/(\d{1,2})(?:/(\d{4}))?', date_str)
        if match:
            dia = match.group(1).zfill(2)
            mes = match.group(2).zfill(2)
            year = match.group(3) if match.group(3) else "2025"
            fecha_str = f"{year}-{mes}-{dia}"
            return datetime.strptime(fecha_str, "%Y-%m-%d")

        logger.warning(f"No se pudo parsear la fecha: {date_str}")
        return None

    except Exception as e:
        logger.error(f"Error parseando fecha '{date_str}': {str(e)}")
        return None


def _maps_url(campo: str) -> Optional[str]:
    """Construye una URL de búsqueda en Google Maps para un campo."""
    if not campo:
        return None
    search_query = f"{campo}, Valencia, España"
    return f"https://www.google.com/maps/search/?api=1&query={quote(search_query)}"


def _normalizar_resultado(resultado_raw: Optional[str]) -> Optional[str]:
    """Normaliza '1 - 3' o '1-3' a '1-3'. Devuelve None si no hay marcador."""
    if not resultado_raw:
        return None
    match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", resultado_raw)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}"


def obtener_partidos_via_api(cod_grupo: str, cod_equipo: str) -> List[Dict]:
    """
    Devuelve todos los partidos del equipo en su grupo iterando jornadas.

    Cada partido conserva el shape histórico usado por los templates y el
    generador .ics:
        {jornada, id_partido, fecha (YYYY-MM-DD), hora (HH:MM), local,
         visitante, campo, resultado (str "G-G" o None), es_local, victoria,
         maps_url}
    """
    logger.info(f"Obteniendo jornadas del grupo {cod_grupo}...")
    jornadas_data = fetch_json("filtros/jornadas_fetch.php", {"cod_grupo": cod_grupo})
    jornadas = jornadas_data.get("jornadas") or []

    if not jornadas:
        raise FFCVAPIError(
            f"La API no devolvió jornadas para cod_grupo={cod_grupo}"
        )

    logger.info(f"✓ {len(jornadas)} jornadas. Recorriendo partidos del equipo {cod_equipo}...")

    partidos: List[Dict] = []
    for jornada_meta in jornadas:
        codjornada = jornada_meta.get("codjornada")
        if not codjornada:
            continue

        jornada_data = fetch_json(
            "partidos/resultados_por_grupo_jornada_data.php",
            {"cod_grupo": cod_grupo, "cod_jornada": codjornada},
        )

        for raw in jornada_data.get("partidos") or []:
            cod_local = str(raw.get("cod_equipo_local") or "")
            cod_visit = str(raw.get("cod_equipo_visitante") or "")
            if cod_equipo not in (cod_local, cod_visit):
                continue

            fecha = None
            fecha_dt = parse_spanish_date(raw.get("fecha") or "")
            if fecha_dt:
                fecha = fecha_dt.strftime("%Y-%m-%d")

            campo = (raw.get("campo") or "").strip()
            resultado = _normalizar_resultado(raw.get("resultado"))
            es_local = cod_local == cod_equipo

            victoria: Optional[bool] = None
            if resultado:
                gl, gv = (int(x) for x in resultado.split("-"))
                goles_favor = gl if es_local else gv
                goles_contra = gv if es_local else gl
                if goles_favor > goles_contra:
                    victoria = True
                elif goles_favor < goles_contra:
                    victoria = False
                # empate → victoria = None

            try:
                jornada_num: Optional[int] = int(codjornada)
            except (TypeError, ValueError):
                jornada_num = None

            partidos.append({
                "jornada": jornada_num,
                "id_partido": raw.get("codacta"),
                "fecha": fecha,
                "hora": raw.get("hora") or None,
                "local": raw.get("local"),
                "visitante": raw.get("visitante"),
                "campo": campo,
                "resultado": resultado,
                "es_local": es_local,
                "victoria": victoria,
                "maps_url": _maps_url(campo),
            })

        # Pequeño respeto al servidor; jornadas son ~18, total <2s.
        time.sleep(0.1)

    # Ordenar por jornada para que el resto del pipeline reciba los partidos
    # en el mismo orden que el HTML antiguo (de menor a mayor jornada).
    partidos.sort(key=lambda p: (p.get("jornada") or 0, p.get("fecha") or ""))

    logger.info(f"✓ Extraídos {len(partidos)} partidos del equipo {cod_equipo}")
    return partidos


def obtener_clasificacion_via_api(cod_grupo: str, cod_jornada: str) -> List[Dict]:
    """
    Devuelve la tabla de clasificación del grupo en la jornada indicada.

    Shape compatible con el código histórico:
        {posicion, equipo, puntos, pj, pg, pe, pp}

    Aprovecha además los campos adicionales que la API expone (gf, gc, racha
    de los últimos 5 partidos, codequipo) para futuras vistas.
    """
    logger.info(f"Obteniendo clasificación de cod_grupo={cod_grupo} jornada={cod_jornada}...")

    data = fetch_json(
        "clasificaciones/clasificaciones_ajax.php",
        {"cod_grupo": cod_grupo, "cod_jornada": cod_jornada},
    )

    raw = data.get("clasificacion") or []
    clasificacion: List[Dict] = []
    for item in raw:
        try:
            posicion = int(item.get("posicion") or 0)
            puntos = int(item.get("puntos") or 0)
            pj = int(item.get("jugados") or 0)
            pg = int(item.get("ganados") or 0)
            pe = int(item.get("empatados") or 0)
            pp = int(item.get("perdidos") or 0)
        except (TypeError, ValueError) as e:
            logger.warning(f"Fila de clasificación con datos no numéricos: {item} ({e})")
            continue

        nombre = item.get("nombre") or ""
        clasificacion.append({
            "posicion": posicion,
            "equipo": nombre,
            "puntos": puntos,
            "pj": pj,
            "pg": pg,
            "pe": pe,
            "pp": pp,
            # Extras útiles para futuras vistas; ignorados por templates actuales.
            "codequipo": item.get("codequipo"),
            "gf": _try_int(item.get("goles_a_favor")),
            "gc": _try_int(item.get("goles_en_contra")),
            "racha": [r.get("tipo") for r in (item.get("racha_partidos") or [])],
        })

    logger.info(f"✓ {len(clasificacion)} equipos en la clasificación")
    return clasificacion


def _try_int(value) -> Optional[int]:
    """Convierte a int sin lanzar; devuelve None si no es convertible."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def obtener_plantilla_via_api(cod_equipo: str) -> List[Dict]:
    """
    Devuelve la plantilla del equipo desde la API.

    Mantiene fotos ya descargadas en `PLANTILLA_IMAGES_DIR` con el nombre
    `jugador_<cod>.png`. No fuerza la descarga de fotos nuevas — los procesos
    de imagen (remove.bg, upscaling) seguían orientados a base64 en el HTML
    antiguo y queda fuera del alcance de la migración inicial.
    """
    logger.info(f"Obteniendo plantilla del equipo {cod_equipo}...")
    data = fetch_json("equipos/ver_equipo.php", {"codequipo": cod_equipo})

    PLANTILLA_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    images_relative_path = f"../{CONFIG['sitio']['images_dir']}"

    plantilla: List[Dict] = []
    for j in data.get("jugadores_equipo") or []:
        jugador_id = str(j.get("cod_jugador") or "").strip()
        nombre = (j.get("nombre") or "").strip()
        if not jugador_id or not nombre:
            continue

        foto_filename = f"jugador_{jugador_id}.png"
        foto_existe = (PLANTILLA_IMAGES_DIR / foto_filename).exists()

        plantilla.append({
            "id": jugador_id,
            "nombre": nombre,
            "foto": f"{images_relative_path}/{foto_filename}" if foto_existe else None,
        })

    logger.info(f"✓ {len(plantilla)} jugadores en la plantilla")
    return plantilla


def _guardar_foto_jugador(codjugador: str, data_uri: str) -> bool:
    """
    Decodifica una foto en formato `data:image/...;base64,...` y la guarda en
    `PLANTILLA_IMAGES_DIR / jugador_<cod>.png`. Idempotente: si el fichero ya
    existe no hace nada. Devuelve True si se guardó algo nuevo.
    """
    if not codjugador or not data_uri:
        return False
    if not data_uri.startswith("data:image"):
        return False

    foto_path = PLANTILLA_IMAGES_DIR / f"jugador_{codjugador}.png"
    if foto_path.exists():
        return False

    try:
        _, encoded = data_uri.split(",", 1)
        img_bytes = base64.b64decode(encoded)
    except (ValueError, TypeError) as e:
        logger.warning(f"Foto {codjugador} con base64 inválido: {e}")
        return False

    foto_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(foto_path, "wb") as f:
            f.write(img_bytes)
    except OSError as e:
        logger.warning(f"No se pudo escribir foto {codjugador}: {e}")
        return False
    return True


def obtener_dorsales_via_api(partidos: List[Dict]) -> Dict[str, str]:
    """
    Obtiene los dorsales y cosecha las fotos de los jugadores del equipo a
    partir de las actas de los últimos partidos jugados consultando
    `api/partidos/ficha_partido_ajax.php?cod_partido=<codacta>`.

    Returns:
        Dict {nombre_jugador (tal como aparece en el acta) -> dorsal}.
        Los nombres en el acta vienen "APELLIDOS, NOMBRE" igual que antes,
        así que el mapeo a la plantilla (mapear_dorsales_a_plantilla) sigue
        funcionando sin cambios.

    Side effects:
        Guarda las fotos base64 que vengan en cada acta en
        `PLANTILLA_IMAGES_DIR / jugador_<codjugador>.png` (skip si existe).
        Sin remove.bg, sin upscale: foto cruda tal como la entrega la FFCV.
    """
    logger.info("Obteniendo dorsales y cosechando fotos (API)...")

    dorsales_acumulados: Dict[str, str] = {}
    fotos_guardadas = 0
    procesados = 0
    max_partidos = 3  # últimos 3 partidos jugados, suficiente para cubrir la plantilla activa

    partidos_con_resultado = [p for p in partidos if p.get("resultado") and p.get("id_partido")]
    partidos_a_procesar = partidos_con_resultado[-max_partidos:]

    for partido in partidos_a_procesar:
        cod_partido = partido.get("id_partido")
        if not cod_partido:
            continue

        try:
            logger.info(
                f"  Partido {partido.get('local')} vs {partido.get('visitante')} (codacta={cod_partido})"
            )
            data = fetch_json("partidos/ficha_partido_ajax.php", {"cod_partido": cod_partido})

            # Sólo nos interesa el equipo cuyo cod coincide con el nuestro.
            for clave in ("jugadores_equipo_local", "jugadores_equipo_visitante"):
                if clave == "jugadores_equipo_local":
                    es_nuestro = str(data.get("codigo_equipo_local") or "") == COD_EQUIPO
                else:
                    es_nuestro = str(data.get("codigo_equipo_visitante") or "") == COD_EQUIPO
                if not es_nuestro:
                    continue

                for jugador in data.get(clave) or []:
                    nombre = (jugador.get("nombre_jugador") or "").strip()
                    dorsal = str(jugador.get("dorsal") or "").strip()
                    if nombre and dorsal:
                        dorsales_acumulados[nombre] = dorsal

                    codj = str(jugador.get("codjugador") or "").strip()
                    if _guardar_foto_jugador(codj, jugador.get("foto") or ""):
                        fotos_guardadas += 1

            procesados += 1
            time.sleep(0.5)

        except Exception as e:
            logger.warning(f"Error procesando acta codacta={cod_partido}: {e}")
            continue

    logger.info(
        f"✓ Dorsales obtenidos de {procesados} partidos: "
        f"{len(dorsales_acumulados)} jugadores, {fotos_guardadas} foto(s) nueva(s)"
    )
    return dorsales_acumulados


def mapear_dorsales_a_plantilla(plantilla: List[Dict], dorsales: Dict[str, str]) -> List[Dict]:
    """
    Mapea los dorsales extraídos de partidos a los jugadores de la plantilla

    Args:
        plantilla: Lista de jugadores de la plantilla
        dorsales: Dict con nombre (del partido) -> dorsal

    Returns:
        plantilla actualizada con dorsales
    """
    logger.info("Mapeando dorsales a jugadores de la plantilla...")

    dorsales_mapeados = 0

    for jugador in plantilla:
        nombre_plantilla = jugador['nombre'].upper()

        # Buscar coincidencia en dorsales
        # Los nombres en partidos vienen como "APELLIDO, NOMBRE"
        # Los nombres en plantilla pueden venir en varios formatos

        for nombre_partido, dorsal in dorsales.items():
            nombre_partido_upper = nombre_partido.upper()

            # Intentar diferentes estrategias de matching
            # 1. Coincidencia exacta
            if nombre_plantilla == nombre_partido_upper:
                jugador['dorsal'] = dorsal
                dorsales_mapeados += 1
                logger.debug(f"Dorsal mapeado (exacto): {nombre_plantilla} -> {dorsal}")
                break

            # 2. Coincidencia por apellido (primera palabra)
            apellido_plantilla = nombre_plantilla.split()[0] if nombre_plantilla else ""
            apellido_partido = nombre_partido_upper.split(',')[0].strip() if ',' in nombre_partido_upper else nombre_partido_upper.split()[0]

            if apellido_plantilla and apellido_partido and apellido_plantilla == apellido_partido:
                jugador['dorsal'] = dorsal
                dorsales_mapeados += 1
                logger.debug(f"Dorsal mapeado (apellido): {nombre_plantilla} -> {dorsal}")
                break

    logger.info(f"✓ Dorsales mapeados: {dorsales_mapeados}/{len(plantilla)} jugadores")
    return plantilla


def generar_calendario_ics(partidos: List[Dict]) -> None:
    """
    Genera archivo .ics con todos los partidos
    """
    logger.info("Generando archivo calendario .ics...")

    calendar = Calendar()
    calendar.creator = "Extramurs Calendar Bot"

    for partido in partidos:
        event = Event()

        # Título del evento (limpio, sin caracteres problemáticos)
        if partido.get('resultado'):
            titulo = f"{partido['local']} {partido['resultado']} {partido['visitante']}"
        else:
            titulo = f"{partido['local']} vs {partido['visitante']}"

        # Limpiar título de caracteres problemáticos
        titulo = titulo.replace('\n', ' ').replace('\r', ' ')
        event.name = titulo

        # Fecha y hora
        if partido.get('fecha') and partido.get('hora'):
            fecha_str = f"{partido['fecha']} {partido['hora']}"
            try:
                evento_dt = datetime.strptime(fecha_str, "%Y-%m-%d %H:%M")
                event.begin = evento_dt
                event.duration = {"hours": 1}  # Duración estimada de 1 hora
            except ValueError:
                logger.warning(f"No se pudo parsear fecha/hora: {fecha_str}")
                continue

        # Descripción simplificada (sin URLs largas que puedan causar problemas)
        campo = partido.get('campo', 'Por determinar')
        descripcion = f"Campo: {campo}"

        # Añadir jornada si existe
        if partido.get('jornada'):
            descripcion = f"Jornada {partido['jornada']}\n{descripcion}"

        # URL de Maps como campo separado (más compatible)
        if partido.get('maps_url'):
            event.url = partido['maps_url']

        event.description = descripcion

        # Ubicación (limpiar caracteres problemáticos)
        ubicacion = campo.replace('\n', ' ').replace('\r', ' ')
        event.location = ubicacion

        calendar.events.add(event)

    # Guardar archivo con encoding UTF-8 + BOM para mejor compatibilidad
    calendar_content = str(calendar)

    # Añadir el nombre del calendario (X-WR-CALNAME) después del PRODID
    calendar_name = f"{TEAM_NAME} - {GRUPO.split(' - ')[0]}"
    calendar_lines = calendar_content.split('\n')

    # Insertar X-WR-CALNAME después de PRODID
    for i, line in enumerate(calendar_lines):
        if line.startswith('PRODID:'):
            calendar_lines.insert(i + 1, f'X-WR-CALNAME:{calendar_name}')
            break

    calendar_content = '\n'.join(calendar_lines)

    with open(OUTPUT_ICS, 'w', encoding='utf-8-sig') as f:
        f.write(calendar_content)

    logger.info(f"✓ Calendario guardado en {OUTPUT_ICS} ({len(calendar.events)} eventos)")


def generar_json(data: Dict) -> None:
    """
    Guarda los datos en JSON
    """
    logger.info("Generando archivo JSON...")

    OUTPUT_JSON.parent.mkdir(exist_ok=True)

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"✓ JSON guardado en {OUTPUT_JSON}")


def generar_html_desde_template(template_name: str, output_path: Path, context: Dict) -> None:
    """
    Genera un archivo HTML desde un template Jinja2
    """
    logger.info(f"Generando {output_path.name} desde template...")

    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template(template_name)

    html = template.render(**context)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    logger.info(f"✓ HTML generado en {output_path}")


def generar_google_calendar_url(ics_url: str) -> str:
    """
    Genera URL para añadir a Google Calendar
    """
    return f"https://calendar.google.com/calendar/r?cid={quote(ics_url)}"


def encontrar_proximo_partido(partidos: List[Dict]) -> Optional[Dict]:
    """
    Encuentra el próximo partido pendiente (ordenado por fecha)
    """
    hoy = datetime.now().date()

    # Filtrar solo partidos futuros sin resultado
    partidos_futuros = []
    for partido in partidos:
        if partido.get('fecha') and not partido.get('resultado'):
            try:
                fecha_partido = datetime.strptime(partido['fecha'], "%Y-%m-%d").date()
                if fecha_partido >= hoy:
                    partidos_futuros.append(partido)
            except ValueError:
                continue

    # Ordenar por fecha y hora
    if partidos_futuros:
        partidos_futuros.sort(key=lambda x: (x.get('fecha', ''), x.get('hora', '')))
        return partidos_futuros[0]

    return None


def _hay_datos_previos(path: Path) -> bool:
    """Devuelve True si `path` existe y contiene partidos en `todos_partidos`."""
    if not path.exists():
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    return bool(data.get("todos_partidos"))


def _cod_jornada_mas_reciente(cod_grupo: str) -> str:
    """
    Devuelve el `codjornada` con la `fecha_jornada` más reciente que no esté en
    el futuro. Si todas las jornadas son futuras (temporada no empezada) usa la
    primera; si todas son pasadas (temporada terminada) usa la última.
    """
    data = fetch_json("filtros/jornadas_fetch.php", {"cod_grupo": cod_grupo})
    jornadas = data.get("jornadas") or []
    if not jornadas:
        raise FFCVAPIError(f"No hay jornadas para cod_grupo={cod_grupo}")

    hoy = datetime.now().date()
    seleccionada = None
    for j in jornadas:
        fecha_dt = parse_spanish_date(j.get("fecha_jornada") or "")
        if not fecha_dt:
            continue
        if fecha_dt.date() <= hoy:
            seleccionada = j  # la más reciente que cumple la condición
    if seleccionada is None:
        seleccionada = jornadas[0]
    return str(seleccionada.get("codjornada"))


def process_team(solo_json: bool = False):
    """
    Procesa un equipo individual usando las variables globales que `setup_globals`
    haya inicializado.

    Args:
        solo_json: si es True, sólo escribe `data/<slug>.json` y omite ICS y
            las plantillas HTML. Es el modo usado por el bucle de discovery
            (Fase 2): los 15 equipos nuevos aún no tienen UI propia.
    """

    try:
        # 1. Calendario y partidos del equipo (itera jornadas del grupo).
        logger.info("\n[1/6] Obteniendo calendario vía API...")
        partidos = obtener_partidos_via_api(COD_GRUPO, COD_EQUIPO)

        # Circuit breaker: si la API devuelve cero partidos pero ya tenemos
        # datos previos válidos, abortar SIN sobrescribir. Esto evita repetir
        # el bug de mayo 2026, en el que el scraper antiguo silenciosamente
        # vació los JSON cuando el portal cambió de estructura.
        if not partidos and _hay_datos_previos(OUTPUT_JSON):
            raise FFCVAPIError(
                f"La API no devolvió partidos para cod_equipo={COD_EQUIPO} en "
                f"cod_grupo={COD_GRUPO}, pero {OUTPUT_JSON.name} existente contiene "
                f"datos. Abortando para no perder información."
            )

        # 2. Clasificación a fecha de la última jornada del grupo.
        cod_jornada_actual = _cod_jornada_mas_reciente(COD_GRUPO)
        logger.info(f"\n[2/6] Obteniendo clasificación (jornada {cod_jornada_actual})...")
        clasificacion = obtener_clasificacion_via_api(COD_GRUPO, cod_jornada_actual)

        # 3. Cosechar dorsales + fotos desde las actas de los últimos partidos.
        # Va antes de obtener_plantilla_via_api para que la plantilla recoja
        # las fotos recién guardadas en el mismo run.
        logger.info("\n[3/6] Cosechando dorsales y fotos desde actas...")
        dorsales = obtener_dorsales_via_api(partidos)

        # 4. Plantilla (nombres + fotos cacheadas en disco).
        logger.info("\n[3.5/6] Obteniendo plantilla vía API...")
        plantilla = obtener_plantilla_via_api(COD_EQUIPO)
        plantilla = mapear_dorsales_a_plantilla(plantilla, dorsales)

        # 5. Preparar datos derivados.
        logger.info("\n[4/6] Procesando datos...")

        # Encontrar próximo partido
        proximo_partido = encontrar_proximo_partido(partidos)

        # Partidos jugados = fecha ya pasó (independiente de si tiene resultado)
        hoy = datetime.now().date()
        partidos_jugados = []
        for p in partidos:
            if p.get('fecha'):
                try:
                    fecha_partido = datetime.strptime(p['fecha'], '%Y-%m-%d').date()
                    if fecha_partido < hoy:
                        partidos_jugados.append(p)
                except ValueError:
                    pass

        # Últimos 5 resultados (solo mostrar los que tienen resultado para el dashboard)
        partidos_con_resultado = [p for p in partidos_jugados if p.get('resultado')]
        ultimos_resultados = sorted(
            partidos_con_resultado,
            key=lambda x: x.get('fecha', ''),
            reverse=True
        )[:5]

        # Calcular racha visual (últimos 5 partidos)
        racha = []
        for partido in ultimos_resultados:
            if partido.get('victoria') is True:
                racha.append('W')
            elif partido.get('victoria') is False:
                racha.append('L')
            else:
                racha.append('D')  # Empate
        racha.reverse()  # Mostrar del más antiguo al más reciente

        # Determinar posición del equipo y mensaje motivacional
        posicion_equipo = None
        total_equipos = len(clasificacion)
        mensaje_motivacional = None

        for equipo_data in clasificacion:
            if TEAM_NAME in equipo_data.get('equipo', '') or 'Extramurs' in equipo_data.get('equipo', ''):
                posicion_equipo = equipo_data.get('posicion')
                break

        # Si está en último lugar, añadir mensaje motivacional
        if posicion_equipo and posicion_equipo == total_equipos:
            mensaje_motivacional = "¡Cada partido es una oportunidad para mejorar! 💪 La temporada recién empieza."

        # Estructura de datos completa
        data = {
            "equipo": TEAM_NAME,
            "grupo": GRUPO,
            "ultima_actualizacion": datetime.now().isoformat(),
            "proximo_partido": proximo_partido,
            "ultimos_resultados": ultimos_resultados,
            "clasificacion": clasificacion,
            "todos_partidos": partidos
        }

        # 6. Generar archivos.
        logger.info("\n[5/6] Generando archivos de salida...")

        # JSON: siempre.
        generar_json(data)

        if solo_json:
            # Modo discovery (Fase 2): los equipos sin UI propia se quedan aquí.
            logger.info(
                f"✓ JSON-only: omitido ICS/HTML para slug={CONFIG['equipo']['nombre_corto']}"
            )
            return

        # Calendario ICS
        generar_calendario_ics(partidos)

        # URL del calendario desde configuración
        base_url = CONFIG['sitio']['url_base']
        output_subdir = CONFIG['sitio']['output_dir']
        ics_url = f"{base_url}/{output_subdir}/partidos.ics"
        webcal_url = ics_url.replace("https://", "webcal://")
        google_calendar_url = generar_google_calendar_url(ics_url)

        # Calcular si el próximo partido es en menos de 24h
        partido_urgente = False
        if proximo_partido and proximo_partido.get('fecha') and proximo_partido.get('hora'):
            try:
                fecha_hora_str = f"{proximo_partido['fecha']} {proximo_partido['hora']}"
                fecha_hora_partido = datetime.strptime(fecha_hora_str, "%Y-%m-%d %H:%M")
                tiempo_restante = fecha_hora_partido - datetime.now()
                partido_urgente = tiempo_restante.total_seconds() < 86400  # 24 horas en segundos
            except ValueError:
                pass

        # Context para templates (con rutas relativas desde output_dir)
        context = {
            'equipo': TEAM_NAME,
            'grupo': GRUPO,
            'logo': f"../{CONFIG['equipo']['logo']}" if CONFIG['equipo']['logo'] else '',
            'background': f"../{CONFIG['equipo']['background']}" if CONFIG['equipo'].get('background') else '',
            'temporada': CONFIG['sitio']['temporada'],
            'ultima_actualizacion': datetime.now().strftime("%d/%m/%Y - %H:%M"),
            'proximo_partido': proximo_partido,
            'partido_urgente': partido_urgente,
            'ultimos_resultados': ultimos_resultados,
            'racha': racha,
            'clasificacion': clasificacion,
            'posicion_equipo': posicion_equipo,
            'mensaje_motivacional': mensaje_motivacional,
            'total_partidos': len(partidos),
            'partidos_jugados': len(partidos_jugados),
            'todos_partidos': partidos,  # Para el calendario interactivo
            'plantilla': plantilla,
            'ics_url': ics_url,
            'webcal_url': webcal_url,
            'google_calendar_url': google_calendar_url
        }

        # Página principal (fusión de landing + dashboard)
        generar_html_desde_template('dashboard_template.html', OUTPUT_INDEX, context)

        # Página de plantilla
        generar_html_desde_template('plantilla_template.html', OUTPUT_PLANTILLA, context)

        # 7. Resumen final.
        logger.info("\n[6/6] Proceso completado exitosamente!")
        logger.info("=" * 60)
        logger.info(f"✓ Partidos scrapeados: {len(partidos)}")
        logger.info(f"✓ Partidos jugados: {len(partidos_jugados)}")
        logger.info(f"✓ Equipos en clasificación: {len(clasificacion)}")
        logger.info(f"✓ Jugadores en plantilla: {len(plantilla)}")
        logger.info(f"✓ Archivos generados:")
        logger.info(f"  - {OUTPUT_JSON}")
        logger.info(f"  - {OUTPUT_ICS}")
        logger.info(f"  - {OUTPUT_INDEX}")
        logger.info(f"  - {OUTPUT_PLANTILLA}")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"\n❌ Error crítico: {str(e)}", exc_info=True)
        raise


def build_config_descubrimiento(equipo: Dict, club_config: Dict) -> Dict:
    """
    Construye un config sintético compatible con setup_globals/process_team a
    partir de una entrada de `club_map.json` y la configuración del club.

    Estos configs sintéticos son los que usa el bucle de discovery: los
    equipos descubiertos generan tanto `data/<slug>.json` como su directorio
    `<slug>/` con index.html + partidos.ics + plantilla.html.
    """
    slug = equipo["slug"]
    nombre_equipo = equipo.get("nombre_equipo") or f"Equipo {slug}"

    # Categoría legible para el título de página, combinada con la letra cuando
    # haya varios equipos del club en la misma categoría.
    titulo_pagina = (
        f"{nombre_equipo} ({equipo.get('categoria') or slug})"
        if equipo.get("categoria") else nombre_equipo
    )

    # Background por convención: si existe Images/bg-<slug>.jpg en el repo,
    # se usa. Si no, sin fondo (el template ya hace `{% if background %}`).
    background_relativo = f"Images/bg-{slug}.jpg"
    background = (
        background_relativo
        if (BASE_DIR / background_relativo).exists()
        else ""
    )

    return {
        "equipo": {
            "nombre": titulo_pagina,
            # nombre_corto se usa como nombre de fichero JSON; para discovery
            # queremos slug directamente (alevin-a.json, etc.).
            "nombre_corto": slug,
            "grupo": equipo.get("nombre_grupo") or "",
            # Por defecto usa el logo del club; configs/<slug>.yaml puede
            # sobreescribirlo cuando un equipo quiera escudo propio.
            "logo": "Images/extramurs.jpg",
            "background": background,
        },
        "ids_ffcv": {
            "temporada": club_config["temporada"]["codigo"],
            "torneo": equipo["cod_grupo"],
            "equipo": equipo["codequipo"],
        },
        "sitio": {
            "url_base": club_config["sitio"]["url_base"],
            "temporada": club_config["temporada"]["nombre"],
            "output_dir": slug,
            "images_dir": f"Images/plantilla-{slug}",
        },
    }


def procesar_club(club_config: Dict) -> List[Dict]:
    """
    Bucle Fase 2: descubre los equipos del club y genera `data/<slug>.json`
    para cada uno. Devuelve la lista de equipos del club_map para que el
    llamador pueda usar la info en pasos posteriores (Fase 3+).
    """
    club_map = cargar_o_descubrir_club_map(
        clave_acceso=str(club_config["club"]["clave_acceso"]),
        cod_temporada=str(club_config["temporada"]["codigo"]),
        cache_path=DATA_DIR / "club_map.json",
    )

    equipos = club_map.get("equipos") or []
    logger.info(f"\n🔭 Procesando {len(equipos)} equipos del club via discovery...\n")

    for idx, equipo in enumerate(equipos, 1):
        slug = equipo["slug"]
        logger.info("-" * 60)
        logger.info(
            f"[{idx}/{len(equipos)}] {slug:25s} ({equipo.get('categoria')})"
        )
        logger.info("-" * 60)
        try:
            cfg = build_config_descubrimiento(equipo, club_config)
            setup_globals(cfg)
            process_team()
        except FFCVAPIError as e:
            logger.warning(f"Saltando {slug} por error de API: {e}")
        except Exception as e:
            logger.error(f"Error procesando {slug}: {e}", exc_info=True)

    return equipos


# ---------------------------------------------------------------------------
# Home del club (Fase 4): agregación y renderizado
# ---------------------------------------------------------------------------

# Orden canónico para presentar las categorías en la home antes de cualquier
# personalización client-side. Categorías mayores arriba.
_ORDEN_CATEGORIAS = [
    "infantil", "alevin", "benjamin", "prebenjamin", "querubines",
]


def _orden_default_equipos(equipos: List[Dict]) -> List[Dict]:
    """Ordena por raíz de categoría según _ORDEN_CATEGORIAS, luego por letra."""
    def key(e: Dict):
        slug = e.get("slug") or ""
        raiz = slug.split("-")[0]
        try:
            idx = _ORDEN_CATEGORIAS.index(raiz)
        except ValueError:
            idx = len(_ORDEN_CATEGORIAS)
        return (idx, slug)
    return sorted(equipos, key=key)


def _parse_partido_dt(partido: Dict) -> Optional[datetime]:
    fecha = partido.get("fecha")
    hora = partido.get("hora") or "00:00"
    if not fecha:
        return None
    try:
        return datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M")
    except ValueError:
        try:
            return datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError:
            return None


def _load_team_data(slug: str) -> Optional[Dict]:
    path = DATA_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def construir_context_home(club_config: Dict, club_map: Dict) -> Dict:
    """
    Lee club_map.json y los `data/<slug>.json` y construye el contexto para
    `home_template.html`: lista de tarjetas + resultados del último finde +
    próximos partidos con coordenadas para el mapa.
    """
    hoy = datetime.now().date()
    inicio_ventana_pasada = hoy - timedelta(days=7)
    fin_ventana_futura = hoy + timedelta(days=7)

    tarjetas: List[Dict] = []
    todos_partidos_pasados: List[Dict] = []
    todos_partidos_futuros: List[Dict] = []

    equipos = _orden_default_equipos(club_map.get("equipos") or [])

    for equipo in equipos:
        slug = equipo["slug"]
        data = _load_team_data(slug)
        if data is None:
            logger.warning(f"Home: no encuentro data/{slug}.json — salto tarjeta")
            continue

        partidos = data.get("todos_partidos") or []
        prox = data.get("proximo_partido")
        ultimos = data.get("ultimos_resultados") or []
        clasif = data.get("clasificacion") or []

        # Racha de los últimos 5 (W/L/D), del más antiguo al más reciente
        racha = []
        for p in sorted(ultimos, key=lambda x: (x.get("fecha") or "", x.get("hora") or "")):
            if p.get("victoria") is True:
                racha.append("W")
            elif p.get("victoria") is False:
                racha.append("L")
            else:
                racha.append("D")

        # Posición del equipo en la clasificación
        posicion = None
        total_equipos_grupo = len(clasif)
        for fila in clasif:
            if "Extramurs" in (fila.get("equipo") or ""):
                posicion = fila.get("posicion")
                break

        tarjetas.append({
            "slug": slug,
            "letra": (equipo.get("letra") or "").upper(),
            "categoria": equipo.get("categoria") or "",
            "categoria_raiz": slug.split("-")[0],
            "nombre_grupo": equipo.get("nombre_grupo") or "",
            "anyo_categoria": equipo.get("anyo_categoria"),
            "modalidad": equipo.get("nombre_grupo_categoria") or "",
            "escudo": equipo.get("escudo") or "",
            "campo_juego": equipo.get("campo_juego") or "",
            "jugar_dia": equipo.get("jugar_dia"),
            "jugar_horario": equipo.get("jugar_horario") or "",
            "proximo_partido": prox,
            "ultimo_resultado": ultimos[0] if ultimos else None,
            "racha": racha,
            "posicion": posicion,
            "total_equipos_grupo": total_equipos_grupo,
            "total_partidos": len(partidos),
            "total_jugados": sum(1 for p in partidos if p.get("resultado")),
        })

        # Partidos para las secciones globales
        for partido in partidos:
            dt = _parse_partido_dt(partido)
            if not dt:
                continue
            fecha_d = dt.date()
            registro = {
                **partido,
                "slug_equipo": slug,
                "letra_equipo": (equipo.get("letra") or "").upper(),
                "categoria_equipo": equipo.get("categoria") or "",
                "fecha_dt": dt.isoformat(),
            }
            if inicio_ventana_pasada <= fecha_d <= hoy and partido.get("resultado"):
                todos_partidos_pasados.append(registro)
            elif hoy <= fecha_d <= fin_ventana_futura and not partido.get("resultado"):
                todos_partidos_futuros.append(registro)

    # Ordenar las secciones
    todos_partidos_pasados.sort(key=lambda p: p["fecha_dt"], reverse=True)
    todos_partidos_futuros.sort(key=lambda p: p["fecha_dt"])

    # Resolver coordenadas de campos vía API FFCV (con caché en disco)
    coords_campos = resolver_coordenadas_campos(
        todos_partidos_futuros, DATA_DIR / "campos.json"
    )

    # Enriquecer próximos con coords; agrupar por campo para los marcadores
    marcadores_mapa: Dict[str, Dict] = {}
    for p in todos_partidos_futuros:
        campo = p.get("campo") or ""
        coord = coords_campos.get(campo) or {}
        p["lat"] = coord.get("lat")
        p["lon"] = coord.get("lon")
        if coord.get("lat") and coord.get("lon"):
            clave = f"{coord['lat']:.5f},{coord['lon']:.5f}"
            if clave not in marcadores_mapa:
                marcadores_mapa[clave] = {
                    "lat": coord["lat"],
                    "lon": coord["lon"],
                    "campo": campo,
                    "partidos": [],
                }
            marcadores_mapa[clave]["partidos"].append({
                "slug": p["slug_equipo"],
                "letra": p.get("letra_equipo") or "",
                "categoria": p.get("categoria_equipo") or "",
                "fecha": p.get("fecha"),
                "hora": p.get("hora"),
                "local": p.get("local"),
                "visitante": p.get("visitante"),
            })

    return {
        "club_nombre": club_config["club"]["nombre"],
        "temporada": club_config["temporada"]["nombre"],
        "url_base": club_config["sitio"]["url_base"],
        "ultima_actualizacion": datetime.now().strftime("%d/%m/%Y - %H:%M"),
        "tarjetas": tarjetas,
        "resultados_finde": todos_partidos_pasados,
        "proximos_partidos": todos_partidos_futuros,
        "marcadores_mapa": list(marcadores_mapa.values()),
    }


def generar_home(club_config: Dict, club_map: Dict) -> None:
    """Renderiza `index.html` raíz con el grid de equipos, resultados y mapa."""
    logger.info("\n🏠 Generando home global del club...")
    context = construir_context_home(club_config, club_map)

    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template("home_template.html")
    html = template.render(**context)

    out_path = BASE_DIR / "index.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(
        f"✓ Home generado: {out_path} "
        f"({len(context['tarjetas'])} tarjetas, "
        f"{len(context['resultados_finde'])} resultados, "
        f"{len(context['proximos_partidos'])} próximos, "
        f"{len(context['marcadores_mapa'])} marcadores)"
    )


def main():
    """
    Función principal - procesa todos los equipos configurados
    """
    logger.info("=" * 60)
    logger.info("🏆 Extramurs Calendar Automation - Multi-Team Scraper")
    logger.info("=" * 60)

    try:
        club_config = load_club_config()
        if not club_config:
            raise RuntimeError(
                "Falta configs/_club.yaml: define {club: {clave_acceso, ...}, "
                "temporada: {codigo}, sitio: {url_base}} para arrancar el discovery."
            )

        procesar_club(club_config)

        # Releer el club_map ya escrito para alimentar la home (en caso de que
        # algún equipo haya quedado sin resolver y se haya saltado durante el
        # procesado, evitamos referenciarlo en las tarjetas).
        with open(DATA_DIR / "club_map.json", "r", encoding="utf-8") as f:
            club_map = json.load(f)
        generar_home(club_config, club_map)

        logger.info("\n" + "=" * 60)
        logger.info("✅ Procesamiento completado")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"\n❌ Error crítico: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
