from django.core.management.base import BaseCommand
from django.db import transaction

from cadastros.models import Empresa, Nat_Lancamento, PlanoContabil


PLANO_CONTABIL = [
    ("1", "ATIVO"),
    ("1.1", "ATIVO CIRCULANTE"),
    ("1.1.01.001", "Caixa Geral"),
    ("1.1.01.002", "Caixa Loja 01"),
    ("1.1.01.003", "Caixa Loja 02"),
    ("1.1.01.004", "Fundo Fixo"),
    ("1.1.02.001", "Banco Conta Movimento"),
    ("1.1.02.002", "Banco Conta Investimento"),
    ("1.1.02.003", "Aplicações Financeiras"),
    ("1.1.02.004", "PIX a Receber"),
    ("1.1.03.001", "Clientes"),
    ("1.1.03.002", "Cartões de Crédito a Receber"),
    ("1.1.03.003", "Cartões de Débito a Receber"),
    ("1.1.03.004", "Cheques a Receber"),
    ("1.1.03.005", "Convênios a Receber"),
    ("1.1.04.001", "Estoque Moda Feminina"),
    ("1.1.04.002", "Estoque Moda Masculina"),
    ("1.1.04.003", "Estoque Acessórios"),
    ("1.1.04.004", "Estoque Calçados"),
    ("1.1.04.005", "Estoque Embalagens"),
    ("1.1.04.006", "Mercadorias em Trânsito"),
    ("1.1.05.001", "Adiantamentos a Fornecedores"),
    ("1.1.05.002", "Adiantamentos a Funcionários"),
    ("1.1.06.001", "ICMS a Recuperar"),
    ("1.1.06.002", "ICMS ST a Recuperar"),
    ("1.1.06.003", "PIS a Recuperar"),
    ("1.1.06.004", "COFINS a Recuperar"),
    ("1.1.06.005", "IRRF a Recuperar"),
    ("1.2", "ATIVO NÃO CIRCULANTE"),
    ("1.2.01", "REALIZÁVEL A LONGO PRAZO"),
    ("1.2.01.001", "Depósitos Judiciais"),
    ("1.2.01.002", "Empréstimos a Sócios"),
    ("1.2.02", "IMOBILIZADO"),
    ("1.2.02.001", "Terrenos"),
    ("1.2.02.002", "Edificações"),
    ("1.2.02.003", "Móveis e Utensílios"),
    ("1.2.02.004", "Máquinas"),
    ("1.2.02.005", "Equipamentos"),
    ("1.2.02.006", "Computadores"),
    ("1.2.02.007", "Impressoras"),
    ("1.2.02.008", "Equipamentos de Informática"),
    ("1.2.02.009", "Veículos"),
    ("1.2.02.010", "Instalações"),
    ("1.2.02.011", "Benfeitorias"),
    ("1.2.03", "DEPRECIAÇÃO ACUMULADA"),
    ("1.2.03.001", "Depreciação Móveis"),
    ("1.2.03.002", "Depreciação Equipamentos"),
    ("1.2.03.003", "Depreciação Computadores"),
    ("1.2.03.004", "Depreciação Veículos"),
    ("1.3", "INTANGÍVEL"),
    ("1.3.01", "Softwares"),
    ("1.3.02", "Licenças"),
    ("1.3.03", "Marcas"),
    ("2", "PASSIVO"),
    ("2.1", "PASSIVO CIRCULANTE"),
    ("2.1.01.001", "Fornecedores"),
    ("2.1.01.002", "Duplicatas a Pagar"),
    ("2.1.02.001", "Salários a Pagar"),
    ("2.1.02.002", "Pró-labore a Pagar"),
    ("2.1.02.003", "Comissões a Pagar"),
    ("2.1.02.004", "Férias a Pagar"),
    ("2.1.02.005", "13º Salário a Pagar"),
    ("2.1.03.001", "FGTS a Recolher"),
    ("2.1.03.002", "INSS a Recolher"),
    ("2.1.04.001", "ICMS a Recolher"),
    ("2.1.04.002", "ICMS ST a Recolher"),
    ("2.1.04.003", "PIS a Recolher"),
    ("2.1.04.004", "COFINS a Recolher"),
    ("2.1.04.005", "ISS a Recolher"),
    ("2.1.04.006", "Simples Nacional"),
    ("2.1.04.007", "IRPJ"),
    ("2.1.04.008", "CSLL"),
    ("2.1.05.001", "Cartões a Pagar"),
    ("2.1.06.001", "Empréstimos Bancários"),
    ("2.1.06.002", "Financiamentos"),
    ("2.1.07.001", "Aluguéis a Pagar"),
    ("2.1.07.002", "Energia a Pagar"),
    ("2.1.07.003", "Água a Pagar"),
    ("2.1.07.004", "Internet a Pagar"),
    ("2.2", "PASSIVO NÃO CIRCULANTE"),
    ("2.2.01", "Empréstimos Longo Prazo"),
    ("2.2.02", "Financiamentos Longo Prazo"),
    ("3", "PATRIMÔNIO LÍQUIDO"),
    ("3.1.01", "Capital Social"),
    ("3.1.02", "Reserva Legal"),
    ("3.1.03", "Reserva de Lucros"),
    ("3.1.04", "Lucros Acumulados"),
    ("3.1.05", "Prejuízos Acumulados"),
    ("4", "RECEITAS"),
    ("4.1", "RECEITAS OPERACIONAIS"),
    ("4.1.01", "Venda Loja Física"),
    ("4.1.02", "Venda E-commerce"),
    ("4.1.03", "Venda Marketplace"),
    ("4.1.04", "Venda Atacado"),
    ("4.1.05", "Venda WhatsApp"),
    ("4.2", "DEDUÇÕES DA RECEITA"),
    ("4.2.01", "Devoluções"),
    ("4.2.02", "Cancelamentos"),
    ("4.2.03", "Descontos Incondicionais"),
    ("4.3", "RECEITAS FINANCEIRAS"),
    ("4.3.01", "Juros Recebidos"),
    ("4.3.02", "Descontos Obtidos"),
    ("4.3.03", "Rendimentos Financeiros"),
    ("4.4", "OUTRAS RECEITAS"),
    ("4.4.01", "Venda de Ativos"),
    ("4.4.02", "Bonificações"),
    ("4.4.03", "Receitas Diversas"),
    ("5", "CUSTOS"),
    ("5.1", "CUSTO DAS MERCADORIAS VENDIDAS"),
    ("5.1.01", "CMV Moda Feminina"),
    ("5.1.02", "CMV Moda Masculina"),
    ("5.1.03", "CMV Acessórios"),
    ("5.1.04", "CMV Calçados"),
    ("5.2", "CUSTOS DE AQUISIÇÃO"),
    ("5.2.01", "Frete sobre Compras"),
    ("5.2.02", "Seguro sobre Compras"),
    ("5.2.03", "Importações"),
    ("5.2.04", "Embalagens"),
    ("5.2.05", "Etiquetas"),
    ("6", "DESPESAS OPERACIONAIS"),
    ("6.1", "DESPESAS COM PESSOAL"),
    ("6.1.01", "Salários"),
    ("6.1.02", "Pró-labore"),
    ("6.1.03", "Comissões"),
    ("6.1.04", "Horas Extras"),
    ("6.1.05", "Férias"),
    ("6.1.06", "Décimo Terceiro"),
    ("6.1.07", "FGTS"),
    ("6.1.08", "INSS"),
    ("6.1.09", "Vale Transporte"),
    ("6.1.10", "Vale Alimentação"),
    ("6.1.11", "Plano de Saúde"),
    ("6.2", "DESPESAS ADMINISTRATIVAS"),
    ("6.2.01", "Aluguel"),
    ("6.2.02", "Condomínio"),
    ("6.2.03", "Energia"),
    ("6.2.04", "Água"),
    ("6.2.05", "Internet"),
    ("6.2.06", "Telefonia"),
    ("6.2.07", "Material Escritório"),
    ("6.2.08", "Material Limpeza"),
    ("6.2.09", "Honorários Contábeis"),
    ("6.2.10", "Honorários Jurídicos"),
    ("6.2.11", "Correios"),
    ("6.2.12", "Seguros"),
    ("6.2.13", "Depreciação"),
    ("6.3", "DESPESAS COM VENDAS"),
    ("6.3.01", "Fretes"),
    ("6.3.02", "Embalagens"),
    ("6.3.03", "Publicidade"),
    ("6.3.04", "Marketing"),
    ("6.3.05", "Meta Ads"),
    ("6.3.06", "Google Ads"),
    ("6.3.07", "Influenciadores"),
    ("6.3.08", "Eventos"),
    ("6.4", "DESPESAS FINANCEIRAS"),
    ("6.4.01", "Juros"),
    ("6.4.02", "Multas"),
    ("6.4.03", "IOF"),
    ("6.4.04", "Tarifas Bancárias"),
    ("6.4.05", "Taxas Cartão Crédito"),
    ("6.4.06", "Taxas Cartão Débito"),
    ("6.4.07", "Taxas PIX"),
    ("6.5", "DESPESAS COM TECNOLOGIA"),
    ("6.5.01", "ERP"),
    ("6.5.02", "Hospedagem"),
    ("6.5.03", "Backup"),
    ("6.5.04", "Licenças"),
    ("6.5.05", "Domínio"),
    ("6.5.06", "Certificado Digital"),
    ("6.5.07", "Equipamentos de TI"),
]


