#!/usr/bin/env python3
"""
Logger - cria a pasta do trabalho e descarrega os cartoes nela.
Abre uma janela no navegador (so no seu Mac, endereco 127.0.0.1).
Feche a janela do Terminal para encerrar.
"""
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

HERE = Path(__file__).resolve().parent
SETTINGS_PATH = cl.STATE_DIR / "settings.json"
CATALOG_PATH = cl.STATE_DIR / "catalogo.json"
DEFAULT_SETTINGS = {
    "base_dir": "",
    "backup_dir": "",
    "folders": ["Ronin", "Fixa", "Fotos", "Áudio", "Action", "Drone"],
    "mode": "padrao",
    "card_prefs": {},  # uuid / nome do volume -> destinos escolhidos da ultima vez
    "rename": False,   # renomear a copia com prefixo da camera
    "prefixes": {"Ronin": "RON", "Fixa": "FIXA", "Fixa Wide": "WIDE", "Fixa Zoom": "ZOOM", "Action": "ACT", "Drone": "DRN", "Áudio": "AUD", "Fotos": ""},
    "mhl": False,      # manifesto padrao cinema (ASC MHL)
    "auto_eject": True,  # ejeta cada cartao assim que ele estiver copiado e conferido
    "folders_on": None,  # pastas ligadas no ultimo trabalho (None = todas)
    "dropbox_on": False,
    "dropbox_dir": "",   # onde ficam as pastas de entrega no Dropbox
    "dropbox_folders": ["Drop", "Fotos", "4x5", "9x16"],
    "dropbox_folders_on": None,
}


def dropbox_guess():
    """Pasta do Dropbox no Mac (app novo usa ~/Library/CloudStorage, antigo usa ~/Dropbox)."""
    home = Path.home()
    cands = sorted((home / "Library" / "CloudStorage").glob("Dropbox*")) + [home / "Dropbox"]
    return next((str(c) for c in cands if c.is_dir()), "")
JOB = {"thread": None, "progress": {}}
LOCK = threading.Lock()


def settings():
    s = dict(DEFAULT_SETTINGS)
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
    return unicodedata.normalize("NFC", "".join(ch for ch in s if ch not in '/:\\').strip())


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


def catalog_hd(path):
    root = Path(path)
    if not root.is_dir():
        raise ValueError("Pasta nao encontrada.")
    with LOCK:
        cat = load_catalog()
        found = media.catalog_hd(cat, root)
        cl.save_json(CATALOG_PATH, cat)
    return {"found": len(found), "jobs": [f.name for f in found]}


def osascript_choose_folder(prompt):
    script = f'POSIX path of (choose folder with prompt "{prompt}")'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
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
        raise ValueError("A pasta do Dropbox nao esta acessivel. Escolha de novo em Dropbox.")
    days = body.get("days") or [body["date"]]
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
        s["dropbox_dir"] = db_dir
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


def card_routes(prof, items, remembered, subs):
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
        if mem == "":
            dest, why = "", "lembrado"          # voce marcou "nao copiar" para esse cartao
        elif mem and (resolve_dest(mem, subs) or not subs):
            dest, why = resolve_dest(mem, subs) or mem, "lembrado"
        else:
            dest = resolve_dest(r["to"], subs) if subs else r["to"]
            why = "regra" if dest else ""
        routes.append({"label": label, "dest": dest if dest is not None else "", "default": r["to"],
                       "ask": dest is None and n > 0, "why": why, "files": n, "bytes": cl.human(b)})
    return routes


def list_cards(target, backup=""):
    cfg = cl.load_config()
    ledger = cl.load_ledger()
    prefs = settings()["card_prefs"]
    subs = subfolders(target) if target else []
    out = []
    exclude = [p for p in (target, backup) if p]
    for vol in cl.mounted_volumes(exclude):
        prof = cl.identify(vol, cfg["profiles"])
        if not prof:
            out.append({"volume": vol.name, "path": str(vol), "profile": None})
            continue
        c = cl.scan_card(vol, prof, ledger)
        remembered = prefs.get(c["uuid"]) or prefs.get("name:" + vol.name)
        routes = card_routes(prof, c["items"], remembered, subs)
        out.append({"volume": vol.name, "path": str(vol), "uuid": c["uuid"], "profile": prof["name"],
                    "new": len(c["items"]), "bytes": cl.human(c["bytes"]), "already": c["already"],
                    "ignored": len(c["ignored"]), "routes": routes, "ask": any(r["ask"] for r in routes),
                    "safe_to_format": c["safe_to_format"], "missing_on_hd": c["missing_on_hd"]})
    return out


def empty_folders(target):
    """Pastas do trabalho que continuam sem nenhum arquivo."""
    out = []
    for name in subfolders(target):
        d = Path(target) / name
        if not any(f.is_file() and not f.name.startswith(".") for f in d.rglob("*")):
            out.append(name)
    return out


