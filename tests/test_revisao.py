"""
Testes da revisao de 14/11/2026 (versao 1.3.3). Cada teste reproduz um achado
do relatorio "Revisao do Logger 1.3.2" e falhava na 1.3.2.
Rodar:  python3 -m unittest tests.test_revisao -v
"""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="logger-revisao-"))
os.environ["CARDLOGGER_HOME"] = str(TMP / "home")
os.environ["CARDLOGGER_VOLUMES"] = str(TMP / "Volumes")
(TMP / "Volumes").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import cardlogger as cl  # noqa: E402
import app  # noqa: E402

CFG = cl.load_config()


def make_card(name, files):
    vol = TMP / "Volumes" / name
    if vol.exists():
        shutil.rmtree(vol)
    for rel, data in files.items():
        p = vol / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return vol


def fresh_job(name="2026_11_14 - Revisao", folders=("Ronin", "Fixa", "Fotos", "Action")):
    job = TMP / "HD" / name
    if job.exists():
        shutil.rmtree(job)
    for f in folders:
        (job / f).mkdir(parents=True, exist_ok=True)
    return job


def card_for_run(vol, ledger, job, skip_labels=()):
    """Como o app faz: rota marcada 'nao copiar' sai da lista."""
    prof = cl.identify(vol, CFG["profiles"])
    c = cl.scan_card(vol, prof, ledger)
    routes = app.card_routes(prof, c["items"], None, app.subfolders(job))
    dests = ["" if r["label"] in skip_labels else (r["dest"] or "") for r in routes]
    c["items"] = [it for it in c["items"] if dests[it["route"]]]
    c["bytes"] = sum(it["size"] for it in c["items"])
    c["dests"] = [d or "_ignorado" for d in dests]
    c["path"] = str(vol)
    return c


def files_in(folder):
    return sorted(p.name for p in Path(folder).iterdir() if p.is_file())


class PodeFormatarHonesto(unittest.TestCase):
    """Achados 1, 2, 7 e 8: so diz 'pode formatar' quando toda a midia saiu do cartao."""

    def setUp(self):
        self.ledger = {"cards": {}}
        self.job = fresh_job()

    def run_card(self, vol, **kw):
        done = []
        c = card_for_run(vol, self.ledger, self.job, **kw)
        rep = cl.run_log(self.job, [c], self.ledger, on_card_done=lambda c, r: done.append(r))
        return rep, done[0]

    def test_insta360_foto_insp_e_copiada(self):
        v = make_card("X4", {"DCIM/Camera01/VID_20261114_120000_00_001.insv": os.urandom(800),
                             "DCIM/Camera01/IMG_20261114_120500_00_002.insp": os.urandom(500)})
        rep, card = self.run_card(v)
        self.assertIn("IMG_20261114_120500_00_002.insp", files_in(self.job / "Action"))
        self.assertTrue(card["pode_formatar"])

    def test_midia_fora_das_regras_nao_libera_formatar(self):
        v = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/JRS00001.MP4": os.urandom(800),
                                 "PRIVATE/M4ROOT/OUTRA/X0001.MXF": os.urandom(300)})
        rep, card = self.run_card(v)
        self.assertTrue(card["conferido_ok"])            # o que foi copiado confere
        self.assertFalse(card["pode_formatar"], "disse 'pode formatar' com video que ficou no cartao")
        self.assertIn("PRIVATE/M4ROOT/OUTRA/X0001.MXF", card["sobrou"])
        self.assertFalse(rep["pode_formatar_tudo"])

    def test_rota_nao_copiar_nao_libera_formatar(self):
        v = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/JRS00001.MP4": os.urandom(800),
                                 "DCIM/100MSDCF/JRS00002.ARW": os.urandom(300)})
        rep, card = self.run_card(v, skip_labels=("Fotos",))
        self.assertFalse(card["pode_formatar"])
        self.assertIn("DCIM/100MSDCF/JRS00002.ARW", card["sobrou"])

    def test_sony_com_miniaturas_ganha_pode_formatar(self):
        v = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/C0001.MP4": os.urandom(800),
                                 "PRIVATE/M4ROOT/THMBNL/C0001T01.JPG": os.urandom(100)})
        rep, card = self.run_card(v)
        self.assertTrue(card["pode_formatar"], "miniatura da Sony impediu o 'pode formatar'")
        again = cl.scan_card(v, cl.identify(v, CFG["profiles"]), self.ledger)
        self.assertTrue(again["safe_to_format"])

    def test_tudo_copiado_libera_formatar(self):
        v = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/JRS00001.MP4": os.urandom(800)})
        rep, card = self.run_card(v)
        self.assertTrue(card["pode_formatar"])
        self.assertTrue(rep["pode_formatar_tudo"])
        self.assertEqual(card["sobrou"], [])


