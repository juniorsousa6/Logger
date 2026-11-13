"""
Testes do motor do Logger (cardlogger.py + regras do app.py).
Rodar:  python3 -m unittest discover -s tests -v
Com Hypothesis instalado, os testes de propriedade rodam com entradas aleatorias;
sem ele, rodam com um gerador proprio (semente impressa para reproduzir).
"""
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="logger-test-"))
os.environ["CARDLOGGER_HOME"] = str(TMP / "home")
os.environ["CARDLOGGER_VOLUMES"] = str(TMP / "Volumes")
(TMP / "Volumes").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import cardlogger as cl  # noqa: E402
import app  # noqa: E402

try:
    from hypothesis import given, settings, strategies as st
    HYP = True
except ImportError:
    HYP = False

SEED = int(os.environ.get("LOGGER_TEST_SEED", random.randrange(1 << 30)))
print(f"[testes] semente aleatoria = {SEED}  (repetir: LOGGER_TEST_SEED={SEED})")
CFG = cl.load_config()
PROFILES = {p["name"]: p for p in CFG["profiles"]}


def b2(data):
    return hashlib.blake2b(data, digest_size=20).hexdigest()


def tree_state(root):
    """Lista (caminho, tamanho, mtime, hash) de tudo dentro de root."""
    out = []
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out.append((p.relative_to(root).as_posix(), p.stat().st_size, int(p.stat().st_mtime), b2(p.read_bytes())))
    return out


def partials(root):
    return [p for p in Path(root).rglob("*.partial")]


def make_card(name, files):
    vol = TMP / "Volumes" / name
    if vol.exists():
        shutil.rmtree(vol)
    for rel, data in files.items():
        p = vol / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return vol


def fresh_job(name="2026_11_14 - Teste", folders=("Ronin", "Fixa", "Fotos", "Drone", "Áudio", "Action")):
    job = TMP / "HD" / name
    if job.exists():
        shutil.rmtree(job)
    for f in folders:
        (job / f).mkdir(parents=True, exist_ok=True)
    return job


def card_for_run(vol, ledger, dests=None):
    prof = cl.identify(vol, CFG["profiles"])
    c = cl.scan_card(vol, prof, ledger)
    routes = app.card_routes(prof, c["items"], None, app.subfolders(TMP / "HD" / "2026_11_14 - Teste"))
    c["dests"] = dests or [r["dest"] or "_ignorado" for r in routes]
    c["path"] = str(vol)
    return c


