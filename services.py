"""Service-layer workflow delegates for identity and currency creation."""

import currency_functions
import id_functions


def register_identity(request):
    import id_create_service as svc
    return id_functions.register_identity(request, svc)


def create_simple_currency(request):
    import id_create_service as svc
    return currency_functions.create_simple_currency(request, svc)


def create_fractional_currency(request):
    import id_create_service as svc
    return currency_functions.create_fractional_currency(request, svc)


def create_currency_from_plan(request):
    import id_create_service as svc
    return currency_functions.create_currency_from_plan(request, svc)
