# cadastros/validators.py
import re
from django.core.exceptions import ValidationError

# ---------------------------------
# Helpers
# ---------------------------------
def only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

# ---------------------------------
# Checks (retornam bool)
# ---------------------------------
def check_cpf(value: str) -> bool:
    """
    True se CPF válido. Exceção: aceita '000.000.000-00' (cliente padrão).
    """
    cpf = only_digits(value)
    if not cpf:
        return True  # opcional
    if cpf == "00000000000":
        return True  # cliente padrão
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    def dv(nums: str) -> int:
        s = sum(int(n) * w for n, w in zip(nums, range(len(nums) + 1, 1, -1)))
        r = (s * 10) % 11
        return 0 if r == 10 else r

    d1 = dv(cpf[:9])
    d2 = dv(cpf[:10])
    return d1 == int(cpf[9]) and d2 == int(cpf[10])


def check_cnpj(value: str) -> bool:
    cnpj = only_digits(value)
    if not cnpj:
        return True
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    pesos1 = [5,4,3,2,9,8,7,6,5,4,3,2]
    pesos2 = [6] + pesos1

    def dv(nums: str, pesos: list[int]) -> int:
        s = sum(int(n) * p for n, p in zip(nums, pesos))
        r = s % 11
        return 0 if r < 2 else 11 - r

    d1 = dv(cnpj[:12], pesos1)
    d2 = dv(cnpj[:13], pesos2)
    return d1 == int(cnpj[12]) and d2 == int(cnpj[13])


def check_email(value: str) -> bool:
    if not value:
        return True
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def check_telefone_br(value: str) -> bool:
    """
    8–11 dígitos (aceita DDD e 9º dígito). Vazio é válido (campo opcional).
    """
    if not value:
        return True
    digits = only_digits(value)
    return 8 <= len(digits) <= 11


def check_cep(value: str) -> bool:
    if not value:
        return True
    return bool(re.match(r"^\d{5}-?\d{3}$", value))

# ---------------------------------
# Validators (lançam ValidationError)
# ---------------------------------
def cpf_validator(value: str) -> None:
    if not check_cpf(value):
        raise ValidationError("CPF inválido.")


def cnpj_validator(value: str) -> None:
    if not check_cnpj(value):
        raise ValidationError("CNPJ inválido.")


def email_simple_validator(value: str) -> None:
    if not check_email(value):
        raise ValidationError("E-mail inválido.")


def telefone_br_validator(value: str) -> None:
    if not check_telefone_br(value):
        raise ValidationError("telefone inválido (use 10 ou 11 dígitos com/sem máscara).")


def cep_validator(value: str) -> None:
    if not check_cep(value):
        raise ValidationError("cep inválido (formato 99999-999).")
