"""
Meus HDs: mapa offline de cada HD externo (nomes, pastas, tamanhos e datas).

- So LE o HD: os.scandir / os.stat. Nada e gravado no HD; o mapa fica em
  ~/.cardlogger/hds.sqlite.
- Reescaneio rapido: toda pasta e listada (nomes sao baratos), mas o tamanho
  de um arquivo que ja estava no mapa so e lido de novo quando a pasta dele mudou.
  Assim o que entrou e o que saiu e sempre exato.
- Se o HD sai no meio da leitura, nada e salvo: o mapa anterior continua valendo.
"""
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
from pathlib import Path

import cardlogger as cl

DB_PATH = cl.STATE_DIR / "hds.sqlite"

# pastas do sistema que nao interessam
SKIP_NAMES = {"$RECYCLE.BIN", "System Volume Information", "Backups.backupdb", "lost+found"}
# pacotes do macOS: entram como um item so (um catalogo do Lightroom tem milhares de previas)
PACKAGES = (".lrdata", ".photoslibrary", ".fcpbundle", ".imovielibrary", ".app", ".bundle", ".aplibrary",
            ".tvlibrary", ".musiclibrary", ".fcpcache", ".band", ".logicx", ".rcproject")


def nfc(s):
    return unicodedata.normalize("NFC", s)


def fold(s):
    """minusculas e sem acento, para a busca achar 'audio' em 'Áudio'."""
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- banco
_LOCAL = threading.local()


def db():
    c = getattr(_LOCAL, "conn", None)
    if c is None or getattr(_LOCAL, "path", None) != str(DB_PATH):
        if c is not None:
            c.close()
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=-65536")  # 64 MB: o reescaneio consulta pasta por pasta
        c.executescript("""
        CREATE TABLE IF NOT EXISTS drives(
            id INTEGER PRIMARY KEY, key TEXT UNIQUE, name TEXT, state TEXT DEFAULT 'mapped',
            total INTEGER DEFAULT 0, free INTEGER DEFAULT 0, files INTEGER DEFAULT 0, dirs INTEGER DEFAULT 0,
            bytes INTEGER DEFAULT 0, first_scan TEXT, last_scan TEXT, last_added INTEGER DEFAULT 0,
            last_removed INTEGER DEFAULT 0, last_changes TEXT DEFAULT '{}', last_seen TEXT, scan_secs REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS entries(
            drive INTEGER, path TEXT, parent TEXT, name TEXT, key TEXT, kind INTEGER,
            size INTEGER, mtime REAL, files INTEGER DEFAULT 0, pkey TEXT, PRIMARY KEY(drive, path)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS entries_parent ON entries(drive, parent);
        """)
        _LOCAL.conn, _LOCAL.path = c, str(DB_PATH)
    return c


FILE, DIR, PKG = 0, 1, 2


def _drive_row(r):
    keys = ("id", "key", "name", "state", "total", "free", "files", "dirs", "bytes", "first_scan", "last_scan",
            "last_added", "last_removed", "last_changes", "last_seen", "scan_secs")
    d = dict(zip(keys, r))
    try:
        d["last_changes"] = json.loads(d["last_changes"] or "{}")
    except ValueError:
        d["last_changes"] = {}
    return d


def drives(state=None):
    sql = "SELECT id,key,name,state,total,free,files,dirs,bytes,first_scan,last_scan,last_added,last_removed,last_changes,last_seen,scan_secs FROM drives"
    rows = db().execute(sql + (" WHERE state=?" if state else "") + " ORDER BY name COLLATE NOCASE", (state,) if state else ()).fetchall()
    return [_drive_row(r) for r in rows]


def drive_by(id_=None, key=None):
    sql = "SELECT id,key,name,state,total,free,files,dirs,bytes,first_scan,last_scan,last_added,last_removed,last_changes,last_seen,scan_secs FROM drives WHERE "
    r = db().execute(sql + ("id=?" if id_ is not None else "key=?"), (id_ if id_ is not None else key,)).fetchone()
    return _drive_row(r) if r else None


IGNORED_PATH = cl.STATE_DIR / "hds_ignored.json"  # HDs que voce disse "nunca mapear" (fora do banco: a leitura pode estar com ele)


def ignored():
    return cl.load_json(IGNORED_PATH, {})


def set_ignored(key, name, on):
    ig = ignored()
    if on:
        ig[key] = name
    else:
        ig.pop(key, None)
    cl.save_json(IGNORED_PATH, ig)


