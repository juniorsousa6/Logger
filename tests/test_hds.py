"""
Meus HDs: mapa offline dos HDs.
Regras que nao podem quebrar:
  - so le o HD (nenhum arquivo criado, alterado ou com data mexida);
  - reescaneio acha exatamente o que entrou e o que saiu;
  - HD que sai no meio da leitura nao apaga o mapa anterior;
  - HD novo pergunta; HD conhecido se atualiza sozinho; cartao nunca entra.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="logger-hds-"))
os.environ["CARDLOGGER_HOME"] = str(TMP / "home")
os.environ["CARDLOGGER_VOLUMES"] = str(TMP / "V")
sys.path.insert(0, str(ROOT))
import cardlogger as cl  # noqa: E402
import hds  # noqa: E402

cl.STATE_DIR = TMP / "home"
cl.VOLUMES_DIR = TMP / "V"


def snapshot(root):
    """Tudo que existe no HD, com tamanho e datas (inclusive ocultos)."""
    out = {}
    for dp, dns, fns in os.walk(root):
        for n in dns + fns:
            p = Path(dp) / n
            st = p.lstat()
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns, st.st_mode)
    st = Path(root).stat()
    out["."] = (st.st_mtime_ns, st.st_mode)
    return out


def touch(p, data=b"x"):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=TMP))
        hds.DB_PATH = self.dir / "hds.sqlite"
        hds.IGNORED_PATH = self.dir / "ign.json"
        self.V = self.dir / "V"
        cl.VOLUMES_DIR = self.V
        self.hd = self.V / "VIOT - 04"
        job = self.hd / "2026_11_14 - Eco Club"
        for i in range(1, 4):
            touch(job / "Ronin" / f"JRS{i:04d}.MP4", b"r" * (100 * i))
        touch(job / "Áudio" / "ZOOM0001.WAV", b"a" * 50)
        touch(self.hd / "Catalogo.lrdata" / "previews" / "p1.jpg", b"p")
        touch(self.hd / ".Spotlight-V100" / "store.db", b"s")
        touch(self.hd / "solto.txt", b"oi")


class Scan(Base):
    def test_primeiro_mapa(self):
        r = hds.scan(self.hd)
        self.assertTrue(r["first"])
        self.assertEqual(r["files"], 6)  # 3 Ronin + 1 WAV + pacote .lrdata + solto.txt (Spotlight fora)
        top = {i["name"]: i for i in hds.ls(r["id"], "")}
        self.assertEqual(set(top), {"2026_11_14 - Eco Club", "Catalogo.lrdata", "solto.txt"})
        self.assertEqual(top["Catalogo.lrdata"]["kind"], "pkg")
        self.assertEqual(hds.ls(r["id"], "Catalogo.lrdata"), [])  # nao entra no pacote
        self.assertEqual(top["2026_11_14 - Eco Club"]["size"], 100 + 200 + 300 + 50)
        self.assertEqual(top["2026_11_14 - Eco Club"]["files"], 4)

    def test_so_leitura(self):
        before = snapshot(self.hd)
        hds.scan(self.hd)
        touch(self.hd / "novo.mov")
        before = snapshot(self.hd)
        hds.scan(self.hd)
        self.assertEqual(snapshot(self.hd), before, "o escaneio mexeu no HD")

    def test_reescaneio_o_que_entrou_e_saiu(self):
        r = hds.scan(self.hd)
        shutil.rmtree(self.hd / "2026_11_14 - Eco Club" / "Áudio")
        os.remove(self.hd / "2026_11_14 - Eco Club" / "Ronin" / "JRS0002.MP4")
        touch(self.hd / "2026_11_14 - Eco Club" / "Drone" / "DJI_0001.MP4", b"d" * 10)
        touch(self.hd / "2026_11_14 - Eco Club" / "Drone" / "DJI_0001.SRT", b"t")
        r2 = hds.scan(self.hd)
        self.assertFalse(r2["first"])
        self.assertEqual((r2["added"], r2["removed"]), (2, 2))
        d = hds.drive_by(id_=r["id"])
        self.assertIn("2026_11_14 - Eco Club/Áudio/", d["last_changes"]["removed"])
        self.assertIn("2026_11_14 - Eco Club/Ronin/JRS0002.MP4", d["last_changes"]["removed"])
        self.assertIn("2026_11_14 - Eco Club/Drone/DJI_0001.MP4", d["last_changes"]["added"])
        self.assertEqual(hds.ls(r["id"], "2026_11_14 - Eco Club/Áudio"), [])
        job = {i["name"]: i for i in hds.ls(r["id"], "")}["2026_11_14 - Eco Club"]
        self.assertEqual(job["files"], 4)
        self.assertEqual(job["size"], 100 + 300 + 10 + 1)

    def test_arquivo_trocado_com_mesmo_nome(self):
        r = hds.scan(self.hd)
        f = self.hd / "solto.txt"
        f.write_bytes(b"x" * 999)
        os.utime(f.parent, (time.time() + 5, time.time() + 5))  # a pasta mudou
        hds.scan(self.hd)
        self.assertEqual({i["name"]: i for i in hds.ls(r["id"], "")}["solto.txt"]["size"], 999)

    def test_pasta_vira_arquivo(self):
        r = hds.scan(self.hd)
        shutil.rmtree(self.hd / "2026_11_14 - Eco Club")
        touch(self.hd / "2026_11_14 - Eco Club", b"agora sou arquivo")
        r2 = hds.scan(self.hd)
        self.assertEqual(r2["removed"], 4)
        top = {i["name"]: i for i in hds.ls(r["id"], "")}
        self.assertEqual(top["2026_11_14 - Eco Club"]["kind"], "file")
        n = hds.db().execute("SELECT COUNT(*) FROM entries WHERE drive=?", (r["id"],)).fetchone()[0]
        self.assertEqual(n, 3)

    def test_hd_some_no_meio_mantem_mapa(self):
        r = hds.scan(self.hd)
        touch(self.hd / "novo1.mov")
        ev = threading.Event()
        ev.set()  # igual a ejetar no meio: a leitura para
        with self.assertRaises(hds.Aborted):
            hds.scan(self.hd, cancel=ev)
        self.assertEqual(hds.drive_by(id_=r["id"])["files"], 6)
        self.assertNotIn("novo1.mov", [i["name"] for i in hds.ls(r["id"], "")])
        # HD arrancado: a pasta some no meio do caminho
        real_scandir = os.scandir
        moved = self.dir / "fora"

        def yank(p):
            if "Ronin" in str(p) and self.hd.exists():
                self.hd.rename(moved)
            return real_scandir(p)
        hds.os.scandir = yank
        try:
            with self.assertRaises(hds.Aborted):
                hds.scan(self.hd)
        finally:
            hds.os.scandir = real_scandir
        self.assertEqual(hds.drive_by(id_=r["id"])["files"], 6, "mapa apagado por um HD arrancado")
        self.assertTrue(hds.ls(r["id"], "2026_11_14 - Eco Club/Ronin"))

    def test_nomes_com_caracteres_especiais(self):
        touch(self.hd / "100% final_v2" / "a_b%c.mov")
        touch(self.hd / "100% final_v20" / "x.mov")
        r = hds.scan(self.hd)
        self.assertEqual([i["name"] for i in hds.ls(r["id"], "100% final_v2")], ["a_b%c.mov"])
        shutil.rmtree(self.hd / "100% final_v2")
        r2 = hds.scan(self.hd)
        self.assertEqual(r2["removed"], 1)
        self.assertEqual([i["name"] for i in hds.ls(r["id"], "100% final_v20")], ["x.mov"])
        self.assertEqual([x["name"] for x in hds.search("_b%")], [])
        touch(self.hd / "c" / "a_b%c.mov")
        hds.scan(self.hd)
        self.assertEqual([x["name"] for x in hds.search("_b%")], ["a_b%c.mov"])
        self.assertEqual(hds.search("b_c"), [])


class Search(Base):
    def test_busca(self):
        hds.scan(self.hd)
        other = self.V / "Extreme Pro"
        touch(other / "2026_09_20 - Casamento Ana" / "Ronin" / "JRS0001.MP4")
        hds.scan(other)
        shutil.move(str(other), str(self.dir / "desligado"))  # desconectado: a busca continua achando
        hits = hds.search("jrs0001")
        self.assertEqual(sorted(h["hd"] for h in hits), ["Extreme Pro", "VIOT - 04"])
        self.assertEqual([h["name"] for h in hds.search("audio")], ["Áudio"])  # sem acento acha com acento
        self.assertEqual([h["path"] for h in hds.search("eco club jrs0003")], ["2026_11_14 - Eco Club/Ronin/JRS0003.MP4"])
        self.assertEqual([h["name"] for h in hds.search("ronin")], ["Ronin", "Ronin"])
        self.assertEqual(hds.search("   "), [])
        self.assertEqual(hds.search("naoexiste"), [])

    def test_esquecer_apaga_so_o_mapa(self):
        before = snapshot(self.hd)
        r = hds.scan(self.hd)
        hds.forget(r["id"])
        self.assertEqual(hds.drives(), [])
        self.assertEqual(hds.search("jrs"), [])
        self.assertEqual(snapshot(self.hd), before)


class Watch(Base):
    def setUp(self):
        super().setUp()
        self.card = self.V / "SD_CARD"
        touch(self.card / "PRIVATE" / "M4ROOT" / "CLIP" / "C0001.MP4")
        self.kind = lambda v: "card" if v.name == "SD_CARD" else "drive"
        self.done = []
        self.w = hds.Watcher(kind_of=self.kind, on_done=lambda k, r: self.done.append(r), interval=0.1)

    def tearDown(self):
        self.w._stop = True

    def wait(self, cond, t=10):
        end = time.time() + t
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def test_hd_novo_pergunta_e_cartao_nao(self):
        self.w.poll()
        st = self.w.status()
        self.assertEqual([p["name"] for p in st["pending"]], ["VIOT - 04"])
        self.assertNotIn("noid-SD_CARD", st["online"])
        self.assertEqual(hds.drives(), [], "mapeou sem perguntar")

    def test_mapear_agora_nao_nunca(self):
        self.w.start()
        self.assertTrue(self.wait(lambda: self.w.status()["pending"]))
        self.w.skip("noid-VIOT - 04")
        self.assertEqual(self.w.status()["pending"], [])
        self.w.poll()
        self.assertEqual(self.w.status()["pending"], [], "'Agora não' perguntou de novo na mesma conexao")
        self.w.map(str(self.hd / "2026_11_14 - Eco Club"))  # qualquer pasta dentro do HD mapeia o HD
        self.assertTrue(self.wait(lambda: self.done))
        self.assertTrue(self.done[0]["ok"] and self.done[0]["first"])
        self.assertEqual([d["name"] for d in hds.drives()], ["VIOT - 04"])

    def test_nunca(self):
        self.w.poll()
        self.w.skip("noid-VIOT - 04", forever=True)
        self.w.seen.clear()  # desconectou e conectou de novo
        self.w.poll()
        self.assertEqual(self.w.status()["pending"], [])
        self.assertIn("noid-VIOT - 04", hds.ignored())

    def test_hd_conhecido_atualiza_sozinho(self):
        hds.scan(self.hd)
        touch(self.hd / "2026_11_14 - Eco Club" / "Fotos" / "DSC0001.ARW")
        self.w.start()
        self.assertTrue(self.wait(lambda: self.done))
        self.assertEqual(self.done[0]["added"], 1)
        self.assertEqual(self.w.status()["pending"], [])
        n = len(self.done)
        time.sleep(0.5)
        self.assertEqual(len(self.done), n, "reescaneou de novo sem reconectar")

    def test_espera_a_copia_de_cartao(self):
        hds.scan(self.hd)
        busy = {"on": True}
        self.w.busy = lambda: busy["on"]
        self.w.start()
        time.sleep(0.8)
        self.assertEqual(self.done, [])
        busy["on"] = False
        self.assertTrue(self.wait(lambda: self.done))

    def test_fora_de_volumes_recusa(self):
        with self.assertRaises(ValueError):
            self.w.map(str(self.dir))


class Api(unittest.TestCase):
    def test_endpoints(self):
        import json
        import subprocess
        import urllib.request
        d = Path(tempfile.mkdtemp(dir=TMP))
        touch(d / "V" / "HD1" / "2026_01_01 - Teste" / "Ronin" / "JRS0001.MP4")
        env = dict(os.environ, CARDLOGGER_HOME=str(d / "home"), CARDLOGGER_VOLUMES=str(d / "V"), LOGGER_PORT="0",
                   LOGGER_NO_BROWSER="1", LOGGER_HD_INTERVAL="0.2", PATH="/nonexistent:" + os.environ["PATH"])
        p = subprocess.Popen([sys.executable, str(ROOT / "app.py")], env=env, stdout=subprocess.PIPE, text=True)
        try:
            url = next(ln.split()[1] for ln in p.stdout if ln.startswith("LOGGER_URL "))

            def call(path, body=None):
                req = urllib.request.Request(url.rstrip("/") + path, method="POST" if body is not None else "GET",
                                             data=json.dumps(body).encode() if body is not None else None,
                                             headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        return r.status, json.loads(r.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())
            for _ in range(50):
                st, r = call("/api/hds")
                if r["pending"]:
                    break
                time.sleep(0.1)
            self.assertEqual([x["name"] for x in r["pending"]], ["HD1"])
            self.assertEqual(call("/api/hds/map", {"path": r["pending"][0]["path"]})[0], 200)
            for _ in range(50):
                st, r = call("/api/hds")
                if r["drives"] and r["drives"][0].get("last_scan"):
                    break
                time.sleep(0.1)
            did = r["drives"][0]["id"]
            self.assertTrue(r["drives"][0]["online"])
            st, ls = call(f"/api/hds/ls?drive={did}&path=2026_01_01%20-%20Teste")
            self.assertEqual([i["name"] for i in ls["items"]], ["Ronin"])
            st, s = call("/api/hds/search?q=jrs0001")
            self.assertEqual(s["results"][0]["path"], "2026_01_01 - Teste/Ronin/JRS0001.MP4")
            self.assertTrue(s["results"][0]["online"])
            self.assertEqual(call("/api/hds/ls?drive=999")[0], 400)
            self.assertEqual(call("/api/hds/reveal", {"drive": did, "path": "../../etc"})[0], 400)
            st, j = call("/api/hds/job", {"drive": did, "path": "2026_01_01 - Teste"})
            self.assertEqual(st, 200)
            self.assertTrue(j["job_dir"].endswith("HD1/2026_01_01 - Teste"))
            # pasta fora do HD (../) nao vira pasta de trabalho
            self.assertEqual(call("/api/hds/job", {"drive": did, "path": "../../.."})[0], 400)
            self.assertEqual(call("/api/hds/job", {"drive": did, "path": ""})[0], 400)
            # a pasta com o mapa dos HDs so e lida pelo proprio usuario
            self.assertEqual(oct((d / "home").stat().st_mode & 0o777), "0o700")
            self.assertEqual(call("/api/hds/rescan", {"drive": did})[0], 200)
            self.assertEqual(call("/api/hds/forget", {"drive": did})[0], 200)
            self.assertEqual(call("/api/hds")[1]["drives"], [])
        finally:
            p.terminate()
            p.wait(5)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
