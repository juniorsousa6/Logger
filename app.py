#!/usr/bin/env python3
"""
Logger - cria a pasta do trabalho e descarrega os cartoes nela.
Abre uma janela no navegador (so no seu Mac, endereco 127.0.0.1).
Feche a janela do Terminal para encerrar.
"""
import copy
import datetime as dt
import json
import re
import os
import subprocess
import sys
import threading
import unicodedata
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cardlogger as cl
import media
import posrender
import fotos
import hds

HERE = Path(__file__).resolve().parent
SETTINGS_PATH = cl.STATE_DIR / "settings.json"
CATALOG_PATH = cl.STATE_DIR / "catalogo.json"
DEFAULT_SETTINGS = {
    "base_dir": "",
    "backup_dir": "",
    "folders": ["Ronin", "Fixa", "Fotos", "Áudio", "Action", "Drone"],
    "mode": "padrao",
    "card_prefs": {},
    "camera_prefs": {},  # numero de serie da camera -> destinos (vale para qualquer cartao nela)  # uuid / nome do volume -> destinos escolhidos da ultima vez
    "rename": False,   # renomear a copia com prefixo da camera
    "prefixes": {"Ronin": "RON", "Fixa": "FIXA", "Fixa Wide": "WIDE", "Fixa Zoom": "ZOOM", "Action": "ACT", "Drone": "DRN", "Áudio": "AUD", "Fotos": ""},
    "mhl": False,      # manifesto padrao cinema (ASC MHL)
    "auto_eject": True,
    "volume_kinds": {},  # uuid / nome -> "card" ou "drive" (quando voce corrige o app)  # ejeta cada cartao assim que ele estiver copiado e conferido
    "folders_on": None,  # pastas ligadas no ultimo trabalho (None = todas)
    "dropbox_on": False,
    "dropbox_dir": "",   # onde ficam as pastas de entrega no Dropbox
    "dropbox_folders": ["Drop", "Fotos", "4x5", "9x16"],
    "dropbox_folders_on": None,
    "dropbox_dest": "viot",  # "viot" = pasta do VIOT no Dropbox (atalho do cliente mais comum) ou "pasta" = outra pasta escolhida
    "viot_dir": "",          # pasta do VIOT no Dropbox (vazio = procura sozinho)
    "viot_title_fix": {},
    "hds_view": "grade",     # Meus HDs: "grade" ou "lista"
    "lr_catalog": "",        # catalogo do Lightroom (vazio = o .lrcat mais recente em ~/Pictures)    # Renomear drops: titulo lido do marcador -> titulo que o Junior corrigiu
}


def dropbox_guess():
    """Pasta do Dropbox no Mac (app novo usa ~/Library/CloudStorage, antigo usa ~/Dropbox)."""
    home = Path.home()
    cands = sorted((home / "Library" / "CloudStorage").glob("Dropbox*")) + [home / "Dropbox"]
    return next((str(c) for c in cands if c.is_dir()), "")


def viot_guess():
    """Pasta VIOT dentro do Dropbox (Dropbox > VIOT), sem ligar para maiusculas ou acento."""
    root = dropbox_guess()
    if not root:
        return ""
    try:
        for c in sorted(Path(root).iterdir()):
            if c.is_dir() and unicodedata.normalize("NFC", c.name).strip().lower() == "viot":
                return str(c)
    except OSError:
        pass
    return ""
JOB = {"thread": None, "progress": {}, "cancel": threading.Event()}
LOCK = threading.Lock()
SETTINGS_LOCK = threading.RLock()  # dois salvamentos ao mesmo tempo nao podem apagar um ao outro
SETTINGS_WRITERS = {"/api/settings", "/api/volume-kind", "/api/folders", "/api/create", "/api/log"}


def settings():
    s = copy.deepcopy(DEFAULT_SETTINGS)  # copia funda: mexer nos ajustes nunca altera o padrao
    s.update(cl.load_json(SETTINGS_PATH, {}))
    return s


def save_settings(s):
    cl.save_json(SETTINGS_PATH, s)


def suggested_date():
    now = dt.datetime.now()
    # depois da meia-noite (ate 6h) o normal e ainda ser o evento de ontem
    if now.hour < 6:
        now -= dt.timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def fmt_date(iso):  # 2026-11-15 -> 2026_11_15
    return iso.replace("-", "_")


def safe_name(s):
    """Nome valido de pasta: sem / : \\, sem caracteres de controle e sem comecar com ponto."""
    s = "".join(ch for ch in str(s or "") if ch not in '/:\\' and unicodedata.category(ch)[0] != "C")
    return unicodedata.normalize("NFC", s.strip().lstrip(".").strip())


def subfolders(path):
    p = Path(path)
    if not p.is_dir():
        return []
    return sorted(unicodedata.normalize("NFC", d.name) for d in p.iterdir() if d.is_dir() and not d.name.startswith((".", "_")))


def load_catalog():
    return cl.load_json(CATALOG_PATH, {"jobs": {}})


def update_catalog(job_dir, summary=None):
    with LOCK:
        cat = load_catalog()
        media.catalog_job(cat, job_dir, summary)
        cl.save_json(CATALOG_PATH, cat)
    if summary is not None and WATCHER is not None:
        WATCHER.rescan_path(job_dir)  # o HD do trabalho mudou: atualiza o mapa dele em Meus HDs


# ---------------------------------------------------------------- Meus HDs
WATCHER = None


