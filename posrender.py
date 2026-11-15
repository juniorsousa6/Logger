#!/usr/bin/env python3
"""
Depois do render no DaVinci: renomeia os drops no padrao do Junior.

  V1-0001_Drop 1.mp4  ->  1_Eco Club.mp4
  V1-0011_Drop 11.mp4 ->  11_Eco Club Cardio.mp4      (musica do VIOT: titulo no lugar do " *")

De onde vem cada coisa (projeto Eco Club, 15/11/2026):
- O Ghost Junior monta as timelines "9x16 · Drops" / "4x5 · Drops" com um clipe "Drop N" por drop
  e marcadores "♪ Artista - Titulo (Mix)" no inicio de cada musica.
- O render sai em "Individual clips", direto na pasta da festa no Dropbox (…/2026_11_14 - Eco Club/9x16).
- Numero = ordem cronologica dos clipes na timeline (1, 2, 3… sem buraco, mesmo se um drop foi apagado).
- Nome da festa = o que vem depois de "AAAA_MM_DD - " na pasta.

Nada e renomeado sem o Junior confirmar a previa; nada e sobrescrito; cada renomeacao fica num
registro em ~/.cardlogger/renomeios/ para poder desfazer.

A leitura do Resolve roda num processo separado (python3 posrender.py --resolve), com prazo:
se o Resolve travar, o Logger nao trava junto.
"""
import difflib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

STATE_DIR = Path(os.environ.get("CARDLOGGER_HOME", Path.home() / ".cardlogger"))
UNDO_DIR = STATE_DIR / "renomeios"
VIDEO_EXT = {".mp4", ".mov", ".mxf", ".m4v"}

# Titulos do VIOT (skill discografia-viot-casa-bonita, Beatport out/2026 + sets de 2026).
# So serve para consertar marcador digitado torto ("Put Id Down 30.8" -> "Put It Down").
VIOT_TITLES = [
    "The Ride", "Flex", "Cardio", "Flower", "Fyrah", "Ritual", "Lucro", "Everytime I Try", "Ocean",
    "Confession", "Momentum", "Anthem", "Blitz", "Sometimes", "Hypno", "So Far So Good", "Prodigy",
    "Betty", "Pop It Low", "The Crew", "Kiki", "Everyday People", "FMF", "Down", "The Joint", "Carrie",
    "VIP Section", "Como Eu Te Quero", "Space Cowboy", "So Easy", "Gold", "Hallucinate", "Good For Me",
    "G Tales", "Prisoner", "On N On", "Misstress", "Jiggy", "Spend Some", "L'amour", "Time",
    "Fundamentals", "Little Bit", "Bossy",
    # tocadas nos sets de 2026 (nomes das pastas do Dropbox e marcadores do Ghost)
    "Put It Down", "Palanque", "Chove Chuva", "Around",
]


# ---------------------------------------------------------------- nomes
def _fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def party_name(path):
    """'…/VIOT/2026_11_14 - Eco Club/9x16' -> 'Eco Club'. Procura na pasta e nas de cima."""
    p = Path(path)
    for part in [p] + list(p.parents):
        m = re.match(r"^\d{4}_\d{2}_\d{2}\s*-\s*(.+)$", unicodedata.normalize("NFC", part.name))
        if m:
            return m.group(1).strip()
    return ""


def parse_song(marker):
    """'♪ Vintage Culture, Viot - Anthem (Original Mix)' -> ('Vintage Culture, Viot', 'Anthem (Original Mix)')."""
    s = re.sub(r"^[\s♪♫]+", "", marker or "").strip()
    if " - " in s:
        a, t = s.split(" - ", 1)
        return a.strip(), t.strip()
    return "", s


def is_viot(marker):
    """Musica do VIOT: ele no artista ou no remix/rework/edit entre parenteses."""
    artists, title = parse_song(marker)
    if re.search(r"\bviot\b", _fold(artists)):
        return True
    for grp in re.findall(r"[\(\[]([^\)\]]*)[\)\]]", title):
        if re.search(r"\bviot\b", _fold(grp)):
            return True
    return False