def start_log(body):
    with LOCK:
        if JOB["thread"] and JOB["thread"].is_alive():
            raise ValueError("Ja tem um logger rodando.")
    target = Path(body["target"])
    if not target.is_dir():
        raise ValueError("Pasta de destino nao existe.")
    cfg = cl.load_config()
    ledger = cl.load_ledger()
    s = settings()
    cards = []
    for sel in body["cards"]:
        vol = Path(sel["path"])
        if not vol.is_dir():
            continue
        prof = cl.identify(vol, cfg["profiles"])
        if not prof:
            continue
        c = cl.scan_card(vol, prof, ledger)  # escaneia de novo na hora de copiar
        dests = sel["dests"]
        if any(d is None for d in dests):
            raise ValueError(f"Escolha a pasta do cartao {vol.name} antes de copiar.")
        # rota marcada como "ignorar" sai da lista
        c["items"] = [it for it in c["items"] if dests[it["route"]]]
        c["bytes"] = sum(it["size"] for it in c["items"])
        c["dests"] = [d or "_ignorado" for d in dests]
        c["path"] = str(vol)
        cards.append(c)
        mem = {r.get("label", r["to"]): d for r, d in zip(prof["routes"], dests)}
        s["card_prefs"][c["uuid"]] = mem
        s["card_prefs"]["name:" + vol.name] = mem
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
    auto_eject = s["auto_eject"]

    def card_done(c, rep):
        """Cartao copiado e conferido: ejeta sozinho e avisa para colocar o proximo."""
        vol = c["volume"]
        if not rep.get("conferido_ok"):
            notify("Cartão com problema", f"{vol}: não ejetei. Veja o que falhou no Logger.", ok=False)
            prog["ejected"].append({"volume": vol, "ok": False, "msg": "com problema, não ejetado"})
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
                             on_card_done=card_done)
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
            msg = "Tudo conferido" if rep["tudo_ok"] else "ATENCAO: ha arquivos com problema"
            if prog["empty_folders"]:
                msg += ". Pastas sem arquivo: " + ", ".join(prog["empty_folders"])
            notify("Logger terminou", msg, ok=rep["tudo_ok"] and not prog["empty_folders"])
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
    msg = msg.replace('"', "'")
    sound = "Glass" if ok else "Basso"
    try:
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{title}" sound name "{sound}"'],
                       capture_output=True)
    except FileNotFoundError:
        pass


def eject(paths):
    res = []
    for p in paths:
        r = subprocess.run(["diskutil", "eject", p], capture_output=True, text=True)
        res.append({"path": p, "ok": r.returncode == 0, "msg": (r.stdout + r.stderr).strip()})
    return res


# ---------------------------------------------------------------- http
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, data, ctype="application/json"):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                return self.send(200, (HERE / "ui.html").read_bytes(), "text/html")
            if u.path == "/api/init":
                s = settings()
                return self.send(200, {"settings": {k: s[k] for k in ("base_dir", "backup_dir", "folders", "mode", "rename", "prefixes", "mhl", "auto_eject",
                                                                       "folders_on", "dropbox_on", "dropbox_dir", "dropbox_folders", "dropbox_folders_on")},
                                       "date": suggested_date(), "dropbox_guess": dropbox_guess()})
            if u.path == "/api/cards":
                return self.send(200, list_cards(q.get("target", ""), q.get("backup", "")))
            if u.path == "/api/subfolders":
                return self.send(200, subfolders(q["path"]))
            if u.path == "/api/search":
                return self.send(200, media.search(load_catalog(), q.get("q", "")))
            if u.path == "/api/progress":
                p = dict(JOB["progress"])
                return self.send(200, p)
            return self.send(404, {"error": "nao encontrado"})
        except Exception as e:  # noqa: BLE001
            return self.send(400, {"error": str(e)})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if self.path == "/api/pick-folder":
                return self.send(200, {"path": osascript_choose_folder(body.get("prompt", "Escolha a pasta"))})
            if self.path == "/api/create":
                return self.send(200, create_job(body))
            if self.path == "/api/open-job":
                return self.send(200, open_job(body["path"]))
            if self.path == "/api/settings":
                s = settings()
                for k in ("base_dir", "backup_dir", "rename", "mhl", "auto_eject", "dropbox_on", "dropbox_dir"):
                    if k in body:
                        s[k] = body[k]
                if isinstance(body.get("prefixes"), dict):
                    s["prefixes"] = {k: safe_name(v) for k, v in body["prefixes"].items()}
                for k in ("folders", "folders_on", "dropbox_folders", "dropbox_folders_on"):
                    if isinstance(body.get(k), list):
                        s[k] = [safe_name(f) for f in body[k] if safe_name(f)]
                save_settings(s)
                return self.send(200, {"ok": True})
            if self.path == "/api/folders":
                s = settings()
                s["folders"] = [safe_name(f) for f in body["folders"] if safe_name(f)]
                save_settings(s)
                return self.send(200, {"ok": True})
            if self.path == "/api/catalog-hd":
                return self.send(200, catalog_hd(body["path"]))
            if self.path == "/api/report":
                path, summary = media.build_report(body["target"])
                return self.send(200, {"path": str(path)})
            if self.path == "/api/log":
                return self.send(200, start_log(body))
            if self.path == "/api/eject":
                return self.send(200, eject(body["paths"]))
            if self.path == "/api/reveal":
                subprocess.run(["open", body["path"]])
                return self.send(200, {"ok": True})
            return self.send(404, {"error": "nao encontrado"})
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
