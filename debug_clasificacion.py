#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Script rápido para guardar HTML de clasificación"""

import time
from playwright.sync_api import sync_playwright

URL = "https://resultadosffcv.isquad.es/clasificacion.php?id_temp=21&id_modalidad=33345&id_competicion=29531322&id_torneo=904301187"

print("Obteniendo clasificación...")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(URL, timeout=30000, wait_until="networkidle")
    page.wait_for_timeout(2000)
    html = page.content()
    browser.close()

with open("debug_clasificacion.html", "w", encoding="utf-8") as f:
    f.write(html)

print("✓ Guardado en debug_clasificacion.html")
