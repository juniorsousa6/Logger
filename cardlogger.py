#!/usr/bin/env python3
"""
cardlogger - motor que descarrega cartoes de memoria para a pasta do trabalho.
Usado pelo app (app.py), mas tambem roda sozinho pelo Terminal:

    python3 cardlogger.py "/Volumes/HD/2026_11_15 - Casamento Ana"           # copia
    python3 cardlogger.py "/Volumes/HD/2026_11_15 - Casamento Ana" --dry-run # so mostra o plano

Regras de seguranca:
  * NUNCA move, renomeia ou apaga nada do cartao. So le.
  * Copia para um arquivo temporario, confere o checksum do destino contra o
    da origem e so entao da o nome final.
  * Lembra o que ja foi logado (~/.cardlogger/ledger.json), entao um cartao
    que nao foi formatado so tem os arquivos novos copiados.
  * Cartoes diferentes sao copiados em paralelo (cada leitor e um caminho
    independente), ate MAX_PARALLEL ao mesmo tempo.
"""
import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "cards.json"
STATE_DIR = Path(os.environ.get("CARDLOGGER_HOME", Path.home() / ".cardlogger"))
LEDGER_PATH = STATE_DIR / "ledger.json"
VOLUMES_DIR = Path(os.environ.get("CARDLOGGER_VOLUMES", "/Volumes"))
CHUNK = 8 * 1024 * 1024
MAX_PARALLEL = 4
SKIP_NAMES = {".DS_Store", ".Spotlight-V100", ".Trashes", ".fseventsd", ".TemporaryItems"}
CARD_SUBFOLDER = "Cartao {:02d}"
VERSION = "2.0"

try:  # xxHash e o padrao dos apps de DIT; se o modulo existir, o MHL usa ele
    import xxhash  # type: ignore
except ImportError:
    xxhash = None


# ---------------------------------------------------------------- json helpers
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_config(path=DEFAULT_CONFIG):
    cfg = load_json(path, None)
    if cfg is None:
        raise SystemExit(f"Config nao encontrada: {path}")
    return cfg


def load_ledger():
    return load_json(LEDGER_PATH, {"cards": {}})


# ---------------------------------------------------------------- volumes
_INFO_CACHE = {}


def volume_info(vol: Path) -> dict:
    """O que o macOS sabe do disco (diskutil info -plist). Vazio fora do Mac."""
    try:
        key = (str(vol), os.stat(vol).st_dev)
    except OSError:
        return {}
    if key in _INFO_CACHE:
        return _INFO_CACHE[key]
    info = {}
    try:
        import plistlib
        out = subprocess.run(["diskutil", "info", "-plist", str(vol)], capture_output=True, timeout=10).stdout
        if out.strip():
            info = plistlib.loads(out)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, Exception):  # noqa: BLE001
        info = {}
    _INFO_CACHE[key] = info
    return info


def volume_uuid(vol: Path) -> str:
    """UUID do volume no macOS (muda quando o cartao e formatado)."""
    u = volume_info(vol).get("VolumeUUID")
    return u or "noid-" + vol.name  # fallback (testes / Linux)


CARD_WORDS = re.compile(r"\b(sd|sdxc|sdhc|microsd|cf|cfast|cfexpress|xqd|card|reader|cardreader|multi-?card)\b", re.I)


def media_kind(vol: Path) -> str:
    """'card' = cartao de memoria (leitor SD do Mac, leitor USB, CFexpress);
    'drive' = HD/SSD; 'unknown' = sem informacao (fora do Mac)."""
    i = volume_info(vol)
    if not i:
        return "unknown"
    if i.get("BusProtocol") == "Secure Digital":
        return "card"  # leitor SD embutido do MacBook
    if i.get("RemovableMedia") or i.get("Removable"):
        return "card"  # leitores de cartao informam midia removivel; HD/SSD externo nao
    names = " ".join(str(i.get(k, "")) for k in ("MediaName", "IORegistryEntryName", "DeviceModel"))
    if CARD_WORDS.search(names.replace("_", " ")):
        return "card"
    return "drive"


