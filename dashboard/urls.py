from django.urls import path

from .views import DashboardEstoqueView, DashboardExecutivoView, DashboardFinanceiroView, DashboardProdutosView, DashboardVendasView

urlpatterns = [
    path("executivo/", DashboardExecutivoView.as_view(), name="dashboard-executivo"),
    path("produtos/", DashboardProdutosView.as_view(), name="dashboard-produtos"),
    path("vendas/", DashboardVendasView.as_view(), name="dashboard-vendas"),
    path("estoque/", DashboardEstoqueView.as_view(), name="dashboard-estoque"),
    path("financeiro/", DashboardFinanceiroView.as_view(), name="dashboard-financeiro"),
]
