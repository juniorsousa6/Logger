"""
media.py - informacoes dos clipes (duracao, resolucao, fps, horario),
miniaturas, relatorio visual (HTML) com linha do tempo e catalogo de trabalhos.
Sem dependencias: le o cabecalho dos MP4/MOV e WAV direto em Python.
Miniaturas: usa ffmpeg se estiver instalado; senao, as ferramentas do proprio
macOS (qlmanage para video, sips para foto).
"""
import datetime as dt
import html
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mts", ".mxf", ".insv", ".lrv"}
AUDIO_EXT = {".wav", ".bwf", ".mp3"}
PHOTO_EXT = {".jpg", ".jpeg", ".arw", ".hif", ".heif", ".heic", ".dng", ".cr3", ".cr2", ".nef", ".raf", ".rw2", ".png"}
SIDE_EXT = {".xml", ".srt", ".thm", ".lrf", ".lrv", ".bim", ".xmp"}
MAC_EPOCH = dt.datetime(1904, 1, 1)


def kind(path):
    e = Path(path).suffix.lower()
    if e in {".lrv", ".lrf", ".thm"}:
        return "side"
    if e in VIDEO_EXT:
        return "video"
    if e in AUDIO_EXT:
        return "audio"
    if e in PHOTO_EXT:
        return "photo"
    return "side"


# ---------------------------------------------------------------- MP4 / MOV
def _atoms(f, start, end):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return
        size, typ = struct.unpack(">I4s", hdr)
        hl = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hl = 16
        elif size == 0:
            size = end - pos
        if size < hl:
            return
        yield typ.decode("latin1"), pos + hl, pos + size
        pos += size


def _find(f, start, end, typ):
    for t, s, e in _atoms(f, start, end):
        if t == typ:
            return s, e
    return None


def probe_mp4(path):
    info = {}
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        moov = _find(f, 0, size, "moov")
        if not moov:
            return {"broken": True}  # sem 'moov' = gravacao interrompida / arquivo corrompido
        mvhd = _find(f, *moov, "mvhd")
        if mvhd:
            f.seek(mvhd[0])
            ver = f.read(1)[0]
            f.read(3)
            if ver == 1:
                ctime, _, ts, dur = struct.unpack(">QQIQ", f.read(28))
            else:
                ctime, _, ts, dur = struct.unpack(">IIII", f.read(16))
            if ts:
                info["duration"] = dur / ts
            if ctime:
                info["created_utc"] = (MAC_EPOCH + dt.timedelta(seconds=ctime)).isoformat()
        for t, s, e in _atoms(f, *moov):
            if t != "trak":
                continue
            mdia = _find(f, s, e, "mdia")
            if not mdia:
                continue
            hdlr = _find(f, *mdia, "hdlr")
            if not hdlr:
                continue
            f.seek(hdlr[0] + 8)
            if f.read(4) != b"vide":
                continue
            tkhd = _find(f, s, e, "tkhd")
            if tkhd:
                f.seek(tkhd[1] - 8)
                w, h = struct.unpack(">II", f.read(8))
                info["width"], info["height"] = w >> 16, h >> 16
            mdhd = _find(f, *mdia, "mdhd")
            ts = None
            if mdhd:
                f.seek(mdhd[0])
                ver = f.read(1)[0]
                f.read(3)
                f.read(16 if ver == 1 else 8)
                ts = struct.unpack(">I", f.read(4))[0]
            minf = _find(f, *mdia, "minf")
            stbl = minf and _find(f, *minf, "stbl")
            stts = stbl and _find(f, *stbl, "stts")
            if stts and ts:
                f.seek(stts[0] + 4)
                n = struct.unpack(">I", f.read(4))[0]
                count = total = 0
                for _ in range(min(n, 100000)):
                    c, d = struct.unpack(">II", f.read(8))
                    count += c
                    total += c * d
                if total:
                    info["fps"] = round(count * ts / total, 3)
            break
    return info


