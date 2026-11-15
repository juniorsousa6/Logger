"""Prepara os testes de pos-producao: Renomear drops (projeto Eco Club real) e Limpar fotos (catalogo simulado)."""
import json, sys
from pathlib import Path
S = Path(sys.argv[1])
d = json.loads((Path(__file__).parent / "fixtures" / "eco_club_resolve.json").read_text(encoding="utf-8"))
for j, sub in zip(d["jobs"], ("9x16", "4x5")):
    j["target_dir"] = str(S / "Dropbox" / "VIOT" / "2026_11_14 - Eco Club" / sub)
    j["status"] = "Complete"
    Path(j["target_dir"]).mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(j["clips"], 1):
        (Path(j["target_dir"]) / f"V{c['track']}-{i:04d}_{c['name']}.mp4").write_text(c["name"])
(S / "resolve.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

# ---- limpeza de fotos: festa no HD simulado + catalogo do Lightroom com o esquema real
sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent.parent)]
from test_fotos import make_catalog  # noqa: E402
festa = S / "HD2" / "2026_11_14 - Eco Club"
(festa / "Fotos" / "101MSDCF").mkdir(parents=True, exist_ok=True)
fotos_files = []
for i in range(20):  # fluxo novo: as 3 com estrela ainda estao em Fotos; o Logger copia
    n = f"JRS{3000 + i}.ARW"
    (festa / "Fotos" / "101MSDCF" / n).write_text(f"raw{i:02d}" * 100)
    fotos_files.append((n, 1 + i % 3 if i in (3, 7, 11) else 0))
(festa / "Fotos" / "101MSDCF" / "JRS3003.xmp").write_text("<x:xmpmeta/>")
make_catalog(S / "2026.lrcat", {str(festa / "Fotos" / "101MSDCF"): fotos_files})
# osascript falso: "Finder delete" move a pasta para $S/Lixeira (como o Finder faria)
(S / "bin" / "osascript").write_text(f"""#!/bin/bash
if [[ "$2" == *Finder*delete* ]]; then mkdir -p "{S}/Lixeira" && mv "$3" "{S}/Lixeira/"; fi
exit 0
""")
