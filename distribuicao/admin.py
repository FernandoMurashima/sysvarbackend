from django.contrib import admin

from .models import (
    Distribuicao,
    DistribuicaoDestino,
    DistribuicaoItem,
    MercadoriaTransito,
    PedidoVendaDistribuicao,
    PedidoVendaDistribuicaoItem,
    PerfilDistribuicao,
    PerfilDistribuicaoItem,
)


admin.site.register(PerfilDistribuicao)
admin.site.register(PerfilDistribuicaoItem)
admin.site.register(Distribuicao)
admin.site.register(DistribuicaoItem)
admin.site.register(DistribuicaoDestino)
admin.site.register(PedidoVendaDistribuicao)
admin.site.register(PedidoVendaDistribuicaoItem)
admin.site.register(MercadoriaTransito)