# ======================================================================= A. regras
class Regras(unittest.TestCase):
    def test_identifica_cada_tipo_de_cartao(self):
        casos = {
            "Sony": {"PRIVATE/M4ROOT/CLIP/JRS00001.MP4": b"x"},
            "Drone DJI": {"DCIM/DJI_001/DJI_0001.MP4": b"x", "DCIM/DJI_001/DJI_0001.SRT": b"1"},
            "Action (Osmo / GoPro / Insta360)": {"DCIM/DJI_001/DJI_20261114_0001_D.MP4": b"x"},
            "Gravador de audio": {"TASCAM_0001_1-2.wav": b"x"},
            "Gravador externo / Blackmagic": {"clips/A001_C001.braw": b"x"},
            "Outra camera": {"DCIM/100CANON/IMG_0001.CR3": b"x"},
        }
        for perfil, files in casos.items():
            with self.subTest(perfil=perfil):
                vol = make_card("ID_" + str(abs(hash(perfil))), files)
                self.assertEqual(cl.identify(vol, CFG["profiles"])["name"], perfil)

    def test_gopro_e_insta(self):
        for files in ({"DCIM/100GOPRO/GX010001.MP4": b"x"}, {"DCIM/Camera01/VID_0001.insv": b"x"}):
            vol = make_card("ACT", files)
            self.assertEqual(cl.identify(vol, CFG["profiles"])["name"], "Action (Osmo / GoPro / Insta360)")

    def test_rotas_sony_jrs_e_outra(self):
        p = PROFILES["Sony"]
        tabela = {
            "PRIVATE/M4ROOT/CLIP/JRS00001.MP4": "Ronin",
            "PRIVATE/M4ROOT/CLIP/JRS00001M01.XML": "Ronin",
            "PRIVATE/M4ROOT/CLIP/jrs00002.mp4": "Ronin",       # minuscula
            "PRIVATE/M4ROOT/CLIP/C8975.MP4": "Fixa",
            "PRIVATE/M4ROOT/CLIP/C8975M01.XML": "Fixa",
            "PRIVATE/M4ROOT/CLIP/FINGER0001.MP4": "Fixa",
            "DCIM/100MSDCF/JRS00001.ARW": "Fotos",
            "DCIM/100MSDCF/DSC00001.JPG": "Fotos",
            "DCIM/100MSDCF/JRS00001.HIF": "Fotos",
        }
        for rel, dest in tabela.items():
            with self.subTest(rel=rel):
                i = cl.route_index(rel, p)
                self.assertIsNotNone(i, rel)
                self.assertEqual(p["routes"][i]["to"], dest)

    def test_rotas_nao_perdem_midia(self):
        """Arquivo de midia comum nunca pode ficar sem rota em um cartao reconhecido."""
        midia = {"Sony": ["PRIVATE/M4ROOT/CLIP/C0001.MP4", "DCIM/100MSDCF/DSC0001.ARW"],
                 "Drone DJI": ["DCIM/DJI_001/DJI_0001.MP4", "DCIM/DJI_001/DJI_0001.JPG", "DCIM/PANORAMA/001/DJI_0001.JPG",
                               "DCIM/DJI_001/DJI_0001.DNG"],
                 "Action (Osmo / GoPro / Insta360)": ["DCIM/DJI_001/X.MP4", "DCIM/100GOPRO/GX01.MP4"],
                 "Gravador de audio": ["FOLDER01/ZOOM0001.WAV", "TASCAM_0001.wav", "REC_0001.MP3"],
                 "Outra camera": ["DCIM/100CANON/IMG_1.CR3", "CLIP/A001.MXF", "DCIM/X.MOV"]}
        for perfil, rels in midia.items():
            for rel in rels:
                with self.subTest(perfil=perfil, rel=rel):
                    self.assertIsNotNone(cl.route_index(rel, PROFILES[perfil]))

    def test_resolve_dest(self):
        r = app.resolve_dest
        self.assertEqual(r("Fixa", ["Fixa Wide", "Ronin"]), "Fixa Wide")
        self.assertIsNone(r("Fixa", ["Fixa Wide", "Fixa Zoom"]))          # ambiguo: pergunta
        self.assertEqual(r("Fixa", ["Fixa", "Fixa Wide"]), "Fixa")         # exato ganha
        self.assertEqual(r("Áudio", ["Audio"]), "Audio")
        self.assertEqual(r("Audio", [unicodedata.normalize("NFD", "Áudio")]), unicodedata.normalize("NFD", "Áudio"))
        self.assertEqual(r("Ronin", ["  ronin  "]), "  ronin  ")
        self.assertIsNone(r("Drone", ["Ronin"]))
        self.assertIsNone(r("", ["Ronin"]))
        self.assertIsNone(r("Fotos", []))
        self.assertIsNone(r("Fixa Wide", ["Fixa Zoom"]))

    def test_card_routes_pergunta_quando_nao_sabe(self):
        p = PROFILES["Sony"]
        items = [{"route": 0, "size": 1}, {"route": 1, "size": 1}]
        rs = app.card_routes(p, items, None, ["Ronin", "Fotos"])
        self.assertEqual(rs[0]["dest"], "Ronin")
        self.assertTrue(rs[1]["ask"])            # nao tem Fixa: pergunta
        self.assertFalse(rs[2]["ask"])           # sem fotos: nao pergunta
        # lembranca no formato antigo (lista) e ignorada
        rs = app.card_routes(p, items, ["Fotos", "Fotos"], ["Ronin", "Fixa", "Fotos"])
        self.assertEqual([r["dest"] for r in rs[:2]], ["Ronin", "Fixa"])
        # lembranca nova vale, mas nao na rota travada (JRS)
        rs = app.card_routes(p, items, {"Vídeos JRS": "Fixa", "Vídeos": "Ronin"}, ["Ronin", "Fixa", "Fotos"])
        self.assertEqual([r["dest"] for r in rs[:2]], ["Ronin", "Ronin"])
        # lembranca de pasta que nao existe neste trabalho: cai na regra
        rs = app.card_routes(p, items, {"Vídeos": "Zoom"}, ["Ronin", "Fixa"])
        self.assertEqual(rs[1]["dest"], "Fixa")
        # "nao copiar" lembrado
        rs = app.card_routes(p, items, {"Vídeos": ""}, ["Ronin", "Fixa"])
        self.assertEqual(rs[1]["dest"], "")
        self.assertFalse(rs[1]["ask"])

    def test_gravador_externo_sempre_pergunta(self):
        p = PROFILES["Gravador externo / Blackmagic"]
        rs = app.card_routes(p, [{"route": 0, "size": 1}], None, [])
        self.assertTrue(rs[0]["ask"])

    def test_safe_name(self):
        self.assertEqual(app.safe_name(" a/b:c\\d "), "abcd")
        for bad in (".", "..", "...", " . ", "\x00", "a\x00b"):
            with self.subTest(bad=bad):
                out = app.safe_name(bad)
                self.assertNotIn(out, (".", ".."))
                self.assertNotIn("\x00", out)
                self.assertFalse(out.startswith("."))

    def test_with_prefix(self):
        self.assertEqual(cl.with_prefix("C0001.MP4", "WIDE"), "WIDE_C0001.MP4")
        self.assertEqual(cl.with_prefix("WIDE_C0001.MP4", "WIDE"), "WIDE_C0001.MP4")
        self.assertEqual(cl.with_prefix("C0001.MP4", ""), "C0001.MP4")
        self.assertEqual(cl.with_prefix("C0001.MP4", None), "C0001.MP4")

    def test_human(self):
        self.assertEqual(cl.human(0), "0.0 B")
        self.assertEqual(cl.human(1024), "1.0 KB")
        self.assertTrue(cl.human(5 * 1024 ** 5).endswith("PB"))

    def test_sub_dir_reaproveita_acento_e_caixa(self):
        parent = TMP / "subdir"
        (parent / unicodedata.normalize("NFD", "Áudio")).mkdir(parents=True, exist_ok=True)
        self.assertEqual(cl.sub_dir(parent, "áudio").name, unicodedata.normalize("NFD", "Áudio"))
        self.assertEqual(cl.sub_dir(parent, "Drone"), parent / "Drone")

    def test_free_name(self):
        d = TMP / "fn"
        d.mkdir(exist_ok=True)
        (d / "A.MP4").write_bytes(b"1")
        res = set()
        a = cl.free_name(d / "A.MP4", res)
        b = cl.free_name(d / "A.MP4", res)
        self.assertEqual(a.name, "A_2.MP4")
        self.assertEqual(b.name, "A_3.MP4")

    def test_suggested_date_meia_noite(self):
        import datetime as dt
        for hora, esperado in ((1, "2026-11-14"), (5, "2026-11-14"), (6, "2026-11-15"), (23, "2026-11-15")):
            fake = dt.datetime(2026, 11, 15, hora, 30)
            with mock.patch.object(app.dt, "datetime", wraps=dt.datetime) as m:
                m.now.return_value = fake
                self.assertEqual(app.suggested_date(), esperado)