def hd_kind(vol):
    """HD/SSD vira 'drive'; cartao (ou o que voce marcou como cartao) nunca entra em Meus HDs."""
    kinds = settings().get("volume_kinds", {})
    if (kinds.get(cl.volume_uuid(vol)) or kinds.get("name:" + vol.name)) == "card":
        return "card"
    kind = cl.media_kind(vol)
    if kind == "unknown":  # fora do Mac (testes): pasta sem nada de camera conta como HD
        if cl.looks_like_work_drive(vol):
            return "drive"
        return "card" if cl.identify(vol, cl.load_config()["profiles"]) else "drive"
    return kind


def hd_done(key, res):
    if res.get("ok") and not res.get("first") and (res.get("added") or res.get("removed")):
        notify("Meus HDs: %s atualizado" % res["name"], "%d entraram, %d saíram" % (res["added"], res["removed"]))
    elif res.get("ok") and res.get("first"):
        notify("Meus HDs: %s mapeado" % res["name"], "%d arquivos no mapa" % res["files"])


def hds_list():
    st = WATCHER.status() if WATCHER else {"online": {}, "pending": [], "queue": [], "current": None, "progress": {}, "last": {}}
    known = hds.drives()
    keys = {d["key"] for d in known}
    out = []
    for d in known:
        d = dict(d)
        d["online"] = d["key"] in st["online"]
        d["scanning"] = st["current"] == d["key"]
        d["queued"] = any(q["key"] == d["key"] for q in st["queue"])
        d["error"] = (st["last"].get(d["key"]) or {}).get("error", "")
        out.append(d)
    # sendo mapeado pela primeira vez (ainda sem linha no banco)
    for q in st["queue"]:
        if q["key"] not in keys:
            out.append({"id": None, "key": q["key"], "name": q["name"], "online": True, "queued": True, "scanning": False, "files": 0})
    if st["current"] and st["current"] not in keys:
        out.append({"id": None, "key": st["current"], "name": st["progress"].get("name", ""), "online": True, "scanning": True, "files": 0})
    ig = hds.ignored()
    loose = [{"key": k, "name": Path(v).name, "path": v, "ignored": k in ig} for k, v in st["online"].items()
             if k not in keys and k != st["current"] and not any(q["key"] == k for q in st["queue"])]
    return {"drives": out, "pending": st["pending"], "unmapped": loose, "progress": st["progress"], "current": st["current"]}


def hd_mount(drive_id):
    d = hds.drive_by(id_=int(drive_id))
    if not d:
        raise ValueError("HD não encontrado.")
    root = (WATCHER.status()["online"] if WATCHER else {}).get(d["key"])
    return d, (Path(root) if root else None)


def hd_reveal(body):
    d, root = hd_mount(body["drive"])
    if root is None:
        raise ValueError("Conecte o HD %s para abrir no Finder." % d["name"])
    rel = str(body.get("path", ""))
    target = (root / rel).resolve() if rel else root
    if root.resolve() not in (target, *target.parents) or not target.exists():
        raise ValueError("Esse item não está mais no HD. Ele será atualizado no próximo escaneio.")
    subprocess.run(["open", "-R", str(target)] if rel else ["open", str(target)])
    return {"ok": True}


def hd_search(q):
    res = hds.search(q)
    online = WATCHER.status()["online"] if WATCHER else {}
    keys = {d["id"]: d["key"] for d in hds.drives()}
    for r in res:
        r["online"] = keys.get(r["drive"]) in online
        r["job"] = bool(media.JOB_RE.match(r["name"])) and r["kind"] == "dir"
    # trabalhos do catalogo antigo que estao em HDs ainda nao mapeados
    mapped = set(keys.values())
    legacy = [j for j in media.search(load_catalog(), q) if q.strip()] if q.strip() else []
    cat = load_catalog().get("jobs", {})
    legacy = [j for j in legacy if (cat.get(j["path"], {}).get("hd_uuid") or "") not in mapped]
    return {"results": res[:300], "more": len(res) > 300, "legacy": legacy[:30]}


def hd_job_path(body):
    """Pasta de trabalho de um HD conectado (para 'Copiar mais cartões aqui')."""
    d, root = hd_mount(body["drive"])
    if root is None:
        raise ValueError("Conecte o HD %s primeiro." % d["name"])
    return str(root / body["path"])


def catalog_hd(path):
    root = Path(path)
    if not root.is_dir():
        raise ValueError("Pasta nao encontrada.")
    with LOCK:
        cat = load_catalog()
        found = media.catalog_hd(cat, root)
        cl.save_json(CATALOG_PATH, cat)
    return {"found": len(found), "jobs": [f.name for f in found]}


