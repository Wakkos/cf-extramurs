#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extramurs Calendar Automation - FFCV API client

Consume la API JSON pública de ffcv.es/competiciones/ (heredera del antiguo
portal resultadosffcv.isquad.es) para generar el calendario, clasificación y
plantilla de cada equipo configurado.
"""

import json
import logging
import re
import time
import yaml
from datetime import datetime
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


def load_config(config_path: Path) -> Dict:
    """
    Carga la configuración desde un archivo YAML
    """
    if not config_path.exists():
        raise FileNotFoundError(
            f"❌ No se encontró el archivo de configuración: {config_path}"
        )

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    logger.info(f"✓ Configuración cargada para: {config['equipo']['nombre']}")
    return config


def load_all_configs() -> List[Dict]:
    """
    Carga todas las configuraciones desde el directorio configs/
    """
    configs_dir = BASE_DIR / "configs"

    if not configs_dir.exists():
        # Fallback a config.yaml si no existe configs/
        logger.warning("⚠️  Directorio configs/ no encontrado, usando config.yaml")
        return [load_config(BASE_DIR / "config.yaml")]

    configs = []
    config_files = sorted(configs_dir.glob("*.yaml"))

    if not config_files:
        raise FileNotFoundError(
            f"❌ No se encontraron archivos de configuración en {configs_dir}"
        )

    for config_file in config_files:
        logger.info(f"📄 Cargando configuración: {config_file.name}")
        config = load_config(config_file)
        configs.append(config)

    return configs


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


def obtener_dorsales_via_api(partidos: List[Dict]) -> Dict[str, str]:
    """
    Obtiene los dorsales de los últimos partidos jugados consultando
    `api/partidos/ficha_partido_ajax.php?cod_partido=<codacta>`.

    Returns:
        Dict {nombre_jugador (tal como aparece en el acta) -> dorsal}.
        Los nombres en el acta vienen "APELLIDOS, NOMBRE" igual que antes,
        así que el mapeo a la plantilla (mapear_dorsales_a_plantilla) sigue
        funcionando sin cambios.
    """
    logger.info("Obteniendo dorsales de partidos jugados (API)...")

    dorsales_acumulados: Dict[str, str] = {}
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
                # Determinar si es nuestro equipo en este partido
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
                        logger.debug(f"Dorsal: {nombre} -> {dorsal}")

            procesados += 1
            time.sleep(0.5)

        except Exception as e:
            logger.warning(f"Error obteniendo dorsales de codacta={cod_partido}: {e}")
            continue

    logger.info(
        f"✓ Dorsales obtenidos de {procesados} partidos: {len(dorsales_acumulados)} jugadores"
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


def process_team():
    """
    Procesa un equipo individual (usa variables globales inicializadas por setup_globals)
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

        # 3. Plantilla (sólo nombres + foto cacheada si existe).
        logger.info("\n[3/6] Obteniendo plantilla vía API...")
        plantilla = obtener_plantilla_via_api(COD_EQUIPO)

        # 4. Dorsales desde las actas de los últimos partidos.
        logger.info("\n[3.5/6] Obteniendo dorsales de partidos...")
        dorsales = obtener_dorsales_via_api(partidos)
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

        # JSON
        generar_json(data)

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


def main():
    """
    Función principal - procesa todos los equipos configurados
    """
    logger.info("=" * 60)
    logger.info("🏆 Extramurs Calendar Automation - Multi-Team Scraper")
    logger.info("=" * 60)

    try:
        # Cargar todas las configuraciones
        configs = load_all_configs()
        logger.info(f"\n📋 {len(configs)} equipo(s) configurado(s)\n")

        # Procesar cada equipo
        for idx, config in enumerate(configs, 1):
            equipo_nombre = config['equipo']['nombre']
            logger.info("=" * 60)
            logger.info(f"⚽ Procesando {idx}/{len(configs)}: {equipo_nombre}")
            logger.info("=" * 60)

            # Inicializar variables globales para este equipo
            setup_globals(config)

            # Procesar el equipo
            process_team()

        logger.info("\n" + "=" * 60)
        logger.info(f"✅ Todos los equipos procesados exitosamente ({len(configs)} equipos)")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"\n❌ Error crítico: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
