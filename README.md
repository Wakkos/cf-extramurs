# ⚽ Calendario FFCV - Sistema Multi-Equipo

Sistema automatizado para generar calendarios, resultados y estadísticas de equipos de la FFCV (Federación de Fútbol de la Comunidad Valenciana).

## 🎯 Características

- **📅 Calendario ICS**: Genera archivos `.ics` compatibles con Google Calendar, iPhone, Outlook
- **🌐 Landing Page**: Página web profesional con calendario, resultados y clasificación
- **👥 Plantilla del Equipo**: Galería de fotos de los jugadores
- **🤖 Actualización Automática**: GitHub Actions ejecuta el scraper diariamente
- **📊 Estadísticas**: Racha de resultados, clasificación, próximos partidos
- **🔧 Multi-Equipo**: Sistema completamente configurable para cualquier equipo mediante `config.yaml`

## 🚀 Configuración para un Nuevo Equipo

Este sistema está diseñado para ser **fácilmente replicable** para cualquier equipo de la FFCV. Solo necesitas actualizar el archivo `config.yaml`.

### Paso 1: Clonar el Repositorio

```bash
# Clona este repositorio
git clone https://github.com/Wakkos/cf-extramurs.git nombre-de-tu-equipo
cd nombre-de-tu-equipo

# Crea tu propio repositorio en GitHub y vincula
git remote set-url origin git@github.com:TU-USUARIO/TU-REPO.git
```

### Paso 2: Obtener los IDs de la FFCV