def osascript(lines, *args, **kw):
    """Roda AppleScript passando os textos como argumentos (item N of argv), nunca dentro do
    codigo: um texto com aspas nao vira comando."""
    cmd = ["osascript", "-e", "on run argv"]
    for ln in lines:
        cmd += ["-e", ln]
    cmd += ["-e", "end run", *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def osascript_choose_folder(prompt):
    r = osascript(["POSIX path of (choose folder with prompt (item 1 of argv))"], prompt)
    return r.stdout.strip().rstrip("/") if r.returncode == 0 else ""


# ---------------------------------------------------------------- actions
def create_job(body):
    base = Path(body["base_dir"]).expanduser()
    if not base.is_dir():
        raise ValueError("Escolha o HD/pasta onde o trabalho vai ficar.")
    event = safe_name(body.get("event", ""))
    if not event:
        raise ValueError("Escreva o nome do evento.")
    folders = [safe_name(f) for f in body.get("folders", []) if safe_name(f)]
    if not folders:
        raise ValueError("Ligue pelo menos uma pasta.")
    db_dir = (body.get("dropbox_dir") or "").strip() if body.get("dropbox_on") else ""
    db_folders = [safe_name(f) for f in body.get("dropbox_folders", []) if safe_name(f)]
    if db_dir and not Path(db_dir).is_dir():
        raise ValueError("A pasta do VIOT nao esta acessivel. O Dropbox esta aberto? Escolha de novo em Espelhar no Dropbox." if body.get("dropbox_dest") == "viot"
                         else "A pasta do Dropbox nao esta acessivel. Escolha de novo em Espelhar no Dropbox.")
    days = body.get("days") or [body.get("date", "")]
    for d in days:
        try:
            dt.date.fromisoformat(str(d))
        except ValueError:
            raise ValueError(f"Data invalida: {d!r}. Use o calendario.") from None
    job = base / f"{fmt_date(days[0])} - {event}"
    targets = []
    if body.get("mode") == "multi":
        for i, d in enumerate(days, 1):
            targets.append(job / f"{fmt_date(d)} - Dia {i}")
    else:
        targets.append(job)
    for t in targets:
        for f in folders:
            (t / f).mkdir(parents=True, exist_ok=True)
    s = settings()
    backup = (body.get("backup_dir") or "").strip()
    if backup:
        if not Path(backup).is_dir():
            raise ValueError("A pasta de backup nao esta acessivel (o HD de backup esta conectado?).")
        for t in targets:
            for f in folders:
                (Path(backup) / t.relative_to(base) / f).mkdir(parents=True, exist_ok=True)
    db_job = ""
    if db_dir:  # espelho de entrega: mesmo nome do trabalho, pastas proprias do Dropbox
        dbj = Path(db_dir) / job.name
        dbj.mkdir(parents=True, exist_ok=True)
        for f in db_folders:
            (dbj / f).mkdir(exist_ok=True)
        db_job = str(dbj)
    s.update({"base_dir": str(base), "backup_dir": backup, "folders_on": folders,
              "mode": body.get("mode", "padrao"), "dropbox_on": bool(body.get("dropbox_on")),
              "dropbox_folders_on": db_folders})
    if db_dir:
        dest = "viot" if body.get("dropbox_dest") == "viot" else "pasta"
        s["dropbox_dest"] = dest
        s["viot_dir" if dest == "viot" else "dropbox_dir"] = db_dir
    save_settings(s)
    update_catalog(job)
    return {"job_dir": str(job), "targets": [str(t) for t in targets], "dropbox_job": db_job}


def open_job(path):
    job = Path(path)
    if not job.is_dir():
        raise ValueError("Pasta nao encontrada.")
    days = [str(job / d) for d in subfolders(job) if re.match(r"\d{4}_\d{2}_\d{2} - Dia \d+$", d)]
    return {"job_dir": str(job), "targets": days or [str(job)]}


def _fold(name):
    """Compara nomes de pasta sem ligar para acento, maiuscula ou espaco."""
    n = unicodedata.normalize("NFKD", name or "")
    return " ".join("".join(ch for ch in n if not unicodedata.combining(ch)).casefold().split())


def resolve_dest(want, subs):
    """Acha a pasta do trabalho para o destino padrao. 'Fixa' acha 'Fixa Wide'.
    Devolve None quando nao existe ou quando ha mais de uma parecida (o app pergunta)."""
    if not want:
        return None
    w = _fold(want)
    exact = [x for x in subs if _fold(x) == w]
    if exact:
        return exact[0]
    near = [x for x in subs if _fold(x).startswith(w) or w.startswith(_fold(x) + " ")]
    return near[0] if len(near) == 1 else None


def job_days(target):
    """Dias aceitos para os arquivos do trabalho: a data da pasta e o dia seguinte (evento que vira a meia-noite)."""
    for part in [Path(target)] + list(Path(target).parents) if target else []:
        m = re.match(r"^(\d{4})_(\d{2})_(\d{2})", part.name)
        if m:
            d = dt.date(*map(int, m.groups()))
            return {d, d + dt.timedelta(days=1)}
    return set()


def other_days(items, days):
    """Arquivos com data fora do trabalho (ex.: gravacao antiga esquecida no cartao)."""
    if not days:
        return 0, ""
    out = [dt.date.fromtimestamp(it["mtime"]) for it in items if it.get("mtime") and dt.date.fromtimestamp(it["mtime"]) not in days]
    if not out:
        return 0, ""
    common = max(set(out), key=out.count)
    return len(out), common.strftime("%d/%m")


def card_routes(prof, items, remembered, subs, days=None):
    """Destino de cada rota: escolha lembrada (se a pasta existe) > padrao do perfil.
    'ask' = o app nao tem certeza e precisa que voce escolha a pasta."""
    remembered = remembered if isinstance(remembered, dict) else {}  # formato antigo (lista) e ignorado
    routes = []
    for i, r in enumerate(prof["routes"]):
        label = r.get("label", r["to"])
        n = sum(1 for it in items if it["route"] == i)
        b = sum(it["size"] for it in items if it["route"] == i)
        dest, why = None, ""
        mem = None if r.get("lock") else remembered.get(label)
        if mem and (resolve_dest(mem, subs) or not subs):
            dest, why = resolve_dest(mem, subs) or mem, "lembrado"
        else:
            dest = (resolve_dest(r["to"], subs) if subs else r["to"]) or None
            why = "regra" if dest else ""
        old, old_day = other_days([it for it in items if it["route"] == i], days or set())
        routes.append({"label": label, "dest": dest if dest is not None else "", "default": r["to"],
                       "ask": dest is None and n > 0, "why": why, "files": n, "bytes": cl.human(b),
                       "other_day": old, "other_day_date": old_day})
    return routes


def work_volumes(ledger, s):
    """Volumes que ja receberam copias do Logger (HDs de trabalho/backup): nunca sao cartao."""
    roots = set()
    vd = str(cl.VOLUMES_DIR) + os.sep
    paths = [s.get("base_dir", ""), s.get("backup_dir", "")]
    for card in ledger.get("cards", {}).values():
        for rec in list(card.get("files", {}).values())[:20]:
            paths.append(rec.get("dest", ""))
    for p in paths:
        if p and p.startswith(vd):
            roots.add(p[len(vd):].split(os.sep)[0])
    return roots


def classify_volume(vol, prof, s, work):
    """'card' (cartao), 'drive_card' (HD/SSD usado como cartao, ex.: Blackmagic) ou 'drive' (esconder)."""
    uuid = cl.volume_uuid(vol)
    kinds = s.get("volume_kinds", {})
    forced = kinds.get(uuid) or kinds.get("name:" + vol.name)
    kind = cl.media_kind(vol)
    if forced == "drive":
        return "drive", "você marcou que não é cartão"
    if forced == "card":
        return ("card" if kind == "card" else "drive_card"), ""
    if kind == "card":
        return "card", ""
    if vol.name in work or cl.looks_like_work_drive(vol):
        return "drive", "HD de trabalho"
    if kind == "unknown":
        return "card", ""  # fora do Mac (testes): sem como saber
    if prof:
        return "drive_card", ""  # HD/SSD so com arquivos de camera: gravador externo
    return "drive", "HD/SSD"


def list_cards(target, backup=""):
    cfg = cl.load_config()
    ledger = cl.load_ledger()
    s = settings()
    prefs = s["card_prefs"]
    work = work_volumes(ledger, s)
    subs = subfolders(target) if target else []
    out = []
    exclude = [p for p in (target, backup) if p]
    for vol in cl.mounted_volumes(exclude):
        prof = cl.identify(vol, cfg["profiles"])
        kind, why = classify_volume(vol, prof, s, work)
        if kind == "drive":
            out.append({"volume": vol.name, "path": str(vol), "uuid": cl.volume_uuid(vol), "profile": None,
                        "hidden": True, "why": why})
            continue
        if not prof:
            out.append({"volume": vol.name, "path": str(vol), "profile": None})
            continue
        c = cl.scan_card(vol, prof, ledger)
        # o nome do volume (Untitled, SD_Card, NO NAME...) se repete entre cameras diferentes:
        # so serve de memoria quando o cartao nao tem UUID
        cam = cl.camera_id(vol)
        cam_mem = s.get("camera_prefs", {}).get(cam["key"]) if cam and cam["key"] else None
        card_mem = prefs.get(c["uuid"]) if not c["uuid"].startswith("noid-") else prefs.get("name:" + vol.name)
        remembered = {**(card_mem or {}), **(cam_mem or {})} if isinstance(card_mem or {}, dict) else cam_mem
        routes = card_routes(prof, c["items"], remembered, subs, job_days(target))
        out.append({"volume": vol.name, "path": str(vol), "uuid": c["uuid"], "profile": prof["name"], "kind": kind,
                    "new": len(c["items"]), "bytes": cl.human(c["bytes"]), "already": c["already"],
                    "ignored": len(c["ignored"]), "routes": routes, "ask": any(r["ask"] for r in routes),
                    "safe_to_format": c["safe_to_format"], "missing_on_hd": c["missing_on_hd"],
                    "offline": c["offline"], "ignored_media": len(c["ignored_media"]),
                    "ignored_media_names": [Path(x).name for x in c["ignored_media"][:5]],
                    "recopy": sum(1 for it in c["items"] if it.get("recopy")),
                    "camera": cam["label"] if cam else "", "camera_known": bool(cam_mem)})
    return out


def folder_has_files(d: Path) -> bool:
    return any(f.is_file() and not f.name.startswith(".") for f in d.rglob("*"))


def empty_folders(target):
    """Pastas do trabalho que continuam sem nenhum arquivo."""
    return [name for name in subfolders(target) if not folder_has_files(Path(target) / name)]


def folder_status(target):
    """Cada pasta do trabalho e se ja recebeu arquivo (para mostrar o que ainda espera cartao)."""
    return [{"name": n, "filled": folder_has_files(Path(target) / n)} for n in subfolders(target)]


START = threading.Lock()


def start_log(body):
    # um clique duplo (ou duas janelas) nao pode iniciar duas copias ao mesmo tempo
    if not START.acquire(blocking=False):
        raise ValueError("Ja tem um logger comecando.")
    try:
        if JOB["thread"] and JOB["thread"].is_alive():
            raise ValueError("Ja tem um logger rodando.")
        return _start_log(body)
    finally:
        START.release()


def _start_log(body):
    target = Path(body["target"])
    if not target.is_dir():
        raise ValueError("Pasta de destino nao existe.")
    cfg = cl.load_config()
    ledger = cl.load_ledger()
    s = settings()
    cards = []
    for sel in body["cards"]:
        vol = Path(sel["path"])
        if not vol.is_dir():  # tirado do leitor entre a tela e o clique: nao some calado do relatorio
            raise ValueError(f"O cartão {vol.name} não está mais conectado. Coloque de novo ou desmarque ele.")
        prof = cl.identify(vol, cfg["profiles"])
        if not prof:
            raise ValueError(f"Não reconheço mais o cartão {vol.name}. Atualize a tela e tente de novo.")
        c = cl.scan_card(vol, prof, ledger)  # escaneia de novo na hora de copiar
        dests = sel["dests"]
        if not isinstance(dests, list) or len(dests) != len(prof["routes"]):
            raise ValueError(f"Os destinos do cartao {vol.name} mudaram. Atualize a tela e tente de novo.")
        dests = [safe_name(d) if isinstance(d, str) else d for d in dests]
        if any(d is None for d in dests):
            raise ValueError(f"Escolha a pasta do cartao {vol.name} antes de copiar.")
        # rota marcada como "ignorar" sai da lista
        c["items"] = [it for it in c["items"] if dests[it["route"]]]
        c["bytes"] = sum(it["size"] for it in c["items"])
        c["dests"] = [d or "_ignorado" for d in dests]
        c["path"] = str(vol)
        cards.append(c)
        # "nao copiar" nunca e lembrado: num cartao com outro conteudo ele deixaria gravacao para tras em silencio
        mem = {r.get("label", r["to"]): d for r, d in zip(prof["routes"], dests) if d}
        s["card_prefs"]["name:" + vol.name if c["uuid"].startswith("noid-") else c["uuid"]] = mem
        cam = cl.camera_id(vol)
        if cam and cam["key"] and mem:   # a camera lembra para onde vai, troque o cartao que for
            s.setdefault("camera_prefs", {})[cam["key"]] = mem
    backups = []
    backup = (body.get("backup_dir") or "").strip()
    if backup:
        if not Path(backup).is_dir():
            raise ValueError("A pasta de backup nao esta acessivel (o HD de backup esta conectado?).")
        backups.append((Path(body["job_dir"]).parent, Path(backup)))
    s["backup_dir"] = backup
    s["rename"] = bool(body.get("rename"))
    s["mhl"] = bool(body.get("mhl"))
    s["auto_eject"] = bool(body.get("auto_eject", s.get("auto_eject", True)))
    if isinstance(body.get("prefixes"), dict):
        s["prefixes"].update({k: safe_name(v) for k, v in body["prefixes"].items()})
    prefixes = s["prefixes"] if s["rename"] else {}
    save_settings(s)
    prog = {"phase": "comecando", "hold": True, "ejected": []}
    JOB["progress"] = prog
    JOB["cancel"] = cancel = threading.Event()
    auto_eject = s["auto_eject"]

    def card_done(c, rep):
        """Cartao copiado e conferido: ejeta sozinho e avisa para colocar o proximo."""
        vol = c["volume"]
        if rep.get("cancelado"):
            prog["ejected"].append({"volume": vol, "ok": False, "msg": "cópia cancelada, não ejetei"})
            return
        if not rep.get("conferido_ok"):
            notify("Cartão com problema", f"{vol}: não ejetei. Veja o que falhou no Logger.", ok=False)
            prog["ejected"].append({"volume": vol, "ok": False, "msg": "com problema, não ejetei"})
            return
        if not rep.get("pode_formatar"):  # copiou certo, mas ainda ha gravacao so no cartao
            n = len(rep.get("sobrou") or [])
            ex = ", ".join(Path(x).name for x in (rep.get("sobrou") or [])[:3]) + ("…" if n > 3 else "")
            msg = (f"ficaram {n} arquivo(s) só no cartão ({ex}), não ejetei" if n
                   else "não consegui conferir o cartão, não ejetei")
            notify("Não formate esse cartão", f"{vol}: {msg}.", ok=False)
            prog["ejected"].append({"volume": vol, "ok": False, "msg": msg})
            return
        if not auto_eject or Path(c["path"]).parent != cl.VOLUMES_DIR:
            return
        r = eject([c["path"]])[0]
        prog["ejected"].append({"volume": vol, "ok": r["ok"], "msg": "ejetado" if r["ok"] else r["msg"]})
        if r["ok"]:
            notify("Cartão ejetado", f"{vol} copiado, conferido e ejetado. Pode colocar o próximo.")
        else:
            notify("Não consegui ejetar", f"{vol} está conferido, mas o Mac não ejetou: {r['msg']}", ok=False)

    def work():
        try:
            rep = cl.run_log(target, cards, ledger, per_card_subfolder=bool(body.get("per_card")),
                             progress=prog, backups=backups, prefixes=prefixes, mhl=s["mhl"],
                             on_card_done=card_done, cancel=cancel)
            prog["phase"] = "relatorio"
            try:
                path, summary = media.build_report(target, prog)
                prog["report_html"] = str(path)
                prog["report_summary"] = {"seconds": media.fmt_dur(summary["seconds"]), "clips": summary["clips"],
                                          "photos": summary["photos"], "alerts": summary["alerts"]}
                for root, broot in backups:  # relatorio tambem no backup
                    try:
                        dst = broot / Path(path).relative_to(root)
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        import shutil
                        shutil.copy2(path, dst)
                        shutil.copytree(Path(target) / "_logs" / "thumbs", dst.parent / "_logs" / "thumbs",
                                        dirs_exist_ok=True)
                    except (ValueError, OSError):
                        pass
                update_catalog(body["job_dir"], summary)
            except Exception as e:  # noqa: BLE001  (relatorio nunca derruba o logger)
                prog["report_error"] = str(e)
            prog["empty_folders"] = empty_folders(target)
            prog["phase"] = "pronto"
            if rep.get("cancelado"):
                msg = "Cópia cancelada. O que foi conferido está salvo"
            elif not rep["tudo_ok"]:
                msg = "ATENÇÃO: há arquivos com problema"
            elif not rep["pode_formatar_tudo"]:
                msg = "Copiado e conferido, mas ficou gravação em algum cartão. Não formate antes de ver"
            else:
                msg = "Tudo conferido"
            if prog["empty_folders"]:
                msg += ". Pastas sem arquivo: " + ", ".join(prog["empty_folders"])
            notify("Logger terminou", msg, ok=rep["pode_formatar_tudo"] and not prog["empty_folders"])
        except Exception as e:  # noqa: BLE001
            prog["phase"] = "erro"
            prog["fatal"] = str(e)
            notify("Logger parou", str(e), ok=False)

    t = threading.Thread(target=work, daemon=True)
    JOB["thread"] = t
    t.start()
    return {"ok": True}


def notify(title, msg, ok=True):
    """Notificacao do macOS + som (para quando voce estiver longe do Mac)."""
    try:
        osascript(["display notification (item 2 of argv) with title (item 1 of argv) sound name (item 3 of argv)"],
                  title, msg, "Glass" if ok else "Basso")
    except FileNotFoundError:
        pass


def eject_msg(raw):
    """Erro do diskutil em portugues: quem esta segurando o cartao e o que fazer."""
    m = re.search(r"dissented by PID \d+ \(([^)]*)\)", raw or "")
    if m or "could not be unmounted" in (raw or "") or "Resource busy" in (raw or ""):
        who = Path(m.group(1)).name if m else ""
        if "QuickLook" in (m.group(1) if m else ""):
            who = "a pré-visualização do Mac (QuickLook)"
        elif who:
            who = f"o programa {who}"
        return ("não ejetei porque " + (who or "outro programa") + " ainda está usando o cartão. "
                "A cópia está ok: feche a janela que mostra esse cartão e ejete pelo Finder.")
    return raw


def eject(paths):
    res = []
    for p in paths:
        if WATCHER is not None:
            WATCHER.cancel_path(p)
        try:
            r = subprocess.run(["diskutil", "eject", p], capture_output=True, text=True, timeout=60)
            ok = r.returncode == 0
            raw = (r.stdout + r.stderr).strip()
            res.append({"path": p, "ok": ok, "msg": raw if ok else eject_msg(raw)})
        except (OSError, subprocess.TimeoutExpired) as e:
            res.append({"path": p, "ok": False, "msg": str(e)})
    return res


# ---------------------------------------------------------------- pos-render: renomear drops
def drops_plan():
    """Le o DaVinci (renders da fila, clipes e marcadores do Ghost) e monta a previa dos nomes."""
    data = posrender.read_resolve_subprocess()
    return posrender.build_plan(data, settings().get("viot_title_fix") or {})


def _undo_path(p):
    """So aceita registro de dentro de ~/.cardlogger/renomeios (nada de caminho vindo de fora)."""
    path = Path(p).resolve()
    if posrender.UNDO_DIR.resolve() not in path.parents or path.suffix != ".json":
        raise ValueError("registro de renomeio invalido")
    return str(path)


def drops_apply(body):
    """body: {target_dir, renames:[{old,new}], fixes:{titulo lido: titulo corrigido}}"""
    folder = Path(body["target_dir"])
    if not folder.is_dir():
        raise ValueError("A pasta do render nao existe mais.")
    renames = [{"old": str(r["old"]), "new": safe_name(str(r["new"]))} for r in body.get("renames", [])
               if isinstance(r, dict) and r.get("old") and r.get("new")]
    for r in renames:
        if "/" in r["old"] or r["old"] in (".", ".."):
            raise ValueError("nome de arquivo invalido")
    fixes = body.get("fixes")
    if isinstance(fixes, dict) and fixes:
        with SETTINGS_LOCK:
            s = settings()
            fx = s.setdefault("viot_title_fix", {})
            for k, v in fixes.items():
                k, v = str(k).strip(), safe_name(str(v)).strip()
                if k and v and k != v:
                    fx[k] = v
            save_settings(s)
    return posrender.apply(folder, renames)


# ---------------------------------------------------------------- limpeza das fotos (Lightroom)
def lr_catalog():
    if os.environ.get("LOGGER_LR_CATALOG"):  # testes
        return os.environ["LOGGER_LR_CATALOG"]
    s = settings()
    if s.get("lr_catalog") and Path(s["lr_catalog"]).is_file():
        return s["lr_catalog"]
    cats = fotos.find_catalogs()
    if not cats:
        raise ValueError("Não achei nenhum catálogo do Lightroom em Imagens.")
    return cats[0]


def fotos_festas():
    cat = lr_catalog()
    with fotos.Catalog(cat) as c:
        return {"catalog": cat, "festas": c.festas()}


def fotos_plan(festa_dir):
    if not Path(festa_dir).is_dir():
        raise ValueError("A pasta da festa não está acessível. O HD está conectado?")
    cat = lr_catalog()
    with fotos.Catalog(cat) as c:
        p = fotos.plan(festa_dir, c.images_under(festa_dir))
    p["catalog"] = cat
    return p


def fotos_trash(body):
    """1) copia as fotos com estrela que faltam para o Select Fotos (conferindo cada uma);
    2) refaz o plano: so segue se TODA foto com estrela esta salva;
    3) manda para a Lixeira so as pastas que a previa mostrou."""
    p = fotos_plan(body["festa_dir"])
    if not p["ready"]:
        raise ValueError("Ainda não dá para limpar: " + " ".join(p["reasons"]))
    want = sorted(body.get("dirs") or [])
    have = sorted(t["dir"] for t in p["trash"])
    if want != have or body.get("files") != p["trash_files"] or body.get("copy", 0) != len(p["to_copy"]):
        raise ValueError("As pastas mudaram desde a prévia. Confira de novo antes de apagar.")
    prog = body.get("_progress")
    copied = fotos.copy_to_select(p["festa"], p["to_copy"], prog) if p["to_copy"] else []
    if prog:
        prog(len(p["to_copy"]), len(p["to_copy"]), "Lixeira")
    bad = [c for c in copied if not c["ok"]]
    if bad:
        return {"copied": copied, "results": [], "select_files": fotos_plan(body["festa_dir"])["select_files"],
                "error": "Não consegui copiar %d foto(s); nada foi para a Lixeira." % len(bad)}
    p = fotos_plan(body["festa_dir"])
    if not p["ready"] or p["to_copy"]:
        raise ValueError("Copiei, mas a conferência não fechou; nada foi para a Lixeira. " + " ".join(p["reasons"]))
    if sorted(t["dir"] for t in p["trash"]) != have:
        raise ValueError("As pastas mudaram durante a cópia; nada foi para a Lixeira.")
    targets = [fotos.check_trash_target(p["festa"], d) for d in have]
    res = fotos.to_trash(targets)
    if all(r["ok"] for r in res):
        notify("Fotos limpas", f"{len(copied)} fotos salvas no Select Fotos e {p['trash_files']} arquivos foram para a Lixeira.")
    return {"copied": copied, "results": res, "select_files": p["select_files"]}


FJOB = {"running": False}
FJOB_LOCK = threading.Lock()


def fotos_start(body):
    """Copiar dezenas de RAW leva minutos: roda numa linha separada e a tela acompanha por /api/fotos/status."""
    with FJOB_LOCK:
        if FJOB.get("running"):
            raise ValueError("Já estou limpando uma festa. Espere terminar.")
        p = fotos_plan(body["festa_dir"])  # confere ja (erro volta na hora)
        if not p["ready"]:
            raise ValueError("Ainda não dá para limpar: " + " ".join(p["reasons"]))
        FJOB.clear()
        FJOB.update({"running": True, "festa": p["festa"], "done": 0, "total": len(p["to_copy"]), "now": ""})

    def prog(i, n, name):
        FJOB.update({"done": i, "total": n, "now": name})

    def run():
        try:
            res = fotos_trash(dict(body, _progress=prog))
            FJOB.update({"result": res})
        except Exception as e:  # noqa: BLE001 - vai para a tela
            FJOB.update({"error": str(e)})
        finally:
            FJOB["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return {"started": True}


def fotos_status():
    return dict(FJOB)


# ---------------------------------------------------------------- http
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def allowed(self, post):
        """So aceita pedidos da propria janela do Logger. Um site aberto no navegador nao pode
        mandar comandos: endereco tem de ser este Mac, origem nula (iframe isolado) e recusada
        e pedido que muda algo tem de vir como JSON (o que obriga o navegador a perguntar antes)."""
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{host}":
            return False
        if post and not self.headers.get("Content-Type", "").split(";")[0].strip().lower() == "application/json":
            return False
        return True

    def send(self, code, data, ctype="application/json"):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed(post=False):
            return self.send(403, {"error": "origem nao permitida"})
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                return self.send(200, (HERE / "ui.html").read_bytes(), "text/html")
            if u.path == "/api/init":
                s = settings()
                return self.send(200, {"settings": {k: s[k] for k in ("base_dir", "backup_dir", "folders", "mode", "rename", "prefixes", "mhl", "auto_eject",
                                                                       "folders_on", "dropbox_on", "dropbox_dir", "dropbox_folders", "dropbox_folders_on",
                                                                       "dropbox_dest", "viot_dir", "hds_view")},
                                       "date": suggested_date(), "dropbox_guess": dropbox_guess(), "viot_guess": viot_guess(),
                                       "warnings": list(cl.WARNINGS)})
            if u.path == "/api/cards":
                return self.send(200, list_cards(q.get("target", ""), q.get("backup", "")))
            if u.path == "/api/folder-status":
                return self.send(200, folder_status(q["target"]))
            if u.path == "/api/subfolders":
                return self.send(200, subfolders(q["path"]))
            if u.path == "/api/hds":
                return self.send(200, hds_list())
            if u.path == "/api/hds/ls":
                d = hds.drive_by(id_=int(q["drive"]))
                if not d:
                    raise ValueError("HD não encontrado.")
                return self.send(200, {"drive": d, "items": hds.ls(d["id"], q.get("path", ""))})
            if u.path == "/api/hds/search":
                return self.send(200, hd_search(q.get("q", "")))
            if u.path == "/api/search":
                return self.send(200, media.search(load_catalog(), q.get("q", "")))
            if u.path == "/api/fotos/status":
                return self.send(200, fotos_status())
            if u.path == "/api/fotos/festas":
                return self.send(200, fotos_festas())
            if u.path == "/api/drops/plan":
                return self.send(200, drops_plan())
            if u.path == "/api/progress":
                for _ in range(20):  # a copia mexe no progresso enquanto lemos: tenta de novo
                    try:
                        return self.send(200, json.loads(json.dumps(JOB["progress"], ensure_ascii=False, default=str)))
                    except RuntimeError:
                        continue
                return self.send(200, {"phase": JOB["progress"].get("phase", "")})
            return self.send(404, {"error": "nao encontrado"})
        except KeyError as e:
            return self.send(400, {"error": f"faltou o campo {e}"})
        except Exception as e:  # noqa: BLE001
            return self.send(400, {"error": str(e)})

    def do_POST(self):
        if not self.allowed(post=True):
            return self.send(403, {"error": "origem nao permitida"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("corpo invalido")
        except ValueError:
            return self.send(400, {"error": "corpo invalido"})
        if self.path in SETTINGS_WRITERS:
            with SETTINGS_LOCK:
                return self._post(body)
        return self._post(body)

    def _post(self, body):
        try:
            if self.path == "/api/pick-folder":
                return self.send(200, {"path": osascript_choose_folder(body.get("prompt", "Escolha a pasta"))})
            if self.path == "/api/create":
                return self.send(200, create_job(body))
            if self.path == "/api/open-job":
                return self.send(200, open_job(body["path"]))
            if self.path == "/api/settings":
                s = settings()
                for k in ("base_dir", "backup_dir", "rename", "mhl", "auto_eject", "dropbox_on", "dropbox_dir", "viot_dir"):
                    if k in body:
                        s[k] = body[k]
                if body.get("hds_view") in ("grade", "lista"):
                    s["hds_view"] = body["hds_view"]
                if body.get("dropbox_dest") in ("viot", "pasta"):
                    s["dropbox_dest"] = body["dropbox_dest"]
                if isinstance(body.get("prefixes"), dict):
                    s["prefixes"] = {k: safe_name(v) for k, v in body["prefixes"].items()}
                for k in ("folders", "folders_on", "dropbox_folders", "dropbox_folders_on"):
                    if isinstance(body.get(k), list):
                        s[k] = [safe_name(f) for f in body[k] if safe_name(f)]
                save_settings(s)
                return self.send(200, {"ok": True})
            if self.path == "/api/volume-kind":
                s = settings()
                kinds = s.setdefault("volume_kinds", {})
                for k in (body.get("uuid"), "name:" + body.get("volume", "")):
                    if k and k != "name:":
                        if body.get("kind") in ("card", "drive"):
                            kinds[k] = body["kind"]
                        else:
                            kinds.pop(k, None)
                save_settings(s)
                return self.send(200, {"ok": True})
            if self.path == "/api/folders":
                s = settings()
                s["folders"] = [safe_name(f) for f in body["folders"] if safe_name(f)]
                save_settings(s)
                return self.send(200, {"ok": True})
            if self.path == "/api/hds/map":
                return self.send(200, WATCHER.map(body["path"]))
            if self.path == "/api/hds/skip":
                WATCHER.skip(str(body["key"]), bool(body.get("forever")))
                return self.send(200, {"ok": True})
            if self.path == "/api/hds/rescan":
                d, root = hd_mount(body["drive"])
                if root is None:
                    raise ValueError("Conecte o HD %s para atualizar." % d["name"])
                WATCHER.enqueue(d["key"])
                return self.send(200, {"ok": True})
            if self.path == "/api/hds/forget":
                WATCHER.forget(int(body["drive"]))
                return self.send(200, {"ok": True})
            if self.path == "/api/hds/reveal":
                return self.send(200, hd_reveal(body))
            if self.path == "/api/hds/job":
                return self.send(200, open_job(hd_job_path(body)))
            if self.path == "/api/catalog-hd":
                return self.send(200, catalog_hd(body["path"]))
            if self.path == "/api/report":
                path, summary = media.build_report(body["target"])
                return self.send(200, {"path": str(path)})
            if self.path == "/api/log":
                return self.send(200, start_log(body))
            if self.path == "/api/cancel":
                JOB["cancel"].set()
                return self.send(200, {"ok": True})
            if self.path == "/api/fotos/plan":
                return self.send(200, fotos_plan(body["festa_dir"]))
            if self.path == "/api/fotos/trash":
                return self.send(200, fotos_start(body))
            if self.path == "/api/drops/apply":
                return self.send(200, drops_apply(body))
            if self.path == "/api/drops/undo":
                return self.send(200, posrender.undo(_undo_path(body["undo"])))
            if self.path == "/api/notify":
                notify(str(body.get("title", "Logger"))[:80], str(body.get("msg", ""))[:200], body.get("ok", True) is not False)
                return self.send(200, {"ok": True})
            if self.path == "/api/eject":
                return self.send(200, eject(body["paths"]))
            if self.path == "/api/reveal":
                if not Path(body["path"]).exists():
                    raise ValueError("Esse arquivo ou pasta nao existe mais.")
                subprocess.run(["open", body["path"]])
                return self.send(200, {"ok": True})
            return self.send(404, {"error": "nao encontrado"})
        except KeyError as e:
            return self.send(400, {"error": f"faltou o campo {e}"})
        except Exception as e:  # noqa: BLE001
            return self.send(400, {"error": str(e)})


def main():
    import urllib.request
    port = int(os.environ.get("LOGGER_PORT", "8765"))
    srv = None
    parent = int(os.environ.get("LOGGER_PARENT_PID", "0") or 0)
    if parent:  # rodando dentro do Logger.app: some junto com o app, mesmo se ele fechar de repente
        def watch():
            import time
            while True:
                time.sleep(2)
                try:
                    os.kill(parent, 0)
                except OSError:
                    os._exit(0)
        threading.Thread(target=watch, daemon=True).start()
    for p in (range(port, port + 10) if port else [0]):
        url = f"http://127.0.0.1:{p}/"
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), H)
            break
        except OSError:
            # porta ocupada: se for o proprio Logger ja aberto, so mostra ele de novo
            try:
                with urllib.request.urlopen(url + "api/init", timeout=2) as r:
                    if b"settings" in r.read():
                        print(f"O Logger ja esta aberto em {url}")
                        if not os.environ.get("LOGGER_NO_BROWSER"):
                            webbrowser.open(url)
                        return
            except Exception:  # noqa: BLE001
                pass
    if srv is None:
        sys.exit("Nao achei uma porta livre (8765-8774). Reinicie o Mac e tente de novo.")
    global WATCHER
    if os.environ.get("LOGGER_HD_WATCH", "1") != "0":
        WATCHER = hds.Watcher(kind_of=hd_kind, busy=lambda: bool(JOB["thread"] and JOB["thread"].is_alive()),
                              on_done=hd_done, interval=float(os.environ.get("LOGGER_HD_INTERVAL", "4"))).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"LOGGER_URL {url}", flush=True)
    print(f"Logger aberto em {url}  (feche esta janela para encerrar)", flush=True)
    if not os.environ.get("LOGGER_NO_BROWSER"):
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