DATED = re.compile(r"^\d{4}_\d{2}_\d{2}")


def looks_like_work_drive(vol: Path, max_depth=2) -> bool:
    """HD de trabalho: tem pastas AAAA_MM_DD (ate 2 niveis) ou pasta _logs do Logger."""
    level = [vol]
    for _ in range(max_depth):
        nxt = []
        for d in level:
            try:
                for e in os.scandir(d):
                    if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                        if DATED.match(e.name) or e.name == "_logs":
                            return True
                        nxt.append(Path(e.path))
            except OSError:
                pass
        level = nxt[:200]
    return False


def is_card_candidate(vol: Path, job_dir=None) -> bool:
    if isinstance(job_dir, (list, tuple)):
        return all(is_card_candidate(vol, j) for j in job_dir) if job_dir else is_card_candidate(vol)
    if vol.name.startswith(".") or not vol.is_dir():
        return False
    try:
        real = vol.resolve()
        if real == Path("/").resolve():
            return False
        if job_dir is not None:
            jd = str(Path(job_dir).resolve())
            # nunca tratar como cartao o disco onde esta a pasta do trabalho
            if jd == str(real) or jd.startswith(str(real) + os.sep):
                return False
    except OSError:
        return False
    return True


def mounted_volumes(job_dir=None):
    try:
        return [v for v in sorted(VOLUMES_DIR.iterdir()) if is_card_candidate(v, job_dir)]
    except FileNotFoundError:
        return []


def identify(vol: Path, profiles):
    """Devolve o primeiro perfil cujas regras batem com o cartao."""
    for p in profiles:
        rule = p.get("match", {})
        names = rule.get("volume_name")
        if names and not any(fnmatch.fnmatch(vol.name.upper(), n.upper()) for n in names):
            continue
        has = rule.get("has_any_path")
        if has and not any(next(iter(vol.glob(g)), None) for g in has):
            continue
        return p
    return None


def sub_dir(parent: Path, name: str) -> Path:
    """Subpasta pelo nome, reaproveitando uma existente que difere so em acento/caixa
    (o macOS pode gravar 'Áudio' decomposto; sem isso nasceria uma pasta duplicada)."""
    want = unicodedata.normalize("NFC", name)
    if parent.is_dir():
        for ch in parent.iterdir():
            if ch.is_dir() and unicodedata.normalize("NFC", ch.name).casefold() == want.casefold():
                return ch
    return parent / want


# ---------------------------------------------------------------- files
def walk(vol: Path):
    for root, dirs, files in os.walk(vol):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_NAMES and not d.startswith("."))
        for name in sorted(files):
            if name in SKIP_NAMES or name.startswith("."):
                continue
            yield Path(root) / name


def route_index(rel: str, profile):
    relu = rel.upper()
    for i, r in enumerate(profile["routes"]):
        if any(fnmatch.fnmatch(relu, pat.upper()) for pat in r["patterns"]):
            return i
    return None


def new_mhl_hasher():
    return ("xxh64", xxhash.xxh64()) if xxhash else ("md5", hashlib.md5())


def hash_file(path: Path, mhl=False, nocache=False):
    h = hashlib.blake2b(digest_size=20)
    algo, m = new_mhl_hasher() if mhl else (None, None)
    with open(path, "rb") as f:
        if nocache:
            _nocache(f)
        while chunk := f.read(CHUNK):
            h.update(chunk)
            if m:
                m.update(chunk)
    return (h.hexdigest(), algo, m.hexdigest()) if mhl else h.hexdigest()


F_NOCACHE = 48  # macOS: nao guardar o arquivo na memoria (a conferencia le do disco de verdade)


