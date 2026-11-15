#!/usr/bin/env python3
"""
Limpeza das fotos da festa, depois da selecao no Lightroom.

Fluxo do Junior (15/11/2026): edita no Lightroom Classic (catalogo ~/Pictures/2026/2026.lrcat), marca 1 a 3
estrelas, arrasta os RAW com estrela para "Select Fotos" dentro da pasta da festa (pelo proprio Lightroom,
assim as edicoes continuam ligadas) e joga fora a pasta Fotos inteira.

O Logger:
1. le o catalogo (numa copia, so leitura: o Lightroom pode estar aberto);
2. confere que TODA foto com estrela daquela festa ja esta em Select Fotos (no catalogo ou no disco);
3. mostra o que vai sair (pastas, quantidade, tamanho, arquivos que nao sao do catalogo);
4. so depois do clique do Junior manda as pastas para a Lixeira pelo Finder (da para recuperar).
Nada e apagado direto, e nada sai se faltar uma foto com estrela no Select Fotos.
"""
import glob
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata
from pathlib import Path

SELECT_NAME = "Select Fotos"
PHOTO_EXT = {"arw", "cr2", "cr3", "nef", "raf", "dng", "orf", "rw2", "jpg", "jpeg", "heic", "tif", "tiff", "png"}


def _nfc(s):
    return unicodedata.normalize("NFC", s or "")


def _fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def find_catalogs(home=None):
    """Catalogos .lrcat em ~/Pictures (o mais recente primeiro)."""
    home = Path(home or Path.home())
    found = glob.glob(str(home / "Pictures" / "*.lrcat")) + glob.glob(str(home / "Pictures" / "*" / "*.lrcat"))
    return sorted(found, key=lambda p: os.path.getmtime(p), reverse=True)


# ---------------------------------------------------------------- catalogo
class Catalog:
    """Copia do catalogo (com o -wal, onde o Lightroom guarda o que ainda nao gravou no arquivo principal)."""

    def __init__(self, path):
        self.path = str(path)
        if not os.path.isfile(self.path):
            raise ValueError("Não achei o catálogo do Lightroom: " + self.path)
        self.tmp = tempfile.mkdtemp(prefix="logger-lrcat-")
        dst = os.path.join(self.tmp, "c.lrcat")
        shutil.copyfile(self.path, dst)
        for ext in ("-wal", "-shm"):
            if os.path.isfile(self.path + ext):
                shutil.copyfile(self.path + ext, dst + ext)
        self.db = sqlite3.connect(dst)

    def close(self):
        try:
            self.db.close()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def images_under(self, folder):
        """Toda imagem do catalogo dentro de `folder`: [{path, rating, pick}]."""
        folder = _nfc(str(folder)).rstrip("/") + "/"
        rows = self.db.execute(
            """select r.absolutePath, f.pathFromRoot, fi.baseName, fi.extension, coalesce(i.rating, 0), coalesce(i.pick, 0)
               from Adobe_images i join AgLibraryFile fi on fi.id_local = i.rootFile
               join AgLibraryFolder f on f.id_local = fi.folder
               join AgLibraryRootFolder r on r.id_local = f.rootFolder""").fetchall()
        out = []
        for root, sub, base, ext, rating, pick in rows:
            p = _nfc((root or "") + (sub or "") + (base or "") + ("." + ext if ext else ""))
            if p.startswith(folder):
                out.append({"path": p, "rating": int(rating or 0), "pick": int(pick or 0)})
        return out

    def festas(self, limit=12):
        """Festas recentes no catalogo: pastas 'AAAA_MM_DD - Nome' que tem fotos, com estrelas e total."""
        rows = self.db.execute(
            """select r.absolutePath || f.pathFromRoot, count(*), sum(coalesce(i.rating, 0) > 0)
               from Adobe_images i join AgLibraryFile fi on fi.id_local = i.rootFile
               join AgLibraryFolder f on f.id_local = fi.folder
               join AgLibraryRootFolder r on r.id_local = f.rootFolder group by 1""").fetchall()
        acc = {}
        for folder, n, stars in rows:
            m = re.match(r"^(.*?/\d{4}_\d{2}_\d{2}\s*-[^/]+)/", _nfc(folder))
            if not m:
                continue
            k = m.group(1)
            a = acc.setdefault(k, {"dir": k, "name": k.rsplit("/", 1)[-1], "total": 0, "stars": 0})
            a["total"] += n
            a["stars"] += stars or 0
        out = sorted(acc.values(), key=lambda a: a["name"], reverse=True)
        for a in out:
            a["online"] = os.path.isdir(a["dir"])
        return out[:limit]


# ---------------------------------------------------------------- plano
def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def _walk(d):
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            if not f.startswith("."):
                yield os.path.join(root, f)


