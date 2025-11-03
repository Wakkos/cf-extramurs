# ⚽ Extramurs Calendar Automation

Sistema automatizado de gestión de partidos de fútbol prebenjamín que scrapea la web de la FFCV (Federación de Fútbol de la Comunidad Valenciana) y genera:

- 📅 **Calendario .ics** sincronizable con Google Calendar, iPhone, Outlook, etc.
- 🌐 **Landing page** con botones de suscripción al calendario
- 📊 **Dashboard** con estadísticas, resultados y clasificación del equipo
- 🤖 **Actualización automática diaria** mediante GitHub Actions

## 🔗 Enlaces Rápidos

- **Calendario**: [https://wakkos.github.io/extramurs-calendar-automation/](https://wakkos.github.io/extramurs-calendar-automation/)
- **Dashboard**: [https://wakkos.github.io/extramurs-calendar-automation/dashboard.html](https://wakkos.github.io/extramurs-calendar-automation/dashboard.html)

## 📋 Información del Equipo

- **Equipo**: C.F. Extramurs Valencia 'B'
- **Categoría**: Prebenjamín (Segona FFCV)
- **Grupo**: Segona FFCV Prebenjamí 2n. any València - Grup 12
- **Temporada**: 2024-2025

## 🚀 Características

### ✨ Para Familias
- **Sincronización automática**: Añade el calendario a tu móvil y recibe actualizaciones automáticas
- **Multiplataforma**: Compatible con Google Calendar, iPhone, Android, Outlook
- **Dashboard en tiempo real**: Consulta resultados, próximo partido y clasificación actualizada diariamente
- **Sin instalación**: Todo funciona desde el navegador

### 🛠️ Técnicas
- **Web Scraping con Playwright**: Navega la web de FFCV que bloquea requests simples
- **Generación de .ics**: Crea archivos de calendario estándar
- **Templates Jinja2**: Genera HTML dinámico desde plantillas
- **GitHub Actions**: Automatización completa sin servidor propio
- **GitHub Pages**: Hosting gratuito y confiable

## 📂 Estructura del Proyecto

```
extramurs-calendar-automation/
├── scraper.py              # Script principal de scraping
├── partidos.ics            # Calendario generado (auto-generado)
├── index.html              # Landing page (auto-generado)
├── dashboard.html          # Dashboard con estadísticas (auto-generado)
├── requirements.txt        # Dependencias Python
├── README.md              # Esta documentación
├── .gitignore             # Archivos ignorados por git
├── .github/
│   └── workflows/
│       └── update.yml     # GitHub Action para actualización diaria
├── templates/
│   ├── index_template.html      # Template Jinja2 para landing
│   └── dashboard_template.html  # Template Jinja2 para dashboard
└── data/
    └── partidos.json      # Datos scrapeados en JSON (auto-generado)
```

## 🔧 Instalación Local

### Requisitos Previos
- Python 3.11 o superior
- Git

### Pasos de Instalación

1. **Clonar el repositorio**
```bash
git clone https://github.com/Wakkos/extramurs-calendar-automation.git
cd extramurs-calendar-automation
```

2. **Crear entorno virtual (recomendado)**
```bash
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
```

3. **Instalar dependencias**
```bash
pip install -r requirements.txt
```

4. **Instalar browsers de Playwright**
```bash
playwright install chromium
```

5. **Ejecutar el scraper**
```bash
python scraper.py
```

## 🤖 Funcionamiento del Scraper

El scraper realiza los siguientes pasos:

1. **Conexión a FFCV**: Usa Playwright para navegar las páginas oficiales de FFCV
2. **Extracción de datos**:
   - Calendario de partidos (fechas, equipos, campos)
   - Resultados de partidos jugados
   - Clasificación del grupo
3. **Procesamiento**:
   - Parsea fechas en español
   - Identifica próximo partido
   - Calcula últimos 5 resultados
4. **Generación de archivos**:
   - `data/partidos.json`: Datos estructurados
   - `partidos.ics`: Calendario en formato iCalendar
   - `index.html`: Landing page desde template
   - `dashboard.html`: Dashboard desde template

### Manejo de Errores

- **Reintentos automáticos**: 3 intentos con delay de 5 segundos
- **Logging detallado**: Información de progreso en cada paso
- **Protección de datos**: No sobrescribe archivos si hay errores

## ⚙️ Configuración de GitHub Actions

El proyecto incluye un workflow de GitHub Actions que se ejecuta:

- **Diariamente** a las 7:00 AM (Europe/Madrid)
- **Manualmente** desde la pestaña Actions en GitHub
- **En cada push** a la rama `main` (para testing)

### Pasos del Workflow

1. Checkout del repositorio
2. Instalar Python 3.11
3. Instalar dependencias y Playwright
4. Ejecutar scraper
5. Verificar si hay cambios
6. Hacer commit y push de archivos actualizados
7. Desplegar a GitHub Pages

### Activar GitHub Actions

1. Ve a tu repositorio en GitHub
2. Navega a **Settings** > **Actions** > **General**
3. En "Workflow permissions", selecciona **Read and write permissions**
4. Habilita **Allow GitHub Actions to create and approve pull requests**
5. Guarda los cambios

## 🌐 Deploy en GitHub Pages

### Primera Configuración

1. **Crear repositorio en GitHub**:
   - Nombre: `extramurs-calendar-automation`
   - Visibilidad: Público (necesario para GitHub Pages gratuito)

2. **Push del proyecto**:
```bash
git init
git add .
git commit -m "🎉 Inicio del proyecto Extramurs Calendar Automation"
git branch -M main
git remote add origin https://github.com/Wakkos/extramurs-calendar-automation.git
git push -u origin main
```

3. **Configurar GitHub Pages**:
   - Ve a **Settings** > **Pages**
   - En "Source", selecciona **Deploy from a branch**
   - Branch: `gh-pages` (se creará automáticamente)
   - Carpeta: `/ (root)`
   - Guarda los cambios

4. **Ejecutar el Action por primera vez**:
   - Ve a la pestaña **Actions**
   - Selecciona el workflow "Update Calendar & Deploy"
   - Haz clic en "Run workflow"
   - Espera a que termine (2-3 minutos)

5. **Verificar el sitio**:
   - Navega a: `https://wakkos.github.io/extramurs-calendar-automation/`
   - Deberías ver la landing page con los botones de suscripción

### Actualización de URLs

**IMPORTANTE**: Después del primer deploy, actualiza las URLs en `scraper.py`:

```python
# En scraper.py, línea ~270
base_url = "https://TU-USUARIO.github.io/TU-REPO"
```

Reemplaza con tu URL real de GitHub Pages y haz push de los cambios.

## 📱 Uso del Calendario

### Para Familias

1. **Accede a la página**: [https://wakkos.github.io/extramurs-calendar-automation/](https://wakkos.github.io/extramurs-calendar-automation/)

2. **Elige tu plataforma**:
   - **iPhone/iPad/Mac**: Toca el botón correspondiente y acepta la suscripción
   - **Google Calendar**: Haz clic en el botón y confirma
   - **Android**: Descarga el .ics e impórtalo en Google Calendar
   - **Outlook**: Descarga el .ics y sigue las instrucciones

3. **Disfruta**: Los partidos se sincronizarán automáticamente cada día

### Ver Dashboard

- Navega a [https://wakkos.github.io/extramurs-calendar-automation/dashboard.html](https://wakkos.github.io/extramurs-calendar-automation/dashboard.html)
- Consulta el próximo partido, últimos resultados y clasificación actualizada

## 🔄 Actualización para Nueva Temporada

Cuando empiece una nueva temporada:

1. Obtén las nuevas URLs de FFCV para el equipo
2. Actualiza en `scraper.py`:
```python
URL_CALENDARIO = "https://resultadosffcv.isquad.es/calendario.php?id_temp=XX&..."
URL_PARTIDOS = "https://resultadosffcv.isquad.es/total_partidos.php?id_temp=XX&..."
GRUPO = "Segona FFCV Prebenjamí 2n. any València - Grup XX"
```
3. Haz commit y push de los cambios
4. El Action se ejecutará automáticamente

## 🛠️ Personalización

### Cambiar Colores

Edita los templates en `templates/`:
- `index_template.html`: Landing page
- `dashboard_template.html`: Dashboard

Los colores están definidos en las secciones `<style>`.

### Cambiar Frecuencia de Actualización

Edita `.github/workflows/update.yml`:
```yaml
schedule:
  - cron: '0 6 * * *'  # Formato: minuto hora día mes día_semana
```

Ejemplos:
- `0 6 * * *`: Diario a las 6:00 UTC
- `0 6,18 * * *`: Dos veces al día (6:00 y 18:00 UTC)
- `0 6 * * 1-5`: Solo días laborables

### Ajustar Selectores CSS

Si la web de FFCV cambia, actualiza los selectores en `scraper.py`:
- Función `scrape_calendario()`: Línea ~150
- Función `scrape_clasificacion()`: Línea ~200

## 📊 Datos Generados

### partidos.json

Estructura del archivo JSON:
```json
{
  "equipo": "C.F. Extramurs Valencia 'B'",
  "grupo": "Segona FFCV Prebenjamí 2n. any València - Grup 12",
  "ultima_actualizacion": "2025-11-02T14:30:00",
  "proximo_partido": {
    "fecha": "2025-11-09",
    "hora": "10:00",
    "local": "C.F. Extramurs Valencia 'B'",
    "visitante": "Rival",
    "campo": "Campo Futbol San Marcelino F-8 Campo 4",
    "maps_url": null
  },
  "ultimos_resultados": [...],
  "clasificacion": [...],
  "todos_partidos": [...]
}
```

## ⚠️ Consideraciones Importantes

1. **Scraping Ético**:
   - El scraper respeta los tiempos de carga (delays de 2-3 segundos)
   - Solo scrapea datos públicos de partidos
   - No extrae información personal de jugadores

2. **Limitaciones de FFCV**:
   - La web bloquea algunos user-agents (por eso usamos Playwright)
   - La estructura HTML puede cambiar sin aviso
   - En caso de error, el scraper mantiene los datos anteriores

3. **Rate Limiting**:
   - El Action se ejecuta una vez al día
   - Evita ejecutar el scraper manualmente muchas veces

4. **Privacidad**:
   - Solo se publican datos de partidos (equipos, resultados, clasificación)
   - No se incluyen nombres de jugadores ni datos personales

## 🐛 Troubleshooting

### El scraper falla localmente

1. Verifica que Playwright esté instalado:
```bash
playwright install chromium
```

2. Verifica las URLs de FFCV (pueden haber cambiado)

3. Revisa los logs para identificar el error específico

### GitHub Actions falla

1. Verifica que tienes permisos de escritura activados (Settings > Actions)
2. Revisa los logs del Action en la pestaña Actions
3. Verifica que las URLs en `scraper.py` sean correctas

### Los calendarios no se sincronizan

1. Verifica que la URL del calendario sea accesible públicamente
2. Asegúrate de haber configurado GitHub Pages correctamente
3. Espera unos minutos, algunos clientes de calendario tardan en sincronizar

## 🙏 Créditos

- **Inspiración**: [ICM-Comedor](https://github.com/Wakkos/ICM-Comedor) por [@Wakkos](https://github.com/Wakkos)
- **Datos**: [Federación de Fútbol de la Comunidad Valenciana (FFCV)](https://resultadosffcv.isquad.es/)
- **Tecnologías**: Playwright, BeautifulSoup, Python, GitHub Actions, GitHub Pages

## 📄 Licencia

Este proyecto es de código abierto y está disponible bajo la Licencia MIT.

## 💬 Contacto

Para dudas o sugerencias:
- Abre un [Issue](https://github.com/Wakkos/extramurs-calendar-automation/issues)
- Pull Requests son bienvenidos

---

**Hecho con ❤️ para las familias del C.F. Extramurs Valencia 'B'**

⚽ ¡Vamos Extramurs! ⚽