1. Ve a la página del calendario de tu equipo en [resultadosffcv.isquad.es](https://resultadosffcv.isquad.es)
2. Navega al calendario de tu equipo
3. Copia la URL completa, que se verá así:

```
https://resultadosffcv.isquad.es/calendario.php?id_temp=21&id_modalidad=33345&id_competicion=29531322&id_torneo=904301187
```

4. Extrae los valores de cada parámetro:
   - `id_temp` = **21** (temporada)
   - `id_modalidad` = **33345** (modalidad)
   - `id_competicion` = **29531322** (competición)
   - `id_torneo` = **904301187** (torneo)

5. Para obtener el `id_equipo`, ve a la página de plantilla del equipo y copia el parámetro `id_equipo` de la URL:

```
https://resultadosffcv.isquad.es/equipo_plantilla.php?id_temp=21&id_modalidad=33345&id_competicion=29531322&id_equipo=900436323&id_torneo=904301187
```

   - `id_equipo` = **900436323**

### Paso 3: Configurar `config.yaml`

Edita el archivo `config.yaml` con los datos de tu equipo:

```yaml
equipo:
  nombre: "Tu Equipo - Nombre Completo"
  nombre_corto: "Tu Equipo"
  grupo: "Tu Grupo / Categoría"
  logo: "Images/tu-logo.jpg"  # Coloca tu logo en la carpeta Images/
  background: "Images/bg.jpg"  # Opcional: imagen de fondo

ids_ffcv:
  temporada: 21           # Del paso 2
  modalidad: 33345        # Del paso 2
  competicion: 29531322   # Del paso 2
  torneo: 904301187       # Del paso 2
  equipo: 900436323       # Del paso 2

sitio:
  url_base: "https://TU-USUARIO.github.io/TU-REPO"
  titulo: "Tu Equipo - Calendario y Resultados"
  descripcion: "Calendario, resultados y clasificación de Tu Equipo - Temporada 2024-2025"
  temporada: "2024-2025"
```

### Paso 4: Añadir Logo e Imágenes

1. Coloca el logo de tu equipo en `Images/tu-logo.jpg`
2. (Opcional) Añade una imagen de fondo en `Images/bg.jpg`
3. Actualiza las rutas en `config.yaml`

### Paso 5: Probar Localmente

```bash
# Instala las dependencias
pip install -r requirements.txt
playwright install chromium

# Ejecuta el scraper
python scraper.py
```

Si todo funciona correctamente, verás:
- `partidos.ics` - Archivo de calendario
- `index.html` - Página principal
- `plantilla.html` - Página de plantilla
- `data/partidos.json` - Datos estructurados

### Paso 6: Publicar en GitHub

```bash
# Añade los archivos
git add .
git commit -m "Configuración inicial para [nombre de tu equipo]"
git push -u origin main
```

### Paso 7: Configurar GitHub Actions

1. Ve a tu repositorio en GitHub
2. **Settings** → **Actions** → **General**
3. En "Workflow permissions", selecciona:
   - ✅ **Read and write permissions**
   - ✅ **Allow GitHub Actions to create and approve pull requests**
4. Guarda los cambios

### Paso 8: Activar GitHub Pages

1. Ve a **Settings** → **Pages**
2. En "Source", selecciona:
   - Branch: `gh-pages`
   - Folder: `/ (root)`
3. Guarda los cambios
4. Espera unos minutos y tu sitio estará disponible en:
   ```
   https://TU-USUARIO.github.io/TU-REPO
   ```

## 📁 Estructura del Proyecto

```
extramurs/
├── config.yaml              # ⚙️ CONFIGURACIÓN DEL EQUIPO (editar aquí)
├── scraper.py               # Script principal de scraping
├── requirements.txt         # Dependencias de Python
├── partidos.ics            # Calendario generado (auto)
├── index.html              # Página principal (auto)
├── plantilla.html          # Página de plantilla (auto)
├── manifest.json           # Manifest PWA
├── data/
│   └── partidos.json       # Datos estructurados (auto)
├── Images/
│   ├── extramurs.jpg       # Logo del equipo
│   ├── bg.jpg              # Imagen de fondo
│   └── plantilla/          # Fotos de jugadores (auto)
├── templates/
│   ├── dashboard_template.html    # Template de la página principal
│   └── plantilla_template.html    # Template de la plantilla
└── .github/workflows/
    └── update.yml          # Workflow de GitHub Actions
```

## 🔄 Actualización Automática

El sistema se actualiza automáticamente todos los días a las **7:00 AM (hora de Madrid)** mediante GitHub Actions.

También puedes ejecutar manualmente:
1. Ve a **Actions** en tu repositorio de GitHub
2. Selecciona "Update Calendar & Deploy"
3. Haz clic en "Run workflow"

## 🛠️ Desarrollo

### Comandos Útiles

```bash
# Ejecutar el scraper
python scraper.py

# Debug: guardar HTML para análisis
python debug_scraper.py

# Ver logs del scraper
python scraper.py 2>&1 | tee scraper.log
```

### Modificar Plantillas

Las plantillas usan [Jinja2](https://jinja.palletsprojects.com/):

- `templates/dashboard_template.html` → Página principal
- `templates/plantilla_template.html` → Página de plantilla

Después de modificar, ejecuta `python scraper.py` para regenerar el HTML.

## 📝 Configuración del `config.yaml`

El archivo `config.yaml` contiene toda la configuración específica del equipo:

```yaml
# Información del equipo
equipo:
  nombre: "C.F. Extramurs Valencia 'B'"
  nombre_corto: "Extramurs B"
  grupo: "Segona FFCV Prebenjamí 2n. any València - Grup 12"
  logo: "Images/extramurs.jpg"
  background: "Images/bg.jpg"

# IDs extraídos de las URLs de FFCV
ids_ffcv:
  temporada: 21
  modalidad: 33345
  competicion: 29531322
  torneo: 904301187
  equipo: 900436323

# URLs base de FFCV (normalmente no necesitas cambiar esto)
urls:
  base_calendario: "https://resultadosffcv.isquad.es/calendario.php"
  base_clasificacion: "https://resultadosffcv.isquad.es/clasificacion.php"
  base_plantilla: "https://resultadosffcv.isquad.es/equipo_plantilla.php"
  base_partido: "https://resultadosffcv.isquad.es/partido.php"

# Configuración del sitio web
sitio:
  url_base: "https://wakkos.github.io/cf-extramurs"
  titulo: "C.F. Extramurs Valencia 'B' - Calendario y Resultados"
  descripcion: "Calendario, resultados y clasificación del C.F. Extramurs Valencia 'B'"
  temporada: "2024-2025"

# Configuración de scraping (valores por defecto recomendados)
scraping:
  max_reintentos: 3
  delay_reintento: 5
  timeout_pagina: 30000
  espera_contenido: 3000
```

## 📝 Configuración Avanzada

### Cambiar la Frecuencia de Actualización

Edita `.github/workflows/update.yml` línea 6:

```yaml
schedule:
  - cron: '0 6 * * *'  # 6:00 UTC = 7:00 AM Madrid
```

Generador de cron: [crontab.guru](https://crontab.guru/)

### Personalizar Estilos

Los templates usan un sistema de diseño basado en variables CSS (shadcn/ui):

```css
:root {
    --primary: 221.2 83.2% 53.3%;
    --secondary: 210 40% 96.1%;
    /* ... más variables */
}
```

Modifica las variables en los archivos `*_template.html`.

## 📱 Uso del Calendario

### Para Familias

1. Accede a la página de tu equipo
2. Elige tu plataforma:
   - **iPhone/iPad/Mac**: Toca el botón correspondiente y acepta la suscripción
   - **Google Calendar**: Haz clic en el botón y confirma
   - **Android**: Descarga el .ics e impórtalo en Google Calendar
   - **Outlook**: Descarga el .ics y sigue las instrucciones
3. Los partidos se sincronizarán automáticamente cada día

## ❓ Solución de Problemas

### Error: "No se encontró el archivo de configuración"

Asegúrate de que `config.yaml` existe en la raíz del proyecto.

### Error: "Permission denied to github-actions[bot]"

1. Ve a **Settings** → **Actions** → **General**
2. Activa "Read and write permissions"

### La página no se actualiza en GitHub Pages

1. Ve a **Actions** y verifica que el workflow se ejecutó correctamente
2. Comprueba que hay cambios en los archivos (si no hay cambios, no se despliega)
3. Espera 2-3 minutos para que GitHub Pages se actualice

### El scraper falla al obtener datos

1. Verifica que las URLs de la FFCV sean correctas
2. Comprueba que los IDs en `config.yaml` sean correctos
3. Ejecuta `python debug_scraper.py` para ver el HTML raw
4. Revisa los logs del scraper para identificar el error específico

### GitHub Actions falla en Ubuntu

Si ves errores relacionados con dependencias del sistema, el workflow ya está configurado para usar Ubuntu 22.04 e instalar manualmente las dependencias de Playwright.

## 🔄 Actualización para Nueva Temporada

Cuando empiece una nueva temporada:

1. Ve a la página de la FFCV y obtén las nuevas URLs
2. Actualiza los IDs en `config.yaml`:
```yaml
ids_ffcv:
  temporada: 22  # Nueva temporada
  # ... otros IDs según corresponda
```
3. Actualiza la temporada en:
```yaml
sitio:
  temporada: "2025-2026"
```
4. Haz commit y push de los cambios
5. El Action se ejecutará automáticamente

## 🤝 Contribuir

¿Encontraste un bug o tienes una mejora? ¡Abre un issue o pull request!

## 📄 Licencia

MIT License - Úsalo libremente para tu equipo

## 🙏 Créditos

- **Scraping**: Playwright + BeautifulSoup4
- **Calendario**: ics library
- **Templates**: Jinja2
- **Diseño**: Inspirado en shadcn/ui
- **Automatización**: GitHub Actions
- **Datos**: [Federación de Fútbol de la Comunidad Valenciana (FFCV)](https://resultadosffcv.isquad.es/)

---

**¿Necesitas ayuda?** Abre un [issue](https://github.com/Wakkos/cf-extramurs/issues) en GitHub.

**Hecho con ❤️ para las familias del C.F. Extramurs Valencia 'B'**