# ======================================================================= discos
class Discos(unittest.TestCase):
    def fake_info(self, info):
        return mock.patch.object(cl, "volume_info", return_value=info)

    def test_media_kind(self):
        v = TMP / "Volumes"
        casos = [({"BusProtocol": "Secure Digital", "Internal": True}, "card"),
                 ({"BusProtocol": "USB", "RemovableMedia": True, "MediaName": "SD Card Reader"}, "card"),
                 ({"BusProtocol": "USB", "MediaName": "CFexpress Reader"}, "card"),
                 ({"BusProtocol": "USB", "MediaName": "SanDisk Extreme Pro 55AF"}, "drive"),
                 ({"BusProtocol": "USB", "MediaName": "Samsung PSSD T7"}, "drive"),
                 ({"BusProtocol": "Thunderbolt", "MediaName": "LaCie Rugged SSD"}, "drive"),
                 ({"BusProtocol": "USB", "MediaName": "WD Elements 25A2"}, "drive"),
                 ({}, "unknown")]
        for info, kind in casos:
            with self.subTest(info=info), self.fake_info(info):
                self.assertEqual(cl.media_kind(v), kind)

    def test_hd_de_trabalho(self):
        hd = TMP / "Volumes" / "WORK"
        (hd / "Trabalhos" / "2026_01_01 - X").mkdir(parents=True, exist_ok=True)
        self.assertTrue(cl.looks_like_work_drive(hd))
        bm = make_card("BM", {"clip.braw": b"x"})
        self.assertFalse(cl.looks_like_work_drive(bm))

    def test_classify(self):
        vol = make_card("SDX", {"PRIVATE/M4ROOT/CLIP/C0001.MP4": b"x"})
        prof = cl.identify(vol, CFG["profiles"])
        s = {"volume_kinds": {}}
        with mock.patch.object(cl, "media_kind", return_value="card"):
            self.assertEqual(app.classify_volume(vol, prof, s, set())[0], "card")
        with mock.patch.object(cl, "media_kind", return_value="drive"):
            self.assertEqual(app.classify_volume(vol, prof, s, set())[0], "drive_card")
            self.assertEqual(app.classify_volume(vol, None, s, set())[0], "drive")
            self.assertEqual(app.classify_volume(vol, prof, s, {"SDX"})[0], "drive")   # ja recebeu copias
        with mock.patch.object(cl, "media_kind", return_value="card"):
            s2 = {"volume_kinds": {"name:SDX": "drive"}}
            self.assertEqual(app.classify_volume(vol, prof, s2, set())[0], "drive")    # usuario manda