class CopiarDeNovo(unittest.TestCase):
    """Achado 9: copia apagada do HD pode ser copiada de novo; HD desligado nao confunde."""

    def setUp(self):
        self.ledger = {"cards": {}}
        self.job = fresh_job()

    def test_copia_apagada_volta_a_ser_nova(self):
        v = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/C0001.MP4": os.urandom(800)})
        cl.run_log(self.job, [card_for_run(v, self.ledger, self.job)], self.ledger)
        (self.job / "Fixa" / "C0001.MP4").unlink()
        c = card_for_run(v, self.ledger, self.job)
        self.assertEqual([it["rel"] for it in c["items"]], ["PRIVATE/M4ROOT/CLIP/C0001.MP4"])
        self.assertEqual(c["missing_on_hd"], 1)
        rep = cl.run_log(self.job, [c], self.ledger)
        self.assertTrue(rep["tudo_ok"])
        self.assertEqual(files_in(self.job / "Fixa"), ["C0001.MP4"])

    def test_hd_desligado_nao_vira_copia_nova(self):
        v = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/C0001.MP4": os.urandom(800)})
        prof = cl.identify(v, CFG["profiles"])
        c = cl.scan_card(v, prof, self.ledger)
        it = c["items"][0]
        dest = cl.VOLUMES_DIR / "HD_DESLIGADO" / "2026_11_13 - Ontem" / "Fixa" / "C0001.MP4"
        self.ledger["cards"][c["uuid"]] = {"name": "SONY_A", "files": {it["key"]: {"dest": str(dest), "bytes": it["size"]}}}
        again = cl.scan_card(v, prof, self.ledger)
        self.assertEqual(again["items"], [], "copiaria para o trabalho errado so porque o HD esta desligado")
        self.assertFalse(again["safe_to_format"])
        self.assertEqual(again["offline"], 1)


class Retomar(unittest.TestCase):
    """Achado 10: rodar de novo depois de uma queda nao cria duplicados (_2)."""

    def test_queda_depois_de_copiar_nao_duplica(self):
        job = fresh_job()
        ledger = {"cards": {}}
        v = make_card("SONY_A", {f"PRIVATE/M4ROOT/CLIP/C000{i}.MP4": os.urandom(5000 + i) for i in range(1, 5)})
        cl.run_log(job, [card_for_run(v, ledger, job)], ledger)
        lost = {"cards": {}}                       # o historico nao chegou a ser salvo (queda)
        rep = cl.run_log(job, [card_for_run(v, lost, job)], lost)
        self.assertTrue(rep["tudo_ok"])
        self.assertEqual(files_in(job / "Fixa"), [f"C000{i}.MP4" for i in range(1, 5)])
        self.assertTrue(cl.scan_card(v, cl.identify(v, CFG["profiles"]), lost)["safe_to_format"])

    def test_mesmo_nome_e_tamanho_mas_conteudo_diferente_nao_e_adotado(self):
        job = fresh_job()
        (job / "Fixa" / "C0001.MP4").write_bytes(b"A" * 100)
        v = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/C0001.MP4": b"B" * 100})
        ledger = {"cards": {}}
        cl.run_log(job, [card_for_run(v, ledger, job)], ledger)
        self.assertEqual(sorted((job / "Fixa").iterdir()), [job / "Fixa" / "C0001.MP4", job / "Fixa" / "C0001_2.MP4"])
        self.assertEqual((job / "Fixa" / "C0001.MP4").read_bytes(), b"A" * 100)


