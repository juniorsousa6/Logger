"""Falhas provocadas na limpeza das fotos (copiar para o Select Fotos e depois a Lixeira).
Regra de ouro: se qualquer copia falhar ou nao bater, NADA vai para a Lixeira."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("CARDLOGGER_HOME", tempfile.mkdtemp(prefix="logger-ff-home-"))
import app  # noqa: E402
import fotos  # noqa: E402
from tests.test_fotos import Base, make_catalog  # noqa: E402


class Falhas(Base):
    def run_app(self, **over):
        p = app.fotos_plan(str(self.festa))
        body = {"festa_dir": str(self.festa), "dirs": [t["dir"] for t in p["trash"]],
                "files": p["trash_files"], "copy": len(p["to_copy"])}
        body.update(over)
        return app.fotos_trash(body)

    def setUp(self):
        super().setUp()
        self.trashed = []
        os.environ["LOGGER_LR_CATALOG"] = str(self.cat)
        self.p1 = mock.patch.object(fotos, "to_trash", side_effect=lambda ps, **k: self.trashed.extend(ps) or
                                    [{"path": x, "ok": True, "msg": ""} for x in ps])
        self.p2 = mock.patch.object(app, "notify", lambda *a, **k: None)
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop()
        os.environ.pop("LOGGER_LR_CATALOG", None)
        super().tearDown()

    def test_fluxo_feliz(self):
        self.setup(moved=False)
        r = self.run_app()
        self.assertEqual(len([c for c in r["copied"] if c["ok"]]), 2)
        self.assertEqual(self.trashed, [os.path.realpath(self.fotos)])

    def test_disco_cheio_no_meio_nao_apaga_nada(self):
        self.setup(moved=False)
        real = os.fsync
        calls = {"n": 0}

        def cheio(fd):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError(28, "No space left on device")
            return real(fd)
        with mock.patch.object(fotos.os, "fsync", cheio):
            r = self.run_app()
        self.assertIn("nada foi para a Lixeira", r["error"])
        self.assertEqual(self.trashed, [])
        self.assertFalse([f for f in os.listdir(self.sel) if f.startswith(".")], "sobrou .parcial")
        self.assertTrue((self.fotos / "JRS3005.ARW").exists())

    def test_copia_corrompida_nao_apaga_nada(self):
        self.setup(moved=False)
        orig = fotos._sha
        seen = {}

        def sha_que_muda(path):
            if path.endswith(".parcial"):
                return "corrompido"
            return orig(path)
        with mock.patch.object(fotos, "_sha", sha_que_muda):
            r = self.run_app()
        self.assertTrue(r.get("error"))
        self.assertEqual(self.trashed, [])
        self.assertFalse(any(self.sel.iterdir()), "deixou copia ruim no Select Fotos")

    def test_previa_velha_recusa(self):
        self.setup(moved=False)
        with self.assertRaises(ValueError):
            self.run_app(copy=0)
        with self.assertRaises(ValueError):
            self.run_app(files=1)
        self.assertEqual(self.trashed, [])
        self.assertFalse(self.sel.exists(), "copiou mesmo com a previa velha")

    def test_foto_com_estrela_sumida_trava_tudo(self):
        self.setup(moved=False)
        os.remove(self.fotos / "JRS3002.ARW")
        with self.assertRaises(ValueError):
            self.run_app()
        self.assertEqual(self.trashed, [])

    def test_sem_permissao_no_select(self):
        if os.geteuid() == 0:
            self.skipTest("root ignora permissao")
        self.setup(moved=False)
        self.sel.mkdir()
        os.chmod(self.sel, 0o500)
        try:
            r = self.run_app()
        finally:
            os.chmod(self.sel, 0o700)
        self.assertTrue(r.get("error"))
        self.assertEqual(self.trashed, [])

    def test_copia_que_diz_ok_mas_nao_copiou(self):
        # a conferencia depois da copia e independente: se a foto nao esta la, nada vai para a Lixeira
        self.setup(moved=False)
        fake = lambda festa, ps, *a: [{"path": x, "ok": True, "msg": ""} for x in ps]
        with mock.patch.object(fotos, "copy_to_select", fake):
            with self.assertRaises(ValueError):
                self.run_app()
        self.assertEqual(self.trashed, [])

    def test_pasta_nova_aparece_durante_a_copia(self):
        self.setup(moved=False)
        real = fotos.copy_to_select

        def copia_e_aparece(festa, ps, *a):
            out = real(festa, ps, *a)
            extra = self.festa / "Fotos 2"
            extra.mkdir()
            (extra / "Z.ARW").write_text("z")
            os.remove(self.cat)
            make_catalog(self.cat, {str(self.fotos): [(f"JRS{3000 + i}.ARW", 1 if i in (2, 5) else 0) for i in range(10)],
                                    str(extra): [("Z.ARW", 0)]})
            return out
        with mock.patch.object(fotos, "copy_to_select", copia_e_aparece):
            with self.assertRaises(ValueError):
                self.run_app()
        self.assertEqual(self.trashed, [])

    def test_clique_duplo_so_roda_uma_vez(self):
        import threading
        import time
        self.setup(moved=False)
        gate = threading.Event()
        real = fotos.copy_to_select

        def lenta(*a, **k):
            gate.wait(5)
            return real(*a, **k)
        p = app.fotos_plan(str(self.festa))
        body = {"festa_dir": str(self.festa), "dirs": [t["dir"] for t in p["trash"]],
                "files": p["trash_files"], "copy": len(p["to_copy"])}
        with mock.patch.object(fotos, "copy_to_select", lenta):
            self.assertTrue(app.fotos_start(body)["started"])
            with self.assertRaises(ValueError):
                app.fotos_start(body)
            gate.set()
            for _ in range(100):
                if not app.fotos_status()["running"]:
                    break
                time.sleep(0.05)
        st = app.fotos_status()
        self.assertFalse(st["running"])
        self.assertNotIn("error", st)
        self.assertEqual(self.trashed, [os.path.realpath(self.fotos)])

    def test_rodar_duas_vezes(self):
        self.setup(moved=False)
        self.run_app()
        # o falso Finder nao apagou: segunda vez nao copia de novo
        p = app.fotos_plan(str(self.festa))
        self.assertEqual(p["to_copy"], [])
        self.assertEqual(sorted(os.listdir(self.sel)), ["JRS3002.ARW", "JRS3005.ARW"])


if __name__ == "__main__":
    unittest.main()