def _nocache(f):
    if sys.platform == "darwin":
        try:
            import fcntl
            fcntl.fcntl(f.fileno(), F_NOCACHE, 1)
        except Exception:  # noqa: BLE001
            pass


def copy_stream(src: Path, dsts, on_bytes=None, mhl=False):
    """Copia em esteira: uma thread le o cartao, outras gravam em cada destino ao mesmo
    tempo e outra calcula o hash. Le o cartao UMA vez. Grava como .partial.
    Devolve (tmps, blake2b, algoritmo_mhl, hash_mhl)."""
    import queue
    dsts = [Path(d) for d in dsts]
    tmps = []
    for d in dsts:
        d.parent.mkdir(parents=True, exist_ok=True)
        tmps.append(d.with_name(d.name + ".partial"))
    h = hashlib.blake2b(digest_size=20)
    algo, m = new_mhl_hasher() if mhl else (None, None)
    errors = []

    def writer(path, q):
        try:
            with open(path, "wb") as fo:
                _nocache(fo)
                while True:
                    chunk = q.get()
                    if chunk is None:
                        break
                    if not errors:
                        fo.write(chunk)
                fo.flush()
                os.fsync(fo.fileno())
        except Exception as e:  # noqa: BLE001
            errors.append(e)
            while q.get() is not None:  # esvazia para o leitor nao travar
                pass

    def hasher(q):
        while True:
            chunk = q.get()
            if chunk is None:
                break
            h.update(chunk)
            if m:
                m.update(chunk)

    qs = [queue.Queue(maxsize=8) for _ in tmps] + [queue.Queue(maxsize=8)]
    ths = [threading.Thread(target=writer, args=(t, q), daemon=True) for t, q in zip(tmps, qs)]
    ths.append(threading.Thread(target=hasher, args=(qs[-1],), daemon=True))
    for t in ths:
        t.start()
    try:
        with open(src, "rb") as fi:
            while True:
                chunk = fi.read(CHUNK)
                if not chunk or errors:
                    break
                for q in qs:
                    q.put(chunk)
                if on_bytes:
                    on_bytes(len(chunk))
    finally:
        for q in qs:
            q.put(None)
        for t in ths:
            t.join()
    if errors:
        for t in tmps:
            t.unlink(missing_ok=True)
        raise errors[0]
    return tmps, h.hexdigest(), algo, (m.hexdigest() if m else None)


def verify_commit(src: Path, tmps, dsts, src_hash):
    """Rele cada copia do disco e compara com o hash do cartao. So entao da o nome final."""
    st = src.stat()
    for t in tmps:
        if t.stat().st_size != st.st_size or hash_file(t, nocache=True) != src_hash:
            for x in tmps:
                x.unlink(missing_ok=True)
            raise IOError(f"checksum diferente apos copiar ({t.parent})")
    for t, d in zip(tmps, dsts):
        os.utime(t, (st.st_atime, st.st_mtime))  # preserva a data original
        os.replace(t, Path(d))


def copy_verified(src: Path, dsts, on_bytes=None, mhl=False):
    """Copia + confere na hora (uso simples, ex.: testes). Devolve (blake2b, algo_mhl, hash_mhl)."""
    tmps, digest, algo, mh = copy_stream(src, dsts, on_bytes, mhl)
    verify_commit(Path(src), tmps, dsts, digest)
    return digest, algo, mh


def free_space(path: Path) -> int:
    p = Path(path)
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free


def free_name(dst: Path, reserved=None) -> Path:
    reserved = reserved if reserved is not None else set()
    cand, i = dst, 2
    while cand.exists() or str(cand) in reserved:
        cand = dst.with_name(f"{dst.stem}_{i}{dst.suffix}")
        i += 1
    reserved.add(str(cand))
    return cand