def plan(festa_dir, images, exists=os.path.exists):
    """festa_dir: pasta da festa no HD. images: Catalog.images_under(festa_dir).

    Devolve o que vai para a Lixeira e se esta tudo seguro:
      starred: cada foto com estrela e onde ela esta (no Select Fotos ou nao)
      trash: pastas inteiras que vao para a Lixeira (as que tem fotos do catalogo fora do Select Fotos)
      ready: True so se toda foto com estrela esta salva no Select Fotos.
    """
    festa = _nfc(str(festa_dir)).rstrip("/")
    select = festa + "/" + SELECT_NAME
    sel_names = set()
    if os.path.isdir(select):
        sel_names = {_fold(os.path.basename(f)) for f in _walk(select)}
    starred, problems = [], []
    for im in images:
        if im["rating"] < 1:
            continue
        p = im["path"]
        in_select = p.startswith(select + "/")
        on_disk = exists(p)
        same_name = _fold(os.path.basename(p)) in sel_names
        safe = (in_select and on_disk) or same_name
        starred.append({"path": p, "rating": im["rating"], "safe": safe,
                        "where": "Select Fotos" if safe else ("sumiu do disco" if in_select else p[len(festa) + 1:])})
        if not safe:
            problems.append(p)
    # pastas a apagar: a pasta onde estao as fotos fora do Select Fotos (normalmente "Fotos").
    # Nunca uma pasta acima disso (ex.: Boma/festa/Fotos apaga Fotos, nao "festa", que tem videos).
    tops = set()
    for im in images:
        p = im["path"]
        if p.startswith(select + "/") or not p.startswith(festa + "/"):
            continue
        d = os.path.dirname(p)
        # Fotos/101MSDCF -> Fotos (sobe so enquanto a pasta de cima tambem e de fotos)
        while os.path.dirname(d) != festa and _fold(os.path.basename(os.path.dirname(d))).startswith("foto"):
            d = os.path.dirname(d)
        if d != festa:
            tops.add(d)
    tops = {t for t in tops if not any(t.startswith(o + "/") for o in tops)}
    trash = []
    catalog_paths = {im["path"] for im in images}
    for t in sorted(tops):
        if not os.path.isdir(t) or _fold(os.path.basename(t)) == _fold(SELECT_NAME):
            continue
        files = list(_walk(t))
        extra = [f for f in files if _nfc(f) not in catalog_paths
                 and f.rsplit(".", 1)[-1].lower() not in PHOTO_EXT | {"xmp"}]
        trash.append({"dir": t, "files": len(files), "bytes": sum(_size(f) for f in files),
                      "not_photos": [f[len(t) + 1:] for f in extra][:30], "not_photos_count": len(extra)})
    n_sel = len(list(_walk(select))) if os.path.isdir(select) else 0
    reasons = []
    if not starred:
        reasons.append("Nenhuma foto com estrela no catálogo para esta festa.")
    if problems:
        reasons.append(f"{len(problems)} foto(s) com estrela ainda fora do Select Fotos.")
    if not os.path.isdir(select):
        reasons.append("A pasta Select Fotos não existe nesta festa.")
    if not trash:
        reasons.append("Nada para limpar: não há fotos fora do Select Fotos.")
    return {"festa": festa, "select": select, "select_exists": os.path.isdir(select), "select_files": n_sel,
            "total": len(images), "starred": starred, "missing": len(problems), "trash": trash,
            "trash_files": sum(t["files"] for t in trash), "trash_bytes": sum(t["bytes"] for t in trash),
            "ready": not reasons, "reasons": reasons}


def check_trash_target(festa_dir, d):
    """Nunca: a festa, o Select Fotos, algo fora da festa, a raiz do HD."""
    festa = os.path.realpath(festa_dir)
    t = os.path.realpath(d)
    if not t.startswith(festa + "/"):
        raise ValueError("Só apago pastas que ficam dentro da pasta da festa.")
    sel = os.path.join(festa, SELECT_NAME)
    if sel.startswith(t + "/"):
        raise ValueError("Essa pasta tem o Select Fotos dentro.")
    if _fold(os.path.basename(t)) == _fold(SELECT_NAME):
        raise ValueError("O Select Fotos nunca vai para a Lixeira.")
    if t.rstrip("/").count("/") < 3:
        raise ValueError("Pasta curta demais para apagar com segurança.")
    return t


def to_trash(paths, runner=subprocess.run):
    """Manda para a Lixeira pelo Finder (da para tirar de la com 'Colocar de volta')."""
    res = []
    for p in paths:
        script = 'on run argv\ntell application "Finder" to delete (POSIX file (item 1 of argv) as alias)\nend run'
        try:
            r = runner(["osascript", "-e", script, p], capture_output=True, text=True, timeout=300)
            ok = r.returncode == 0 and not os.path.exists(p)
            res.append({"path": p, "ok": ok, "msg": "" if ok else (r.stderr or "não saiu da pasta").strip()})
        except (OSError, subprocess.TimeoutExpired) as e:
            res.append({"path": p, "ok": False, "msg": str(e)})
    return res
