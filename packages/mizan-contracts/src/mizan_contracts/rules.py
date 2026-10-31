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
| basic leave, teachers | ≥ 56 days | 118 |
| basic leave, minors | ≥ 35 days (16-17), ≥ 42 (under 16) | 119 |
| unused leave | never waived; compensated on any termination | 135, 144 |
| night work, overtime | none for minors; pregnant women only with written consent | 254, 242 |
| fines | only if a collective agreement provides, ≤ 1/4 of monthly wage | 186 |
| changes to wages | not unilateral, not without a month's notice | 56 |
| waivers of statutory rights | void | 9 |
| wages | in manat; ≤ 20% in kind | 174 |
| deductions | only the listed grounds (no fines); ≤ 20% per payment | 175, 176 |
| resignation | the employee's notice is one calendar month | 69 |
| dismissal | only on the grounds of art. 70, with the guarantees of art. 77 | 70, 77 |
| dismissal | not of pregnant women, parents of children under 3, union members | 79, 16 |

**Facts, not phrasings.** Contracts state the same fact many ways: an age or a birth date, a
term or two dates, "gündə 9 saat" or "08:00–18:00 with an hour for lunch". The rules read the
fact (``Context``, ``daily_hours``) and compare it with the limit. The first version matched
its author's phrasing and found 4 of 29 violations in another author's contracts
(evals/results/f10_contracts/test).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

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
    long_leave_job: str | None  # a job title with longer basic leave (114.3, 118)
    pregnant: bool = False


_WORDS = {
    "bir": 1, "iki": 2, "üç": 3, "dörd": 4, "beş": 5, "altı": 6, "yeddi": 7, "səkkiz": 8,
    "doqquz": 9, "on": 10, "on iki": 12, "on dörd": 14, "on beş": 15, "iyirmi": 20,
    "iyirmi bir": 21, "iyirmi səkkiz": 28, "otuz": 30, "qırx": 40, "qırx səkkiz": 48,
    "əlli": 50, "yarım": 0.5, "bir yarım": 1.5, "on altı": 16, "on yeddi": 17,
}  # fmt: skip
_WORD_RX = "|".join(sorted((re.escape(w) for w in _WORDS), key=len, reverse=True))
_NUM = rf"(\d+(?:[.,]\d+)?|(?<![a-zçəğıöşü])(?:{_WORD_RX})(?![a-zçəğıöşü]))"
_PAREN = r"(?:\s*\([^)]{0,40}\))?"
_DURATION = re.compile(rf"{_NUM}{_PAREN}\s+(?:təqvim\s+|iş\s+)?(gün|həftə|ay|il)")
_HOURS = re.compile(rf"{_NUM}{_PAREN}\s+saat")
_PERCENT = re.compile(rf"{_NUM}{_PAREN}\s*(?:faiz|%)")
_MULTIPLE = re.compile(rf"{_NUM}\s*(?:misli|qat)|(ikiqat|üçqat)")

_DAYS = {"gün": 1.0, "həftə": 7.0, "ay": 30.0, "il": 365.0}

# Jobs with longer basic leave: art. 118 (56 days for teachers) and 114.3 (30 days).
LEAVE_BY_JOB: dict[str, tuple[int, str]] = {
    "müəllim": (56, "art:118"),
    "həkim": (30, "art:114"), "tibb bacısı": (30, "art:114"), "orta tibb": (30, "art:114"),
    "əczaçı": (30, "art:114"), "kənd təsərrüfatı": (30, "art:114"), "laborant": (30, "art:114"),
    "kitabxanaçı": (30, "art:114"), "metodist": (30, "art:114"), "dayə": (30, "art:114"),
    "elmi işçi": (30, "art:114"), "istehsalat təlimi ustası": (30, "art:114"),
}  # fmt: skip

