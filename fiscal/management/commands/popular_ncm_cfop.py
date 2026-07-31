import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cadastros.models import Empresa
from fiscal.models import Cfop
from produto.models import Ncm


CFOPS_BASICOS = [
    ("1101", "Compra para industrializacao", Cfop.TIPO_COMPRA, Cfop.DESTINO_DENTRO, True, True, "Compra interna de insumos para industrializacao."),
    ("1102", "Compra para comercializacao", Cfop.TIPO_COMPRA, Cfop.DESTINO_DENTRO, True, True, "Compra interna de mercadorias para revenda."),
    ("1124", "Industrializacao efetuada por outra empresa", Cfop.TIPO_COMPRA, Cfop.DESTINO_DENTRO, True, True, "Retorno/cobranca de industrializacao por terceiros."),
    ("1403", "Compra para comercializacao com substituicao tributaria", Cfop.TIPO_COMPRA, Cfop.DESTINO_DENTRO, True, True, "Compra interna com ICMS-ST."),
    ("1556", "Compra de material para uso ou consumo", Cfop.TIPO_COMPRA, Cfop.DESTINO_DENTRO, True, True, "Compra interna de uso e consumo."),
    ("1949", "Outra entrada de mercadoria ou prestacao de servico", Cfop.TIPO_OUTROS, Cfop.DESTINO_DENTRO, True, False, "Entrada interna nao classificada nas demais operacoes."),
    ("2101", "Compra interestadual para industrializacao", Cfop.TIPO_COMPRA, Cfop.DESTINO_FORA, True, True, "Compra interestadual de insumos para industrializacao."),
    ("2102", "Compra interestadual para comercializacao", Cfop.TIPO_COMPRA, Cfop.DESTINO_FORA, True, True, "Compra interestadual de mercadorias para revenda."),
    ("2124", "Industrializacao interestadual efetuada por outra empresa", Cfop.TIPO_COMPRA, Cfop.DESTINO_FORA, True, True, "Retorno/cobranca interestadual de industrializacao por terceiros."),
    ("2403", "Compra interestadual para comercializacao com substituicao tributaria", Cfop.TIPO_COMPRA, Cfop.DESTINO_FORA, True, True, "Compra interestadual com ICMS-ST."),
    ("2556", "Compra interestadual de material para uso ou consumo", Cfop.TIPO_COMPRA, Cfop.DESTINO_FORA, True, True, "Compra interestadual de uso e consumo."),
    ("2949", "Outra entrada interestadual de mercadoria ou servico", Cfop.TIPO_OUTROS, Cfop.DESTINO_FORA, True, False, "Entrada interestadual nao classificada nas demais operacoes."),
    ("5101", "Venda de producao do estabelecimento", Cfop.TIPO_VENDA, Cfop.DESTINO_DENTRO, True, True, "Venda interna de produto proprio."),
    ("5102", "Venda de mercadoria adquirida ou recebida de terceiros", Cfop.TIPO_VENDA, Cfop.DESTINO_DENTRO, True, True, "Venda interna de mercadoria de revenda."),
    ("5152", "Transferencia de mercadoria adquirida ou recebida de terceiros", Cfop.TIPO_TRANSFERENCIA, Cfop.DESTINO_DENTRO, True, False, "Transferencia interna entre unidades da empresa."),
    ("5201", "Devolucao de compra para industrializacao", Cfop.TIPO_DEVOLUCAO, Cfop.DESTINO_DENTRO, True, True, "Devolucao interna de compra de insumos."),
    ("5202", "Devolucao de compra para comercializacao", Cfop.TIPO_DEVOLUCAO, Cfop.DESTINO_DENTRO, True, True, "Devolucao interna de compra para revenda."),
    ("5405", "Venda de mercadoria sujeita ao regime de substituicao tributaria", Cfop.TIPO_VENDA, Cfop.DESTINO_DENTRO, True, True, "Venda interna com ICMS-ST."),
    ("5556", "Devolucao de compra de material de uso ou consumo", Cfop.TIPO_DEVOLUCAO, Cfop.DESTINO_DENTRO, True, True, "Devolucao interna de uso e consumo."),
    ("5915", "Remessa para conserto ou reparo", Cfop.TIPO_OUTROS, Cfop.DESTINO_DENTRO, True, False, "Remessa interna para conserto, reparo ou ajuste."),
    ("5916", "Retorno de mercadoria recebida para conserto ou reparo", Cfop.TIPO_OUTROS, Cfop.DESTINO_DENTRO, True, False, "Retorno interno de conserto, reparo ou ajuste."),
    ("5929", "Lancamento efetuado em decorrencia de emissao de documento fiscal relativo a ECF", Cfop.TIPO_VENDA, Cfop.DESTINO_DENTRO, False, True, "Operacao interna vinculada a cupom fiscal/NFC-e quando aplicavel."),
    ("5949", "Outra saida de mercadoria ou prestacao de servico", Cfop.TIPO_OUTROS, Cfop.DESTINO_DENTRO, True, False, "Saida interna nao classificada nas demais operacoes."),
    ("6101", "Venda interestadual de producao do estabelecimento", Cfop.TIPO_VENDA, Cfop.DESTINO_FORA, True, True, "Venda interestadual de produto proprio."),
    ("6102", "Venda interestadual de mercadoria adquirida ou recebida de terceiros", Cfop.TIPO_VENDA, Cfop.DESTINO_FORA, True, True, "Venda interestadual de mercadoria de revenda."),
    ("6152", "Transferencia interestadual de mercadoria adquirida ou recebida de terceiros", Cfop.TIPO_TRANSFERENCIA, Cfop.DESTINO_FORA, True, False, "Transferencia interestadual entre unidades da empresa."),
    ("6201", "Devolucao interestadual de compra para industrializacao", Cfop.TIPO_DEVOLUCAO, Cfop.DESTINO_FORA, True, True, "Devolucao interestadual de compra de insumos."),
    ("6202", "Devolucao interestadual de compra para comercializacao", Cfop.TIPO_DEVOLUCAO, Cfop.DESTINO_FORA, True, True, "Devolucao interestadual de compra para revenda."),
    ("6405", "Venda interestadual de mercadoria sujeita a substituicao tributaria", Cfop.TIPO_VENDA, Cfop.DESTINO_FORA, True, True, "Venda interestadual com ICMS-ST."),
    ("6556", "Devolucao interestadual de compra de material de uso ou consumo", Cfop.TIPO_DEVOLUCAO, Cfop.DESTINO_FORA, True, True, "Devolucao interestadual de uso e consumo."),
    ("6915", "Remessa interestadual para conserto ou reparo", Cfop.TIPO_OUTROS, Cfop.DESTINO_FORA, True, False, "Remessa interestadual para conserto, reparo ou ajuste."),
    ("6916", "Retorno interestadual de mercadoria recebida para conserto ou reparo", Cfop.TIPO_OUTROS, Cfop.DESTINO_FORA, True, False, "Retorno interestadual de conserto, reparo ou ajuste."),
    ("6949", "Outra saida interestadual de mercadoria ou servico", Cfop.TIPO_OUTROS, Cfop.DESTINO_FORA, True, False, "Saida interestadual nao classificada nas demais operacoes."),
]