# ---------------------------------------------------------------- WAV / BWF
def probe_wav(path):
    info = {}
    with open(path, "rb") as f:
        if f.read(4) != b"RIFF":
            return {"broken": True}
        f.read(4)
        if f.read(4) != b"WAVE":
            return {"broken": True}
        byte_rate = None
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            cid, size = struct.unpack("<4sI", hdr)
            start = f.tell()
            if cid == b"fmt ":
                fmt = f.read(16)
                ch, sr, br = struct.unpack("<HII", fmt[2:12])
                bits = struct.unpack("<H", fmt[14:16])[0]
                byte_rate = br
                info.update({"channels": ch, "samplerate": sr, "bits": bits})
            elif cid == b"bext":
                b = f.read(min(size, 346))
                if len(b) >= 338:
                    d, t = b[320:330].decode("latin1"), b[330:338].decode("latin1")
                    try:  # gravadores escrevem a hora de inicio aqui
                        info["start"] = dt.datetime.strptime(d.replace(":", "-").replace("/", "-") + " " +
                                                             t.replace("-", ":").replace(".", ":"),
                                                             "%Y-%m-%d %H:%M:%S").isoformat()
                    except ValueError:
                        pass
            elif cid == b"data":
                if byte_rate:
                    real = min(size, os.path.getsize(path) - start)
                    info["duration"] = real / byte_rate
                    if real < size:
                        info["broken"] = True
            f.seek(start + size + (size & 1))
    return info


def probe(path):
    p = Path(path)
    k = kind(p)
    info = {"kind": k}
    try:
        if p.suffix.lower() in {".mp4", ".mov", ".m4v", ".insv"}:
            info.update(probe_mp4(p))
        elif p.suffix.lower() in {".wav", ".bwf"}:
            info.update(probe_wav(p))
    except (OSError, struct.error, IndexError, UnicodeDecodeError):
        info["broken"] = True
    st = p.stat()
    end = dt.datetime.fromtimestamp(st.st_mtime)
    dur = info.get("duration") or 0
    if "start" not in info:
        # a camera grava a data do arquivo no fim da gravacao
        info["start"] = (end - dt.timedelta(seconds=dur)).isoformat() if k in ("video", "audio") else end.isoformat()
    info["size"] = st.st_size
    return info


# ---------------------------------------------------------------- miniaturas
def thumbnail(src, out):
    """Gera miniatura JPG/PNG (320px). Devolve o caminho ou None."""
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    k = kind(src)
    try:
        if shutil.which("ffmpeg") and k in ("video", "photo"):
            ss = ["-ss", "1"] if k == "video" else []
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", *ss, "-i", str(src), "-frames:v", "1",
                                "-vf", "scale=320:-2", str(out)], capture_output=True, timeout=60)
            if r.returncode == 0 and out.exists():
                return out
            if k == "video":  # clipe com menos de 1s
                r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1",
                                    "-vf", "scale=320:-2", str(out)], capture_output=True, timeout=60)
                if r.returncode == 0 and out.exists():
                    return out
        if k == "photo" and shutil.which("sips"):
            r = subprocess.run(["sips", "-Z", "320", "-s", "format", "jpeg", str(src), "--out", str(out)],
                               capture_output=True, timeout=60)
            if r.returncode == 0 and out.exists():
                return out
        if k in ("video", "photo") and shutil.which("qlmanage"):
            subprocess.run(["qlmanage", "-t", "-s", "320", "-o", str(out.parent), str(src)],
                           capture_output=True, timeout=60)
            ql = out.parent / (src.name + ".png")
            if ql.exists():
                ql_out = out.with_suffix(".png")
                os.replace(ql, ql_out)
                return ql_out
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