def clean_title(raw, known=VIOT_TITLES):
    """So o nome da musica: sem (Extended Mix), sem remix, sem feat., sem numero solto no fim."""
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", raw or "")         # (Extended Mix), (Viot Remix)…
    t = re.split(r"\s+(?:feat\.?|ft\.?|featuring)\s+", t, flags=re.I)[0]
    t = re.sub(r"(\s+[\d.,:]+)+\s*$", "", t)                      # "Put Id Down 30.8"
    t = re.sub(r'[/\\:*?"<>|_]+', " ", t)                         # nada que quebre nome de arquivo
    t = re.sub(r"\s+", " ", t).strip(" -.")
    if not t:
        return ""
    best, score = None, 0.0
    for k in known:
        r = difflib.SequenceMatcher(None, _fold(t), _fold(k)).ratio()
        if r > score:
            best, score = k, r
    return best if best and score >= 0.8 else t


def drop_target_name(n, party, title=""):
    base = f"{n}_{party}" if party else str(n)
    return f"{base} {title}" if title else base


# ---------------------------------------------------------------- plano
FILE_RE = re.compile(r"^V(\d+)-\d+_(.+)$")  # "V1-0001_Drop 1" (Individual clips, nomes unicos)


def song_at(markers, frame):
    """Ultima musica (marcador ♪) que comecou ate `frame`. Dois marcadores colados (ate 2 quadros,
    ex.: Blitz Original + Intro Mix): fica o roxo, que e o que o Ghost poe."""
    songs = sorted((m for m in markers if (m.get("name") or "").lstrip().startswith(("♪", "♫"))
                    and m["frame"] <= frame), key=lambda m: m["frame"])
    if not songs:
        return None
    last = songs[-1]
    near = [m for m in songs if last["frame"] - m["frame"] <= 2]
    return next((m for m in near if m.get("color") == "Purple"), last)


def plan_job(job, files, titles_fix=None):
    """Um render (um TargetDir) -> lista de renomeacoes.

    job: {"target_dir", "timeline", "mark_in", "mark_out", "clips": [{"track","name","start","end"}],
          "markers": [{"frame" (absoluto, como os clipes), "name", "color"}]}
    files: nomes de arquivo que existem no TargetDir.
    titles_fix: correcoes que o Junior ja fez {titulo limpo: titulo certo}.
    """
    titles_fix = titles_fix or {}
    party = party_name(job["target_dir"])
    mi, mo = job.get("mark_in"), job.get("mark_out")
    clips = [c for c in job["clips"] if (mi is None or c["end"] > mi) and (mo is None or c["start"] <= mo)]
    clips.sort(key=lambda c: c["start"])
    drops = sorted(m["frame"] for m in job["markers"] if re.match(r"^DROP\b", m.get("name") or "", re.I))
    by_name = {}
    for n, c in enumerate(clips, 1):
        inside = [f for f in drops if c["start"] <= f < c["end"]]
        at = inside[0] if inside else (c["start"] + c["end"]) // 2
        song = song_at(job["markers"], at)
        viot = bool(song and is_viot(song["name"]))
        title = ""
        if viot:
            title = clean_title(parse_song(song["name"])[1])
            title = titles_fix.get(title, title)
        by_name.setdefault((c.get("track", 1), c["name"]), []).append({"n": n, "song": song["name"].lstrip("♪♫ ") if song else "",
                                                   "viot": viot, "title": title})
    rows, taken = [], set(files)
    for f in sorted(files, key=lambda x: _natkey(x)):
        stem, ext = os.path.splitext(f)
        if ext.lower() not in VIDEO_EXT:
            continue
        m = FILE_RE.match(stem)
        row = {"old": f, "new": "", "n": None, "song": "", "viot": False, "title": "", "status": ""}
        if not m:
            if re.match(r"^\d+_", stem):
                row["status"] = "já renomeado"
            else:
                row["status"] = "não é um drop deste render"
            rows.append(row)
            continue
        track, clip = int(m.group(1)), m.group(2)
        hits = by_name.get((track, clip))
        if not hits:
            row["status"] = "clipe não achado na timeline"
            rows.append(row)
            continue
        if len(hits) > 1:
            row["status"] = "dois clipes com o mesmo nome na timeline"
            rows.append(row)
            continue
        h = hits[0]
        row.update(n=h["n"], song=h["song"], viot=h["viot"], title=h["title"])
        row["new"] = drop_target_name(h["n"], party, h["title"]) + ext
        rows.append(row)
    # nomes de destino repetidos ou ja ocupados por outro arquivo
    seen = {}
    for r in rows:
        if r["new"]:
            seen.setdefault(r["new"].lower(), []).append(r)
    for k, rs in seen.items():
        if len(rs) > 1:
            for r in rs:
                r["status"], r["new"] = "nome repetido", ""
        elif any(f.lower() == k and f != rs[0]["old"] for f in taken):
            rs[0]["status"], rs[0]["new"] = "já existe um arquivo com esse nome", ""
    for r in rows:
        if r["new"] and not r["status"]:
            r["status"] = "ok"
    return {"target_dir": job["target_dir"], "timeline": job.get("timeline", ""), "party": party,
            "rows": rows, "ok": sum(1 for r in rows if r["status"] == "ok")}