# ======================================================================= B. copia com falhas
class Copia(unittest.TestCase):
    def setUp(self):
        self.src_dir = TMP / "src"
        self.src_dir.mkdir(exist_ok=True)
        self.dst_dir = TMP / "dst"
        if self.dst_dir.exists():
            shutil.rmtree(self.dst_dir)
        self.dst_dir.mkdir()
        self.old_chunk = cl.CHUNK
        cl.CHUNK = 1024

    def tearDown(self):
        cl.CHUNK = self.old_chunk

    def src(self, data, name="A.MP4"):
        p = self.src_dir / name
        p.write_bytes(data)
        return p

    def run_with_timeout(self, fn, secs=10):
        box = {}

        def go():
            try:
                box["ok"] = fn()
            except BaseException as e:  # noqa: BLE001
                box["err"] = e
        t = threading.Thread(target=go, daemon=True)
        t.start()
        t.join(secs)
        self.assertFalse(t.is_alive(), "TRAVOU: a copia nao terminou (deadlock)")
        return box

    def test_copia_tamanhos_limite(self):
        rnd = random.Random(SEED)
        for n in (0, 1, 1023, 1024, 1025, 4096, 10000):
            with self.subTest(n=n):
                data = bytes(rnd.getrandbits(8) for _ in range(n))
                s = self.src(data)
                d1, d2 = self.dst_dir / "a" / f"{n}.MP4", self.dst_dir / "b" / f"{n}.MP4"
                digest, _, _ = cl.copy_verified(s, [d1, d2])
                self.assertEqual(digest, b2(data))
                self.assertEqual(d1.read_bytes(), data)
                self.assertEqual(d2.read_bytes(), data)
                self.assertEqual(int(d1.stat().st_mtime), int(s.stat().st_mtime))
        self.assertEqual(partials(self.dst_dir), [])

    def test_disco_cheio_no_fsync_nao_trava(self):
        """Erro ao gravar no fim do arquivo (disco cheio aparece no fsync) nao pode travar o app."""
        s = self.src(os.urandom(5000))

        def boom(fd):
            raise OSError(28, "No space left on device")
        with mock.patch.object(cl.os, "fsync", boom):
            box = self.run_with_timeout(lambda: cl.copy_stream(s, [self.dst_dir / "A.MP4"]))
        self.assertIn("err", box)
        self.assertEqual(partials(self.dst_dir), [])

    def test_disco_cheio_no_meio(self):
        s = self.src(os.urandom(5000))
        real_open = open
        calls = {"n": 0}

        class Full:
            def __init__(self, f):
                self.f = f

            def write(self, b):
                calls["n"] += 1
                if calls["n"] > 2:
                    raise OSError(28, "No space left on device")
                return self.f.write(b)

            def __getattr__(self, k):
                return getattr(self.f, k)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

        def fake_open(p, mode="r", *a, **k):
            f = real_open(p, mode, *a, **k)
            return Full(f) if "w" in mode else f
        with mock.patch.object(cl, "open", fake_open, create=True):
            box = self.run_with_timeout(lambda: cl.copy_stream(s, [self.dst_dir / "A.MP4"]))
        self.assertIn("err", box)
        self.assertEqual(partials(self.dst_dir), [])

    def test_cartao_arrancado_no_meio_da_leitura(self):
        s = self.src(os.urandom(5000))
        real_open = open

        class Yank:
            def __init__(self, f):
                self.f, self.n = f, 0

            def read(self, k):
                self.n += 1
                if self.n > 2:
                    raise OSError(5, "Input/output error")
                return self.f.read(k)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

        def fake_open(p, mode="r", *a, **k):
            f = real_open(p, mode, *a, **k)
            return Yank(f) if Path(p) == s and "r" in mode else f
        with mock.patch.object(cl, "open", fake_open, create=True):
            box = self.run_with_timeout(lambda: cl.copy_stream(s, [self.dst_dir / "A.MP4", self.dst_dir / "B.MP4"]))
        self.assertIn("err", box)
        self.assertEqual(partials(self.dst_dir), [], "sobrou .partial no HD depois de erro de leitura")

    def test_copia_corrompida_e_pega(self):
        s = self.src(os.urandom(3000))
        d = self.dst_dir / "A.MP4"
        tmps, digest, _, _ = cl.copy_stream(s, [d])
        with open(tmps[0], "r+b") as f:  # um bit trocado no HD
            f.seek(100)
            b = f.read(1)
            f.seek(100)
            f.write(bytes([b[0] ^ 1]))
        with self.assertRaises(IOError):
            cl.verify_commit(s, tmps, [d], digest)
        self.assertFalse(d.exists())
        self.assertEqual(partials(self.dst_dir), [])

    def test_cartao_some_antes_de_conferir(self):
        s = self.src(os.urandom(3000))
        d = self.dst_dir / "A.MP4"
        tmps, digest, _, _ = cl.copy_stream(s, [d])
        s.unlink()
        with self.assertRaises(OSError):
            cl.verify_commit(s, tmps, [d], digest)
        self.assertEqual(partials(self.dst_dir), [], "sobrou .partial quando o cartao saiu antes da conferencia")