class Backup(unittest.TestCase):
    """Achado 4: o backup nunca sobrescreve um arquivo que ja estava la."""

    def test_backup_nao_sobrescreve(self):
        job = fresh_job()
        bk = TMP / "BACKUP"
        if bk.exists():
            shutil.rmtree(bk)
        (bk / job.name / "Fixa").mkdir(parents=True)
        old = bk / job.name / "Fixa" / "C0100.MP4"
        old.write_bytes(b"ANTIGO")
        v = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/C0100.MP4": os.urandom(900)})
        ledger = {"cards": {}}
        rep = cl.run_log(job, [card_for_run(v, ledger, job)], ledger, backups=[(TMP / "HD", bk)])
        self.assertTrue(rep["tudo_ok"])
        self.assertEqual(old.read_bytes(), b"ANTIGO", "o backup antigo foi sobrescrito")
        f = rep["cards"][0]["copiados"][0]
        self.assertEqual(Path(f["destino"]).name, Path(f["backups"][0]).name)   # mesmo nome nos dois HDs
        self.assertEqual(Path(f["destino"]).read_bytes(), Path(f["backups"][0]).read_bytes())


class GravacaoGarantida(unittest.TestCase):
    """Achado 5: cada copia e as pastas passam pelo F_FULLFSYNC antes de conferir/ejetar."""

    def test_full_sync_em_cada_copia_e_pasta(self):
        job = fresh_job()
        ledger = {"cards": {}}
        v = make_card("SONY_A", {"PRIVATE/M4ROOT/CLIP/C0001.MP4": os.urandom(900),
                                 "PRIVATE/M4ROOT/CLIP/C0002.MP4": os.urandom(900)})
        calls = []
        real = cl.full_sync
        with mock.patch.object(cl, "full_sync", lambda fd: (calls.append(fd), real(fd))):
            cl.run_log(job, [card_for_run(v, ledger, job)], ledger)
        self.assertGreaterEqual(len(calls), 3)      # 2 arquivos + pelo menos 1 pasta


class Cancelar(unittest.TestCase):
    """Achado 12: da para cancelar; nada pela metade fica no HD e nada e ejetado."""

    def test_cancelar(self):
        job = fresh_job()
        ledger = {"cards": {}}
        v = make_card("SONY_A", {f"PRIVATE/M4ROOT/CLIP/C000{i}.MP4": os.urandom(3000) for i in range(1, 6)})
        stop = threading.Event()
        stop.set()
        done = []
        rep = cl.run_log(job, [card_for_run(v, ledger, job)], ledger, cancel=stop,
                         on_card_done=lambda c, r: done.append(r))
        self.assertTrue(rep["cancelado"])
        self.assertFalse(rep["tudo_ok"])
        self.assertFalse(done[0]["pode_formatar"])
        self.assertEqual(list(job.rglob("*.partial")), [])


class ArquivosCorrompidos(unittest.TestCase):
    """Achado 13: historico corrompido volta da copia de seguranca e o estragado e guardado."""

    def test_historico_corrompido_volta_do_bak(self):
        path = cl.STATE_DIR / "teste-ledger.json"   # so os arquivos do Logger ganham .bak
        cl.save_json(path, {"cards": {"A": 1}})
        cl.save_json(path, {"cards": {"A": 2}})
        path.write_text("{estragado")
        data = cl.load_json(path, {"cards": {}})
        self.assertEqual(data, {"cards": {"A": 1}})
        self.assertTrue(list(path.parent.glob("teste-ledger.json.corrompido-*")), "o arquivo estragado foi descartado")
        self.assertTrue(cl.WARNINGS)


# ------------------------------------------------------------------ servidor
PROC = URL = None
SV = TMP / "srv"