# ---------------------------------------------------------------- relatorio
def fmt_dur(s):
    s = int(round(s or 0))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}h{m:02d}m" if h else f"{m}m{sec:02d}s"


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def job_date_from_name(path):
    for part in reversed(Path(path).parts):
        m = re.match(r"(\d{4})_(\d{2})_(\d{2})", part)
        if m:
            return dt.date(*map(int, m.groups()))
    return None


def scan_target(target):
    """Todos os clipes da pasta (dia ou trabalho), agrupados por pasta de camera."""
    target = Path(target)
    clips = []
    for root, dirs, files in os.walk(target):
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_")))
        for name in sorted(files):
            if name.startswith(".") or name.endswith(".partial"):
                continue
            p = Path(root) / name
            k = kind(p)
            if k == "side":
                continue
            rel = p.relative_to(target)
            group = "/".join(rel.parts[:-1]) or "(raiz)"
            camera = rel.parts[0] if len(rel.parts) > 1 else "(raiz)"
            clips.append({"path": p, "rel": rel.as_posix(), "group": group, "camera": camera, "kind": k})
    return clips


def build_report(target, progress=None):
    """Gera <target>/Relatorio.html com miniaturas, alertas e linha do tempo. Devolve (caminho, resumo)."""
    target = Path(target)
    thumbs = target / "_logs" / "thumbs"
    cache_path = target / "_logs" / "media-cache.json"
    import json
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        cache = {}
    clips = scan_target(target)
    jdate = job_date_from_name(target)
    for i, c in enumerate(clips, 1):
        if progress is not None:
            progress["current"] = c["rel"]
            progress["rep_i"], progress["rep_n"] = i, len(clips)
        st = c["path"].stat()
        key = f"{c['rel']}|{st.st_size}|{int(st.st_mtime)}"
        info = cache.get(key)
        if not info:
            info = probe(c["path"])
            if c["kind"] in ("video", "photo"):
                tname = re.sub(r"[^A-Za-z0-9._-]", "_", c["rel"]) + ".jpg"
                t = thumbnail(c["path"], thumbs / tname)
                info["thumb"] = t.relative_to(target).as_posix() if t else None
            cache[key] = info
        c.update(info)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    # ---- alertas
    alerts = []
    for c in clips:
        if c.get("broken"):
            alerts.append(("bad", f"{c['rel']}: arquivo parece corrompido ou a gravação foi interrompida"))
        elif c["kind"] in ("video", "audio") and (c.get("duration") or 0) < 1 and "duration" in c:
            alerts.append(("bad", f"{c['rel']}: clipe com menos de 1 segundo"))
    if jdate:
        by_cam = {}
        for c in clips:
            try:
                d = dt.datetime.fromisoformat(c["start"]).date()
            except (KeyError, ValueError):
                continue
            if abs((d - jdate).days) > 1:
                by_cam.setdefault(c["group"], []).append(d)
        for g, ds in by_cam.items():
            alerts.append(("warn", f"{g}: {len(ds)} arquivo(s) com data {min(ds).strftime('%d/%m/%Y')}"
                                   f" (o trabalho é de {jdate.strftime('%d/%m/%Y')}). Relógio da câmera errado?"))

    # ---- resumo por camera
    groups = {}
    for c in clips:
        g = groups.setdefault(c["group"], {"clips": 0, "photos": 0, "seconds": 0, "bytes": 0})
        g["bytes"] += c.get("size", 0)
        if c["kind"] == "photo":
            g["photos"] += 1
        else:
            g["clips"] += 1
            g["seconds"] += c.get("duration") or 0
    summary = {"groups": groups, "alerts": alerts,
               "seconds": sum(g["seconds"] for g in groups.values()),
               "bytes": sum(g["bytes"] for g in groups.values()),
               "clips": sum(g["clips"] for g in groups.values()),
               "photos": sum(g["photos"] for g in groups.values())}

    out = target / "Relatorio.html"
    out.write_text(render_html(target, clips, summary), encoding="utf-8")
    return out, summary


