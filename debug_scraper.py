#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vuelca las respuestas JSON de la API FFCV para inspeccionar/depurar.

Uso:
    python debug_scraper.py [<slug-config>]

Donde `<slug-config>` es el nombre (sin extensión) de un archivo dentro de
configs/. Si se omite, se usa el primero por orden alfabético. Las respuestas
se guardan en `debug_<endpoint>.json` en la raíz del repo.
"""

import json
import sys
from pathlib import Path

import yaml

from scraper import fetch_json, _cod_jornada_mas_reciente, FFCV_API_BASE  # noqa: E402

BASE_DIR = Path(__file__).parent
CONFIGS_DIR = BASE_DIR / "configs"


def cargar_config(slug: str | None) -> dict:
    configs = sorted(CONFIGS_DIR.glob("*.yaml"))
    if not configs:
        raise SystemExit("❌ No hay configuraciones en configs/")

    if slug:
        target = CONFIGS_DIR / f"{slug}.yaml"
        if not target.exists():
            raise SystemExit(f"❌ No existe configs/{slug}.yaml")
    else:
        target = configs[0]

    with open(target, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def dump(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ {path}")


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else None
    config = cargar_config(slug)
    cod_grupo = str(config["ids_ffcv"]["torneo"])
    cod_equipo = str(config["ids_ffcv"]["equipo"])

    print(f"🔍 API: {FFCV_API_BASE}")
    print(f"   cod_grupo = {cod_grupo}")
    print(f"   codequipo = {cod_equipo}")

    jornadas = fetch_json("filtros/jornadas_fetch.php", {"cod_grupo": cod_grupo})
    dump(BASE_DIR / "debug_jornadas.json", jornadas)

    cod_jornada = _cod_jornada_mas_reciente(cod_grupo)
    print(f"   jornada más reciente = {cod_jornada}")

    clasif = fetch_json(
        "clasificaciones/clasificaciones_ajax.php",
        {"cod_grupo": cod_grupo, "cod_jornada": cod_jornada},
    )
    dump(BASE_DIR / "debug_clasificacion.json", clasif)

    partidos = fetch_json(
        "partidos/resultados_por_grupo_jornada_data.php",
        {"cod_grupo": cod_grupo, "cod_jornada": cod_jornada},
    )
    dump(BASE_DIR / "debug_partidos.json", partidos)

    equipo = fetch_json("equipos/ver_equipo.php", {"codequipo": cod_equipo})
    dump(BASE_DIR / "debug_equipo.json", equipo)


if __name__ == "__main__":
    main()