def forget(id_):
    """Apaga so o mapa guardado no Mac. O HD nao e tocado."""
    c = db()
    c.execute("BEGIN")
    c.execute("DELETE FROM entries WHERE drive=?", (id_,))
    c.execute("DELETE FROM drives WHERE id=?", (id_,))
    c.execute("COMMIT")


# ---------------------------------------------------------------- quais volumes sao HDs
def volume_key(vol):
    return cl.volume_uuid(Path(vol))


def is_hd(vol, kind_of=None):
    """HD/SSD externo (nao cartao, nao imagem de disco, nao rede, nao o disco do Mac)."""
    vol = Path(vol)
    if vol.name.startswith(".") or not vol.is_dir():
        return False
    try:
        if vol.resolve() == Path("/").resolve():
            return False
    except OSError:
        return False
    if (vol / "Backups.backupdb").exists() or (vol / ".timemachine").exists():
        return False  # disco do Time Machine
    info = cl.volume_info(vol)
    if info:
        if info.get("BusProtocol") == "Disk Image" or info.get("VirtualOrPhysical") == "Virtual":
            return False
    elif sys.platform == "darwin":
        return False  # sem diskutil: compartilhamento de rede ou algo que nao e disco
    if kind_of is not None:
        return kind_of(vol) == "drive"
    return cl.media_kind(vol) in ("drive", "unknown")


def volume_root(path):
    """/Volumes/NOME de qualquer caminho dentro de um HD externo."""
    p = Path(path)
    vd = cl.VOLUMES_DIR
    try:
        rel = p.resolve().relative_to(vd.resolve())
    except ValueError:
        try:
            rel = p.relative_to(vd)
        except ValueError:
            return None
    return vd / rel.parts[0] if rel.parts else None


def mounted():
    """{key: Path} dos volumes montados agora."""
    out = {}
    try:
        vols = sorted(cl.VOLUMES_DIR.iterdir())
    except OSError:
        return out
    for v in vols:
        if v.name.startswith(".") or not v.is_dir():
            continue
        try:
            if v.resolve() == Path("/").resolve():
                continue
        except OSError:
            continue
        out[volume_key(v)] = v
    return out


# ---------------------------------------------------------------- leitura (so leitura!)
class Aborted(Exception):
    pass


def _skip(name):
    return name.startswith(".") or name in SKIP_NAMES or name.endswith(".partial")


