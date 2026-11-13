#!/usr/bin/env python3
"""Gera o pacote de uma versao: dist/Logger-<versao>.zip + latest.json.
Uso: python3 tools/release.py "o que mudou nesta versao"
O app instalado le latest.json no GitHub e oferece a atualizacao sozinho."""
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["app.py", "cardlogger.py", "media.py", "cards.json", "ui.html", "VERSION",
         "Instalar Logger.command", "Abrir Logger.command", "LEIA-ME.md",
         "mac/LoggerApp.swift", "mac/Info.plist", "mac/UPDATE_URL", "mac/icon.png"]
RAW = "https://raw.githubusercontent.com/juniorsousa6/Logger/main/"

ver = (ROOT / "VERSION").read_text().strip()
notes = sys.argv[1] if len(sys.argv) > 1 else ""
out = ROOT / "dist" / f"Logger-{ver}.zip"
out.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in FILES:
        p = ROOT / f
        zi = zipfile.ZipInfo.from_file(p, "Logger/" + f)
        zi.external_attr = ((0o100755 if f.endswith(".command") else 0o100644) << 16)
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, p.read_bytes())
(ROOT / "latest.json").write_text(json.dumps(
    {"version": ver, "url": RAW + f"dist/Logger-{ver}.zip", "notes": notes}, ensure_ascii=False, indent=2) + "\n")
print(out, "+ latest.json", ver)
