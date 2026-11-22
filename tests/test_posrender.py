"""Renomear drops depois do render (posrender.py), com os dados reais do projeto Eco Club (15/11/2026)."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import posrender as p

FIX = Path(__file__).parent / "fixtures" / "eco_club_resolve.json"


def eco():
    return json.loads(FIX.read_text(encoding="utf-8"))


def render_files(job):
    return [f"V{c['track']}-{i:04d}_{c['name']}.mp4" for i, c in enumerate(job["clips"], 1)]


class Nomes(unittest.TestCase):
    def test_festa(self):
        self.assertEqual(p.party_name("/Users/j/Dropbox/VIOT/2026_11_14 - Eco Club/9x16"), "Eco Club")
        self.assertEqual(p.party_name("/x/2026_08_22 - Tônica"), "Tônica")
        self.assertEqual(p.party_name("/x/sem data/9x16"), "")

    def test_viot(self):
        for m in ["♪ Viot - Cardio (Extended Mix)", "♪ Vintage Culture, Viot - Anthem (Original Mix)",
                  "♪ Jorge Ben - Chove Chuva (Viot & Let BR Remix)", "♪ Noir feat. Haze - Around (Viot, Gabe & Bhaskar Remix)",
                  "♪ Viot & Vinter - Good Life (Inner City New Mix)", "♪ Adam Ten, Viot - Put Id Down 30.8"]:
            self.assertTrue(p.is_viot(m), m)
        for m in ["♪ Fer BR - Mousy (Original Mix)", "♪ Carloh - Back To House (Original Mix)", "♪ Viotti - X", "DROP 3"]:
            self.assertFalse(p.is_viot(m), m)

    def test_titulo(self):
        casos = {"Cardio (Extended Mix)": "Cardio", "Put Id Down 30.8": "Put It Down",
                 "Chove Chuva (Viot & Let BR Remix)": "Chove Chuva", "Blitz (Você Não Soube Me Amar) (Extended Mix)": "Blitz",
                 "No Brakes (Final mix)": "No Brakes", "Ocean feat. Anduze (Original Mix)": "Ocean",
                 "Como Eu Te Quero (Remix)": "Como Eu Te Quero", "Good Life (Inner City New Mix)": "Good Life",
                 "A/B: C": "A B C"}
        for raw, want in casos.items():
            self.assertEqual(p.clean_title(raw), want, raw)


class Plano(unittest.TestCase):
    def setUp(self):
        self.d = eco()
        self.j = self.d["jobs"][0]

    def plan(self, files=None, fixes=None):
        files = render_files(self.j) if files is None else files
        return p.plan_job(self.j, files, fixes)

    def test_eco_club(self):
        rows = {r["old"]: r for r in self.plan()["rows"]}
        self.assertEqual(rows["V1-0001_Drop 1.mp4"]["new"], "1_Eco Club.mp4")
        self.assertEqual(rows["V1-0011_Drop 11.mp4"]["new"], "11_Eco Club Cardio.mp4")
        self.assertEqual(rows["V1-0021_Drop 21.mp4"]["new"], "21_Eco Club Blitz.mp4")
        self.assertIn("Original Mix", rows["V1-0021_Drop 21.mp4"]["song"])   # marcador roxo, nao o "Intro Mix" colado
        self.assertEqual(rows["V1-0040_Drop 40.mp4"]["new"], "40_Eco Club Put It Down.mp4")
        self.assertEqual(rows["V1-0039_Drop 39.mp4"]["new"], "39_Eco Club.mp4")
        self.assertEqual(rows["V2-0052_Drop 52.mp4"]["new"], "52_Eco Club Como Eu Te Quero.mp4")  # clipe na V2 tambem conta
        self.assertTrue(all(r["status"] == "ok" for r in rows.values()))
        self.assertEqual(sorted(int(r["new"].split("_")[0]) for r in rows.values()), list(range(1, 53)))

    def test_render_com_nome_proprio(self):
        """Lagun (2026-11-21): render com Custom name, todo arquivo vira 'V1-0001_9x16 · Drops'.
        O numero do V1- diz qual clipe e."""
        files = [f"V{c['track']}-{i:04d}_9x16 · Drops.mp4" for i, c in enumerate(self.j["clips"], 1)]
        rows = {r["old"]: r for r in self.plan(files)["rows"]}
        self.assertEqual(rows["V1-0001_9x16 · Drops.mp4"]["new"], "1_Eco Club.mp4")
        self.assertEqual(rows["V1-0011_9x16 · Drops.mp4"]["new"], "11_Eco Club Cardio.mp4")
        self.assertEqual(rows["V2-0052_9x16 · Drops.mp4"]["new"], "52_Eco Club Como Eu Te Quero.mp4")
        self.assertTrue(all(r["status"] == "ok" for r in rows.values()))
        # numero que nao existe ou trilha errada: nao chuta
        rows = {r["old"]: r for r in self.plan(["V1-0999_9x16 · Drops.mp4", "V2-0001_9x16 · Drops.mp4"])["rows"]}
        self.assertTrue(all(r["status"] == "clipe não achado na timeline" for r in rows.values()))

    def test_musica_so_vale_no_trecho_dela(self):
        # Eco Club 15/11: a Cardio tem 3 drops (10-12); 13-16 sao de uma musica que o Ghost nao reconheceu
        rows = {r["old"]: r for r in self.plan()["rows"]}
        for n in (10, 11, 12):
            self.assertEqual(rows[f"V1-{n:04d}_Drop {n}.mp4"]["new"], f"{n}_Eco Club Cardio.mp4")
        for n in (13, 14, 15, 16):
            self.assertEqual(rows[f"V1-{n:04d}_Drop {n}.mp4"]["new"], f"{n}_Eco Club.mp4")
        for n in (17, 18, 19):
            self.assertEqual(rows[f"V1-{n:04d}_Drop {n}.mp4"]["new"], f"{n}_Eco Club Flex.mp4")
        self.assertEqual(rows["V1-0020_Drop 20.mp4"]["new"], "20_Eco Club.mp4")
        self.assertEqual(rows["V1-0009_Drop 9.mp4"]["song"], "")   # Mousy ja tinha acabado

    def test_prime_mgf_flex(self):
        """Prime MGF 21/11: drops 14-16 sao a Flex. 14: o Ghost so marca a Flex 310 quadros depois do drop,
        ainda dentro do clipe. 15: a Blitz (Sand) entra por cima da Flex (roxa). 16: a Blitz ja acabou,
        mas a Flex continua."""
        j = json.loads((FIX.parent / "prime_mgf_resolve.json").read_text(encoding="utf-8"))["jobs"][0]
        rows = p.plan_job(j, [f"V1-{i:04d}_Drop {i}.mp4" for i in range(1, 37)])["rows"]
        new = {int(r["old"].split("-")[1][:4]): r["new"] for r in rows}
        for n in (14, 15, 16):
            self.assertEqual(new[n], f"{n}_Prim MGF Flex.mp4")
        for n in (7, 8, 9):   # mesmo caso da 14: Cardio entra logo depois do drop 7
            self.assertEqual(new[n], f"{n}_Prim MGF Cardio.mp4")
        for n in (4, 5, 6, 10, 11, 12, 13):   # musica que o Ghost nao reconheceu: sem titulo
            self.assertEqual(new[n], f"{n}_Prim MGF.mp4")
        self.assertEqual(new[17], "17_Prim MGF.mp4")          # Kickback nao e do VIOT
        self.assertEqual(new[36], "36_Prim MGF Ratata.mp4")
        self.assertTrue(all(r["status"] == "ok" for r in rows))

    def test_marcador_sem_duracao_vale_ate_o_proximo(self):
        ms = [{"frame": 100, "name": "♪ Viot - Cardio (Extended Mix)", "color": "Purple"}]
        self.assertIsNotNone(p.song_at(ms, 99999))
        ms[0]["duration"] = 50
        self.assertIsNotNone(p.song_at(ms, 149))
        self.assertIsNone(p.song_at(ms, 150))

    def test_numero_sem_buraco_quando_drop_apagado(self):
        self.j["clips"] = [c for c in self.j["clips"] if c["name"] not in ("Drop 2", "Drop 3")]
        rows = {r["old"]: r for r in self.plan()["rows"]}
        self.assertEqual(rows["V1-0002_Drop 4.mp4"]["new"], "2_Eco Club.mp4")

    def test_fora_do_in_out_nao_conta(self):
        self.j["mark_in"] = 95526
        rows = {r["old"]: r for r in self.plan()["rows"]}
        self.assertEqual(rows["V1-0002_Drop 2.mp4"]["new"], "1_Eco Club.mp4")
        self.assertEqual(rows["V1-0001_Drop 1.mp4"]["status"], "clipe não achado na timeline")

    def test_outros_arquivos_nao_mexe(self):
        files = render_files(self.j)[:3] + ["7_Eco Club.mp4", "capa.jpg", "Video final.mp4"]
        rows = {r["old"]: r for r in self.plan(files)["rows"]}
        self.assertEqual(rows["7_Eco Club.mp4"]["status"], "já renomeado")
        self.assertEqual(rows["Video final.mp4"]["status"], "não é um drop deste render")
        self.assertNotIn("capa.jpg", rows)

    def test_nao_sobrescreve_nome_existente(self):
        files = render_files(self.j)[:2] + ["1_Eco Club.mp4"]
        rows = {r["old"]: r for r in self.plan(files)["rows"]}
        self.assertEqual(rows["V1-0001_Drop 1.mp4"]["status"], "já existe um arquivo com esse nome")
        self.assertEqual(rows["V1-0001_Drop 1.mp4"]["new"], "")

    def test_correcao_lembrada(self):
        rows = {r["old"]: r for r in self.plan(fixes={"Cardio": "Cardio VIP"})["rows"]}
        self.assertEqual(rows["V1-0011_Drop 11.mp4"]["new"], "11_Eco Club Cardio VIP.mp4")

    def test_render_em_andamento_fica_de_fora(self):
        self.d["jobs"][0]["status"] = "Rendering"
        plan = p.build_plan(self.d, listdir=lambda d: render_files(self.j))
        self.assertEqual(plan["jobs"], [])
        self.assertEqual([x["timeline"] for x in plan["pending"]], ["9x16 · Drops", "4x5 · Drops"])

    def test_pasta_sumiu(self):
        def boom(d):
            raise FileNotFoundError(d)
        self.d["jobs"][1]["status"] = "Complete"
        plan = p.build_plan(self.d, listdir=boom)
        self.assertIn("pasta do render", plan["jobs"][0]["error"])


class Aplicar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "2026_11_14 - Eco Club" / "9x16"
        self.dir.mkdir(parents=True)
        self.undo = Path(self.tmp.name) / "undo"

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, *names):
        for n in names:
            (self.dir / n).write_text(n)

    def test_renomeia_e_desfaz(self):
        self.touch("V1-0001_Drop 1.mp4", "V1-0002_Drop 2.mp4")
        r = p.apply(self.dir, [{"old": "V1-0001_Drop 1.mp4", "new": "1_Eco Club.mp4"},
                               {"old": "V1-0002_Drop 2.mp4", "new": "2_Eco Club Cardio.mp4"}], undo_dir=self.undo)
        self.assertEqual(len(r["done"]), 2)
        self.assertEqual(sorted(os.listdir(self.dir)), ["1_Eco Club.mp4", "2_Eco Club Cardio.mp4"])
        self.assertEqual((self.dir / "2_Eco Club Cardio.mp4").read_text(), "V1-0002_Drop 2.mp4")
        u = p.undo(r["undo"])
        self.assertEqual(u["errors"], [])
        self.assertEqual(sorted(os.listdir(self.dir)), ["V1-0001_Drop 1.mp4", "V1-0002_Drop 2.mp4"])

    def test_nunca_sobrescreve(self):
        self.touch("V1-0001_Drop 1.mp4", "1_Eco Club.mp4")
        r = p.apply(self.dir, [{"old": "V1-0001_Drop 1.mp4", "new": "1_Eco Club.mp4"}], undo_dir=self.undo)
        self.assertEqual(r["done"], [])
        self.assertEqual((self.dir / "1_Eco Club.mp4").read_text(), "1_Eco Club.mp4")
        self.assertTrue((self.dir / "V1-0001_Drop 1.mp4").exists())

    def test_troca_de_nomes_entre_si(self):
        self.touch("1_x.mp4", "2_x.mp4")
        r = p.apply(self.dir, [{"old": "1_x.mp4", "new": "2_x.mp4"}, {"old": "2_x.mp4", "new": "1_x.mp4"}], undo_dir=self.undo)
        self.assertEqual(len(r["done"]), 2)
        self.assertEqual((self.dir / "1_x.mp4").read_text(), "2_x.mp4")

    def test_nome_com_barra_recusado(self):
        self.touch("a.mp4")
        r = p.apply(self.dir, [{"old": "a.mp4", "new": "../fora.mp4"}], undo_dir=self.undo)
        self.assertEqual(r["done"], [])
        self.assertTrue((self.dir / "a.mp4").exists())

    def test_arquivo_sumiu(self):
        r = p.apply(self.dir, [{"old": "nao.mp4", "new": "1_x.mp4"}], undo_dir=self.undo)
        self.assertEqual(r["errors"][0]["error"], "arquivo não está mais na pasta")



class Pacote(unittest.TestCase):
    def test_modulos_vao_no_instalador_e_no_pacote(self):
        root = Path(__file__).parent.parent
        inst = (root / "Instalar Logger.command").read_text(encoding="utf-8")
        rel = (root / "tools" / "release.py").read_text(encoding="utf-8")
        for py in sorted(x.name for x in root.glob("*.py")):
            self.assertIn(py, inst, f"{py} falta no Instalar Logger.command")
            self.assertIn(f'"{py}"', rel, f"{py} falta no tools/release.py")


if __name__ == "__main__":
    unittest.main()
