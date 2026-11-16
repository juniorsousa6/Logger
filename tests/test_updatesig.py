"""Assinatura das atualizações: o texto assinado, o release.py e as travas do app do Mac.
O Swift não compila aqui; o que dá para conferir dele é conferido no texto do código."""
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import updatesig  # noqa: E402

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    HAVE = True
except ImportError:
    HAVE = False

SWIFT = (ROOT / "mac" / "LoggerApp.swift").read_text(encoding="utf-8")


def keypair():
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return k, base64.b64encode(pub).decode()


def sign(k, j):
    msg = updatesig.message(j["version"], j["sha256"], j["size"])
    return base64.b64encode(k.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()


@unittest.skipUnless(HAVE, "pacote cryptography ausente")
class Assinatura(unittest.TestCase):
    def setUp(self):
        self.k, self.pub = keypair()
        self.j = {"version": "1.6.1", "sha256": "ab" * 32, "size": 123456, "url": "x"}
        self.j["sig"] = sign(self.k, self.j)

    def test_confere(self):
        self.assertTrue(updatesig.verify(self.j, self.pub))

    def test_mexer_em_qualquer_campo_assinado_derruba(self):
        for campo, valor in [("version", "9.9.9"), ("sha256", "cd" * 32), ("size", 123457)]:
            j = dict(self.j, **{campo: valor})
            self.assertFalse(updatesig.verify(j, self.pub), campo)

    def test_outra_chave_nao_vale(self):
        _, outra = keypair()
        self.assertFalse(updatesig.verify(self.j, outra))

    def test_sem_assinatura_nao_vale(self):
        j = dict(self.j); del j["sig"]
        self.assertFalse(updatesig.verify(j, self.pub))

    def test_maiuscula_no_sha_nao_muda_o_texto(self):
        j = dict(self.j, sha256=self.j["sha256"].upper())
        self.assertTrue(updatesig.verify(j, self.pub))


class MesmoTextoNoSwift(unittest.TestCase):
    def test_formato_igual(self):
        m = re.search(r'Data\("(logger-update:v1[^"]*)"\.utf8\)', SWIFT)
        self.assertIsNotNone(m, "texto assinado não achado no Swift")
        py = updatesig.message("V", "s", 7).decode()
        sw = (m.group(1).replace("\\n", "\n").replace("\\(version)", "V")
              .replace("\\(sha.lowercased())", "s").replace("\\(size)", "7"))
        self.assertEqual(py, sw)

    def test_app_confere_antes_de_oferecer(self):
        body = SWIFT[SWIFT.index("func checkUpdates("):SWIFT.index("func offerUpdate(")]
        self.assertLess(body.index("signatureProblem("), body.index("self.offerUpdate("))

    def test_pacote_baixado_confere_tamanho_e_sha(self):
        body = SWIFT[SWIFT.index("func offerUpdate("):SWIFT.index("func osChangedSinceBuild(")]
        guard = body.index("guard n > 0, n <= MAX_UPDATE_BYTES")
        self.assertIn("sha256Hex(z) == sha.lowercased()", body[guard:guard + 200])
        self.assertLess(guard, body.index("ditto"))

    def test_com_chave_exige_tamanho_e_assinatura(self):
        body = SWIFT[SWIFT.index("func signatureProblem("):SWIFT.index("func sha256Hex(")]
        self.assertIn("if keys.isEmpty { return nil }", body)
        self.assertLess(body.index("keys.isEmpty"), body.index('j["sig"]'))

    def test_so_assina_link_do_repositorio(self):
        self.assertIn('u.host == "raw.githubusercontent.com"', SWIFT)
        self.assertIn('hasPrefix("/juniorsousa6/Logger/")', SWIFT)

    def test_instalador_e_plist_levam_a_chave(self):
        inst = (ROOT / "Instalar Logger.command").read_text(encoding="utf-8")
        self.assertIn("__UPDATE_KEY__", (ROOT / "mac" / "Info.plist").read_text())
        self.assertIn('s|__UPDATE_KEY__|$KEY|g', inst)
        self.assertIn("mac/UPDATE_KEY", inst)
        self.assertIn('"mac/UPDATE_KEY"', (ROOT / "tools" / "release.py").read_text())


@unittest.skipUnless(HAVE, "pacote cryptography ausente")
class Release(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for f in ["app.py", "cardlogger.py", "media.py", "posrender.py", "fotos.py", "hds.py", "cards.json",
                  "ui.html", "VERSION", "Instalar Logger.command", "Abrir Logger.command", "LEIA-ME.md",
                  "mac/LoggerApp.swift", "mac/Info.plist", "mac/UPDATE_URL", "mac/icon.png",
                  "tools/release.py", "tools/updatesig.py"]:
            (self.tmp / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / f, self.tmp / f)
        self.k, pub = keypair()
        (self.tmp / "mac" / "UPDATE_KEY").write_text(pub + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_rel(self, *a):
        return subprocess.run([sys.executable, str(self.tmp / "tools" / "release.py"), *a],
                              capture_output=True, text=True)

    def test_fluxo_completo(self):
        self.assertEqual(self.run_rel("notas").returncode, 0)
        j = json.loads((self.tmp / "latest.json").read_text())
        z = self.tmp / "dist" / ("Logger-%s.zip" % j["version"])
        self.assertEqual(j["size"], z.stat().st_size)
        self.assertNotIn("sig", j)
        r = self.run_rel("--sig", sign(self.k, j))
        self.assertEqual(r.returncode, 0, r.stderr)
        j2 = json.loads((self.tmp / "latest.json").read_text())
        self.assertTrue(updatesig.verify(j2, (self.tmp / "mac" / "UPDATE_KEY").read_text().strip()))

    def test_assinatura_errada_nao_grava(self):
        self.run_rel("notas")
        antes = (self.tmp / "latest.json").read_text()
        outra, _ = keypair()
        r = self.run_rel("--sig", sign(outra, json.loads(antes)))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.tmp / "latest.json").read_text(), antes)


if __name__ == "__main__":
    unittest.main()