def setUpModule():
    global PROC, URL
    V = SV / "V"
    (V / "HD").mkdir(parents=True)
    info = {}
    for name, files in {"SONYOK": {"PRIVATE/M4ROOT/CLIP/C0001.MP4": 4000},
                        "SONYSOBRA": {"PRIVATE/M4ROOT/CLIP/JRS00001.MP4": 4000, "PRIVATE/M4ROOT/OUTRA/Z.MXF": 500},
                        "SUMIU": {"PRIVATE/M4ROOT/CLIP/C0009.MP4": 4000}}.items():
        for rel, size in files.items():
            p = V / name / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(os.urandom(size))
        info[name] = {"BusProtocol": "Secure Digital", "RemovableMedia": True, "VolumeUUID": "UUID-" + name}
    info["HD"] = {"BusProtocol": "USB", "MediaName": "LaCie", "VolumeUUID": "UUID-HD"}
    (SV / "info").mkdir()
    (SV / "ej").mkdir()
    for k, v in info.items():
        (SV / "info" / f"{k}.plist").write_bytes(plistlib.dumps(v))
    b = SV / "bin"
    b.mkdir()
    (b / "diskutil").write_text(f'''#!/bin/bash
D="{SV}"
if [ "$1" = "info" ]; then cat "$D/info/$(basename "$3").plist" 2>/dev/null; exit 0; fi
if [ "$1" = "eject" ]; then mv "$2" "$D/ej/"; exit 0; fi
''')
    (b / "osascript").write_text(f'#!/bin/bash\nfor a in "$@"; do printf "%s\\n--\\n" "$a"; done >> "{SV}/osascript.log"\necho /tmp\n')
    (b / "open").write_text("#!/bin/bash\nexit 0\n")
    for f in b.iterdir():
        f.chmod(0o755)
    env = dict(os.environ, CARDLOGGER_HOME=str(SV / "home"), CARDLOGGER_VOLUMES=str(V), LOGGER_PORT="0",
               LOGGER_NO_BROWSER="1", PATH=f"{b}:{os.environ['PATH']}")
    PROC = subprocess.Popen([sys.executable, str(ROOT / "app.py")], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    for line in PROC.stdout:
        if line.startswith("LOGGER_URL "):
            URL = line.split()[1].rstrip("/")
            break
    threading.Thread(target=lambda: [None for _ in PROC.stdout], daemon=True).start()


def tearDownModule():
    PROC.terminate()
    PROC.wait(5)
    shutil.rmtree(TMP, ignore_errors=True)


def call(path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(URL + path, data=data, method="POST" if data is not None else "GET", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {}


class Servidor(unittest.TestCase):
    """Achados 3, 6 e 12 no servidor de verdade."""

    def test_1_origem_nula_e_recusada(self):
        code, _ = call("/api/reveal", {"path": "/bin"}, {"Origin": "null"})
        self.assertEqual(code, 403)

    def test_2_corpo_que_nao_e_json_e_recusado(self):
        code, _ = call("/api/reveal", {"path": "/bin"}, {"Content-Type": "text/plain"})
        self.assertEqual(code, 403)

    def test_3_outro_endereco_e_recusado(self):
        port = URL.rsplit(":", 1)[1]
        code, _ = call("/api/init", headers={"Host": f"site-malicioso.com:{port}"})
        self.assertEqual(code, 403)

    def test_4_texto_nao_vira_comando_no_applescript(self):
        payload = 'x" & (do shell script "touch /tmp/PWNED") & "'
        code, _ = call("/api/pick-folder", {"prompt": payload})
        self.assertEqual(code, 200)
        log = (SV / "osascript.log").read_text()
        scripts = [a for a in log.split("\n--\n") if "choose folder" in a]
        self.assertTrue(scripts)
        self.assertTrue(all("do shell script" not in s for s in scripts), "o texto recebido entrou no codigo do AppleScript")

    def test_5_fluxo_cartao_sumido_e_cartao_com_sobra(self):
        job = call("/api/create", {"base_dir": str(SV / "V" / "HD"), "event": "Servidor", "date": "2026-11-14",
                                   "folders": ["Ronin", "Fixa", "Fotos"]})[1]
        tgt = job["targets"][0]
        cards = [c for c in call("/api/cards?target=" + urllib.request.quote(tgt))[1] if c.get("profile")]
        sel = [{"path": c["path"], "dests": [r["dest"] for r in c["routes"]]} for c in cards]
        os.rename(SV / "V" / "SUMIU", SV / "SUMIU-fora")
        code, r = call("/api/log", {"job_dir": job["job_dir"], "target": tgt, "cards": sel, "auto_eject": True})
        self.assertEqual(code, 400, "comecou a copia com um cartao que nao esta mais conectado")
        self.assertIn("SUMIU", r["error"])
        sel = [s for s in sel if not s["path"].endswith("SUMIU")]
        code, r = call("/api/log", {"job_dir": job["job_dir"], "target": tgt, "cards": sel, "auto_eject": True})
        self.assertEqual(code, 200)
        for _ in range(300):
            p = call("/api/progress")[1]
            if p.get("phase") in ("pronto", "erro"):
                break
            time.sleep(0.1)
        ej = {e["volume"]: e for e in p["ejected"]}
        self.assertTrue(ej["SONYOK"]["ok"])
        self.assertFalse(ej["SONYSOBRA"]["ok"], "ejetou um cartao com video que nao foi copiado")
        self.assertTrue((SV / "V" / "SONYSOBRA").is_dir())
        cards = {c["volume"]: c for c in p["report"]["cards"]}
        self.assertFalse(cards["SONYSOBRA"]["pode_formatar"])
        self.assertTrue(cards["SONYOK"]["pode_formatar"])

    def test_6_cancelar_existe(self):
        code, _ = call("/api/cancel", {})
        self.assertEqual(code, 200)



class EspelhoVIOT(unittest.TestCase):
    """1.3.4: atalho para espelhar direto em Dropbox/VIOT, sem perder a opcao de outra pasta."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="logger-viot-"))
        self.db = self.tmp / "Dropbox"
        (self.db / "Viot").mkdir(parents=True)
        (self.db / "Outros").mkdir()
        self.hd = self.tmp / "HD"
        self.hd.mkdir()
        self.saved = {}
        self.p = [mock.patch.object(app, "dropbox_guess", return_value=str(self.db)),
                  mock.patch.object(app, "save_settings", side_effect=self.saved.update),
                  mock.patch.object(app, "update_catalog")]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def body(self, **kw):
        b = {"base_dir": str(self.hd), "event": "Show", "date": "2026-11-14", "folders": ["Ronin"],
             "dropbox_on": True, "dropbox_folders": ["Drop", "9x16"]}
        b.update(kw)
        return b

    def test_acha_pasta_viot_sem_ligar_para_maiuscula(self):
        self.assertEqual(app.viot_guess(), str(self.db / "Viot"))

    def test_sem_dropbox_nao_quebra(self):
        with mock.patch.object(app, "dropbox_guess", return_value=""):
            self.assertEqual(app.viot_guess(), "")

    def test_cria_no_viot_e_guarda_cada_pasta_no_seu_lugar(self):
        r = app.create_job(self.body(dropbox_dir=str(self.db / "Viot"), dropbox_dest="viot"))
        self.assertTrue((self.db / "Viot" / "2026_11_14 - Show" / "9x16").is_dir())
        self.assertEqual(r["dropbox_job"], str(self.db / "Viot" / "2026_11_14 - Show"))
        self.assertEqual(self.saved["dropbox_dest"], "viot")
        self.assertEqual(self.saved["viot_dir"], str(self.db / "Viot"))

    def test_outra_pasta_nao_mexe_no_viot(self):
        app.create_job(self.body(dropbox_dir=str(self.db / "Outros"), dropbox_dest="pasta"))
        self.assertTrue((self.db / "Outros" / "2026_11_14 - Show" / "Drop").is_dir())
        self.assertFalse((self.db / "Viot" / "2026_11_14 - Show").exists())
        self.assertEqual(self.saved["dropbox_dest"], "pasta")
        self.assertEqual(self.saved["dropbox_dir"], str(self.db / "Outros"))

    def test_viot_sumiu_avisa_em_portugues(self):
        with self.assertRaisesRegex(ValueError, "VIOT"):
            app.create_job(self.body(dropbox_dir=str(self.db / "Nao"), dropbox_dest="viot"))

if __name__ == "__main__":
    unittest.main()