def _timeline_svg(clips):
    items = []
    for c in clips:
        if c["kind"] not in ("video", "audio", "photo"):
            continue
        try:
            s = dt.datetime.fromisoformat(c["start"])
        except (KeyError, ValueError):
            continue
        d = c.get("duration") or 0
        items.append((c["group"], s, s + dt.timedelta(seconds=max(d, 0)), c))
    if not items:
        return ""
    # ignora horarios muito fora (relogio errado) para nao esmagar o grafico
    starts = sorted(i[1] for i in items)
    med = starts[len(starts) // 2]
    items = [i for i in items if abs((i[1] - med).total_seconds()) < 36 * 3600]
    t0 = min(i[1] for i in items)
    t1 = max(i[2] for i in items)
    span = max((t1 - t0).total_seconds(), 60)
    rows = sorted({i[0] for i in items})
    W, label, rh = 1000, 170, 26
    H = 30 + rh * len(rows)
    x = lambda t: label + (W - label - 10) * (t - t0).total_seconds() / span  # noqa: E731
    out = [f'<svg viewBox="0 0 {W} {H}" class="tl" role="img" aria-label="Linha do tempo">']
    # marcas de hora
    step = 3600 if span > 3 * 3600 else 1800 if span > 3600 else 600 if span > 1200 else 60
    t = t0.replace(minute=0, second=0, microsecond=0)
    while t <= t1:
        if t >= t0:
            xx = x(t)
            out.append(f'<line x1="{xx:.1f}" y1="18" x2="{xx:.1f}" y2="{H}" class="grid"/>'
                       f'<text x="{xx:.1f}" y="12" class="tick">{t.strftime("%H:%M")}</text>')
        t += dt.timedelta(seconds=step)
    for r, g in enumerate(rows):
        y = 22 + r * rh
        out.append(f'<text x="0" y="{y + 15}" class="rowl">{html.escape(g[:24])}</text>')
        for gg, s, e, c in items:
            if gg != g:
                continue
            x1, x2 = x(s), x(e)
            if c["kind"] == "photo":
                out.append(f'<line x1="{x1:.1f}" y1="{y + 4}" x2="{x1:.1f}" y2="{y + 18}" class="ph">'
                           f'<title>{html.escape(c["rel"])} {s.strftime("%H:%M:%S")}</title></line>')
            else:
                cls = "au" if c["kind"] == "audio" else "vi"
                out.append(f'<rect x="{x1:.1f}" y="{y + 3}" width="{max(x2 - x1, 2):.1f}" height="16" rx="2" class="{cls}">'
                           f'<title>{html.escape(c["rel"])}  {s.strftime("%H:%M:%S")} ({fmt_dur(c.get("duration"))})</title></rect>')
    out.append("</svg>")
    return "".join(out)


def render_html(target, clips, s):
    e = html.escape
    gen = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    rows = "".join(
        f"<tr><td>{e(g)}</td><td>{v['clips']}</td><td>{fmt_dur(v['seconds']) if v['clips'] else '-'}</td>"
        f"<td>{v['photos'] or '-'}</td><td>{human(v['bytes'])}</td></tr>" for g, v in sorted(s["groups"].items()))
    alerts = "".join(f'<li class="{k}">{e(m)}</li>' for k, m in s["alerts"]) or '<li class="ok">Nenhum problema encontrado.</li>'
    order = sorted(clips, key=lambda c: c.get("start", ""))
    cards = []
    for g in sorted({c["group"] for c in clips}):
        items = []
        for c in [c for c in order if c["group"] == g]:
            meta = []
            if c.get("duration"):
                meta.append(fmt_dur(c["duration"]))
            if c.get("width"):
                meta.append(f"{c['width']}x{c['height']}")
            if c.get("fps"):
                meta.append(f"{c['fps']:g} fps")
            if c.get("samplerate"):
                meta.append(f"{c['samplerate'] / 1000:g} kHz {c.get('bits', '')}bit")
            try:
                meta.append(dt.datetime.fromisoformat(c["start"]).strftime("%H:%M:%S"))
            except (KeyError, ValueError):
                pass
            img = (f'<img loading="lazy" src="{e(c["thumb"])}">' if c.get("thumb")
                   else f'<div class="noimg">{"🎙" if c["kind"] == "audio" else "🎞" if c["kind"] == "video" else "🖼"}</div>')
            bad = ' bad' if c.get("broken") else ''
            items.append(f'<figure class="clip{bad}">{img}<figcaption><b>{e(c["path"].name)}</b>'
                         f'<span>{" · ".join(meta)}</span></figcaption></figure>')
        cards.append(f'<h3>{e(g)}</h3><div class="grid">{"".join(items)}</div>')
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Relatório - {e(target.name)}</title>
<style>
:root{{--bg:#111316;--p:#1a1d22;--l:#2a2e35;--t:#e8eaed;--m:#9aa0a8;--a:#4f9dff;--ok:#3ecf8e;--w:#f5b84a;--b:#ff5d5d}}
body{{margin:0;background:var(--bg);color:var(--t);font:14px/1.45 -apple-system,Helvetica,Arial,sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:24px 16px 60px}}h1{{margin:0;font-size:22px}}h2{{font-size:16px;margin:28px 0 10px}}h3{{font-size:14px;margin:22px 0 8px;color:var(--m)}}
.sub{{color:var(--m)}}.kpis{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}}.kpi{{background:var(--p);border:1px solid var(--l);border-radius:10px;padding:12px 16px;min-width:130px}}
.kpi b{{display:block;font-size:22px}}.kpi span{{color:var(--m);font-size:12px}}
table{{border-collapse:collapse;width:100%;background:var(--p);border-radius:10px;overflow:hidden}}td,th{{padding:8px 12px;border-bottom:1px solid var(--l);text-align:left}}th{{color:var(--m);font-weight:500}}
ul.al{{list-style:none;padding:0}}ul.al li{{padding:8px 12px;border-radius:8px;margin-bottom:6px;background:var(--p)}}li.bad{{border-left:3px solid var(--b)}}li.warn{{border-left:3px solid var(--w)}}li.ok{{border-left:3px solid var(--ok)}}
.tlw{{background:var(--p);border:1px solid var(--l);border-radius:10px;padding:12px;overflow-x:auto}}svg.tl{{width:100%;min-width:700px}}
.tick{{fill:var(--m);font-size:10px}}.rowl{{fill:var(--t);font-size:11px}}.grid{{stroke:var(--l)}}rect.vi{{fill:var(--a)}}rect.au{{fill:var(--ok)}}line.ph{{stroke:var(--w);stroke-width:2}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}}
.clip{{margin:0;background:var(--p);border:1px solid var(--l);border-radius:8px;overflow:hidden}}.clip.bad{{border-color:var(--b)}}
.clip img,.noimg{{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#000}}.noimg{{display:flex;align-items:center;justify-content:center;font-size:28px}}
figcaption{{padding:6px 8px;font-size:12px}}figcaption b{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}figcaption span{{color:var(--m)}}
.leg span{{margin-right:14px;color:var(--m);font-size:12px}}.leg i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}}
</style></head><body><main>
<h1>{e(target.name)}</h1><div class="sub">{e(str(target))} · gerado em {gen}</div>
<div class="kpis"><div class="kpi"><b>{fmt_dur(s['seconds'])}</b><span>de material gravado</span></div>
<div class="kpi"><b>{s['clips']}</b><span>clipes de vídeo/áudio</span></div><div class="kpi"><b>{s['photos']}</b><span>fotos</span></div>
<div class="kpi"><b>{human(s['bytes'])}</b><span>no total</span></div></div>
<h2>Alertas</h2><ul class="al">{alerts}</ul>
<h2>Por câmera</h2><table><tr><th>Pasta</th><th>Clipes</th><th>Duração</th><th>Fotos</th><th>Tamanho</th></tr>{rows}</table>
<h2>Linha do tempo</h2><div class="leg"><span><i style="background:var(--a)"></i>vídeo</span><span><i style="background:var(--ok)"></i>áudio</span><span><i style="background:var(--w)"></i>foto</span></div>
<div class="tlw">{_timeline_svg(clips)}</div>
<h2>Clipes</h2>{''.join(cards)}
</main></body></html>"""


# ---------------------------------------------------------------- catalogo
def hd_of(path):
    p = Path(path).resolve()
    parts = p.parts
    if len(parts) > 2 and parts[1] == "Volumes":
        return parts[2], Path("/Volumes") / parts[2]
    return "Mac (disco interno)", Path("/")


def volume_uuid(root):
    try:
        out = subprocess.run(["diskutil", "info", str(root)], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"Volume UUID:\s+(\S+)", out)
        return m.group(1) if m else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


JOB_RE = re.compile(r"^\d{4}_\d{2}_\d{2} - .+")


def catalog_job(catalog, job_dir, summary=None):
    job = Path(job_dir)
    hd_name, hd_root = hd_of(job)
    folders, files, total = {}, [], 0
    for root, dirs, fs in os.walk(job):
        dirs[:] = [d for d in dirs if not d.startswith((".", "_")) and d != "ascmhl"]
        for n in fs:
            if n.startswith(".") or n.endswith(".partial") or n == "Relatorio.html":
                continue
            p = Path(root) / n
            rel = p.relative_to(job)
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            top = rel.parts[0] if len(rel.parts) > 1 else "(raiz)"
            f = folders.setdefault(top, {"files": 0, "bytes": 0})
            f["files"] += 1
            f["bytes"] += sz
            total += sz
            files.append(rel.as_posix())
    entry = catalog.setdefault("jobs", {}).get(str(job), {})
    entry.update({"name": job.name, "path": str(job), "hd": hd_name, "hd_uuid": volume_uuid(hd_root) or entry.get("hd_uuid", ""),
                  "folders": folders, "bytes": total, "files": files,
                  "updated": dt.datetime.now().isoformat(timespec="seconds")})
    if summary:
        entry["seconds"] = summary.get("seconds", entry.get("seconds"))
    catalog["jobs"][str(job)] = entry
    return entry


def catalog_hd(catalog, root, progress=None):
    """Procura pastas de trabalho (AAAA_MM_DD - nome) ate 3 niveis dentro de root."""
    root = Path(root)
    found = []
    for dirpath, dirs, _ in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        keep = []
        for d in dirs:
            if d.startswith("."):
                continue
            if JOB_RE.match(d) and not re.match(r"^\d{4}_\d{2}_\d{2} - Dia \d+$", d):
                found.append(Path(dirpath) / d)
            elif depth < 2:
                keep.append(d)
        dirs[:] = keep
    for i, j in enumerate(found, 1):
        if progress is not None:
            progress["current"] = f"{i}/{len(found)} {j.name}"
        catalog_job(catalog, j)
    return found


def search(catalog, q, limit=50):
    q = q.strip().lower()
    res = []
    for j in catalog.get("jobs", {}).values():
        name_hit = q in j["name"].lower() or q in j.get("hd", "").lower()
        file_hits = [f for f in j.get("files", []) if q in f.lower()] if q else []
        if not q or name_hit or file_hits:
            res.append({"name": j["name"], "path": j["path"], "hd": j["hd"], "online": Path(j["path"]).exists(),
                        "bytes": human(j.get("bytes", 0)), "seconds": fmt_dur(j["seconds"]) if j.get("seconds") else "",
                        "folders": {k: v["files"] for k, v in j.get("folders", {}).items()},
                        "file_hits": file_hits[:20], "more_hits": max(0, len(file_hits) - 20)})
    res.sort(key=lambda r: r["name"], reverse=True)
    return res[:limit]