# ======================================================================= ponta a ponta do motor
class Logger(unittest.TestCase):
    def setUp(self):
        self.ledger = {"cards": {}}
        self.job = fresh_job()
        for v in (TMP / "Volumes").iterdir():
            shutil.rmtree(v)

    def files(self, n, prefix, size=3000):
        return {f"PRIVATE/M4ROOT/CLIP/{prefix}{i:05d}.MP4": os.urandom(size + i) for i in range(1, n + 1)}

    def test_fluxo_completo_e_cartao_intocado(self):
        a = make_card("SONY_A", {**self.files(3, "JRS"), "DCIM/100MSDCF/JRS00001.ARW": os.urandom(500)})
        b = make_card("SONY_B", self.files(2, "C"))
        before = (tree_state(a), tree_state(b))
        done = []
        cards = [card_for_run(a, self.ledger), card_for_run(b, self.ledger)]
        rep = cl.run_log(self.job, cards, self.ledger, on_card_done=lambda c, r: done.append((c["volume"], r["conferido_ok"])))
        self.assertTrue(rep["tudo_ok"])
        self.assertEqual(sorted(done), [("SONY_A", True), ("SONY_B", True)])
        self.assertEqual((tree_state(a), tree_state(b)), before, "o cartao foi alterado!")
        self.assertEqual(len(list((self.job / "Ronin").iterdir())), 3)
        self.assertEqual(len(list((self.job / "Fixa").iterdir())), 2)
        self.assertEqual(len(list((self.job / "Fotos").iterdir())), 1)
        self.assertEqual(partials(self.job), [])
        # a copia e identica ao original
        for f in rep["cards"][0]["copiados"]:
            self.assertEqual(b2((a / f["origem"]).read_bytes()), b2(Path(f["destino"]).read_bytes()))

    def test_rodar_de_novo_nao_duplica(self):
        a = make_card("SONY_A", self.files(3, "JRS"))
        cl.run_log(self.job, [card_for_run(a, self.ledger)], self.ledger)
        c2 = card_for_run(a, self.ledger)
        self.assertEqual(c2["items"], [])
        self.assertEqual(c2["already"], 3)
        self.assertTrue(c2["safe_to_format"])
        cl.run_log(self.job, [c2], self.ledger)
        self.assertEqual(len(list((self.job / "Ronin").iterdir())), 3)

    def test_mesmo_nome_em_dois_cartoes(self):
        a = make_card("SONY_1", {"PRIVATE/M4ROOT/CLIP/C0001.MP4": b"um"})
        b = make_card("SONY_2", {"PRIVATE/M4ROOT/CLIP/C0001.MP4": b"dois"})
        rep = cl.run_log(self.job, [card_for_run(a, self.ledger), card_for_run(b, self.ledger)], self.ledger)
        self.assertTrue(rep["tudo_ok"])
        got = sorted(p.read_bytes() for p in (self.job / "Fixa").iterdir())
        self.assertEqual(got, [b"dois", b"um"])

    def test_arquivo_ruim_nao_vira_conferido(self):
        a = make_card("SONY_A", self.files(3, "JRS"))
        c = card_for_run(a, self.ledger)
        bad = c["items"][1]["src"]
        real = cl.verify_commit

        def flaky(src, tmps, dsts, h):
            if Path(src) == Path(bad):
                h = "0" * 40
            return real(src, tmps, dsts, h)
        done = []
        with mock.patch.object(cl, "verify_commit", flaky):
            rep = cl.run_log(self.job, [c], self.ledger, on_card_done=lambda c, r: done.append(r["conferido_ok"]))
        self.assertFalse(rep["tudo_ok"])
        self.assertEqual(done, [False])          # nao ejeta
        self.assertEqual(len(rep["cards"][0]["erros"]), 1)
        self.assertEqual(len(list((self.job / "Ronin").iterdir())), 2)
        self.assertEqual(partials(self.job), [])
        # o arquivo ruim continua "novo" para a proxima tentativa
        again = card_for_run(a, self.ledger)
        self.assertEqual([it["rel"] for it in again["items"]], [str(Path(bad).relative_to(a).as_posix())])
        self.assertFalse(again["safe_to_format"])

    def test_sem_espaco_nao_comeca(self):
        a = make_card("SONY_A", self.files(2, "JRS"))
        with mock.patch.object(cl, "free_space", return_value=10):
            with self.assertRaises(IOError):
                cl.run_log(self.job, [card_for_run(a, self.ledger)], self.ledger)
        self.assertEqual(list((self.job / "Ronin").iterdir()), [])

    def test_cartao_sem_arquivo_some_no_meio(self):
        a = make_card("SONY_A", self.files(3, "JRS"))
        c = card_for_run(a, self.ledger)
        Path(c["items"][2]["src"]).unlink()      # arquivo some depois do scan
        rep = cl.run_log(self.job, [c], self.ledger)
        self.assertFalse(rep["tudo_ok"])
        self.assertEqual(len(rep["cards"][0]["erros"]), 1)
        self.assertEqual(partials(self.job), [])

    def test_backup_em_dois_hds(self):
        a = make_card("SONY_A", self.files(2, "JRS"))
        bk = TMP / "BACKUP"
        if bk.exists():
            shutil.rmtree(bk)
        bk.mkdir()
        rep = cl.run_log(self.job, [card_for_run(a, self.ledger)], self.ledger, backups=[(TMP / "HD", bk)])
        self.assertTrue(rep["tudo_ok"])
        for f in rep["cards"][0]["copiados"]:
            self.assertEqual(Path(f["destino"]).read_bytes(), Path(f["backups"][0]).read_bytes())

    def test_midia_ignorada_impede_formatar(self):
        a = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/JRS00001.MP4": b"x"})
        cl.run_log(self.job, [card_for_run(a, self.ledger)], self.ledger)
        (a / "PRIVATE" / "M4ROOT" / "SUB").mkdir()
        (a / "PRIVATE" / "M4ROOT" / "SUB" / "X.MXF").write_bytes(b"video")   # midia que nao bate em nenhuma rota
        c = card_for_run(a, self.ledger)
        self.assertFalse(c["safe_to_format"], "disse 'pode formatar' com video que nao foi copiado")

    def test_arquivo_movido_dentro_do_trabalho_nao_e_sumido(self):
        a = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/C0001.MP4": b"abc"})
        cl.run_log(self.job, [card_for_run(a, self.ledger)], self.ledger)
        (self.job / "Fixa" / "C0001.MP4").rename(self.job / "Ronin" / "C0001.MP4")   # usuario corrigiu a pasta
        c = card_for_run(a, self.ledger)
        self.assertEqual(c["missing_on_hd"], 0)
        self.assertTrue(c["safe_to_format"])
        (self.job / "Ronin" / "C0001.MP4").unlink()                                   # agora sumiu de verdade
        gone = card_for_run(a, self.ledger)
        self.assertEqual(gone["missing_on_hd"], 1)
        self.assertFalse(gone["safe_to_format"], "disse 'pode formatar' com arquivo que sumiu do HD")

    def test_pastas_vazias(self):
        a = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/JRS00001.MP4": b"x"})
        cl.run_log(self.job, [card_for_run(a, self.ledger)], self.ledger)
        (self.job / "Drone" / ".DS_Store").write_bytes(b"x")
        vaz = app.empty_folders(self.job)
        self.assertIn("Drone", vaz)
        self.assertNotIn("Ronin", vaz)

    def test_quatro_cartoes_em_paralelo(self):
        vols = [make_card(f"S{i}", self.files(4, "C", 2000 + i)) for i in range(6)]
        rep = cl.run_log(self.job, [card_for_run(v, self.ledger) for v in vols], self.ledger)
        self.assertTrue(rep["tudo_ok"])
        self.assertEqual(len(list((self.job / "Fixa").iterdir())), 24)
        self.assertEqual(len(rep["cards"]), 6)


