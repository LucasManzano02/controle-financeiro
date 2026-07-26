# -*- coding: utf-8 -*-
"""
publicar.py — Cifra o app e publica no GitHub Pages.

Uso:
    py publicar.py                      (mensagem de commit automática)
    py publicar.py "minha mensagem"

Passos:
  1. node build/build.js  → gera caixa-v9.html cifrado (AES-256-GCM + 2FA)
  2. git add/commit/push  → GitHub Pages atualiza em ~1 minuto

Nunca versiona caixa-v9-local-backup.html nem caixa-secrets.json (.gitignore).
"""
import os, sys, subprocess, datetime, re

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
PROIBIDOS = ("caixa-v9-local-backup.html", "caixa-secrets.json",
             "totp-secret.txt", "totp-qr.png", "PosicaoDetalhada")


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=BASE, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def main():
    print("→ Cifrando o app…")
    r = run(["node", "build/build.js"], shell=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr.strip())
        sys.exit("✗ build falhou — nada foi publicado.")

    print("\n→ Preparando o commit…")
    run(["git", "add", "caixa-v9.html", "index.html", ".gitignore",
         "importar_xp.py", "publicar.py", "update_prices.py",
         "atualizar-precos.bat", "build/build.js", "build/lock-code.js"], shell=True)

    staged = run(["git", "diff", "--cached", "--name-only"], shell=True).stdout.split()
    if not staged:
        print("Nada mudou — nada a publicar.")
        return

    # trava de segurança: nenhum arquivo sensível pode entrar no commit
    vazando = [f for f in staged if any(p in f for p in PROIBIDOS)]
    if vazando:
        sys.exit(f"✗ ABORTADO: arquivo sensível no commit: {vazando}")
    print("  arquivos:", ", ".join(staged))

    msg = sys.argv[1] if len(sys.argv) > 1 else \
        "Atualiza carteira e app (" + datetime.date.today().strftime("%d/%m/%Y") + ")"
    r = run(["git", "commit", "-m", msg,
             "-m", "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"], shell=True)
    print(r.stdout.strip() or r.stderr.strip())

    print("\n→ Publicando…")
    r = run(["git", "push"], shell=True)
    out = (r.stdout + r.stderr).strip()
    print(out)
    if r.returncode != 0:
        sys.exit("✗ push falhou.")

    print("\n✓ Publicado — https://lucasmanzano02.github.io/controle-financeiro/")
    print("  O site atualiza em ~1 minuto. Ao abrir, ele vai pedir senha + código")
    print("  do autenticador uma vez para recarregar a carteira nova.")


if __name__ == "__main__":
    main()
