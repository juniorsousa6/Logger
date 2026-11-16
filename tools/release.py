#!/usr/bin/env python3
"""Gera o pacote de uma versao: dist/Logger-<versao>.zip + latest.json.
Uso: python3 tools/release.py "o que mudou nesta versao"
     python3 tools/release.py --sig ASSINATURA   (so acrescenta a assinatura feita no Mac ao latest.json)
O app instalado le latest.json no GitHub e so instala se a assinatura conferir."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["app.py", "cardlogger.py", "media.py", "posrender.py", "fotos.py", "hds.py", "cards.json", "ui.html", "VERSION",
         "Instalar Logger.command", "Abrir Logger.command", "LEIA-ME.md",
         "mac/LoggerApp.swift", "mac/Info.plist", "mac/UPDATE_URL", "mac/UPDATE_KEY", "mac/icon.png"]
RAW = "https://raw.githubusercontent.com/juniorsousa6/Logger/main/"

if sys.argv[1:2] == ["--sig"]:
    j = json.loads((ROOT / "latest.json").read_text())
    j["sig"] = sys.argv[2].strip()
    sys.path.insert(0, str(ROOT / "tools"))
    import updatesig
    key = (ROOT / "mac" / "UPDATE_KEY").read_text().strip()
    if not updatesig.verify(j, key):
        sys.exit("A assinatura nao confere com mac/UPDATE_KEY. Nada mudou.")
    (ROOT / "latest.json").write_text(json.dumps(j, ensure_ascii=False, indent=2) + "\n")
    sys.exit(print("assinatura conferida e gravada em latest.json"))

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
    {"version": ver, "url": RAW + f"dist/Logger-{ver}.zip", "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
     "size": out.stat().st_size, "notes": notes}, ensure_ascii=False, indent=2) + "\n")
print(out, "+ latest.json", ver)