# ======================================================================= propriedades
def _names():
    rnd = random.Random(SEED)
    alfa = "abcAÁãçÇ éÉ_-.()[]0123456789 ünicode😀"
    for _ in range(300):
        yield "".join(rnd.choice(alfa) for _ in range(rnd.randint(0, 20)))


class Propriedades(unittest.TestCase):
    def check_safe_name(self, s):
        out = app.safe_name(s)
        self.assertFalse(set(out) & set("/:\\\x00"))
        self.assertNotIn(out, (".", ".."))
        self.assertEqual(out, out.strip())
        self.assertEqual(app.safe_name(out), out)                        # idempotente

    def check_prefix(self, name, prefix):
        once = cl.with_prefix(name, prefix)
        self.assertEqual(cl.with_prefix(once, prefix), once)             # idempotente
        self.assertTrue(once.endswith(name))

    def check_resolve(self, want, subs):
        r = app.resolve_dest(want, subs)
        self.assertTrue(r is None or r in subs)
        if r is not None and any(app._fold(x) == app._fold(want) for x in subs):
            self.assertEqual(app._fold(r), app._fold(want))               # exato sempre ganha

    def test_props_gerador_proprio(self):
        names = list(_names())
        rnd = random.Random(SEED)
        for s in names:
            self.check_safe_name(s)
            self.check_prefix(s or "x", rnd.choice(["", "WIDE", "ACT", " a "]))
            self.check_resolve(s, rnd.sample(names, 4))

    if HYP:
        @settings(max_examples=400, deadline=None)
        @given(st.text())
        def test_hyp_safe_name(self, s):
            self.check_safe_name(s)

        @settings(max_examples=300, deadline=None)
        @given(st.text(min_size=1), st.text(max_size=6))
        def test_hyp_prefix(self, n, p):
            self.check_prefix(n, p)

        @settings(max_examples=300, deadline=None)
        @given(st.text(max_size=12), st.lists(st.text(max_size=12), max_size=6))
        def test_hyp_resolve(self, w, subs):
            self.check_resolve(w, subs)

        @settings(max_examples=60, deadline=None)
        @given(st.binary(max_size=5000))
        def test_hyp_copia_igual(self, data):
            old = cl.CHUNK
            cl.CHUNK = 777
            try:
                d = TMP / "hyp"
                d.mkdir(exist_ok=True)
                s = d / "src.bin"
                s.write_bytes(data)
                dst = d / "out.bin"
                if dst.exists():
                    dst.unlink()
                digest, _, _ = cl.copy_verified(s, [dst])
                self.assertEqual(dst.read_bytes(), data)
                self.assertEqual(digest, b2(data))
            finally:
                cl.CHUNK = old