def next_card_folder(folder: Path, taken: set) -> Path:
    n = 1
    while True:
        cand = folder / CARD_SUBFOLDER.format(n)
        if not cand.exists() and str(cand) not in taken:
            return cand
        n += 1


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def with_prefix(name: str, prefix: str) -> str:
    prefix = (prefix or "").strip()
    if not prefix or name.upper().startswith(prefix.upper() + "_"):
        return name
    return f"{prefix}_{name}"


# ---------------------------------------------------------------- scan
def scan_card(vol: Path, profile, ledger):
    """Lista arquivos novos (ainda nao logados) do cartao, ja com a rota."""
    uuid = volume_uuid(vol)
    seen = ledger.get("cards", {}).get(uuid, {}).get("files", {})
    items, old, ignored = [], 0, []
    for src in walk(vol):
        rel = src.relative_to(vol).as_posix()
        ri = route_index(rel, profile)
        if ri is None:
            ignored.append(rel)
            continue
        st = src.stat()
        key = f"{rel}|{st.st_size}|{int(st.st_mtime)}"
        if key in seen:
            old += 1
            continue
        items.append({"src": src, "rel": rel, "size": st.st_size, "key": key, "route": ri})
    # cartao "pode formatar": nada novo e todo arquivo ja logado ainda existe no HD
    missing = 0
    if not items:
        for rec in seen.values():
            if not Path(rec.get("dest", "")).exists():
                missing += 1
    return {"volume": vol.name, "path": str(vol), "uuid": uuid, "profile": profile["name"],
            "items": items, "already": old, "ignored": ignored, "missing_on_hd": missing,
            "safe_to_format": not items and old > 0 and missing == 0,
            "bytes": sum(i["size"] for i in items)}


# ---------------------------------------------------------------- ASC MHL
def c4_id(data: bytes) -> str:
    """Identificador C4 (SMPTE ST 2114) usado pela cadeia do ASC MHL."""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(hashlib.sha512(data).digest(), "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = alphabet[r] + s
    return "c4" + s.rjust(88, "1")


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def write_ascmhl(root: Path, entries, process="transfer"):
    """
    Grava uma nova geracao ASC MHL v2.0 em <root>/ascmhl/ com os arquivos
    copiados nesta rodada. entries: lista de dicts com path (absoluto),
    size, mtime, algo, hash.
    """
    from xml.sax.saxutils import escape
    root = Path(root)
    d = root / "ascmhl"
    d.mkdir(parents=True, exist_ok=True)
    chain_path = d / "ascmhl_chain.xml"
    gens = sorted(d.glob("[0-9][0-9][0-9][0-9]_*.mhl"))
    seq = (int(gens[-1].name[:4]) + 1) if gens else 1
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    name = f"{seq:04d}_{root.name}_{now.astimezone(dt.timezone.utc).strftime('%Y-%m-%d_%H%M%SZ')}.mhl"
    hashes = []
    for e in entries:
        rel = Path(e["path"]).relative_to(root).as_posix()
        hashes.append(
            f'    <hash>\n      <path size="{e["size"]}" lastmodificationdate="{_iso(e["mtime"])}">{escape(rel)}</path>\n'
            f'      <{e["algo"]} action="original" hashdate="{now.isoformat(timespec="seconds")}">{e["hash"]}</{e["algo"]}>\n'
            f'    </hash>')
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<hashlist version="2.0" xmlns="urn:ASC:MHL:v2.0">\n'
           f'  <creatorinfo>\n    <creationdate>{now.isoformat(timespec="seconds")}</creationdate>\n'
           f'    <hostname>{escape(socket.gethostname())}</hostname>\n    <tool version="{VERSION}">Logger</tool>\n'
           f'  </creatorinfo>\n  <processinfo>\n    <process>{process}</process>\n'
           f'    <ignore>\n      <pattern>.DS_Store</pattern>\n      <pattern>ascmhl</pattern>\n      <pattern>ascmhl/</pattern>\n'
           f'      <pattern>_logs</pattern>\n      <pattern>Relatorio.html</pattern>\n    </ignore>\n  </processinfo>\n'
           f'  <hashes>\n' + "\n".join(hashes) + '\n  </hashes>\n</hashlist>\n')
    data = xml.encode("utf-8")
    (d / name).write_bytes(data)
    # cadeia: lista todas as geracoes com o C4 de cada uma
    chain = []
    for g in sorted(d.glob("[0-9][0-9][0-9][0-9]_*.mhl")):
        chain.append(f'  <hashlist sequencenr="{int(g.name[:4])}">\n    <path>{escape(g.name)}</path>\n'
                     f'    <c4>{c4_id(g.read_bytes())}</c4>\n  </hashlist>')
    chain_path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<ascmhldirectory xmlns="urn:ASC:MHL:DIRECTORY:v2.0">\n'
                          + "\n".join(chain) + "\n</ascmhldirectory>\n", encoding="utf-8")
    return d / name


