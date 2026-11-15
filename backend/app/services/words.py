# -*- coding: utf-8 -*-
"""Сумма прописью для счетов и актов: «Сто двадцать три рубля 45 копеек»."""
from decimal import ROUND_HALF_UP, Decimal

_M = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_F = ["", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_TEEN = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
         "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_HUND = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]
_BIG = [None, ("тысяча", "тысячи", "тысяч"), ("миллион", "миллиона", "миллионов"),
        ("миллиард", "миллиарда", "миллиардов"), ("триллион", "триллиона", "триллионов")]


def plural(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(int(n)) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    return forms[1] if 2 <= n <= 4 else forms[2]


def _triplet(n: int, female: bool) -> list[str]:
    t = n % 100
    words = [_HUND[n // 100]]
    if 10 <= t <= 19:
        words.append(_TEEN[t - 10])
    else:
        words += [_TENS[t // 10], (_F if female else _M)[t % 10]]
    return [w for w in words if w]


def amount_in_words(amount: Decimal | int | float | str) -> str:
    d = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    kop_total = max(int(d * 100), 0)
    rub, kop = divmod(kop_total, 100)
    if rub >= 10 ** 15:
        return f"{rub:,} руб. {kop:02d} коп.".replace(",", " ")
    groups, n = [], rub
    while n:
        groups.append(n % 1000)
        n //= 1000
    parts: list[str] = []
    for i in range(len(groups) - 1, -1, -1):
        if groups[i]:
            parts += _triplet(groups[i], female=(i == 1))
            if i:
                parts.append(plural(groups[i], _BIG[i]))
    if not parts:
        parts = ["ноль"]
    text = "%s %s %02d %s" % (" ".join(parts), plural(rub, ("рубль", "рубля", "рублей")), kop,
                              plural(kop, ("копейка", "копейки", "копеек")))
    return text[0].upper() + text[1:]