# ======================================================================= fuzzing do leitor de metadados
import media  # noqa: E402


class Fuzz(unittest.TestCase):
    """Arquivo corrompido, cortado ou lixo nunca pode derrubar o relatorio."""

    def test_probe_aguenta_lixo(self):
        rnd = random.Random(SEED)
        d = TMP / "fuzz"
        d.mkdir(exist_ok=True)
        real = None
        for f in (TMP / "Volumes").rglob("*.MP4"):
            real = f.read_bytes()
            break
        seeds = [b"", b"\x00" * 8, b"\x00\x00\x00\x01ftyp", b"\xff" * 64, b"RIFF\x00\x00\x00\x00WAVE",
                 b"RIFF" + b"\xff" * 40, b"\x00\x00\x00\x10moov\x00\x00\x00\x08mvhd"]
        for i in range(400):
            base = rnd.choice(seeds + [os.urandom(rnd.randint(0, 300))])
            data = bytearray(base)
            for _ in range(rnd.randint(0, 6)):           # corrompe bytes ao acaso
                if data:
                    data[rnd.randrange(len(data))] = rnd.randrange(256)
            ext = rnd.choice([".MP4", ".MOV", ".WAV", ".JPG", ".ARW"])
            p = d / f"f{i}{ext}"
            p.write_bytes(bytes(data))
            try:
                media.probe(p)
            except Exception as e:  # noqa: BLE001
                self.fail(f"probe quebrou com {p.name} ({len(data)} bytes, semente {SEED}): {e!r}")

    def test_relatorio_com_arquivos_ruins(self):
        job = fresh_job("2026_11_15 - Fuzz")
        (job / "Ronin" / "vazio.MP4").write_bytes(b"")
        (job / "Ronin" / "lixo.MP4").write_bytes(os.urandom(5000))
        (job / "Áudio" / "ruim.WAV").write_bytes(b"RIFF\xff\xff\xff\xffWAVEfmt ")
        (job / "Fotos" / "x.JPG").write_bytes(b"\xff\xd8\xff")
        path, summary = media.build_report(job)
        self.assertTrue(Path(path).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