def normalizar_categoria(valor):
    texto = (valor or "").strip().lower()
    if "vest" in texto or "roup" in texto or "confecc" in texto or "malha" in texto:
        return Ncm.CATEGORIA_VESTUARIO
    if "tecid" in texto or "fibra" in texto or "fio" in texto:
        return Ncm.CATEGORIA_TECIDO
    if "aviamento" in texto or "bot" in texto or "zip" in texto or "renda" in texto or "fita" in texto:
        return Ncm.CATEGORIA_AVIAMENTO
    if "embalag" in texto or "sacola" in texto:
        return Ncm.CATEGORIA_EMBALAGEM
    return Ncm.CATEGORIA_OUTROS


class Command(BaseCommand):
    help = "Popula NCMs de confeccao/insumos a partir de JSON e CFOPs basicos por empresa."

    def add_arguments(self, parser):
        parser.add_argument(
            "--ncm-json",
            default=r"C:\Users\ferna\Downloads\ncm_confeccao_roupas_insumos_tipi_2026.json",
            help="Caminho do JSON com registros de NCM.",
        )
        parser.add_argument(
            "--empresa-id",
            type=int,
            action="append",
            help="ID de empresa para popular CFOP. Pode repetir. Se omitido, popula todas as empresas.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        caminho = Path(options["ncm_json"])
        if not caminho.exists():
            raise CommandError(f"Arquivo de NCM nao encontrado: {caminho}")

        with caminho.open("r", encoding="utf-8") as arquivo:
            payload = json.load(arquivo)

        registros = payload.get("registros", [])
        if not isinstance(registros, list):
            raise CommandError("JSON invalido: chave 'registros' deve ser uma lista.")

        empresas = Empresa.objects.all()
        if options.get("empresa_id"):
            empresas = empresas.filter(id__in=options["empresa_id"])

        ncm_criados = 0
        ncm_atualizados = 0
        for empresa in empresas:
            for item in registros:
                codigo = (item.get("ncm") or "").strip()
                descricao = (item.get("descricao_tipi") or "").strip()
                if not codigo or not descricao:
                    continue
                _, criado = Ncm.objects.update_or_create(
                    empresa=empresa,
                    ncm=codigo,
                    defaults={
                        "descricao": descricao,
                        "categoria": normalizar_categoria(item.get("categoria") or descricao),
                        "campo1": str(item.get("ipi_tipi") or "")[:25],
                        "ativo": True,
                    },
                )
                if criado:
                    ncm_criados += 1
                else:
                    ncm_atualizados += 1

        cfop_criados = 0
        cfop_atualizados = 0
        for empresa in empresas:
            for codigo, descricao, tipo, destino, estoque, financeiro, observacoes in CFOPS_BASICOS:
                _, criado = Cfop.objects.update_or_create(
                    empresa=empresa,
                    codigo=codigo,
                    defaults={
                        "descricao": descricao,
                        "tipo_operacao": tipo,
                        "destino": destino,
                        "movimenta_estoque": estoque,
                        "gera_financeiro": financeiro,
                        "observacoes": observacoes,
                        "ativo": True,
                    },
                )
                if criado:
                    cfop_criados += 1
                else:
                    cfop_atualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f"NCMs criados: {ncm_criados}; atualizados: {ncm_atualizados}. "
            f"CFOPs criados: {cfop_criados}; atualizados: {cfop_atualizados}; empresas: {empresas.count()}."
        ))
