"""
Testes de todos os endpoints do servidor do Logger, com entradas boas e ruins.
Sobe o app.py de verdade numa porta livre, com pastas temporarias.
"""
import json
import os
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

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="logger-api-"))
VOL = TMP / "Volumes"
HD = VOL / "HD"
PROC = URL = None


def setUpModule():
    global PROC, URL
    HD.mkdir(parents=True)
    card = VOL / "SONY_A" / "PRIVATE" / "M4ROOT" / "CLIP"
    card.mkdir(parents=True)
    for i in range(1, 6):
        (card / f"JRS{i:05d}.MP4").write_bytes(os.urandom(200_000))
    card2 = VOL / "SONY_B" / "PRIVATE" / "M4ROOT" / "CLIP"
    card2.mkdir(parents=True)
    (card2 / "C0001.MP4").write_bytes(os.urandom(100_000))
    env = dict(os.environ, CARDLOGGER_HOME=str(TMP / "home"), CARDLOGGER_VOLUMES=str(VOL),
               LOGGER_PORT="0", LOGGER_NO_BROWSER="1", PATH="/nonexistent:" + os.environ["PATH"])
    PROC = subprocess.Popen([sys.executable, str(ROOT / "app.py")], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    for line in PROC.stdout:
        if line.startswith("LOGGER_URL "):
            URL = line.split()[1]
            break
    threading.Thread(target=lambda: [None for _ in PROC.stdout], daemon=True).start()


def tearDownModule():
    PROC.terminate()
    PROC.wait(5)
    shutil.rmtree(TMP, ignore_errors=True)


def call(path, body=None, raw=None, headers=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(URL.rstrip("/") + path, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read()) if r.headers.get_content_type() == "application/json" else r.read()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait_done(timeout=60):
    t = time.time()
    while time.time() - t < timeout:
        code, p = call("/api/progress")
        assert code == 200, p
        if p.get("phase") in ("pronto", "erro"):
            return p
        time.sleep(0.2)
    raise AssertionError("a copia nao terminou")


class API(unittest.TestCase):
    def test_00_pagina_e_init(self):
        code, html = call("/")
        self.assertEqual(code, 200)
        self.assertIn(b"LOGGER", html.upper())
        code, j = call("/api/init")
        self.assertEqual(code, 200)
        for k in ("base_dir", "folders", "auto_eject", "dropbox_folders"):
            self.assertIn(k, j["settings"])
        self.assertRegex(j["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_01_rotas_desconhecidas_e_campos_faltando(self):
        self.assertEqual(call("/api/nada")[0], 404)
        self.assertEqual(call("/api/nada", {})[0], 404)
        code, j = call("/api/subfolders")
        self.assertEqual(code, 400)
        self.assertIn("faltou", j["error"])
        self.assertEqual(call("/api/subfolders?path=/nao/existe")[1], [])
        self.assertEqual(call("/api/cards")[0], 200)
        self.assertEqual(call("/api/search?q=")[0], 200)
        self.assertEqual(call("/api/progress")[0], 200)

    def test_02_corpo_invalido(self):
        self.assertEqual(call("/api/create", raw=b"{nao e json")[0], 400)
        self.assertEqual(call("/api/create", raw=b"[1,2]")[0], 400)
        self.assertEqual(call("/api/create", raw=b"")[0], 400)   # corpo vazio = {} -> campo faltando
        self.assertEqual(call("/api/log", {})[0], 400)

    def test_03_outro_site_nao_pode_mandar(self):
        code, _ = call("/api/settings", {"base_dir": "/"}, headers={"Origin": "https://site-malicioso.com"})
        self.assertEqual(code, 403)

    def test_04_criar_trabalho_valida_tudo(self):
        base = {"base_dir": str(HD), "event": "Casamento", "date": "2026-11-14", "folders": ["Ronin", "Fixa"]}
        self.assertEqual(call("/api/create", {**base, "base_dir": "/nao/existe"})[0], 400)
        self.assertEqual(call("/api/create", {**base, "event": "  "})[0], 400)
        self.assertEqual(call("/api/create", {**base, "folders": []})[0], 400)
        self.assertEqual(call("/api/create", {**base, "folders": ["..", "/", "."]})[0], 400)
        self.assertEqual(call("/api/create", {**base, "date": "14/11/2026"})[0], 400)
        self.assertEqual(call("/api/create", {**base, "date": "2026-02-30"})[0], 400)
        code, j = call("/api/create", {**base, "event": "../../fora", "folders": ["Ronin", "../Fixa", "Fotos"]})
        self.assertEqual(code, 200, j)
        job = Path(j["job_dir"])
        self.assertEqual(job.parent, HD)                     # nao escapou do HD
        self.assertTrue((job / "Ronin").is_dir())
        self.assertTrue((job / "Fixa").is_dir())
        self.assertFalse((HD / "Fixa").exists())
        code, j = call("/api/create", {**base, "event": "Festival", "mode": "multi",
                                       "days": ["2026-11-14", "2026-11-15"]})
        self.assertEqual(code, 200, j)
        self.assertEqual([Path(t).name for t in j["targets"]], ["2026_11_14 - Dia 1", "2026_11_15 - Dia 2"])
        code, j2 = call("/api/open-job", {"path": j["job_dir"]})
        self.assertEqual(len(j2["targets"]), 2)
        self.assertEqual(call("/api/open-job", {"path": "/nao/existe"})[0], 400)

    def test_05_settings(self):
        self.assertEqual(call("/api/settings", {"folders": "nao-e-lista", "rename": True})[0], 200)
        s = call("/api/init")[1]["settings"]
        self.assertIsInstance(s["folders"], list)
        self.assertTrue(s["rename"])
        call("/api/settings", {"rename": False, "folders": ["Ronin", "Fixa", "Fotos", "", "  "]})
        self.assertEqual(call("/api/init")[1]["settings"]["folders"], ["Ronin", "Fixa", "Fotos"])

    def test_06_volume_kind(self):
        self.assertEqual(call("/api/volume-kind", {"volume": "SONY_A", "kind": "drive"})[0], 200)
        cards = call("/api/cards")[1]
        self.assertTrue(any(c["volume"] == "SONY_A" and c.get("hidden") for c in cards))
        call("/api/volume-kind", {"volume": "SONY_A", "kind": None})
        cards = call("/api/cards")[1]
        self.assertTrue(any(c["volume"] == "SONY_A" and c.get("profile") for c in cards))

    def test_07_copia_valida_destinos(self):
        code, j = call("/api/create", {"base_dir": str(HD), "event": "Copia", "date": "2026-11-16",
                                       "folders": ["Ronin", "Fixa", "Fotos"]})
        target = j["targets"][0]
        cards = call("/api/cards?target=" + urllib.request.quote(target))[1]
        a = next(c for c in cards if c["volume"] == "SONY_A")
        b = next(c for c in cards if c["volume"] == "SONY_B")
        self.assertEqual([r["dest"] for r in a["routes"] if r["files"]], ["Ronin"])
        self.assertEqual([r["dest"] for r in b["routes"] if r["files"]], ["Fixa"])
        body = {"job_dir": j["job_dir"], "target": target}
        self.assertEqual(call("/api/log", {**body, "cards": [{"path": a["path"], "dests": ["Ronin"]}]})[0], 400)
        self.assertEqual(call("/api/log", {**body, "cards": [{"path": a["path"], "dests": [None, "Fixa", "Fotos"]}]})[0], 400)
        self.assertEqual(call("/api/log", {**body, "target": "/nao/existe", "cards": []})[0], 400)
        # clique duplo: so uma copia pode comecar
        sel = [{"path": a["path"], "dests": [r["dest"] for r in a["routes"]]},
               {"path": b["path"], "dests": [r["dest"] for r in b["routes"]]}]
        results = []
        ths = [threading.Thread(target=lambda: results.append(call("/api/log", {**body, "cards": sel})[0])) for _ in range(4)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        self.assertEqual(results.count(200), 1, results)
        # o progresso aguenta ser lido sem parar durante a copia
        errs = []

        def hammer():
            for _ in range(150):
                c, _ = call("/api/progress")
                if c != 200:
                    errs.append(c)
        hs = [threading.Thread(target=hammer) for _ in range(4)]
        [h.start() for h in hs]
        p = wait_done()
        [h.join() for h in hs]
        self.assertEqual(errs, [])
        self.assertEqual(p["phase"], "pronto", p.get("fatal"))
        self.assertTrue(p["report"]["tudo_ok"])
        self.assertEqual(len(list((Path(target) / "Ronin").iterdir())), 5)
        self.assertEqual(sorted(p["empty_folders"]), ["Fotos"])
        # de novo: nada novo, cartao pode formatar
        cards = call("/api/cards?target=" + urllib.request.quote(target))[1]
        a = next(c for c in cards if c["volume"] == "SONY_A")
        self.assertEqual(a["new"], 0)
        self.assertTrue(a["safe_to_format"])

    def test_08_ejetar_e_mostrar_nao_quebram(self):
        code, j = call("/api/eject", {"paths": ["/nao/existe"]})
        self.assertEqual(code, 200)
        self.assertFalse(j[0]["ok"])
        self.assertEqual(call("/api/reveal", {"path": "/nao/existe"})[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
