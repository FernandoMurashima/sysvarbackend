# populate_ncm.py
"""
Importa NCM a partir de um CSV (tipi_varejo.csv) mantendo o formato COM PONTOS: ####.##.##
Compatível com model produto.Ncm(ncm=CharField(max_length=10, regex ^\\d{4}\\.\\d{2}\\.\\d{2}$).

Uso:
  python populate_ncm.py caminho\para\tipi_varejo.csv --truncate
  python populate_ncm.py caminho\para\tipi_varejo.csv

Se DJANGO_SETTINGS_MODULE não estiver setado, assume Varejo.settings.
CSV esperado com ; (ponto e vírgula) e colunas: ncm;campo1;descricao;aliquota
- ncm pode vir com pontos (####.##.##) ou só dígitos (########) -> será formatado para ####.##.##
- aliquota aceita: "18", "18,0", "18.0", "18%", "NT", vazio -> grava Decimal ou None
"""

import os
import sys
import csv
from decimal import Decimal, InvalidOperation

# Django setup
if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Varejo.settings")

import django  # noqa: E402
django.setup()  # noqa: E402

from produto.models import Ncm  # noqa: E402


def only_digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def normalize_ncm(raw: str) -> str | None:
    """
    Retorna NCM no formato ####.##.## (10 chars) ou None se inválido.
    Aceita entrada '####.##.##' ou '########'.
    """
    if not raw:
        return None
    s = str(raw).strip()
    # Já no formato ####.##.## ?
    if len(s) == 10 and s[4:5] == "." and s[7:8] == "." and s.replace(".", "").isdigit():
        return s
    # Somente dígitos (########) -> formata
    d = only_digits(s)
    if len(d) == 8:
        return f"{d[:4]}.{d[4:6]}.{d[6:8]}"
    # Qualquer outra coisa: inválido
    return None


def parse_aliquota(raw) -> Decimal | None:
    if raw is None:
        return None
    txt = str(raw).strip()
    if not txt:
        return None
    up = txt.upper()
    if up in ("NT", "N/T", "NA", "N/A", "-"):
        return None
    txt = txt.replace("%", "").replace(",", ".")
    try:
        return Decimal(txt)
    except InvalidOperation:
        return None


def populate_ncm(csv_file_path: str, truncate: bool = False):
    if truncate:
        Ncm.objects.all().delete()

    created = 0
    updated = 0
    skipped = 0

    with open(csv_file_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")

        # Header?
        first = next(reader, None)
        if first is None:
            print("CSV vazio.")
            return
        first0 = (first[0] or "").strip().lower()
        if first0 in ("ncm", "codigo", "código", "codigo ncm", "código ncm"):
            pass  # header detectado
        else:
            # re-injeta a primeira linha lida
            rest = list(reader)
            reader = iter([first] + rest)

        for row in reader:
            if not row:
                continue

            ncm_raw = (row[0] if len(row) > 0 else "").strip()
            ncm_fmt = normalize_ncm(ncm_raw)
            if not ncm_fmt:
                skipped += 1
                continue

            campo1 = (row[1] if len(row) > 1 else "").strip() or None
            descricao = (row[2] if len(row) > 2 else "").strip()
            aliq_raw = (row[3] if len(row) > 3 else "").strip()
            aliq = parse_aliquota(aliq_raw)

            obj, was_created = Ncm.objects.update_or_create(
                ncm=ncm_fmt,
                defaults={
                    "campo1": campo1,
                    "descricao": descricao[:1000],
                    "aliquota": aliq,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

    print(f"OK — criados={created} atualizados={updated} ignorados={skipped}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python populate_ncm.py caminho\\tipi_varejo.csv [--truncate]")
        sys.exit(1)
    csv_path = sys.argv[1]
    truncate_flag = "--truncate" in sys.argv[2:]
    populate_ncm(csv_path, truncate=truncate_flag)