def _natkey(s):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)]


def build_plan(resolve_data, titles_fix=None, listdir=os.listdir):
    """resolve_data = saida de read_resolve(). So entram renders terminados."""
    out = {"project": resolve_data.get("project", ""), "jobs": [], "pending": []}
    for job in resolve_data.get("jobs", []):
        st = (job.get("status") or "").lower()
        if st and st not in ("complete", "completo", "concluído"):
            out["pending"].append({"timeline": job.get("timeline", ""), "status": job.get("status"),
                                   "pct": job.get("pct", 0), "eta_ms": job.get("eta_ms")})
            continue
        try:
            files = listdir(job["target_dir"])
        except OSError:
            out["jobs"].append({"target_dir": job["target_dir"], "timeline": job.get("timeline", ""),
                                "party": party_name(job["target_dir"]), "rows": [], "ok": 0,
                                "error": "pasta do render não encontrada (o Dropbox está aberto?)"})
            continue
        out["jobs"].append(plan_job(job, files, titles_fix))
    return out


# ---------------------------------------------------------------- aplicar / desfazer
def apply(folder, renames, undo_dir=None):
    """renames: [{"old","new"}]. Nao sobrescreve nada; registra para desfazer.
    Feito em duas etapas (nome temporario) para '2_x' -> '1_x' e '1_x' -> '2_x' nao se atropelarem."""
    undo_dir = Path(undo_dir or UNDO_DIR)
    folder = Path(folder)
    todo, done, errors = [], [], []
    olds = {r["old"] for r in renames}
    for r in renames:
        old, new = r["old"], r["new"]
        if not new or "/" in new or new in (".", ".."):
            errors.append({"old": old, "error": "nome novo inválido"})
            continue
        if not (folder / old).is_file():
            errors.append({"old": old, "error": "arquivo não está mais na pasta"})
            continue
        if (folder / new).exists() and new not in olds:
            errors.append({"old": old, "error": "já existe um arquivo com o nome novo"})
            continue
        todo.append((old, new))
    tag = f".logger-renomeando-{os.getpid()}-{int(time.time())}"
    staged = []
    for old, new in todo:
        tmp = old + tag
        try:
            os.rename(folder / old, folder / tmp)
            staged.append((old, tmp, new))
        except OSError as e:
            errors.append({"old": old, "error": str(e)})
    for old, tmp, new in staged:
        try:
            if (folder / new).exists():
                raise OSError("já existe um arquivo com o nome novo")
            os.rename(folder / tmp, folder / new)
            done.append({"old": old, "new": new})
        except OSError as e:
            os.rename(folder / tmp, folder / old)
            errors.append({"old": old, "error": str(e)})
    log = ""
    if done:
        undo_dir.mkdir(parents=True, exist_ok=True)
        log = str(undo_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{party_name(folder) or 'drops'}-{folder.name}.json")
        with open(log, "w", encoding="utf-8") as f:
            json.dump({"folder": str(folder), "when": time.strftime("%Y-%m-%d %H:%M:%S"), "renames": done},
                      f, ensure_ascii=False, indent=1)
    return {"done": done, "errors": errors, "undo": log}


def undo(log_path):
    with open(log_path, encoding="utf-8") as f:
        data = json.load(f)
    back = [{"old": r["new"], "new": r["old"]} for r in data["renames"]]
    res = apply(data["folder"], back, undo_dir=Path(log_path).parent / "desfeitos")
    if not res["errors"]:
        os.replace(log_path, str(Path(log_path).parent / "desfeitos" / Path(log_path).name)) \
            if (Path(log_path).parent / "desfeitos").is_dir() else os.remove(log_path)
    return res


# ---------------------------------------------------------------- Resolve
_MODULES = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/"
_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"


def _connect():
    import glob
    if not os.environ.get("RESOLVE_SCRIPT_LIB") and not os.path.exists(_LIB):
        found = sorted(glob.glob("/Applications/**/DaVinci Resolve*.app/Contents/Libraries/Fusion/fusionscript.so",
                                 recursive=True))
        if found:
            os.environ["RESOLVE_SCRIPT_LIB"] = found[-1]
    sys.path.append(_MODULES)
    try:
        import DaVinciResolveScript as dvr  # type: ignore
    except ImportError:
        raise RuntimeError("Não achei o DaVinci Resolve Studio neste Mac.")
    r = dvr.scriptapp("Resolve")
    if r is None:
        raise RuntimeError("O DaVinci não respondeu. Ele está aberto, com Preferences > System > General > "
                           "External scripting using em Local?")
    return r


def read_resolve():
    """Le do projeto aberto: os renders da fila, os clipes e os marcadores de cada timeline."""
    fake = os.environ.get("LOGGER_RESOLVE_FIXTURE")  # testes: um JSON no lugar do DaVinci
    if fake:
        with open(fake, encoding="utf-8") as f:
            return json.load(f)
    r = _connect()
    p = r.GetProjectManager().GetCurrentProject()
    if not p:
        raise RuntimeError("Nenhum projeto aberto no DaVinci.")
    tls = {}
    for i in range(1, (p.GetTimelineCount() or 0) + 1):
        t = p.GetTimelineByIndex(i)
        tls[t.GetName()] = t
    out = {"project": p.GetName(), "jobs": []}
    for j in p.GetRenderJobList() or []:
        st = p.GetRenderJobStatus(j["JobId"]) or {}
        t = tls.get(j.get("TimelineName"))
        job = {"timeline": j.get("TimelineName", ""), "target_dir": j.get("TargetDir", ""),
               "mark_in": j.get("MarkIn"), "mark_out": j.get("MarkOut"), "mode": j.get("RenderMode", ""),
               "status": st.get("JobStatus", ""), "pct": st.get("CompletionPercentage", 0),
               "eta_ms": st.get("EstimatedTimeRemainingInMs"), "clips": [], "markers": []}
        if t is not None:
            base = t.GetStartFrame()
            for tr in range(1, (t.GetTrackCount("video") or 0) + 1):
                for it in t.GetItemListInTrack("video", tr) or []:
                    job["clips"].append({"track": tr, "name": it.GetName(), "start": it.GetStart(), "end": it.GetEnd()})
            for f, m in (t.GetMarkers() or {}).items():
                job["markers"].append({"frame": int(base + f), "name": m.get("name", ""), "color": m.get("color", "")})
        if "individual" not in (job["mode"] or "").lower():
            job["status"] = job["status"] or "?"
            job["note"] = "render em arquivo único: não dá para separar os drops"
        out["jobs"].append(job)
    return out


def read_resolve_subprocess(timeout=60, python=None):
    """Roda read_resolve() noutro processo: o scripting do Resolve pode travar se ele nao responder."""
    py = python or os.environ.get("LOGGER_RESOLVE_PYTHON") or sys.executable
    try:
        r = subprocess.run([py, str(Path(__file__).resolve()), "--resolve"], capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("O DaVinci demorou demais para responder. Ele está travado ou renderizando pesado?")
    try:
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError("Não consegui ler o DaVinci. " + (r.stderr.strip().splitlines() or [""])[-1])
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data


if __name__ == "__main__" and sys.argv[1:] == ["--resolve"]:
    try:
        print(json.dumps(read_resolve(), ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