PLANO_FINANCEIRO = [
    ("1101", "RECEITAS", "Vendas", "Venda Loja Física", "RECEITA", True, "4.1.01"),
    ("1102", "RECEITAS", "Vendas", "Venda E-commerce", "RECEITA", True, "4.1.02"),
    ("1103", "RECEITAS", "Vendas", "Venda Marketplace", "RECEITA", True, "4.1.03"),
    ("1104", "RECEITAS", "Vendas", "Venda WhatsApp", "RECEITA", True, "4.1.05"),
    ("1105", "RECEITAS", "Vendas", "Venda Condicional", "RECEITA", True, "4.1.01"),
    ("1106", "RECEITAS", "Vendas", "Venda Atacado", "RECEITA", True, "4.1.04"),
    ("1107", "RECEITAS", "Vendas", "Venda Uniformes", "RECEITA", True, "4.1.01"),
    ("1201", "RECEITAS", "Receitas Financeiras", "Juros Recebidos", "RECEITA", True, "4.3.01"),
    ("1202", "RECEITAS", "Receitas Financeiras", "Descontos Obtidos", "RECEITA", True, "4.3.02"),
    ("1203", "RECEITAS", "Receitas Financeiras", "Rendimentos Financeiros", "RECEITA", True, "4.3.03"),
    ("1204", "RECEITAS", "Receitas Financeiras", "Recuperação de Despesas", "RECEITA", True, "4.4.03"),
    ("1301", "RECEITAS", "Outras Receitas", "Venda de Ativo Imobilizado", "RECEITA", True, "4.4.01"),
    ("1302", "RECEITAS", "Outras Receitas", "Bonificações Recebidas", "RECEITA", True, "4.4.02"),
    ("1303", "RECEITAS", "Outras Receitas", "Receitas Diversas", "RECEITA", True, "4.4.03"),
    ("2100", "CUSTOS DAS MERCADORIAS", "CMV", "CMV - Custo da mercadoria vendida", "DESPESA", True, "5.1.01"),
    ("2101", "CUSTOS DAS MERCADORIAS", "Compras", "Compra Mercadorias Femininas", "DESPESA", True, "5.1.01"),
    ("2102", "CUSTOS DAS MERCADORIAS", "Compras", "Compra Mercadorias Masculinas", "DESPESA", True, "5.1.02"),
    ("2103", "CUSTOS DAS MERCADORIAS", "Compras", "Compra de Acessórios", "DESPESA", True, "5.1.03"),
    ("2104", "CUSTOS DAS MERCADORIAS", "Compras", "Compra de Calçados", "DESPESA", True, "5.1.04"),
    ("2105", "CUSTOS DAS MERCADORIAS", "Compras", "Frete sobre Compras", "DESPESA", True, "5.2.01"),
    ("2106", "CUSTOS DAS MERCADORIAS", "Compras", "Seguro sobre Compras", "DESPESA", True, "5.2.02"),
    ("2107", "CUSTOS DAS MERCADORIAS", "Compras", "Importação", "DESPESA", True, "5.2.03"),
    ("2108", "CUSTOS DAS MERCADORIAS", "Compras", "Embalagens", "DESPESA", True, "5.2.04"),
    ("2109", "CUSTOS DAS MERCADORIAS", "Compras", "Etiquetas", "DESPESA", True, "5.2.05"),
    ("2110", "CUSTOS DAS MERCADORIAS", "Compras", "Custos Diversos de Compras", "DESPESA", True, "5.2.04"),
    ("3101", "DESPESAS OPERACIONAIS", "Pessoal", "Salários", "DESPESA", True, "6.1.01"),
    ("3102", "DESPESAS OPERACIONAIS", "Pessoal", "Pró-labore", "DESPESA", True, "6.1.02"),
    ("3103", "DESPESAS OPERACIONAIS", "Pessoal", "Comissões", "DESPESA", True, "6.1.03"),
    ("3104", "DESPESAS OPERACIONAIS", "Pessoal", "Horas Extras", "DESPESA", True, "6.1.04"),
    ("3105", "DESPESAS OPERACIONAIS", "Pessoal", "INSS", "DESPESA", True, "6.1.08"),
    ("3106", "DESPESAS OPERACIONAIS", "Pessoal", "FGTS", "DESPESA", True, "6.1.07"),
    ("3107", "DESPESAS OPERACIONAIS", "Pessoal", "Vale Transporte", "DESPESA", True, "6.1.09"),
    ("3108", "DESPESAS OPERACIONAIS", "Pessoal", "Vale Alimentação", "DESPESA", True, "6.1.10"),
    ("3109", "DESPESAS OPERACIONAIS", "Pessoal", "Férias", "DESPESA", True, "6.1.05"),
    ("3110", "DESPESAS OPERACIONAIS", "Pessoal", "Décimo Terceiro Salário", "DESPESA", True, "6.1.06"),
    ("3111", "DESPESAS OPERACIONAIS", "Pessoal", "Plano de Saúde", "DESPESA", True, "6.1.11"),
    ("3112", "DESPESAS OPERACIONAIS", "Pessoal", "Rescisões", "DESPESA", True, "6.1.05"),
    ("3113", "DESPESAS OPERACIONAIS", "Pessoal", "Uniformes Funcionários", "DESPESA", True, "6.2.07"),
    ("3201", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Aluguel", "DESPESA", True, "6.2.01"),
    ("3202", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Condomínio", "DESPESA", True, "6.2.02"),
    ("3203", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Energia Elétrica", "DESPESA", True, "6.2.03"),
    ("3204", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Água", "DESPESA", True, "6.2.04"),
    ("3205", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Internet", "DESPESA", True, "6.2.05"),
    ("3206", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Telefonia", "DESPESA", True, "6.2.06"),
    ("3207", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Material de Limpeza", "DESPESA", True, "6.2.08"),
    ("3208", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Material de Escritório", "DESPESA", True, "6.2.07"),
    ("3209", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Segurança", "DESPESA", True, "6.2.12"),
    ("3210", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Manutenção Predial", "DESPESA", True, "6.2.12"),
    ("3211", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Manutenção de Equipamentos", "DESPESA", True, "6.2.12"),
    ("3212", "DESPESAS OPERACIONAIS", "Estrutura da Loja", "Dedetização", "DESPESA", True, "6.2.12"),
    ("3301", "DESPESAS OPERACIONAIS", "Marketing", "Meta Ads", "DESPESA", True, "6.3.05"),
    ("3302", "DESPESAS OPERACIONAIS", "Marketing", "Google Ads", "DESPESA", True, "6.3.06"),
    ("3303", "DESPESAS OPERACIONAIS", "Marketing", "Instagram", "DESPESA", True, "6.3.04"),
    ("3304", "DESPESAS OPERACIONAIS", "Marketing", "Facebook", "DESPESA", True, "6.3.04"),
    ("3305", "DESPESAS OPERACIONAIS", "Marketing", "TikTok", "DESPESA", True, "6.3.04"),
    ("3306", "DESPESAS OPERACIONAIS", "Marketing", "Influenciadores", "DESPESA", True, "6.3.07"),
    ("3307", "DESPESAS OPERACIONAIS", "Marketing", "Eventos", "DESPESA", True, "6.3.08"),
    ("3308", "DESPESAS OPERACIONAIS", "Marketing", "Material Gráfico", "DESPESA", True, "6.3.03"),
    ("3309", "DESPESAS OPERACIONAIS", "Marketing", "Brindes", "DESPESA", True, "6.3.04"),
    ("3310", "DESPESAS OPERACIONAIS", "Marketing", "Fotografia", "DESPESA", True, "6.3.04"),
    ("3311", "DESPESAS OPERACIONAIS", "Marketing", "Produção de Conteúdo", "DESPESA", True, "6.3.04"),
    ("3401", "DESPESAS OPERACIONAIS", "Tecnologia", "ERP", "DESPESA", True, "6.5.01"),
    ("3402", "DESPESAS OPERACIONAIS", "Tecnologia", "Hospedagem", "DESPESA", True, "6.5.02"),
    ("3403", "DESPESAS OPERACIONAIS", "Tecnologia", "Backup", "DESPESA", True, "6.5.03"),
    ("3404", "DESPESAS OPERACIONAIS", "Tecnologia", "Domínio", "DESPESA", True, "6.5.05"),
    ("3405", "DESPESAS OPERACIONAIS", "Tecnologia", "Licenças de Software", "DESPESA", True, "6.5.04"),
    ("3406", "DESPESAS OPERACIONAIS", "Tecnologia", "Suporte Técnico", "DESPESA", True, "6.5.04"),
    ("3407", "DESPESAS OPERACIONAIS", "Tecnologia", "Equipamentos de TI", "DESPESA", False, "6.5.07"),
    ("3408", "DESPESAS OPERACIONAIS", "Tecnologia", "Certificado Digital", "DESPESA", True, "6.5.06"),
    ("3501", "DESPESAS OPERACIONAIS", "Financeiras", "Taxas Cartão de Crédito", "DESPESA", True, "6.4.05"),
    ("3502", "DESPESAS OPERACIONAIS", "Financeiras", "Taxas Cartão de Débito", "DESPESA", True, "6.4.06"),
    ("3503", "DESPESAS OPERACIONAIS", "Financeiras", "Tarifas Bancárias", "DESPESA", True, "6.4.04"),
    ("3504", "DESPESAS OPERACIONAIS", "Financeiras", "Taxas PIX", "DESPESA", True, "6.4.07"),
    ("3505", "DESPESAS OPERACIONAIS", "Financeiras", "Juros Pagos", "DESPESA", True, "6.4.01"),
    ("3506", "DESPESAS OPERACIONAIS", "Financeiras", "Multas", "DESPESA", True, "6.4.02"),
    ("3507", "DESPESAS OPERACIONAIS", "Financeiras", "IOF", "DESPESA", True, "6.4.03"),
    ("3601", "DESPESAS OPERACIONAIS", "Tributos", "Simples Nacional", "DESPESA", True, "2.1.04.006"),
    ("3602", "DESPESAS OPERACIONAIS", "Tributos", "ICMS", "DESPESA", True, "2.1.04.001"),
    ("3603", "DESPESAS OPERACIONAIS", "Tributos", "ICMS-ST", "DESPESA", True, "2.1.04.002"),
    ("3604", "DESPESAS OPERACIONAIS", "Tributos", "PIS", "DESPESA", True, "2.1.04.003"),
    ("3605", "DESPESAS OPERACIONAIS", "Tributos", "COFINS", "DESPESA", True, "2.1.04.004"),
    ("3606", "DESPESAS OPERACIONAIS", "Tributos", "ISS", "DESPESA", True, "2.1.04.005"),
    ("3607", "DESPESAS OPERACIONAIS", "Tributos", "IRPJ", "DESPESA", True, "2.1.04.007"),
    ("3608", "DESPESAS OPERACIONAIS", "Tributos", "CSLL", "DESPESA", True, "2.1.04.008"),
    ("3609", "DESPESAS OPERACIONAIS", "Tributos", "Taxas Municipais", "DESPESA", True, "6.2.12"),
    ("3701", "DESPESAS OPERACIONAIS", "Administrativas", "Honorários Contábeis", "DESPESA", True, "6.2.09"),
    ("3702", "DESPESAS OPERACIONAIS", "Administrativas", "Consultorias", "DESPESA", True, "6.2.10"),
    ("3703", "DESPESAS OPERACIONAIS", "Administrativas", "Correios", "DESPESA", True, "6.2.11"),
    ("3704", "DESPESAS OPERACIONAIS", "Administrativas", "Fretes", "DESPESA", True, "6.3.01"),
    ("3705", "DESPESAS OPERACIONAIS", "Administrativas", "Combustível", "DESPESA", True, "6.2.12"),
    ("3706", "DESPESAS OPERACIONAIS", "Administrativas", "Pedágios", "DESPESA", True, "6.2.12"),
    ("3707", "DESPESAS OPERACIONAIS", "Administrativas", "Viagens", "DESPESA", True, "6.2.12"),
    ("3708", "DESPESAS OPERACIONAIS", "Administrativas", "Treinamentos", "DESPESA", True, "6.2.12"),
    ("3709", "DESPESAS OPERACIONAIS", "Administrativas", "Associações", "DESPESA", True, "6.2.12"),
    ("3710", "DESPESAS OPERACIONAIS", "Administrativas", "Despesas Jurídicas", "DESPESA", True, "6.2.10"),
    ("3711", "DESPESAS OPERACIONAIS", "Administrativas", "Material de Copa", "DESPESA", True, "6.2.07"),
    ("3712", "DESPESAS OPERACIONAIS", "Administrativas", "Despesas Diversas", "DESPESA", True, "6.2.12"),
    ("4101", "INVESTIMENTOS", "Investimentos", "Reforma da Loja", "AJUSTE", False, "1.2.02.011"),
    ("4102", "INVESTIMENTOS", "Investimentos", "Compra de Computadores", "AJUSTE", False, "1.2.02.006"),
    ("4103", "INVESTIMENTOS", "Investimentos", "Compra de Móveis", "AJUSTE", False, "1.2.02.003"),
    ("4104", "INVESTIMENTOS", "Investimentos", "Compra de Equipamentos", "AJUSTE", False, "1.2.02.005"),
    ("4105", "INVESTIMENTOS", "Investimentos", "Software", "AJUSTE", False, "1.3.01"),
    ("4106", "INVESTIMENTOS", "Investimentos", "Obras", "AJUSTE", False, "1.2.02.011"),
    ("4107", "INVESTIMENTOS", "Investimentos", "Veículos", "AJUSTE", False, "1.2.02.009"),
    ("5101", "EMPRÉSTIMOS E FINANCIAMENTOS", "Empréstimos", "Empréstimos Bancários", "AJUSTE", False, "2.1.06.001"),
    ("5102", "EMPRÉSTIMOS E FINANCIAMENTOS", "Empréstimos", "Financiamentos", "AJUSTE", False, "2.1.06.002"),
    ("5103", "EMPRÉSTIMOS E FINANCIAMENTOS", "Empréstimos", "Parcelas de Empréstimos", "AJUSTE", False, "2.1.06.001"),
    ("5104", "EMPRÉSTIMOS E FINANCIAMENTOS", "Empréstimos", "Juros de Financiamentos", "DESPESA", True, "6.4.01"),
    ("6101", "RETIRADAS DOS SÓCIOS", "Sócios", "Pró-labore", "DESPESA", True, "6.1.02"),
    ("6102", "RETIRADAS DOS SÓCIOS", "Sócios", "Distribuição de Lucros", "AJUSTE", False, "3.1.04"),
    ("6103", "RETIRADAS DOS SÓCIOS", "Sócios", "Retirada de Sócios", "AJUSTE", False, "3.1.04"),
]


class Command(BaseCommand):
    help = "Importa plano contábil e plano financeiro padrão para empresas de moda."

    def add_arguments(self, parser):
        parser.add_argument("--empresa", type=int, help="Importa apenas para a empresa informada.")

    @transaction.atomic
    def handle(self, *args, **options):
        empresas = Empresa.objects.all().order_by("id")
        if options.get("empresa"):
            empresas = empresas.filter(pk=options["empresa"])
        if not empresas.exists():
            self.stderr.write(self.style.ERROR("Nenhuma empresa encontrada."))
            return

        total_contas = 0
        total_naturezas = 0
        total_vinculos = 0
        for empresa in empresas:
            contas = self._importar_plano_contabil(empresa)
            criadas, vinculadas = self._importar_plano_financeiro(empresa, contas)
            total_contas += len(contas)
            total_naturezas += criadas
            total_vinculos += vinculadas

        self.stdout.write(self.style.SUCCESS(
            f"Importação concluída. Contas processadas: {total_contas}. "
            f"Naturezas processadas: {total_naturezas}. Vínculos atualizados: {total_vinculos}."
        ))

    def _importar_plano_contabil(self, empresa):
        explicit = dict(PLANO_CONTABIL)
        all_codes = set(explicit)
        for code in list(explicit):
            parts = code.split(".")
            for i in range(1, len(parts)):
                all_codes.add(".".join(parts[:i]))

        children_by_parent = {code: False for code in all_codes}
        for code in all_codes:
            parent = self._parent_code(code)
            if parent in children_by_parent:
                children_by_parent[parent] = True

        contas = {}
        for code in sorted(all_codes, key=self._sort_key):
            parent_code = self._parent_code(code)
            parent = contas.get(parent_code)
            descricao = explicit.get(code) or f"Grupo {code}"
            classe = self._classe_contabil(code)
            natureza = self._natureza_contabil(classe)
            conta, _ = PlanoContabil.objects.update_or_create(
                empresa=empresa,
                codigo=code,
                defaults={
                    "descricao": descricao,
                    "classe": classe,
                    "natureza": natureza,
                    "conta_pai": parent,
                    "nivel": len(code.split(".")),
                    "analitica": not children_by_parent.get(code, False),
                    "ativa": True,
                },
            )
            contas[code] = conta
        return contas

    def _importar_plano_financeiro(self, empresa, contas):
        criadas = 0
        vinculadas = 0
        for codigo, categoria, subcategoria, descricao, operacao, entra_dre, conta_codigo in PLANO_FINANCEIRO:
            tipo_natureza = "CREDITO" if operacao == "RECEITA" else "DEBITO" if operacao == "DESPESA" else "NEUTRO"
            tipo = self._tipo_financeiro(categoria)
            conta = contas.get(conta_codigo)
            natureza, _ = Nat_Lancamento.objects.update_or_create(
                empresa=empresa,
                codigo=codigo,
                defaults={
                    "categoria_principal": categoria[:50],
                    "subcategoria": subcategoria[:50],
                    "descricao": descricao[:255],
                    "tipo": tipo,
                    "status": "ATIVO",
                    "tipo_natureza": tipo_natureza,
                    "natureza_operacao": operacao,
                    "categoria_gerencial": subcategoria[:50],
                    "movimenta_financeiro": True,
                    "entra_dre": entra_dre,
                    "plano_contabil": conta,
                    "conta_contabil": conta.codigo if conta else None,
                    "ativo": True,
                },
            )
            criadas += 1
            if natureza.plano_contabil_id:
                vinculadas += 1
        return criadas, vinculadas

    def _classe_contabil(self, code):
        first = code.split(".")[0]
        return {
            "1": PlanoContabil.CLASSE_ATIVO,
            "2": PlanoContabil.CLASSE_PASSIVO,
            "3": PlanoContabil.CLASSE_PATRIMONIO,
            "4": PlanoContabil.CLASSE_RECEITA,
            "5": PlanoContabil.CLASSE_CUSTO,
            "6": PlanoContabil.CLASSE_DESPESA,
        }.get(first, PlanoContabil.CLASSE_RESULTADO)

    def _natureza_contabil(self, classe):
        if classe in {PlanoContabil.CLASSE_PASSIVO, PlanoContabil.CLASSE_PATRIMONIO, PlanoContabil.CLASSE_RECEITA}:
            return PlanoContabil.NATUREZA_CREDITO
        return PlanoContabil.NATUREZA_DEBITO

    def _tipo_financeiro(self, categoria):
        if categoria.startswith("RECEITAS"):
            return "OPERACIONAL"
        if categoria.startswith("CUSTOS"):
            return "OPERACIONAL"
        if categoria.startswith("DESPESAS"):
            return "OPERACIONAL"
        if categoria.startswith("INVESTIMENTOS"):
            return "INVESTIMENTO"
        if categoria.startswith("EMPRÉSTIMOS"):
            return "FINANCEIRO"
        return "AJUSTE"

    def _parent_code(self, code):
        if "." not in code:
            return None
        return ".".join(code.split(".")[:-1])

    def _sort_key(self, code):
        return tuple(int(part) for part in code.split("."))
