#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extramurs Calendar Automation - Scraper
Scrapea la web de FFCV para obtener partidos y clasificación del equipo
"""

import base64
import json
import logging
import re
import time
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote, urlencode

from bs4 import BeautifulSoup
from dateutil import parser
from ics import Calendar, Event
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from PIL import Image
import io
import requests

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


def load_config() -> Dict:
    """
    Carga la configuración desde config.yaml
    """
    config_path = BASE_DIR / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(
            f"❌ No se encontró el archivo de configuración: {config_path}\n"
            "Por favor, crea un archivo config.yaml basado en config.yaml.example"
        )

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    logger.info(f"✓ Configuración cargada para: {config['equipo']['nombre']}")
    return config


def build_url(base_url: str, params: Dict) -> str:
    """
    Construye una URL con parámetros desde la configuración
    """
    query_string = urlencode(params)
    return f"{base_url}?{query_string}"


# Cargar configuración global
CONFIG = load_config()

# Extraer valores de configuración para fácil acceso
TEAM_NAME = CONFIG['equipo']['nombre']
TEAM_SHORT_NAME = CONFIG['equipo']['nombre_corto']
GRUPO = CONFIG['equipo']['grupo']

# Construir URLs dinámicamente desde config
ids = CONFIG['ids_ffcv']
params_base = {
    'id_temp': ids['temporada'],
    'id_modalidad': ids['modalidad'],
    'id_competicion': ids['competicion'],
    'id_torneo': ids['torneo']
}

URL_CALENDARIO = build_url(CONFIG['urls']['base_calendario'], params_base)
URL_CLASIFICACION = build_url(CONFIG['urls']['base_clasificacion'], params_base)

params_plantilla = {**params_base, 'id_equipo': ids['equipo'], 'torneo_equipo': ''}
URL_PLANTILLA = build_url(CONFIG['urls']['base_plantilla'], params_plantilla)

# Constantes de scraping desde config
MAX_RETRIES = CONFIG['scraping']['max_reintentos']
RETRY_DELAY = CONFIG['scraping']['delay_reintento']
TIMEOUT = CONFIG['scraping']['timeout_pagina']


def fetch_page_with_retry(url: str, max_retries: int = MAX_RETRIES) -> str:
    """
    Obtiene el HTML de una página usando Playwright con reintentos
    """
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Intentando obtener {url} (intento {attempt}/{max_retries})")

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

                # Navegar a la página
                page.goto(url, timeout=TIMEOUT, wait_until="networkidle")

                # Esperar un poco para asegurar que todo cargue
                page.wait_for_timeout(2000)

                # Obtener el HTML
                html = page.content()
                browser.close()

                logger.info(f"✓ Página obtenida exitosamente")
                return html

        except PlaywrightTimeout:
            logger.warning(f"Timeout en intento {attempt}")
            if attempt < max_retries:
                logger.info(f"Esperando {RETRY_DELAY} segundos antes de reintentar...")
                time.sleep(RETRY_DELAY)
            else:
                raise Exception(f"No se pudo obtener la página después de {max_retries} intentos")
        except Exception as e:
            logger.error(f"Error en intento {attempt}: {str(e)}")
            if attempt < max_retries:
                time.sleep(RETRY_DELAY)
            else:
                raise


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


def scrape_calendario(html: str) -> List[Dict]:
    """
    Extrae los partidos del calendario
    Estructura HTML:
    <tr>
        <td class="p-t-20" width="40%">Equipos (separados por &nbsp;-&nbsp;)</td>
        <td class="centrado p-t-20" width="20%">Resultado (2 spans con números)</td>
        <td style="font-size: 12px; width: 10%">
            <div class="negrita">Fecha</div>
            <div>Hora</div>
        </td>
        <td class="negrita p-t-20" width="20%">Campo</td>
    </tr>
    """
    logger.info("Parseando calendario de partidos...")
    soup = BeautifulSoup(html, 'html.parser')
    partidos = []

    # Buscar todas las filas <tr> que contengan el nombre del equipo
    all_rows = soup.find_all('tr')

    for row in all_rows:
        try:
            texto_row = row.get_text()

            # Verificar si esta fila contiene nuestro equipo
            if TEAM_NAME not in texto_row and TEAM_SHORT_NAME not in texto_row:
                continue

            # Buscar las celdas específicas por su contenido y estructura
            # 1. Celda de equipos: tiene width: 40% y contiene 2 enlaces con nombres de equipos
            equipos_cell = None
            jornada = None
            id_partido = None

            for cell in row.find_all('td'):
                if cell.find_all('a') and len(cell.find_all('a')) >= 2:
                    # Verificar que tiene ambos equipos como links
                    links = cell.find_all('a')
                    if len(links) >= 2:
                        equipos_cell = cell

                        # Extraer jornada e id_partido del href del primer link
                        href = links[0].get('href', '')
                        jornada_match = re.search(r'jornada=(\d+)', href)
                        if jornada_match:
                            jornada = int(jornada_match.group(1))

                        # Extraer id_partido del href
                        id_partido_match = re.search(r'id_partido=(\d+)', href)
                        if id_partido_match:
                            id_partido = id_partido_match.group(1)

                        break

            if not equipos_cell:
                continue

            equipos_links = equipos_cell.find_all('a')
            local = equipos_links[0].get_text(strip=True)
            visitante = equipos_links[1].get_text(strip=True)

            # 2. Celda de resultado: tiene clase "centrado p-t-20" y contiene spans con números
            resultado = None
            resultado_cell = row.find('td', class_='centrado')
            if resultado_cell:
                resultado_spans = resultado_cell.find_all('span')
                if len(resultado_spans) >= 2:
                    goles_local = resultado_spans[0].get_text(strip=True)
                    goles_visitante = resultado_spans[1].get_text(strip=True)

                    # Si ambos tienen contenido numérico
                    if goles_local and goles_visitante and goles_local.isdigit() and goles_visitante.isdigit():
                        resultado = f"{goles_local}-{goles_visitante}"

            # 3. Celda de fecha y hora: tiene style="font-size: 12px" y contiene 2 divs
            fecha = None
            hora = None
            fecha_cell = row.find('td', style=lambda x: x and 'font-size: 12px' in x)
            if fecha_cell:
                fecha_divs = fecha_cell.find_all('div')
                if len(fecha_divs) >= 2:
                    fecha_str = fecha_divs[0].get_text(strip=True)
                    hora_str = fecha_divs[1].get_text(strip=True)

                    # Parsear fecha
                    fecha_dt = parse_spanish_date(fecha_str)
                    if fecha_dt:
                        fecha = fecha_dt.strftime("%Y-%m-%d")

                    hora = hora_str

            # 4. Campo: última celda con clase "negrita p-t-20"
            campo = ''
            campo_cells = row.find_all('td', class_='negrita p-t-20')
            if campo_cells:
                # La última celda con esta clase suele ser el campo
                campo_cell = campo_cells[-1]
                campo = campo_cell.get_text(strip=True)

            # Limpiar el texto del campo (quitar el ícono de glyphicon)
            campo = re.sub(r'\s+', ' ', campo).strip()

            # Generar URL de Google Maps
            maps_url = None
            if campo:
                # Añadir ", Valencia, España" para mejor precisión
                search_query = f"{campo}, Valencia, España"
                maps_url = f"https://www.google.com/maps/search/?api=1&query={quote(search_query)}"

            # Determinar si es local o visitante
            es_local = TEAM_NAME in local or TEAM_SHORT_NAME in local

            # Determinar victoria/derrota si hay resultado
            victoria = None
            if resultado:
                goles = resultado.split('-')
                if len(goles) == 2:
                    if es_local:
                        victoria = int(goles[0]) > int(goles[1])
                    else:
                        victoria = int(goles[1]) > int(goles[0])

            partido = {
                'jornada': jornada,
                'id_partido': id_partido,
                'fecha': fecha,
                'hora': hora,
                'local': local,
                'visitante': visitante,
                'campo': campo,
                'resultado': resultado,
                'es_local': es_local,
                'victoria': victoria,
                'maps_url': maps_url
            }

            partidos.append(partido)
            logger.debug(f"Partido extraído: {local} vs {visitante} - {fecha} {hora}")

        except Exception as e:
            logger.warning(f"Error parseando fila de partido: {str(e)}")
            continue

    logger.info(f"✓ Extraídos {len(partidos)} partidos")
    return partidos


def scrape_clasificacion(html: str) -> List[Dict]:
    """
    Extrae la tabla de clasificación
    Estructura HTML:
    <table class="table clasificacion">
      <tr>
        <td class="bloque_collapse_flecha noprint">...</td>
        <td class="celda_peque p-t-15">Posición</td>
        <td class="p-t-15"><a class="equipo_tabla-clasi">Nombre</a></td>
        <td class="centrado p-t-15"><span>PJ</span></td>
        <td class="centrado p-t-15"><span>PG</span>...</td>
        <td class="centrado p-t-15"><span>PE</span>...</td>
        <td class="centrado p-t-15"><span>PP</span>...</td>
        <td class="centrado p-t-15">GF...</td>
        <td class="centrado p-t-15">GC...</td>
        <td class="centrado p-t-15">DIF</td>
        <td class="negrita centrado p-t-15">Puntos</td>
      </tr>
    </table>
    """
    logger.info("Parseando clasificación...")
    soup = BeautifulSoup(html, 'html.parser')
    clasificacion = []

    # Buscar la tabla con clase "clasificacion"
    table = soup.find('table', class_='clasificacion')

    if not table:
        logger.warning("No se encontró la tabla de clasificación")
        return clasificacion

    # Buscar todas las filas del tbody
    tbody = table.find('tbody')
    if not tbody:
        logger.warning("No se encontró tbody en la tabla de clasificación")
        return clasificacion

    rows = tbody.find_all('tr', style=lambda x: x and 'background: #fbfbfb' in x)

    for row in rows:
        try:
            # Buscar celdas específicas
            cells = row.find_all('td')

            if len(cells) < 10:
                continue

            # 1. Posición: celda con clase "celda_peque p-t-15"
            pos_cell = row.find('td', class_='celda_peque p-t-15')
            if not pos_cell:
                continue

            posicion = int(pos_cell.get_text(strip=True))

            # 2. Nombre del equipo: link con clase "equipo_tabla-clasi"
            equipo_link = row.find('a', class_='equipo_tabla-clasi')
            if not equipo_link:
                continue

            equipo = equipo_link.get_text(strip=True)

            # 3. Extraer datos de las celdas centradas
            centrado_cells = row.find_all('td', class_='centrado p-t-15')

            if len(centrado_cells) < 7:
                continue

            # PJ - Partidos Jugados (1ra celda centrada, tiene span)
            pj_span = centrado_cells[0].find('span')
            pj = int(pj_span.get_text(strip=True)) if pj_span else 0

            # PG - Partidos Ganados (2da celda centrada, tiene span)
            pg_span = centrado_cells[1].find('span')
            pg = int(pg_span.get_text(strip=True)) if pg_span else 0

            # PE - Partidos Empatados (3ra celda centrada, tiene span)
            pe_span = centrado_cells[2].find('span')
            pe = int(pe_span.get_text(strip=True)) if pe_span else 0

            # PP - Partidos Perdidos (4ta celda centrada, tiene span)
            pp_span = centrado_cells[3].find('span')
            pp = int(pp_span.get_text(strip=True)) if pp_span else 0

            # 4. Puntos: última celda con clase "negrita centrado p-t-15"
            puntos_cell = row.find('td', class_='negrita centrado p-t-15')
            if not puntos_cell:
                continue

            puntos = int(puntos_cell.get_text(strip=True))

            equipo_data = {
                'posicion': posicion,
                'equipo': equipo,
                'puntos': puntos,
                'pj': pj,
                'pg': pg,
                'pe': pe,
                'pp': pp
            }

            clasificacion.append(equipo_data)
            logger.debug(f"Equipo extraído: {posicion}º {equipo} - {puntos} pts")

        except (ValueError, AttributeError) as e:
            logger.warning(f"Error parseando fila de clasificación: {str(e)}")
            continue

    logger.info(f"✓ Extraídos {len(clasificacion)} equipos de la clasificación")
    return clasificacion


def process_player_image(img_data: bytes, jugador_nombre: str) -> bytes:
    """
    Procesa la imagen del jugador: quita fondo y hace upscaling

    Args:
        img_data: Bytes de la imagen original
        jugador_nombre: Nombre del jugador (para logs)

    Returns:
        Bytes de la imagen procesada
    """
    try:
        # Verificar si el procesamiento está habilitado
        if not CONFIG.get('image_processing', {}).get('enabled', False):
            return img_data

        logger.debug(f"Procesando imagen de {jugador_nombre}...")

        # Cargar imagen original
        img = Image.open(io.BytesIO(img_data))
        original_size = img.size
        logger.debug(f"  Tamaño original: {original_size}")

        # 1. BACKGROUND REMOVAL usando remove.bg API
        if CONFIG['image_processing'].get('remove_background', False):
            api_key = CONFIG['image_processing'].get('removebg_api_key')
            if api_key:
                logger.debug(f"  Quitando fondo con remove.bg API...")
                try:
                    # Convertir imagen a base64 para la API
                    img_b64 = base64.b64encode(img_data).decode('utf-8')

                    # Llamar a remove.bg API
                    response = requests.post(
                        'https://api.remove.bg/v1.0/removebg',
                        headers={'X-Api-Key': api_key},
                        data={
                            'image_file_b64': img_b64,
                            'size': 'auto'
                        },
                        timeout=30
                    )

                    if response.status_code == 200:
                        # Cargar imagen sin fondo
                        img = Image.open(io.BytesIO(response.content))
                        logger.debug(f"  ✓ Fondo removido con remove.bg")
                    else:
                        logger.warning(f"  ⚠️  Error en remove.bg API: {response.status_code} - {response.text[:100]}")

                except Exception as e:
                    logger.warning(f"  ⚠️  Error llamando remove.bg API: {str(e)}")
            else:
                logger.debug(f"  ⊘ Background removal solicitado pero no hay API key configurada")

        # 2. UPSCALING
        if CONFIG['image_processing'].get('upscale', False):
            upscale_factor = CONFIG['image_processing'].get('upscale_factor', 2)
            new_size = (
                int(img.width * upscale_factor),
                int(img.height * upscale_factor)
            )
            logger.debug(f"  Upscaling {upscale_factor}x: {img.size} → {new_size}")
            # Usar LANCZOS para mejor calidad
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            logger.debug(f"  ✓ Upscaling completado")

        # 3. GUARDAR A BYTES
        output_format = CONFIG['image_processing'].get('output_format', 'PNG')
        output = io.BytesIO()
        img.save(output, format=output_format)
        processed_data = output.getvalue()

        size_before = len(img_data) / 1024
        size_after = len(processed_data) / 1024
        logger.debug(f"  Tamaño: {size_before:.1f}KB → {size_after:.1f}KB")
        logger.info(f"✓ Imagen procesada: {jugador_nombre}")

        return processed_data

    except Exception as e:
        logger.warning(f"Error procesando imagen de {jugador_nombre}: {str(e)}")
        logger.warning(f"Usando imagen original sin procesar")
        return img_data


def scrape_plantilla(html: str) -> List[Dict]:
    """
    Extrae la plantilla del equipo con fotos
    """
    logger.info("Procesando plantilla del equipo...")
    soup = BeautifulSoup(html, 'html.parser')

    plantilla = []

    # Buscar todos los jugadores
    jugadores_cards = soup.find_all('a', class_='card_jugador')

    # Crear directorio para imágenes si no existe
    PLANTILLA_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    for card in jugadores_cards:
        try:
            # Nombre del jugador
            nombre_tag = card.find('h4')
            if not nombre_tag:
                continue

            nombre = nombre_tag.get_text(strip=True).replace('<br>', ' ')

            # ID del jugador
            href = card.get('href', '')
            id_match = re.search(r'id_jugador=(\d+)', href)
            if not id_match:
                continue

            jugador_id = id_match.group(1)

            # Foto en base64
            img_tag = card.find('img', class_='card_imagen_jugador')
            foto_filename = f"jugador_{jugador_id}.png"
            foto_path = PLANTILLA_IMAGES_DIR / foto_filename

            # Verificar si la imagen ya existe (ya procesada con remove.bg)
            if foto_path.exists():
                logger.debug(f"✓ Imagen ya existe (sin re-procesar): {foto_filename}")
            elif img_tag and img_tag.get('src'):
                src = img_tag['src']
                if src.startswith('data:image'):
                    # Extraer el base64
                    try:
                        # Formato: data:image/png;base64,XXXXXX
                        header, encoded = src.split(',', 1)
                        img_data = base64.b64decode(encoded)

                        # Procesar imagen (remove background + upscale)
                        processed_data = process_player_image(img_data, nombre)

                        # Guardar imagen (PNG para transparencia)
                        with open(foto_path, 'wb') as f:
                            f.write(processed_data)

                        logger.debug(f"Foto guardada: {foto_filename}")

                    except Exception as e:
                        logger.warning(f"Error guardando foto de {nombre}: {str(e)}")
                        foto_filename = None
            else:
                foto_filename = None

            jugador_data = {
                'id': jugador_id,
                'nombre': nombre,
                'foto': f"Images/plantilla/{foto_filename}" if foto_filename else None
            }

            plantilla.append(jugador_data)
            logger.debug(f"Jugador extraído: {nombre}")

        except Exception as e:
            logger.warning(f"Error parseando jugador: {str(e)}")
            continue

    logger.info(f"✓ Extraídos {len(plantilla)} jugadores de la plantilla")
    return plantilla


def scrape_dorsales_partido(html: str) -> Dict[str, str]:
    """
    Extrae los dorsales de los jugadores de una página de partido

    Estructura HTML:
    <span style="font-size: 16px; color: #ffa500;">10 </span>
    APELLIDO, NOMBRE

    Returns:
        Dict con nombre completo del jugador como key y dorsal como value
    """
    soup = BeautifulSoup(html, 'html.parser')
    dorsales = {}

    try:
        # Buscar todos los spans naranjas que contienen dorsales
        dorsal_spans = soup.find_all('span', style=lambda x: x and 'color: #ffa500' in x)

        for span in dorsal_spans:
            dorsal_text = span.get_text(strip=True)

            # Verificar que es un número (dorsal)
            if dorsal_text.isdigit():
                dorsal = dorsal_text

                # El nombre del jugador viene después del span
                # Buscar el siguiente texto después del span
                siguiente = span.next_sibling
                if siguiente and isinstance(siguiente, str):
                    nombre = siguiente.strip()

                    # Limpiar el nombre (viene como "APELLIDO, NOMBRE")
                    if nombre:
                        dorsales[nombre] = dorsal
                        logger.debug(f"Dorsal encontrado: {nombre} -> {dorsal}")

        logger.debug(f"Total dorsales extraídos: {len(dorsales)}")

    except Exception as e:
        logger.warning(f"Error extrayendo dorsales del partido: {str(e)}")

    return dorsales


def obtener_dorsales_de_partidos(partidos: List[Dict]) -> Dict[str, str]:
    """
    Obtiene dorsales scrapeando las páginas de partidos jugados

    Returns:
        Dict con nombre del jugador como key y dorsal como value
    """
    logger.info("Obteniendo dorsales de partidos jugados...")

    dorsales_acumulados = {}
    partidos_procesados = 0
    max_partidos = 3  # Procesar solo los últimos 3 partidos para no sobrecargar

    # Filtrar solo partidos jugados (con resultado)
    partidos_con_resultado = [p for p in partidos if p.get('resultado') and p.get('id_partido')]

    # Tomar los últimos partidos
    partidos_a_procesar = partidos_con_resultado[-max_partidos:]

    for partido in partidos_a_procesar:
        id_partido = partido.get('id_partido')
        if not id_partido:
            continue

        try:
            # Construir URL del partido
            params_partido = {
                **{k: v for k, v in zip(
                    ['id_temp', 'id_modalidad', 'id_competicion', 'id_torneo'],
                    [CONFIG['ids_ffcv']['temporada'], CONFIG['ids_ffcv']['modalidad'],
                     CONFIG['ids_ffcv']['competicion'], CONFIG['ids_ffcv']['torneo']]
                )},
                'id_partido': id_partido,
                'jornada': partido.get('jornada', '')
            }

            url_partido = build_url(CONFIG['urls']['base_partido'], params_partido)

            logger.info(f"  Scrapeando partido {partido['local']} vs {partido['visitante']}...")

            # Obtener HTML del partido
            html_partido = fetch_page_with_retry(url_partido)

            # Extraer dorsales
            dorsales_partido = scrape_dorsales_partido(html_partido)

            # Acumular dorsales (los más recientes sobrescriben los anteriores)
            dorsales_acumulados.update(dorsales_partido)

            partidos_procesados += 1

            # Delay para no sobrecargar el servidor
            time.sleep(2)

        except Exception as e:
            logger.warning(f"Error obteniendo dorsales del partido {id_partido}: {str(e)}")
            continue

    logger.info(f"✓ Dorsales obtenidos de {partidos_procesados} partidos: {len(dorsales_acumulados)} jugadores")
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
    with open(OUTPUT_ICS, 'w', encoding='utf-8-sig') as f:
        f.write(str(calendar))

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


def main():
    """
    Función principal del scraper
    """
    logger.info("=" * 60)
    logger.info("Iniciando Extramurs Calendar Automation Scraper")
    logger.info("=" * 60)

    try:
        # 1. Obtener HTML de las páginas
        logger.info("\n[1/7] Obteniendo páginas de FFCV...")
        html_calendario = fetch_page_with_retry(URL_CALENDARIO)
        time.sleep(2)  # Delay entre requests
        html_clasificacion = fetch_page_with_retry(URL_CLASIFICACION)
        time.sleep(2)  # Delay entre requests
        html_plantilla = fetch_page_with_retry(URL_PLANTILLA)

        # 2. Scrapear datos
        logger.info("\n[2/7] Scrapeando calendario...")
        partidos = scrape_calendario(html_calendario)

        logger.info("\n[3/7] Scrapeando clasificación...")
        clasificacion = scrape_clasificacion(html_clasificacion)

        logger.info("\n[4/7] Scrapeando plantilla...")
        plantilla = scrape_plantilla(html_plantilla)

        # Obtener dorsales de partidos jugados
        logger.info("\n[4.5/7] Obteniendo dorsales de partidos...")
        dorsales = obtener_dorsales_de_partidos(partidos)

        # Mapear dorsales a plantilla
        plantilla = mapear_dorsales_a_plantilla(plantilla, dorsales)

        # 3. Preparar datos
        logger.info("\n[5/7] Procesando datos...")

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

        # 4. Generar archivos
        logger.info("\n[6/7] Generando archivos de salida...")

        # JSON
        generar_json(data)

        # Calendario ICS
        generar_calendario_ics(partidos)

        # URL del calendario desde configuración
        base_url = CONFIG['sitio']['url_base']
        ics_url = f"{base_url}/partidos.ics"
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

        # Context para templates
        context = {
            'equipo': TEAM_NAME,
            'grupo': GRUPO,
            'logo': CONFIG['equipo']['logo'],
            'background': CONFIG['equipo'].get('background', ''),
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
            'plantilla': plantilla,
            'ics_url': ics_url,
            'webcal_url': webcal_url,
            'google_calendar_url': google_calendar_url
        }

        # Página principal (fusión de landing + dashboard)
        generar_html_desde_template('dashboard_template.html', OUTPUT_INDEX, context)

        # Página de plantilla
        generar_html_desde_template('plantilla_template.html', OUTPUT_PLANTILLA, context)

        # 5. Resumen final
        logger.info("\n[7/7] Proceso completado exitosamente!")
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


if __name__ == "__main__":
    main()