_MONTHS = {
    "yanvar": 1, "fevral": 2, "mart": 3, "aprel": 4, "may": 5, "iyun": 6, "iyul": 7,
    "avqust": 8, "sentyabr": 9, "oktyabr": 10, "noyabr": 11, "dekabr": 12,
}  # fmt: skip
_MONTH_RX = "|".join(_MONTHS)
_DATES = (
    # "1 iyun 2026", "10 dekabr 2009-cu"
    (re.compile(rf"\b(\d{{1,2}})\s+({_MONTH_RX})[a-zçəğıöşü]*\s+(\d{{4}})"), (3, 2, 1)),
    # "2026-cı il noyabrın 3"
    (
        re.compile(rf"\b(\d{{4}})-c[ıiuü]\s+il\s+({_MONTH_RX})[a-zçəğıöşü]*\s+(\d{{1,2}})"),
        (1, 2, 3),
    ),
    # "01.06.2026"
    (re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b"), (3, 2, 1)),
)


def number(token: str) -> float:
    token = token.strip()
    if token in _WORDS:
        return float(_WORDS[token])
    return float(token.replace(",", "."))


def durations_days(text: str) -> list[float]:
    return [number(m.group(1)) * _DAYS[m.group(2)] for m in _DURATION.finditer(text)]


def dates_in(text: str) -> list[date]:
    """Calendar dates in Azerbaijani text, in order of appearance."""
    found: list[tuple[int, date]] = []
    for rx, (y, m, d) in _DATES:
        for match in rx.finditer(text):
            month = match.group(m)
            try:
                when = date(
                    int(match.group(y)),
                    _MONTHS[month] if month in _MONTHS else int(month),
                    int(match.group(d)),
                )
            except ValueError:
                continue
            found.append((match.start(), when))
    return [d for _, d in sorted(found)]


def _years(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def _fmt(n: float) -> str:
    return str(int(n)) if n == int(n) else str(n).replace(".", ",")


def context_of(clauses: Sequence[Clause], as_of: date | None = None) -> Context:
    on = as_of or datetime.now(ZoneInfo("Asia/Baku")).date()
    term: float | None = None
    minor: int | None = None
    job: str | None = None
    pregnant = False
    for c in clauses:
        low = az_lower(c.text)
        if term is None and "bağlan" in low and ("müddətinə" in low or "müddətli" in low):
            days = durations_days(low)
            if days:
                term = round(max(days) / 30, 1)
        if term is None and "müqavilə" in low and re.search(r"(?:dək|qədər)\b", low):
            # "1 iyun 2026-cı ildən 31 iyul 2026-cı ilədək bağlanır"
            span = dates_in(low)
            if len(span) >= 2 and span[1] > span[0]:
                term = round(((span[1] - span[0]).days + 1) / 30.4, 1)
        if minor is None:
            m = re.search(r"\((1[0-7])\s*yaş", low) or re.search(r"(1[0-7])\s*yaşında", low)
            if m:
                minor = int(m.group(1))
            elif "doğum tarixi" in low or "təvəllüd" in low:
                born = dates_in(low)
                if born and _years(born[0], on) < 18:
                    minor = _years(born[0], on)
        if job is None and ("vəzifəsinə" in low or "peşəsinə" in low or "işə qəbul" in low):
            job = next((k for k in LEAVE_BY_JOB if k in low), None)
        if re.search(r"\bhamilə", low) and not re.search(r"hamilə\s+(?:qadınlar|işçilər)", low):
            pregnant = True
    return Context(term, minor, job, pregnant)


Rule = Callable[[Clause, str, Context], list[Finding]]


def _f(c: Clause, issue: str, articles: tuple[str, ...], why: str) -> Finding:
    return Finding(c.number, "violation", issue, articles, why, "rule")


def probation(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if "sınaq" not in low and "sınağ" not in low:
        return []
    days = durations_days(low)
    if not days:
        return []
    # "üç aylıq sınaq … əlavə üç ay uzadıla bilər": an extension adds up.
    longest = sum(days) if "uzad" in low else max(days)
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


_TIME = r"(\d{1,2})[:.](\d{2})"
_SPAN = re.compile(rf"{_TIME}\s*(?:[–—-]|-?dan\s+|-?dən\s+)\s*{_TIME}")
_START_END = re.compile(rf"{_TIME}[^.;]*?başla[^.;]*?{_TIME}[^.;]*?qurtar")
_OVERTIME = re.compile(r"iş\s+vaxtından\s+artıq|artıq\s+iş|əlavə\s+iş")


def _hours(match: re.Match[str], first: int) -> float:
    start = int(match.group(first)) + int(match.group(first + 1)) / 60
    end = int(match.group(first + 2)) + int(match.group(first + 3)) / 60
    return (end - start) % 24


def daily_hours(low: str) -> list[float]:
    """Hours of work per day: stated ("hər gün 9 saat") or from a schedule minus lunch."""
    stated = [
        number(m.group(1))
        for m in re.finditer(rf"(?:gündə|hər\s+gün)\s+{_NUM}{_PAREN}\s+saat", low)
    ]
    if stated:
        return stated
    spans = [_hours(m, 1) for m in _SPAN.finditer(low)]
    se = _START_END.search(low)
    if se:
        spans.insert(0, _hours(se, 1))
    if not spans:
        return []
    work, *rest = spans
    lunch = 0.0
    if "nahar" in low or "fasilə" in low:
        stated_break = re.search(rf"{_NUM}{_PAREN}\s+saat(?:lıq)?\s+(?:nahar|fasilə)", low)
        if stated_break:
            lunch = number(stated_break.group(1))
        elif rest:
            lunch = rest[0]
    return [round(work - lunch, 2)] if work > lunch else []


def working_time(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if ("saat" not in low and not _SPAN.search(low)) or _OVERTIME.search(low):
        return []
    daily = daily_hours(low)
    weekly = [
        number(m.group(1))
        for m in re.finditer(rf"həftə(?:də|\s+ərzində)(?:[^.;]*?\s)?{_NUM}{_PAREN}\s+saat", low)
    ]
    days = re.search(
        rf"(?:həftədə|iş\s+həftəsi)\s+(?:[^.;]*?\s)?{_NUM}{_PAREN}\s+gün", low
    ) or re.search(rf"həftə[^.;]*?{_NUM}{_PAREN}\s+gündən\s+ibarət", low)
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
    if not _OVERTIME.search(low):
        return []
    out: list[Finding] = []
    pair = re.search(
        rf"iki\s+(?:ardıcıl\s+)?(?:iş\s+)?günü?\s+ərzində\s+{_NUM}{_PAREN}\s+saat", low
    )
    per_day = re.search(rf"(?:hər\s+gün|gündə|hər\s+birində)\s+{_NUM}{_PAREN}\s+saat", low)
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
    out: list[Finding] = []
    if "imtina" in low:
        why = (
            "İşçinin əmək məzuniyyətindən imtinası etibarsızdır; məzuniyyətin verilməməsi "
            "qadağandır."
        )
        out.append(_f(c, "leave_waiver", ("art:135",), why))
    if "istifadə edilməmiş" in low and re.search(r"verilmir|ödənilmir|ödənmir", low):
        why = (
            "Əmək müqaviləsinə xitam verilərkən istifadə edilməmiş əsas məzuniyyətə görə "
            "kompensasiya hər hansı şərt qoyulmadan ödənilməlidir."
        )
        out.append(_f(c, "unused_leave_compensation", ("art:144",), why))
    if out or ("əlavə" in low and "əsas" not in low) or "uşağ" in low or "doğuş" in low:
        return out
    days = [number(m.group(1)) for m in re.finditer(rf"{_NUM}{_PAREN}\s+(?:təqvim\s+)?gün", low)]
    if not days:
        return []
    least = min(days)
    need, article, who = 21, "art:114", "işçi"
    if ctx.minor_age is not None:
        need, article = (42 if ctx.minor_age < 16 else 35), "art:119"
        who = f"{ctx.minor_age} yaşlı işçi"
    if ctx.long_leave_job is not None and LEAVE_BY_JOB[ctx.long_leave_job][0] > need:
        need, article = LEAVE_BY_JOB[ctx.long_leave_job]
        who = f"«{ctx.long_leave_job}»"
    if least < need:
        why = (
            f"Ödənişli əsas məzuniyyət {who} üçün ən azı {need} təqvim günüdür; "
            f"bənddə {_fmt(least)} gün yazılıb."
        )
        return [_f(c, "leave_too_short", (article,), why)]
    return []


_FOREIGN = re.compile(r"dollar|avro|euro|\busd\b|\beur\b|rubl|lirə|\$|€")


def wages(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if "əmək haqq" not in low and "əməkhaqq" not in low and "maaş" not in low:
        return []
    out: list[Finding] = []
    if _FOREIGN.search(low):
        why = "Əmək haqqı Azərbaycan Respublikasının pul vahidi – manatla ödənilir."
        out.append(_f(c, "wage_currency", ("art:174",), why))
    percents = [number(m.group(1)) for m in _PERCENT.finditer(low)]
    if ("natura" in low or "mallarla" in low) and any(p > 20 for p in percents):
        why = "Əmək haqqının natura formasında verilən hissəsi 20 faizdən çox ola bilməz."
        out.append(_f(c, "wage_in_kind", ("art:174",), why))
    # "nəzərdə tutulur" (provided for) is not a deduction.
    if re.search(r"\btut(ul|a\b|ar|ma)", low.replace("nəzərdə tutul", "")):
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
    employee_notice = ("xəbərdar" in low and "işəgötürəni" in low) or (
        "ərizə" in low and re.search(r"öz\s+təşəbbüsü|işdən\s+çıx|xitam", low)
    )
    if not employee_notice:
        return []  # only the employee's notice to the employer
    if "tarixsiz" in low:
        why = (
            "Əmək müqaviləsinə işçinin təşəbbüsü ilə xitam onun öz iradəsi ilə verilir; "
            "əvvəlcədən tarixsiz ərizə tələb etmək işçini işdən çıxmağa məcbur etməkdir."
        )
        return [_f(c, "forced_resignation", ("art:69",), why)]
    days = durations_days(low)
    if days and max(days) > 31:
        why = (
            "İşçi əmək müqaviləsinə bir təqvim ayı qabaqcadan xəbərdar etməklə xitam verə "
            "bilər; daha uzun müddət tələb edilə bilməz."
        )
        return [_f(c, "resignation_notice", ("art:69",), why)]
    return []


_NIGHT = re.compile(r"gecə|\b(?:2[0-3]|0?[0-6])[:.]\d{2}\s*[–—-]\s*(?:0?[0-9])[:.]\d{2}")
_NO_CONSENT = re.compile(
    r"razılığı\s+olmadan|razılıq\s+tələb\s+(?:olunmur|edilmir)|razılığı\s+tələb\s+(?:olunmur|edilmir)"
)


def protected_hours(c: Clause, low: str, ctx: Context) -> list[Finding]:
    """Night work and overtime: never for minors (254), pregnant women only by consent (242)."""
    hours = [(_hours(m, 1), int(m.group(1))) for m in _SPAN.finditer(low)]
    late = any(start >= 20 or start + span > 22 for span, start in hours)
    night = bool(_NIGHT.search(low)) or late
    extra = bool(_OVERTIME.search(low)) or "istirahət günü" in low
    if not (night or extra):
        return []
    assigned = re.search(r"cəlb|təyin|məcburi|iştirak\s+etməlidir|işləyir|işləməlidir", low)
    if ctx.minor_age is not None and assigned and "qadağan" not in low:
        why = (
            "18 yaşından az işçinin gecə vaxtı (20:00–07:00), iş vaxtından artıq və istirahət "
            "günlərində işə cəlb edilməsi qadağandır."
        )
        return [_f(c, "minor_night_or_overtime", ("art:254",), why)]
    pregnant = ctx.pregnant or "hamilə" in low
    if pregnant and _NO_CONSENT.search(low):
        why = (
            "Hamilə qadının gecə işinə və iş vaxtından artıq işə cəlb edilməsinə yalnız onun "
            "yazılı razılığı və həkim rəyi ilə yol verilir."
        )
        return [_f(c, "pregnant_night_or_overtime", ("art:242",), why)]
    return []


def discipline(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if "cərimə" not in low:
        return []
    provided = re.search(r"kollektiv\s+müqavilə(?:də|si\s+ilə)\s+nəzərdə\s+tutul", low)
    quarter = re.search(r"dörddə\s+bir|1/4|25\s*faiz", low)
    if provided and quarter:
        return []
    why = (
        "Cərimə intizam tənbehi kimi yalnız kollektiv müqavilədə nəzərdə tutulduqda və aylıq "
        "əmək haqqının 1/4-dən çox olmamaqla tətbiq edilə bilər."
    )
    return [_f(c, "fine", ("art:186",), why)]


def waivers_and_changes(c: Clause, low: str, ctx: Context) -> list[Finding]:
    out: list[Finding] = []
    waiver = re.search(r"hüququndan\s+(?:tam\s+)?imtina|hüquqlarından\s+(?:tam\s+)?imtina", low)
    if waiver and "məzuniyyət" not in low and "müavinət" not in low:
        why = (
            "İşçinin Əmək Məcəlləsi ilə təmin edilmiş hüquqlarından (məhkəməyə, əmək "
            "müfəttişliyinə müraciət, əmək haqqı tələbi və s.) imtinası etibarsızdır."
        )
        out.append(_f(c, "rights_waiver", ("art:9",), why))
    if (
        re.search(r"birtərəfli", low)
        and re.search(r"azal|dəyiş", low)
        and ("əmək haqq" in low or "əməkhaqq" in low or "maaş" in low)
    ):
        why = (
            "Əmək şəraitinin şərtləri, o cümlədən əmək haqqı, işçi azı bir ay əvvəl yazılı "
            "xəbərdar edilmədən birtərəfli dəyişdirilə bilməz."
        )
        out.append(_f(c, "unilateral_change", ("art:56",), why))
    return out


def dismissal(c: Clause, low: str, ctx: Context) -> list[Finding]:
    if "xitam" not in low and "işdən azad" not in low:
        return []
    out: list[Finding] = []
    allowed = re.search(
        r"xitam\s+ver(ə|il[əe])\s+bil(ər|ər\b)|xitam\s+verilir|işdən\s+azad\s+edilə\s+bilər", low
    )
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
    if "həmkarlar ittifaq" in low and not forbidden and re.search(r"xitam|azad", low):
        why = (
            "Həmkarlar ittifaqına üzvlüyə görə ayrı-seçkilik və bu motivlə əmək müqaviləsinə "
            "xitam verilməsi qadağandır."
        )
        out.append(_f(c, "union_dismissal", ("art:16", "art:79"), why))
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
    probation, working_time, overtime, protected_hours, leave, wages, discipline, resignation,
    dismissal, waivers_and_changes,
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


def check(
    clauses: Sequence[Clause], ctx: Context | None = None, as_of: date | None = None
) -> list[Finding]:
    ctx = ctx or context_of(clauses, as_of)
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
