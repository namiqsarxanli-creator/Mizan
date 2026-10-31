"""Deterministic checks for the clauses whose legality is a number or a fixed phrase.

Most violations in employment contracts are quantitative: a six-month probation, a 48-hour
week, 14 days of leave, overtime at 1.5x. A rule reads the number and compares it with the
Labour Code's limit. It needs no LLM, cannot hallucinate and costs nothing. The LLM review
(``review.py``) handles everything else.

Each rule names the article it enforces. The limits are those in force on 2026-10-31:

| rule | limit | article |
|---|---|---|
| probation | ≤ 3 months; ≤ 2 weeks in a contract of up to 6 months | 51.1 |
| probation | none for minors or contracts of up to 2 months | 52 |
| working time | ≤ 8 h a day, ≤ 40 h a week | 89 |
| working time, minors | ≤ 36 h a week (16-17), ≤ 24 h (under 16) | 91.2 |
| overtime | ≤ 4 h in two consecutive working days | 100.1 |
| overtime pay | ≥ 2x the hourly rate, never replaced by time off | 165 |
| basic leave | ≥ 21 days; 30 for doctors, agriculture, education staff ... | 114 |
| wages | in manat; ≤ 20% in kind | 174 |
| deductions | only the listed grounds (no fines); ≤ 20% per payment | 175, 176 |
| resignation | the employee's notice is one calendar month | 69 |
| dismissal | only on the grounds of art. 70, with the guarantees of art. 77 | 70, 77 |
| dismissal | not of pregnant women or parents of children under 3 | 79 |
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from mizan_contracts.clauses import Clause
from mizan_core.security import screen
from mizan_ingestion.text import az_lower

Verdict = Literal["violation", "risk", "injection"]
Origin = Literal["rule", "llm", "screen"]


@dataclass(frozen=True, slots=True)
class Finding:
    clause: str
    verdict: Verdict
    issue: str
    articles: tuple[str, ...]
    explanation: str  # Azerbaijani, shown to the user
    origin: Origin
    quote: str | None = None  # the statute's words, verified against the source (LLM review)


@dataclass(frozen=True, slots=True)
class Context:
    """Facts about the whole contract that change what a single clause may say."""

    term_months: float | None  # fixed-term length; None when open-ended or not stated
    minor_age: int | None
    long_leave_job: str | None  # the job title that earns 30 days of basic leave (114.3)


_WORDS = {
    "bir": 1, "iki": 2, "üç": 3, "dörd": 4, "beş": 5, "altı": 6, "yeddi": 7, "səkkiz": 8,
    "doqquz": 9, "on": 10, "on iki": 12, "on dörd": 14, "on beş": 15, "iyirmi": 20,
    "iyirmi bir": 21, "iyirmi səkkiz": 28, "otuz": 30, "qırx": 40, "qırx səkkiz": 48,
    "əlli": 50, "yarım": 0.5, "bir yarım": 1.5,
}  # fmt: skip
_WORD_RX = "|".join(sorted((re.escape(w) for w in _WORDS), key=len, reverse=True))
_NUM = rf"(\d+(?:[.,]\d+)?|(?<![a-zçəğıöşü])(?:{_WORD_RX})(?![a-zçəğıöşü]))"
_PAREN = r"(?:\s*\([^)]{0,40}\))?"
_DURATION = re.compile(rf"{_NUM}{_PAREN}\s+(?:təqvim\s+|iş\s+)?(gün|həftə|ay|il)")
_HOURS = re.compile(rf"{_NUM}{_PAREN}\s+saat")
_PERCENT = re.compile(rf"{_NUM}{_PAREN}\s*(?:faiz|%)")
_MULTIPLE = re.compile(rf"{_NUM}\s*(?:misli|qat)|(ikiqat|üçqat)")

_DAYS = {"gün": 1.0, "həftə": 7.0, "ay": 30.0, "il": 365.0}

# Art. 114.3: jobs with 30 calendar days of basic leave (the common ones).
_LONG_LEAVE = (
    "həkim", "tibb bacısı", "orta tibb", "əczaçı", "kənd təsərrüfatı", "laborant",
    "kitabxanaçı", "metodist", "dayə", "elmi işçi", "istehsalat təlimi ustası",
)  # fmt: skip


def number(token: str) -> float:
    token = token.strip()
    if token in _WORDS:
        return float(_WORDS[token])
    return float(token.replace(",", "."))


def durations_days(text: str) -> list[float]:
    return [number(m.group(1)) * _DAYS[m.group(2)] for m in _DURATION.finditer(text)]


def _fmt(n: float) -> str:
    return str(int(n)) if n == int(n) else str(n).replace(".", ",")


def context_of(clauses: Sequence[Clause]) -> Context:
    term: float | None = None
    minor: int | None = None
    job: str | None = None
    for c in clauses:
        low = az_lower(c.text)
        if term is None and "bağlan" in low and ("müddətinə" in low or "müddətli" in low):
            days = durations_days(low)
            if days:
                term = round(max(days) / 30, 1)
        if minor is None:
            m = re.search(r"\((1[0-7])\s*yaş", low) or re.search(r"(1[0-7])\s*yaşında", low)
            if m:
                minor = int(m.group(1))
        if job is None and ("vəzifəsinə" in low or "peşəsinə" in low or "işə qəbul" in low):
            job = next((k for k in _LONG_LEAVE if k in low), None)
    return Context(term, minor, job)


Rule = Callable[[Clause, str, Context], list[Finding]]


def _f(c: Clause, issue: str, articles: tuple[str, ...], why: str) -> Finding:
    return Finding(c.number, "violation", issue, articles, why, "rule")


def probation(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if "sınaq" not in low:
        return []
    days = durations_days(low)
    if not days:
        return []
    longest = max(days)
    if ctx.minor_age is not None:
        why = "18 yaşından az şəxslə əmək müqaviləsində sınaq müddəti müəyyən edilmir."
        return [_f(c, "probation_not_allowed", ("art:52",), why)]
    if ctx.term_months is not None and ctx.term_months <= 2:
        why = "İki ayadək müddətə bağlanan əmək müqaviləsində sınaq müddəti müəyyən edilmir."
        return [_f(c, "probation_not_allowed", ("art:52",), why)]
    if ctx.term_months is not None and ctx.term_months <= 6 and longest > 14:
        why = (
            f"Müqavilə {_fmt(ctx.term_months)} aylıqdır; altı ayadək müqavilədə sınaq müddəti "
            "ən çoxu iki həftə ola bilər."
        )
        return [_f(c, "probation_too_long", ("art:51",), why)]
    if longest > 92:
        return [_f(c, "probation_too_long", ("art:51",), "Sınaq müddəti ən çoxu üç ay ola bilər.")]
    return []


def working_time(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if "saat" not in low or "artıq" in low or "nahar" in low:
        return []
    daily = [number(m.group(1)) for m in re.finditer(rf"gündə\s+{_NUM}{_PAREN}\s+saat", low)]
    weekly = [
        number(m.group(1))
        for m in re.finditer(rf"həftə(?:də|\s+ərzində)(?:[^.;]*?\s)?{_NUM}{_PAREN}\s+saat", low)
    ]
    days = re.search(rf"həftədə\s+{_NUM}{_PAREN}\s+gün", low)
    if days and daily:
        weekly.append(number(days.group(1)) * max(daily))
    week = max(weekly, default=0.0)
    if ctx.minor_age is not None:
        cap = 24 if ctx.minor_age < 16 else 36
        if week > cap:
            why = f"{ctx.minor_age} yaşlı işçi üçün iş həftəsi {cap} saatdan artıq olmamalıdır."
            return [_f(c, "hours_minor", ("art:91",), why)]
    if week > 40:
        why = (
            f"Həftəlik iş vaxtı {_fmt(week)} saatdır; normal iş həftəsi 40 saatdan artıq ola "
            "bilməz."
        )
        return [_f(c, "working_hours", ("art:89",), why)]
    if max(daily, default=0.0) > 8 and "cəmləşdirilmiş" not in low:
        why = (
            f"Gündəlik iş vaxtı {_fmt(max(daily))} saatdır; normal iş günü 8 saatdan artıq ola "
            "bilməz."
        )
        return [_f(c, "working_hours", ("art:89",), why)]
    return []


def overtime(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if "iş vaxtından artıq" not in low:
        return []
    out: list[Finding] = []
    pair = re.search(rf"iki\s+iş\s+günü\s+ərzində\s+{_NUM}{_PAREN}\s+saat", low)
    per_day = re.search(rf"(?:hər\s+gün|gündə)\s+{_NUM}{_PAREN}\s+saat", low)
    two_days = number(pair.group(1)) if pair else 2 * number(per_day.group(1)) if per_day else 0.0
    if two_days > 4:
        why = (
            "İşçi ardıcıl iki iş günü ərzində 4 saatdan çox iş vaxtından artıq işə cəlb edilə "
            f"bilməz; bənd {_fmt(two_days)} saata icazə verir."
        )
        out.append(_f(c, "overtime_limit", ("art:100",), why))
    paid = "ödən" in low or "haqq" in low
    m = _MULTIPLE.search(low)
    rate = (
        (2.0 if m.group(2) == "ikiqat" else 3.0)
        if m and m.group(2)
        else (number(m.group(1)) if m else None)
    )
    if paid and rate is not None and rate < 2:
        why = (
            f"İş vaxtından artıq iş saatlıq maaşın {_fmt(rate)} misli ilə ödənilir; ən azı "
            "ikiqat ödənilməlidir."
        )
        out.append(_f(c, "overtime_pay", ("art:165",), why))
    elif "istirahət gün" in low and ("əvəz" in low or "ödənilmir" in low):
        why = "İş vaxtından artıq iş əlavə istirahət günü ilə əvəz edilə bilməz; ödənilməlidir."
        out.append(_f(c, "overtime_pay", ("art:165",), why))
    elif re.search(r"(haqq|ödəniş)\s+ödənilmir", low):
        why = "İş vaxtından artıq iş ən azı ikiqat məbləğdə ödənilməlidir."
        out.append(_f(c, "overtime_pay", ("art:165",), why))
    return out


def leave(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if "məzuniyyət" not in low or "ödənişsiz" in low:
        return []
    if "əlavə" in low and "əsas" not in low:
        return []
    days = [number(m.group(1)) for m in re.finditer(rf"{_NUM}{_PAREN}\s+(?:təqvim\s+)?gün", low)]
    if not days:
        return []
    least = min(days)
    if least < 21:
        why = f"Əsas məzuniyyət {_fmt(least)} gündür; ən azı 21 təqvim günü olmalıdır."
        return [_f(c, "leave_too_short", ("art:114",), why)]
    if ctx.long_leave_job and least < 30:
        why = (
            f"«{ctx.long_leave_job}» üçün ödənişli əsas məzuniyyət 30 təqvim günüdür; bənddə "
            f"{_fmt(least)} gün yazılıb."
        )
        return [_f(c, "leave_too_short", ("art:114",), why)]
    return []


_FOREIGN = re.compile(r"dollar|avro|euro|\busd\b|\beur\b|rubl|lirə|\$|€")


def wages(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if "əmək haqq" not in low and "maaş" not in low:
        return []
    out: list[Finding] = []
    if _FOREIGN.search(low):
        why = "Əmək haqqı Azərbaycan Respublikasının pul vahidi – manatla ödənilir."
        out.append(_f(c, "wage_currency", ("art:174",), why))
    percents = [number(m.group(1)) for m in _PERCENT.finditer(low)]
    if ("natura" in low or "mallarla" in low) and any(p > 20 for p in percents):
        why = "Əmək haqqının natura formasında verilən hissəsi 20 faizdən çox ola bilməz."
        out.append(_f(c, "wage_in_kind", ("art:174",), why))
    if re.search(r"\btut(ul|a\b|ar|ma)", low):
        articles: list[str] = []
        reasons: list[str] = []
        if "cərimə" in low:
            articles.append("art:175")
            reasons.append("cərimə əmək haqqından tutulmanın qanuni əsasları sırasında deyil")
        if any(p > 20 for p in percents):
            articles.append("art:176")
            reasons.append("hər ödənişdə tutulmalar əmək haqqının 20 faizindən çox ola bilməz")
        if articles:
            why = "Tutulma qanuna ziddir: " + "; ".join(reasons) + "."
            out.append(_f(c, "deductions", tuple(articles), why))
    return out


def resignation(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if "xəbərdar" not in low or "işəgötürəni" not in low:
        return []  # only the employee's notice to the employer
    days = durations_days(low)
    if days and max(days) > 31:
        why = (
            "İşçi əmək müqaviləsinə bir təqvim ayı qabaqcadan xəbərdar etməklə xitam verə "
            "bilər; daha uzun müddət tələb edilə bilməz."
        )
        return [_f(c, "resignation_notice", ("art:69",), why)]
    return []


def dismissal(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if "xitam" not in low:
        return []
    out: list[Finding] = []
    allowed = re.search(r"xitam\s+ver(ə|il[əe])\s+bil(ər|ər\b)|xitam\s+verilir", low)
    forbidden = re.search(r"bilməz|qadağan", low)
    if (
        allowed and not forbidden and
        re.search(r"hamilə|üç\s+yaşınadək|3\s+yaşınadək|əmək\s+qabiliyyətini\s+müvəqqəti", low)
    ):  # fmt: skip
        why = (
            "Hamilə, üç yaşınadək uşağı olan qadınlarla və digər qorunan işçilərlə əmək "
            "müqaviləsinə işəgötürənin təşəbbüsü ilə xitam verilməsi qadağandır."
        )
        out.append(_f(c, "protected_dismissal", ("art:79",), why))
    articles: list[str] = []
    if re.search(r"səbəb\s+göstərmədən|əsas\s+göstərmədən|istənilən\s+vaxt", low):
        articles.append("art:70")
    if re.search(r"xəbərdar(lıq)?\s+etmədən|müavinət\s+ödənilmir|heç\s+bir\s+müavinət", low):
        articles.append("art:77")
    if articles:
        why = (
            "İşəgötürən əmək müqaviləsinə yalnız Əmək Məcəlləsinin 70-ci maddəsindəki əsaslarla, "
            "xəbərdarlıq və işdənçıxarma müavinəti kimi təminatlarla xitam verə bilər."
        )
        out.append(_f(c, "termination_at_will", tuple(articles), why))
    if "müavinət" in low and "imtina" in low:
        why = (
            "İşçinin qanunla təmin edilmiş işdənçıxarma müavinətindən əvvəlcədən imtinası "
            "etibarsızdır."
        )
        out.append(_f(c, "severance_waiver", ("art:77",), why))
    return out


RULES: tuple[Rule, ...] = (
    probation, working_time, overtime, leave, wages, resignation, dismissal,
)  # fmt: skip


def injection(c: Clause) -> Finding | None:
    verdict = screen(c.text, "document")
    if not verdict.blocked:
        return None
    why = (
        "Bəndin mətnində tərəflərə deyil, sənədi təhlil edən sistemə ünvanlanmış göstəriş var. "
        "Bənd avtomatik təhlil edilmədi; diqqətlə oxuyun."
    )
    return Finding(c.number, "injection", "injection", (), why, "screen")


def check(clauses: Sequence[Clause], ctx: Context | None = None) -> list[Finding]:
    ctx = ctx or context_of(clauses)
    out: list[Finding] = []
    for c in clauses:
        flagged = injection(c)
        if flagged is not None:
            out.append(flagged)
            continue
        low = az_lower(c.text)
        for rule in RULES:
            out.extend(rule(c, low, ctx))
    return out
