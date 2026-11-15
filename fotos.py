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
import hashlib
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


_COPY_SUFFIX = re.compile(r" \(\d+\)$")


def _key(path, size):
    stem, ext = os.path.splitext(os.path.basename(path))
    return (_fold(_COPY_SUFFIX.sub("", stem)), ext.lower(), size)


def _select_index(select):
    """Arquivos do Select Fotos por (nome sem ' (2)', extensao, tamanho)."""
    if not os.path.isdir(select):
        return set()
    return {_key(f, _size(f)) for f in _walk(select)}


def sidecars(path):
    """Arquivos que acompanham a foto: o .xmp (edicao do Lightroom, quando ele grava no arquivo)."""
    base = os.path.splitext(path)[0]
    out = []
    for ext in (".xmp", ".XMP", ".Xmp"):
        if os.path.isfile(base + ext) and (base + ext) not in out:
            out.append(base + ext)
    return out


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _free_name(dst_dir, name, src_size, src_sha):
    """Nome livre no Select Fotos. Se ja existe o mesmo arquivo (mesmo conteudo), devolve None (nada a fazer)."""
    stem, ext = os.path.splitext(name)
    n = 1
    while True:
        cand = os.path.join(dst_dir, name if n == 1 else f"{stem} ({n}){ext}")
        if not os.path.lexists(cand):
            return cand
        if os.path.isfile(cand) and _size(cand) == src_size and _sha(cand) == src_sha:
            return None
        n += 1
        if n > 999:
            raise OSError("nomes demais iguais no Select Fotos: " + name)


def _copy_one(src, dst_dir):
    """Copia conferida: escreve num .parcial, compara SHA-256 com a origem e so entao da o nome final.
    Nunca sobrescreve. Devolve o caminho final (ou o que ja existia igual)."""
    size, sha = _size(src), _sha(src)
    dst = _free_name(dst_dir, os.path.basename(src), size, sha)
    if dst is None:
        return None
    tmp = os.path.join(dst_dir, "." + os.path.basename(dst) + ".parcial")
    try:
        with open(src, "rb") as a, open(tmp, "wb") as b:
            shutil.copyfileobj(a, b, 4 << 20)
            b.flush()
            os.fsync(b.fileno())
        shutil.copystat(src, tmp)
        if _size(tmp) != size or _sha(tmp) != sha:
            raise OSError("a cópia não bateu com o original")
        if os.path.lexists(dst):
            raise OSError("apareceu um arquivo com o mesmo nome durante a cópia")
        os.rename(tmp, dst)
        return dst
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def copy_to_select(festa_dir, paths, progress=None):
    """Copia as fotos com estrela (e o .xmp delas) para <festa>/Select Fotos, conferindo cada uma."""
    festa = os.path.realpath(festa_dir)
    dst_dir = os.path.join(festa, SELECT_NAME)
    os.makedirs(dst_dir, exist_ok=True)
    res = []
    for i, p in enumerate(paths):
        if progress:
            progress(i, len(paths), os.path.basename(p))
        if not os.path.realpath(p).startswith(festa + "/"):
            res.append({"path": p, "ok": False, "msg": "fora da pasta da festa"})
            continue
        try:
            out = _copy_one(p, dst_dir)
            dst = out or os.path.join(dst_dir, os.path.basename(p))
            for x in sidecars(p):
                sx = os.path.splitext(dst)[0] + os.path.splitext(x)[1]
                if not os.path.lexists(sx):
                    shutil.copy2(x, sx)
            res.append({"path": p, "ok": True, "to": dst, "msg": "" if out else "já estava lá"})
        except OSError as e:
            res.append({"path": p, "ok": False, "msg": str(e)})
    return res


def plan(festa_dir, images, exists=os.path.exists):
    """festa_dir: pasta da festa no HD. images: Catalog.images_under(festa_dir).

    Devolve o que vai para a Lixeira e se esta tudo seguro:
      starred: cada foto com estrela e onde ela esta (no Select Fotos ou nao)
      trash: pastas inteiras que vao para a Lixeira (as que tem fotos do catalogo fora do Select Fotos)
      ready: True so se toda foto com estrela esta salva no Select Fotos.
    """
    festa = _nfc(str(festa_dir)).rstrip("/")
    select = festa + "/" + SELECT_NAME
    sel_index = _select_index(select)
    starred, problems = [], []
    for im in images:
        if im["rating"] < 1:
            continue
        p = im["path"]
        in_select = p.startswith(select + "/")
        on_disk = exists(p)
        if on_disk:
            safe = in_select or _key(p, _size(p)) in sel_index
        else:  # original ja apagado: basta ter uma foto com o mesmo nome no Select Fotos
            safe = not in_select and _key(p, None)[:2] in {k[:2] for k in sel_index}
        copyable = not safe and not in_select and on_disk
        starred.append({"path": p, "rating": im["rating"], "safe": safe, "copy": copyable,
                        "where": "Select Fotos" if safe else ("sumiu do disco" if in_select or not on_disk else p[len(festa) + 1:])})
        if not safe and not copyable:
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
    to_copy = [s["path"] for s in starred if s["copy"]]
    reasons = []
    if not starred:
        reasons.append("Nenhuma foto com estrela no catálogo para esta festa.")
    if problems:
        reasons.append(f"{len(problems)} foto(s) com estrela não estão no disco (nem no Select Fotos).")
    if not trash:
        reasons.append("Nada para limpar: não há fotos fora do Select Fotos.")
    return {"festa": festa, "select": select, "select_exists": os.path.isdir(select), "select_files": n_sel,
            "total": len(images), "starred": starred, "missing": len(problems), "trash": trash,
            "to_copy": to_copy, "copy_bytes": sum(_size(f) + sum(_size(x) for x in sidecars(f)) for f in to_copy),
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