def scan(vol, key=None, progress=None, cancel=None):
    """Le o HD inteiro (ou so o que mudou, se ja tem mapa) e atualiza o mapa numa transacao so."""
    vol = Path(vol)
    key = key or volume_key(vol)
    name = nfc(vol.name)
    try:
        dev = os.stat(vol).st_dev
    except OSError as e:
        raise Aborted("O HD não está conectado.") from e
    c = db()
    row = drive_by(key=key)
    if row is None:
        c.execute("INSERT INTO drives(key,name,state) VALUES(?,?,'mapped')", (key, name))
        row = drive_by(key=key)
    did = row["id"]
    first = not row["last_scan"]
    t0 = time.time()
    st = {"files": 0, "dirs": 0, "added": 0, "removed": 0, "changed": 0, "add_names": [], "rm_names": []}
    if progress is not None:
        progress.update({"name": name, "files": 0, "dirs": 0, "current": "", "first": first, "started": t0})

    def check():
        if cancel is not None and cancel.is_set():
            raise Aborted("Leitura interrompida.")

    def gone():
        try:
            return os.stat(vol).st_dev != dev
        except OSError:
            return True

    def note(lst, rel):
        if len(lst) < 40:
            lst.append(rel)

    def drop_subtree(rel):
        """Tira do mapa uma pasta que saiu do HD (e tudo dentro dela)."""
        n = c.execute("SELECT COUNT(*) FROM entries WHERE drive=? AND kind!=1 AND path>=? AND path<?",
                      (did, rel + "/", rel + "0")).fetchone()[0]
        c.execute("DELETE FROM entries WHERE drive=? AND (path=? OR (path>=? AND path<?))", (did, rel, rel + "/", rel + "0"))
        return n

    def walk(rel, absdir):
        """Devolve (bytes, arquivos) da pasta. '/' depois de rel nunca falha: '0' vem logo depois de '/'."""
        check()
        stored = {r[0]: r for r in c.execute(
            "SELECT path,kind,size,mtime,files FROM entries INDEXED BY entries_parent WHERE drive=? AND parent=?", (did, rel))}
        try:
            dmt = os.stat(absdir).st_mtime
            it = list(os.scandir(absdir))
        except OSError:
            if gone():
                raise Aborted("O HD foi desconectado durante a leitura; o mapa anterior foi mantido.")
            # pasta sem permissao: fica como estava no mapa
            return sum(r[2] or 0 for r in stored.values()), sum((r[4] if r[1] == DIR else 1) for r in stored.values())
        me = c.execute("SELECT mtime FROM entries WHERE drive=? AND path=?", (did, rel)).fetchone() if rel else None
        same_dir = me is not None and me[0] == dmt
        if rel == "":
            same_dir = False
        st["dirs"] += 1
        if progress is not None:
            progress.update({"dirs": st["dirs"], "files": st["files"], "current": nfc(rel) or name})
        total = nfiles = 0
        seen = set()
        for e in it:
            if _skip(e.name):
                continue
            crel = (rel + "/" + e.name) if rel else e.name
            try:
                if e.is_symlink():
                    continue
                isdir = e.is_dir(follow_symlinks=False)
            except OSError:
                continue
            seen.add(crel)
            old = stored.get(crel)
            if isdir and not e.name.lower().endswith(PACKAGES):
                if old is not None and old[1] != DIR:
                    c.execute("DELETE FROM entries WHERE drive=? AND path=?", (did, crel))
                    old = None
                sz, nf = walk(crel, e.path)
                try:
                    mt = os.stat(e.path).st_mtime
                except OSError:
                    mt = 0
                if old is None:
                    c.execute("INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (did, crel, rel, nfc(e.name), fold(nfc(e.name)), DIR, sz, mt, nf, fold(nfc(crel))))
                    if not first:
                        note(st["add_names"], nfc(crel) + "/")
                elif (old[2], old[3], old[4]) != (sz, mt, nf):
                    c.execute("UPDATE entries SET size=?, mtime=?, files=? WHERE drive=? AND path=?", (sz, mt, nf, did, crel))
                total += sz
                nfiles += nf
                continue
            kind = PKG if isdir else FILE
            if old is not None and old[1] == kind and same_dir:
                sz, mt = old[2], old[3]
            else:
                try:
                    s = e.stat(follow_symlinks=False)
                    sz, mt = (0 if isdir else s.st_size), s.st_mtime
                except OSError:
                    if gone():
                        raise Aborted("O HD foi desconectado durante a leitura; o mapa anterior foi mantido.")
                    continue
                if old is None:
                    c.execute("INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (did, crel, rel, nfc(e.name), fold(nfc(e.name)), kind, sz, mt, 1, fold(nfc(crel))))
                    st["added"] += 1
                    if not first:
                        note(st["add_names"], nfc(crel))
                elif old[1] != kind:
                    if old[1] == DIR:
                        st["removed"] += drop_subtree(crel)
                        note(st["rm_names"], nfc(crel) + "/")
                    st["added"] += 1
                    note(st["add_names"], nfc(crel))
                    c.execute("INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (did, crel, rel, nfc(e.name), fold(nfc(e.name)), kind, sz, mt, 1, fold(nfc(crel))))
                elif (old[2], old[3]) != (sz, mt):
                    c.execute("UPDATE entries SET size=?, mtime=? WHERE drive=? AND path=?", (sz, mt, did, crel))
                    st["changed"] += 1
            st["files"] += 1
            total += sz
            nfiles += 1
        for p, r in stored.items():
            if p not in seen:
                n = drop_subtree(p) if r[1] == DIR else 0
                if r[1] != DIR:
                    c.execute("DELETE FROM entries WHERE drive=? AND path=?", (did, p))
                    n = 1
                st["removed"] += n
                note(st["rm_names"], nfc(p) + ("/" if r[1] == DIR else ""))
        if rel:
            c.execute("UPDATE entries SET mtime=? WHERE drive=? AND path=?", (dmt, did, rel))
        return total, nfiles

    c.execute("BEGIN IMMEDIATE")
    try:
        nbytes, nfiles = walk("", str(vol))
        if gone():
            raise Aborted("O HD foi desconectado durante a leitura; o mapa anterior foi mantido.")
        try:
            du = shutil.disk_usage(vol)
            tot, free = du.total, du.free
        except OSError:
            tot, free = row["total"], row["free"]
        ts = now_iso()
        added = 0 if first else st["added"]
        changes = {"added": st["add_names"], "removed": st["rm_names"]}
        c.execute("""UPDATE drives SET name=?, state='mapped', total=?, free=?, files=?, dirs=?, bytes=?,
                     first_scan=COALESCE(first_scan, ?), last_scan=?, last_added=?, last_removed=?, last_changes=?,
                     last_seen=?, scan_secs=? WHERE id=?""",
                  (name, tot, free, nfiles, st["dirs"] - 1, nbytes, ts, ts, added, st["removed"],
                   json.dumps(changes, ensure_ascii=False), ts, round(time.time() - t0, 1), did))
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise
    return {"id": did, "name": name, "first": first, "files": nfiles, "bytes": nbytes, "added": added,
            "removed": st["removed"], "changed": st["changed"], "secs": round(time.time() - t0, 1)}


# ---------------------------------------------------------------- consulta
def ls(drive_id, path=""):
    rows = db().execute("SELECT path,name,kind,size,mtime,files FROM entries INDEXED BY entries_parent WHERE drive=? AND parent=? "
                        "ORDER BY kind=0, name COLLATE NOCASE", (drive_id, path)).fetchall()
    return [{"path": r[0], "name": r[1], "kind": ("file", "dir", "pkg")[r[2]], "size": r[3] or 0,
             "mtime": r[4] or 0, "files": r[5] or 0} for r in rows]


def search(q, limit=300):
    words = [fold(nfc(w)) for w in q.split() if w.strip()]
    if not words:
        return []
    sql = ("SELECT e.drive, d.name, e.path, e.name, e.kind, e.size, e.mtime, e.files FROM entries e "
           "JOIN drives d ON d.id=e.drive WHERE d.state='mapped'")
    # cada palavra tem de estar no caminho (pasta ou nome) e pelo menos uma no nome:
    # "eco club jrs0001" acha o JRS0001 da pasta Eco Club; "ronin" acha as pastas Ronin, nao tudo dentro delas
    like = ["%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for w in words]
    args = []
    for w in like:
        sql += " AND e.pkey LIKE ? ESCAPE '\\'"
        args.append(w)
    sql += " AND (" + " OR ".join(["e.key LIKE ? ESCAPE '\\'"] * len(like)) + ")"
    args += like
    sql += " ORDER BY e.kind=0, d.name COLLATE NOCASE, e.path LIMIT ?"
    args.append(limit + 1)
    rows = db().execute(sql, args).fetchall()
    return [{"drive": r[0], "hd": r[1], "path": r[2], "name": r[3], "kind": ("file", "dir", "pkg")[r[4]],
             "size": r[5] or 0, "mtime": r[6] or 0, "files": r[7] or 0} for r in rows]


# ---------------------------------------------------------------- vigia
class Watcher:
    """Olha /Volumes a cada poucos segundos. HD conhecido que chega: reescaneia sozinho
    (uma vez por conexao). HD novo: fica na lista de 'perguntar'. Uma leitura por vez."""

    def __init__(self, kind_of=None, busy=None, on_done=None, interval=4):
        self.kind_of = kind_of        # funcao vol -> 'card'/'drive' (a mesma regra da tela de cartoes)
        self.busy = busy or (lambda: False)  # copia de cartao rodando: espera para nao disputar o HD
        self.on_done = on_done
        self.interval = interval
        self.lock = threading.RLock()
        self.kinds = {}       # key -> (st_dev, e HD?)
        self.seen = {}        # key -> st_dev da conexao ja tratada
        self.online = {}      # key -> Path (HDs conectados agora)
        self.pending = {}     # key -> {"name","path"} HD novo esperando resposta
        self.dismissed = set()
        self.queue = []       # keys
        self.progress = {}    # key -> progresso da leitura atual
        self.cancels = {}
        self.current = None
        self.last = {}        # key -> resultado/erro da ultima leitura
        self._wake = threading.Event()
        self._stop = False

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()
        return self

    def poll(self):
        found = {}
        for key, v in mounted().items():
            try:
                dev = os.stat(v).st_dev
                ck = self.kinds.get(key)
                if ck is None or ck[0] != dev:  # so decide uma vez por conexao (diskutil e lento)
                    ck = self.kinds[key] = (dev, is_hd(v, self.kind_of))
                if not ck[1]:
                    continue
            except OSError:
                continue
            found[key] = (v, dev)
        with self.lock:
            self.online = {k: v for k, (v, _) in found.items()}
            for k in list(self.seen):
                if k not in found:
                    self.seen.pop(k)
                    self.pending.pop(k, None)
                    self.dismissed.discard(k)
            for k, (v, dev) in found.items():
                if self.seen.get(k) == dev:
                    continue
                self.seen[k] = dev
                row = drive_by(key=k)
                if row:
                    self.enqueue(k)
                elif k not in self.dismissed and k not in ignored():
                    self.pending[k] = {"key": k, "name": nfc(v.name), "path": str(v)}

    def enqueue(self, key):
        with self.lock:
            if key != self.current and key not in self.queue:
                self.queue.append(key)
        self._wake.set()

    def map(self, path):
        root = volume_root(path)
        if root is None or not root.is_dir():
            raise ValueError("Escolha um HD externo (ele aparece em /Volumes).")
        key = volume_key(root)
        with self.lock:
            self.pending.pop(key, None)
            self.online[key] = root
            try:
                self.seen[key] = os.stat(root).st_dev
            except OSError:
                pass
        set_ignored(key, "", False)
        self.enqueue(key)
        return {"key": key, "name": nfc(root.name)}

    def skip(self, key, forever=False):
        with self.lock:
            p = self.pending.pop(key, None)
            self.dismissed.add(key)
        if forever and p:
            set_ignored(key, p["name"], True)

    def forget(self, id_):
        """Esquece o mapa (so o que esta no Mac). Se ele estiver sendo lido, para a leitura antes."""
        row = drive_by(id_=id_)
        if not row:
            return
        with self.lock:
            if row["key"] in self.queue:
                self.queue.remove(row["key"])
            other = self.current
            ev = self.cancels.get(other) if other else None
        if ev:
            ev.set()
            for _ in range(100):
                if self.current != other:
                    break
                time.sleep(0.1)
            if other != row["key"]:
                self.enqueue(other)
        forget(id_)

    def cancel_path(self, path):
        """Antes de ejetar: para a leitura daquele HD (senao o macOS diz que ele esta em uso)."""
        root = volume_root(path)
        with self.lock:
            for k, v in self.online.items():
                if root is not None and Path(v) == root:
                    ev = self.cancels.get(k)
                    if ev:
                        ev.set()
                    if k in self.queue:
                        self.queue.remove(k)

    def rescan_path(self, path):
        """Depois de uma copia: o HD do trabalho mudou, atualiza o mapa (se ele esta em Meus HDs)."""
        root = volume_root(path)
        if root is None:
            return
        key = volume_key(root)
        row = drive_by(key=key)
        if row:
            with self.lock:
                self.online[key] = root
            self.enqueue(key)

    def status(self):
        with self.lock:
            return {"online": {k: str(v) for k, v in self.online.items()}, "pending": list(self.pending.values()),
                    "queue": [{"key": k, "name": nfc(self.online[k].name) if k in self.online else ""} for k in self.queue],
                    "current": self.current,
                    "progress": dict(self.progress.get(self.current, {})) if self.current else {},
                    "last": dict(self.last)}

    def _loop(self):
        while not self._stop:
            try:
                self.poll()
            except Exception:  # noqa: BLE001 - o vigia nunca pode cair
                pass
            time.sleep(self.interval)

    def _worker(self):
        while not self._stop:
            self._wake.wait(timeout=5)
            self._wake.clear()
            while True:
                if self.busy():
                    time.sleep(3)
                    continue
                with self.lock:
                    if not self.queue:
                        break
                    key = self.queue.pop(0)
                    vol = self.online.get(key)
                    self.current = key
                    self.progress[key] = {}
                    ev = self.cancels[key] = threading.Event()
                try:
                    if vol is None:
                        raise Aborted("O HD não está conectado.")
                    res = scan(vol, key, self.progress[key], ev)
                    res["ok"] = True
                except Aborted as e:
                    res = {"ok": False, "error": str(e), "name": vol.name if vol else ""}
                except Exception as e:  # noqa: BLE001
                    res = {"ok": False, "error": "Não consegui ler o HD: %s" % e, "name": vol.name if vol else ""}
                res["at"] = now_iso()
                with self.lock:
                    self.last[key] = res
                    self.current = None
                    self.progress.pop(key, None)
                    self.cancels.pop(key, None)
                if self.on_done:
                    try:
                        self.on_done(key, res)
                    except Exception:  # noqa: BLE001
                        pass
