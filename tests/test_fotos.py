"""Limpeza das fotos (fotos.py): catalogo do Lightroom simulado com o esquema real (2026.lrcat, 15/11/2026)."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import fotos


def make_catalog(path, roots):
    """roots: {pasta raiz: [(nome.ext, estrelas), ...]}"""
    db = sqlite3.connect(path)
    db.executescript("""
      create table AgLibraryRootFolder (id_local integer primary key, absolutePath, name);
      create table AgLibraryFolder (id_local integer primary key, rootFolder, pathFromRoot);
      create table AgLibraryFile (id_local integer primary key, folder, baseName, extension, sidecarExtensions);
      create table Adobe_images (id_local integer primary key, rootFile, rating, pick);""")
    for r, (root, files) in enumerate(roots.items(), 1):
        db.execute("insert into AgLibraryRootFolder values (?,?,?)", (r, root.rstrip("/") + "/", os.path.basename(root)))
        db.execute("insert into AgLibraryFolder values (?,?,?)", (r, r, ""))
        for name, stars in files:
            base, ext = name.rsplit(".", 1)
            fid = db.execute("insert into AgLibraryFile (folder, baseName, extension) values (?,?,?)", (r, base, ext)).lastrowid
            db.execute("insert into Adobe_images (rootFile, rating, pick) values (?,?,0)", (fid, stars or None))
    db.commit()
    db.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.festa = Path(self.tmp.name) / "Volumes" / "Extreme Pro" / "2026_11_14 - Eco Club"
        self.fotos = self.festa / "Fotos"
        self.sel = self.festa / "Select Fotos"
        self.fotos.mkdir(parents=True)
        (self.festa / "Ronin").mkdir()
        (self.festa / "Ronin" / "JRS0001.MP4").write_text("video")
        self.cat = Path(self.tmp.name) / "2026.lrcat"

    def tearDown(self):
        self.tmp.cleanup()

    def setup(self, n=10, stars=(2, 5), moved=True):
        fotos_files, sel_files = [], []
        for i in range(n):
            name = f"JRS{3000 + i}.ARW"
            st = 1 if i in stars else 0
            if st and moved:
                self.sel.mkdir(exist_ok=True)
                (self.sel / name).write_text("raw")
                sel_files.append((name, st))
            else:
                (self.fotos / name).write_text("raw" * 10)
                fotos_files.append((name, st))
        roots = {str(self.fotos): fotos_files}
        if sel_files:
            roots[str(self.sel)] = sel_files
        make_catalog(self.cat, roots)

    def plan(self):
        with fotos.Catalog(self.cat) as c:
            return fotos.plan(self.festa, c.images_under(self.festa))


class Plano(Base):
    def test_pronto_quando_select_salvo(self):
        self.setup()
        p = self.plan()
        self.assertTrue(p["ready"], p["reasons"])
        self.assertEqual(len(p["starred"]), 2)
        self.assertEqual([t["dir"] for t in p["trash"]], [str(self.fotos)])
        self.assertEqual(p["trash_files"], 8)
        self.assertEqual(p["select_files"], 2)

    def test_foto_com_estrela_fora_do_select_vai_ser_copiada(self):
        self.setup(moved=False)
        p = self.plan()
        self.assertTrue(p["ready"], p["reasons"])
        self.assertEqual(p["missing"], 0)
        self.assertEqual(sorted(os.path.basename(x) for x in p["to_copy"]), ["JRS3002.ARW", "JRS3005.ARW"])
        self.assertFalse(p["select_exists"])

    def test_trava_se_foto_com_estrela_sumiu(self):
        self.setup(moved=False)
        os.remove(self.fotos / "JRS3002.ARW")
        p = self.plan()
        self.assertFalse(p["ready"])
        self.assertEqual(p["missing"], 1)
        self.assertIn("não estão no disco", " ".join(p["reasons"]))

    def test_arrastou_pelo_finder_tambem_vale(self):
        self.setup(moved=False)
        self.sel.mkdir()
        for n in ("JRS3002.ARW", "JRS3005.ARW"):
            (self.sel / n).write_text("raw" * 10)
        p = self.plan()
        self.assertTrue(p["ready"])
        self.assertEqual(p["to_copy"], [])

    def test_mesmo_nome_outro_conteudo_nao_conta_como_salvo(self):
        self.setup(moved=False)
        self.sel.mkdir()
        (self.sel / "JRS3002.ARW").write_text("outra foto")
        self.assertEqual(len(self.plan()["to_copy"]), 2)

    def test_select_apagado_do_disco(self):
        self.setup()
        os.remove(self.sel / "JRS3002.ARW")
        p = self.plan()
        self.assertFalse(p["ready"])

    def test_sem_estrela_nao_apaga(self):
        self.setup(stars=())
        p = self.plan()
        self.assertFalse(p["ready"])

    def test_arquivo_que_nao_e_foto_aparece(self):
        self.setup()
        (self.fotos / "clip.MP4").write_text("v")
        p = self.plan()
        self.assertEqual(p["trash"][0]["not_photos"], ["clip.MP4"])

    def test_nunca_pasta_de_cima(self):
        # Boma/festa/Fotos: apaga Fotos, nunca "festa" (que tem videos)
        sub = self.festa / "festa" / "Fotos"
        sub.mkdir(parents=True)
        (self.festa / "festa" / "video.MP4").write_text("v")
        (sub / "A.ARW").write_text("r")
        self.sel.mkdir()
        (self.sel / "B.ARW").write_text("r")
        make_catalog(self.cat, {str(sub): [("A.ARW", 0)], str(self.sel): [("B.ARW", 1)]})
        p = self.plan()
        self.assertEqual([t["dir"] for t in p["trash"]], [str(sub)])

    def test_subpasta_da_camera_sobe_ate_fotos(self):
        sub = self.fotos / "101MSDCF"
        sub.mkdir()
        (sub / "A.ARW").write_text("r")
        self.sel.mkdir()
        (self.sel / "B.ARW").write_text("r")
        make_catalog(self.cat, {str(sub): [("A.ARW", 0)], str(self.sel): [("B.ARW", 1)]})
        self.assertEqual([t["dir"] for t in self.plan()["trash"]], [str(self.fotos)])

    def test_festas_recentes(self):
        self.setup()
        with fotos.Catalog(self.cat) as c:
            f = c.festas()
        self.assertEqual(f[0]["name"], "2026_11_14 - Eco Club")
        self.assertEqual((f[0]["total"], f[0]["stars"]), (10, 2))

    def test_catalogo_com_wal(self):
        self.setup()
        db = sqlite3.connect(self.cat)
        db.execute("pragma journal_mode=wal")
        db.execute("update Adobe_images set rating=3 where id_local=1")
        db.commit()  # fica no -wal enquanto a conexao (o "Lightroom") esta aberta
        try:
            with fotos.Catalog(self.cat) as c:
                self.assertIn(3, [i["rating"] for i in c.images_under(self.festa)])
        finally:
            db.close()


class Copia(Base):
    def test_copia_confere_e_depois_esta_pronto(self):
        self.setup(moved=False)
        (self.fotos / "JRS3002.xmp").write_text("<x:xmpmeta/>")
        p = self.plan()
        res = fotos.copy_to_select(self.festa, p["to_copy"])
        self.assertTrue(all(r["ok"] for r in res), res)
        self.assertEqual((self.sel / "JRS3002.ARW").read_text(), "raw" * 10)
        self.assertTrue((self.sel / "JRS3002.xmp").exists())
        self.assertFalse([f for f in os.listdir(self.sel) if f.endswith(".parcial")])
        p2 = self.plan()
        self.assertEqual(p2["to_copy"], [])
        self.assertEqual(p2["select_files"], 3)
        self.assertTrue(p2["ready"])
        # de novo: nao duplica
        res = fotos.copy_to_select(self.festa, p["to_copy"])
        self.assertEqual(sorted(os.listdir(self.sel)), ["JRS3002.ARW", "JRS3002.xmp", "JRS3005.ARW"])

    def test_nome_repetido_nunca_sobrescreve(self):
        self.setup(moved=False)
        self.sel.mkdir()
        (self.sel / "JRS3002.ARW").write_text("outra foto")
        fotos.copy_to_select(self.festa, self.plan()["to_copy"])
        self.assertEqual((self.sel / "JRS3002.ARW").read_text(), "outra foto")
        self.assertEqual((self.sel / "JRS3002 (2).ARW").read_text(), "raw" * 10)
        self.assertEqual(self.plan()["to_copy"], [])

    def test_duas_subpastas_com_o_mesmo_nome(self):
        a, b = self.fotos / "100MSDCF", self.fotos / "101MSDCF"
        a.mkdir(); b.mkdir()
        (a / "DSC0001.ARW").write_text("primeira")
        (b / "DSC0001.ARW").write_text("segunda!!")
        make_catalog(self.cat, {str(a): [("DSC0001.ARW", 1)], str(b): [("DSC0001.ARW", 2)]})
        fotos.copy_to_select(self.festa, self.plan()["to_copy"])
        self.assertEqual(sorted(p.read_text() for p in self.sel.iterdir()), ["primeira", "segunda!!"])
        p = self.plan()
        self.assertEqual(p["to_copy"], [])
        self.assertTrue(p["ready"])

    def test_nao_copia_de_fora_da_festa(self):
        self.setup(moved=False)
        fora = Path(self.tmp.name) / "fora.ARW"
        fora.write_text("x")
        self.assertFalse(fotos.copy_to_select(self.festa, [str(fora)])[0]["ok"])


class Seguranca(Base):
    def test_alvos_proibidos(self):
        self.setup()
        with self.assertRaises(ValueError):
            fotos.check_trash_target(self.festa, self.festa)
        with self.assertRaises(ValueError):
            fotos.check_trash_target(self.festa, self.sel)
        with self.assertRaises(ValueError):
            fotos.check_trash_target(self.festa, self.festa.parent)
        self.assertEqual(fotos.check_trash_target(self.festa, self.fotos), os.path.realpath(self.fotos))

    def test_lixeira_pelo_finder(self):
        calls = []

        class R:
            returncode = 0
            stderr = ""

        def runner(cmd, **kw):
            calls.append(cmd)
            return R()
        res = fotos.to_trash([str(self.fotos)], runner=runner)
        self.assertIn("Finder", calls[0][2])
        self.assertFalse(res[0]["ok"])  # a pasta continua la (o falso Finder nao apagou): nao diz que foi


if __name__ == "__main__":
    unittest.main()
