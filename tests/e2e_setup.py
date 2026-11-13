"""Monta cartoes e HDs simulados para o teste da interface. Uso: python3 e2e_setup.py <pasta>"""
import os
import plistlib
import sys
from pathlib import Path

D = Path(sys.argv[1])
V = D / "V"
for d in ("HD", "BACKUP", "Dropbox"):
    (V / d).mkdir(parents=True, exist_ok=True)


def put(rel, size):
    p = V / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(os.urandom(size))


for i in range(1, 4):
    put(f"SONY_JRS/PRIVATE/M4ROOT/CLIP/JRS{i:05d}.MP4", 400_000 + i)
put("SONY_JRS/DCIM/100MSDCF/JRS00001.ARW", 90_000)
for i in range(1, 3):
    put(f"SONY_C/PRIVATE/M4ROOT/CLIP/C{8970 + i}.MP4", 300_000 + i)
put("DJI/DCIM/DJI_001/DJI_0001.MP4", 500_000)
put("DJI/DCIM/DJI_001/DJI_0001.SRT", 2_000)
put("Extreme Pro/2026_10_01 - Show/Áudio/TASCAM_0001.wav", 1000)   # HD de trabalho

info = {"SONY_JRS": {"BusProtocol": "USB", "RemovableMedia": True, "MediaName": "SD Card Reader"},
        "SONY_C": {"BusProtocol": "Secure Digital", "Internal": True, "RemovableMedia": True},
        "DJI": {"BusProtocol": "USB", "RemovableMedia": True, "MediaName": "microSD Reader"},
        "Extreme Pro": {"BusProtocol": "USB", "MediaName": "SanDisk Extreme Pro 55AF"},
        "HD": {"BusProtocol": "USB", "MediaName": "LaCie"},
        "BACKUP": {"BusProtocol": "USB", "MediaName": "LaCie"},
        "Dropbox": {"BusProtocol": "USB", "MediaName": "LaCie"}}
(D / "info").mkdir(exist_ok=True)
(D / "ej").mkdir(exist_ok=True)
for k, v in info.items():
    v["VolumeUUID"] = "UUID-" + k.replace(" ", "")
    (D / "info" / f"{k}.plist").write_bytes(plistlib.dumps(v))

b = D / "bin"
b.mkdir(exist_ok=True)
(b / "diskutil").write_text(f"""#!/bin/bash
D="{D}"
if [ "$1" = "info" ]; then cat "$D/info/$(basename "$3").plist" 2>/dev/null; exit 0; fi
if [ "$1" = "eject" ]; then [ -d "$2" ] || {{ echo "nao achei $2"; exit 1; }}; mv "$2" "$D/ej/"; echo "Disk ejected"; exit 0; fi
exit 0
""")
(b / "diskutil").chmod(0o755)
for tool in ("osascript", "open"):
    (b / tool).write_text("#!/bin/bash\nexit 0\n")
    (b / tool).chmod(0o755)
print(D)
