"""
Teste de mutacao rapido: planta bugs de proposito nas regras criticas e confere se
a suite pega cada um. Um bug que "sobrevive" = falta teste.
Uso: python3 tests/mutation.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUTANTS = [
    ("cardlogger.py", "hash_file(t, nocache=True) != src_hash", "hash_file(t, nocache=True) != src_hash and False",
     "conferencia de checksum desligada"),
    ("cardlogger.py", "        if key in seen:\n", "        if False and key in seen:\n", "ledger ignorado (copia de novo)"),
    ("cardlogger.py", "old > 0 and missing == 0 and not ignored_media", "old > 0 and not ignored_media",
     "pode formatar mesmo com arquivo sumido"),
    ("cardlogger.py", "while cand.exists() or str(cand) in reserved", "while str(cand) in reserved",
     "sobrescreve arquivo existente"),
    ("cardlogger.py", "while not ended and q.get() is not None", "while q.get() is not None", "volta o travamento"),
    ("cardlogger.py", "if errors or read_error:", "if errors:", "sobra .partial quando o cartao sai"),
    ("app.py", "return near[0] if len(near) == 1 else None", "return near[0] if near else None",
     "nao pergunta quando ha duas pastas parecidas"),
    ("app.py", 'mem = None if r.get("lock") else remembered.get(label)', "mem = remembered.get(label)",
     "lembranca passa por cima da regra JRS"),
    ("app.py", "if vol.name in work or cl.looks_like_work_drive(vol):", "if False:", "HD de trabalho aparece como cartao"),
    ("app.py", '.lstrip(".")', "", "pasta com nome '..' escapa"),
]


def main():
    survived = []
    for i, (f, old, new, desc) in enumerate(MUTANTS, 1):
        d = Path(tempfile.mkdtemp(prefix="mut-"))
        shutil.copytree(ROOT, d / "L", ignore=shutil.ignore_patterns("dist", "__pycache__", ".git"))
        src = (d / "L" / f).read_text(encoding="utf-8")
        assert src.count(old) == 1, f"mutante {i} nao achou o trecho: {old!r}"
        (d / "L" / f).write_text(src.replace(old, new), encoding="utf-8")
        r = subprocess.run([sys.executable, "-m", "unittest", "tests.test_engine", "tests.test_api"], cwd=d / "L",
                           capture_output=True, text=True, timeout=300)
        killed = r.returncode != 0
        print(f"{'morto ' if killed else 'VIVO  '} {i:2}. {desc}")
        if not killed:
            survived.append(desc)
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{len(MUTANTS) - len(survived)}/{len(MUTANTS)} bugs plantados foram pegos pelos testes")
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()