# ---------------------------------------------------------------- copia
def run_log(job_dir, cards, ledger=None, per_card_subfolder=False, progress=None, backups=None,
            prefixes=None, mhl=False, on_card_done=None):
    """
    cards: lista de dicts vindos de scan_card, cada um com "dests": lista de
    subpastas (uma por rota do perfil), relativa a job_dir.
    progress: dict opcional atualizado ao vivo (para a interface).
    backups: lista de (pasta_raiz_principal, pasta_raiz_backup); o mesmo caminho
    relativo e recriado no backup (ex.: segundo HD).
    prefixes: {pasta_destino: prefixo} para renomear a COPIA (ex.: Fixa Wide -> WIDE_C0001.MP4).
    mhl: tambem gera manifesto ASC MHL (padrao de cinema) em <job_dir>/ascmhl.
    on_card_done(card, rep): chamado assim que cada cartao termina de copiar e conferir
    (o app usa para ejetar o cartao e avisar).
    Devolve o relatorio (dict) e grava _logs/ dentro de job_dir.
    """
    job_dir = Path(job_dir)
    backups = [(Path(a), Path(b)) for a, b in (backups or [])]
    prefixes = prefixes or {}
    lock = threading.Lock()

    def mirrors(p: Path):
        out = []
        for root, broot in backups:
            try:
                out.append(broot / p.relative_to(root))
            except ValueError:
                pass
        return out

    ledger = ledger if ledger is not None else load_ledger()
    prog = progress if progress is not None else {}
    total_bytes = sum(c["bytes"] for c in cards)
    prog.update({"total_bytes": total_bytes, "done_bytes": 0, "total_files": sum(len(c["items"]) for c in cards),
                 "done_files": 0, "current": "", "phase": "copiando", "errors": [], "per_card": {}})
    report = {"job": str(job_dir), "started": dt.datetime.now().isoformat(timespec="seconds"), "cards": []}

    # espaco livre: precisa caber em todos os destinos (com 2% de folga)
    need = int(total_bytes * 1.02)
    for root in [job_dir] + mirrors(job_dir):
        free = free_space(root)
        if free < need:
            prog["phase"] = "erro"
            prog["fatal"] = f"Sem espaco em {root}: precisa {human(need)}, livre {human(free)}"
            raise IOError(prog["fatal"])

    # pastas de destino de cada cartao (decididas antes de comecar, sem corrida)
    taken, reserved = set(), set()
    for c in cards:
        bases = []
        for d in c["dests"]:
            b = sub_dir(job_dir, d)
            if per_card_subfolder and c["items"]:
                b = next_card_folder(b, taken)
                taken.add(str(b))
            bases.append(b)
        c["_bases"] = bases

    def verify_card(rep):
        for f in rep["copiados"]:
            for p in [Path(f["destino"])] + [Path(x) for x in f.get("backups", [])]:
                # o hash de cada arquivo ja foi conferido lendo do HD logo apos a copia;
                # aqui so garantimos que nada sumiu ou mudou de tamanho no caminho
                if not p.exists() or p.stat().st_size != f["bytes"]:
                    rep["erros"].append({"arquivo": str(p), "erro": "falhou na conferencia final"})
        rep["conferido_ok"] = len(rep["copiados"]) == rep["novos"] and not rep["erros"]

    def do_card(c):
        with lock:
            entry = ledger.setdefault("cards", {}).setdefault(c["uuid"], {"name": c["volume"], "files": {}})
            entry["name"] = c["volume"]
        seen = entry["files"]
        bases = c["_bases"]
        rep = {"volume": c["volume"], "uuid": c["uuid"], "perfil": c["profile"],
               "destinos": [str(b) for b in bases], "novos": len(c["items"]), "bytes": c["bytes"],
               "ja_logados": c["already"], "ignorados": c["ignored"], "copiados": [], "erros": []}
        pc = prog["per_card"].setdefault(c["volume"], {"done": 0, "total": c["bytes"], "file": ""})

        def add_bytes(n):
            with lock:
                prog["done_bytes"] += n
                pc["done"] += n

        def record(n, it, src, dst, extra, digest, algo, mh):
            with lock:
                prog["done_files"] += 1
                seen[it["key"]] = {"hash": digest, "dest": str(dst),
                                   "quando": dt.datetime.now().isoformat(timespec="seconds")}
                if n % 20 == 0:
                    save_json(LEDGER_PATH, ledger)
            f = {"origem": it["rel"], "destino": str(dst), "backups": [str(x) for x in extra],
                 "bytes": it["size"], "blake2b": digest}
            if mhl:
                f["mhl"] = {"algo": algo, "hash": mh, "mtime": src.stat().st_mtime}
            rep["copiados"].append(f)

        def fail(it, e):
            rep["erros"].append({"arquivo": it["rel"], "erro": str(e)})
            with lock:
                prog["errors"].append(f"{c['volume']}/{it['rel']}: {e}")

        # a conferencia do arquivo N (relendo do HD) roda enquanto o N+1 e copiado
        pending = []

        def settle(block):
            while pending and (block or pending[0][0].done()):
                fut, args = pending.pop(0)
                try:
                    fut.result()
                    record(*args)
                except Exception as e:  # noqa: BLE001
                    fail(args[1], e)

        with ThreadPoolExecutor(max_workers=1) as vex:
            for n, it in enumerate(c["items"], 1):
                src = Path(it["src"])
                folder = c["dests"][it["route"]]
                pc["file"] = it["rel"]
                prog["current"] = f"{c['volume']}: {it['rel']}"
                with lock:
                    dst = free_name(bases[it["route"]] / with_prefix(src.name, prefixes.get(folder, "")), reserved)
                    reserved.add(str(dst))
                extra = mirrors(dst)
                try:
                    tmps, digest, algo, mh = copy_stream(src, [dst] + extra, add_bytes, mhl=mhl)
                except Exception as e:  # noqa: BLE001
                    fail(it, e)
                    continue
                pending.append((vex.submit(verify_commit, src, tmps, [dst] + extra, digest),
                                (n, it, src, dst, extra, digest, algo, mh)))
                settle(block=len(pending) > 2)
            settle(block=True)
        pc["file"] = "conferindo"
        with lock:
            save_json(LEDGER_PATH, ledger)
        verify_card(rep)
        pc["file"] = "pronto" if rep["conferido_ok"] else "com problema"
        if on_card_done:
            try:
                on_card_done(c, rep)
            except Exception as e:  # noqa: BLE001  (ejetar nunca derruba o logger)
                rep["eject_error"] = str(e)
        return rep

    with ThreadPoolExecutor(max_workers=max(1, min(MAX_PARALLEL, len(cards)))) as ex:
        report["cards"] = list(ex.map(do_card, cards))

    prog["phase"] = "conferindo"
    report["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    report["tudo_ok"] = all(r["conferido_ok"] for r in report["cards"])

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = free_name(job_dir / "_logs" / f"logger-{stamp}.json")
    save_json(json_path, report)
    lines = [f"Logger {report['started']}  ->  {job_dir}", ""]
    for r in report["cards"]:
        ok = "OK" if r["conferido_ok"] else "COM PROBLEMA"
        lines.append(f"{ok:13} {r['volume']} ({r['perfil']}): {len(r['copiados'])}/{r['novos']} copiados, "
                     f"{human(r['bytes'])}, {r['ja_logados']} ja logados antes, {len(r['erros'])} erro(s)")
        for e in r["erros"]:
            lines.append(f"              ! {e['arquivo']}: {e['erro']}")
    lines += ["", "TUDO CONFERIDO" if report["tudo_ok"] else "ATENCAO: ha arquivos com problema, veja acima"]
    json_path.with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # manifesto simples (hash  caminho relativo); confere com: b2sum -l 160 -c arquivo.b2sum
    man = [f"{f['blake2b']}  {Path(f['destino']).relative_to(job_dir).as_posix()}"
           for r in report["cards"] for f in r["copiados"] if Path(f["destino"]).is_relative_to(job_dir)]
    if man:
        json_path.with_suffix(".b2sum").write_text("\n".join(man) + "\n", encoding="utf-8")
    # manifesto padrao cinema (opcional)
    if mhl:
        entries = [{"path": f["destino"], "size": f["bytes"], "mtime": f["mhl"]["mtime"],
                    "algo": f["mhl"]["algo"], "hash": f["mhl"]["hash"]}
                   for r in report["cards"] for f in r["copiados"] if "mhl" in f]
        if entries:
            gen = write_ascmhl(job_dir, entries)
            report["ascmhl"] = str(gen)
            for m in mirrors(gen.parent):
                shutil.copytree(gen.parent, m, dirs_exist_ok=True)
    for m in mirrors(json_path.parent):
        m.mkdir(parents=True, exist_ok=True)
        for ext in (".json", ".txt", ".b2sum"):
            src = json_path.with_suffix(ext)
            if src.exists():
                shutil.copy2(src, m / src.name)
    report["summary"] = lines
    report["log_file"] = str(json_path.with_suffix(".txt"))
    prog["report"] = report
    if not prog.get("hold"):  # o app segura o "pronto" ate gerar o relatorio visual
        prog["phase"] = "pronto"
    return report


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="Loga os cartoes conectados na pasta do trabalho")
    ap.add_argument("job_dir")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--por-cartao", action="store_true", help="cada cartao numa subpasta 'Cartao NN'")
    ap.add_argument("--mhl", action="store_true", help="gera tambem o manifesto ASC MHL")
    args = ap.parse_args()

    job_dir = Path(args.job_dir).expanduser()
    if not job_dir.is_dir():
        sys.exit(f"Pasta do trabalho nao existe: {job_dir}")
    cfg = load_config(args.config)
    ledger = load_ledger()
    cards = []
    for vol in mounted_volumes(job_dir):
        prof = identify(vol, cfg["profiles"])
        if not prof:
            print(f"? {vol.name}: nao reconheci esse cartao, pulei")
            continue
        c = scan_card(vol, prof, ledger)
        c["dests"] = [r["to"] for r in prof["routes"]]
        print(f"■ {vol.name} -> {prof['name']}: {len(c['items'])} novo(s), {human(c['bytes'])}"
              f" (ja logados: {c['already']}, ignorados: {len(c['ignored'])})")
        cards.append(c)
    if args.dry_run:
        print("\n(dry-run: nada foi copiado)")
        return
    rep = run_log(job_dir, cards, ledger, per_card_subfolder=args.por_cartao, mhl=args.mhl)
    print("\n" + "\n".join(rep["summary"]))
    sys.exit(0 if rep["tudo_ok"] else 1)


if __name__ == "__main__":
    main()
