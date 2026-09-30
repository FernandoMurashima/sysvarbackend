# Sysvar Central Backend

Backend da retaguarda central do Sysvar ERP.

## Papel no produto

Este repositório concentra a API e as regras de negócio centrais do Sysvar, incluindo cadastros mestres, compras, estoque, financeiro, fiscal, produção, distribuição, vendas e integração com o Sysvar Hub.

O Sysvar é multiempresa/multi-tenant. Regras de escopo, autorização e isolamento devem permanecer no backend.

## Stack principal

- Python
- Django 4.2
- Django REST Framework 3.14
- MySQL
- Celery e Redis quando aplicáveis

As versões efetivas estão em `requirements.txt`.

## Integração com o Sysvar Hub

A Central é a autoridade para cadastros e dados corporativos consolidados. O Hub opera localmente na loja e se integra à Central por APIs específicas.

Código de integração relacionado encontra-se principalmente no app `hub` e nos módulos de negócio envolvidos em cada fluxo.

## Documentação

Documentação central e arquitetural do Projeto Sysvar fica no repositório:

`FernandoMurashima/sysvar-vault`

Pasta principal:

`takeshi/10 Projetos/Sysvar`

O diretório `docs/` deste repositório deve conter apenas documentação técnica diretamente acoplada à implementação do backend.

Documentos técnicos atuais incluem, entre outros:

- `docs/fiscal-devolucao-entrada.md`
- `docs/vale-troca-online.md`

Não duplicar aqui a documentação central completa do produto.

## Desenvolvimento

Entrada principal do Django:

```text
manage.py
```

Script local utilizado no ambiente de desenvolvimento Windows:

```text
iniciar-central-dev.ps1
```

Antes de alterações relevantes, consultar a documentação vigente no `sysvar-vault` e o código atual da branch `main`.
