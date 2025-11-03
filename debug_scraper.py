#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de debugging para inspeccionar la estructura HTML de FFCV
"""

import time
from playwright.sync_api import sync_playwright

URL_CALENDARIO = "https://resultadosffcv.isquad.es/calendario.php?id_temp=21&id_modalidad=33345&id_competicion=29531322&id_torneo=904301187"
URL_PARTIDOS = "https://resultadosffcv.isquad.es/total_partidos.php?id_temp=21&id_modalidad=33345&id_competicion=29531322&id_torneo=904301187"

print("🔍 Debugging Scraper - Guardando HTML de las páginas...")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    # Página de calendario
    print("\n1️⃣ Obteniendo página de calendario...")
    page.goto(URL_CALENDARIO, timeout=30000, wait_until="networkidle")
    page.wait_for_timeout(2000)

    html_calendario = page.content()
    with open("debug_calendario.html", "w", encoding="utf-8") as f:
        f.write(html_calendario)
    print("✓ Guardado en: debug_calendario.html")

    # Página de partidos
    print("\n2️⃣ Obteniendo página de partidos/clasificación...")
    time.sleep(2)
    page.goto(URL_PARTIDOS, timeout=30000, wait_until="networkidle")
    page.wait_for_timeout(2000)

    html_partidos = page.content()
    with open("debug_partidos.html", "w", encoding="utf-8") as f:
        f.write(html_partidos)
    print("✓ Guardado en: debug_partidos.html")

    browser.close()

print("\n✅ HTML guardado. Ahora puedes abrir los archivos y ver la estructura:")
print("   - debug_calendario.html")
print("   - debug_partidos.html")
print("\n💡 Abre estos archivos en un navegador y usa 'Inspeccionar elemento' (F12)")
print("   para ver cómo están estructurados los partidos y la clasificación.")
