"""Builders for small documents in e-qanun's Word-HTML dialect.

The real files are 5-10 MB; these reproduce the markup patterns that matter (centred
headings, bold or <h3> article headings, endnote anchors and bodies, annexes) in a few lines.
"""

from __future__ import annotations

from html import escape

HEAD = (
    '<html><head><meta http-equiv=Content-Type content="text/html; charset=windows-1251">'
    "<title>Az&#601;rbaycan Respublikas&#305; Prezidentinin F&#601;rman&#305;</title></head>"
    "<body><div class=WordSection1>"
)
TAIL = "</body></html>"


def centered(text: str, *, bold: bool = True) -> str:
    inner = f"<b><span>{escape(text)}</span></b>" if bold else f"<span>{escape(text)}</span>"
    return f"<p class=MsoNormal align=center style='text-align:center'>{inner}</p>"


def article(number: str, title: str, *, edn: int | None = None, h3: bool = False) -> str:
    ref = f'<a href="#_edn{edn}" name="_ednref{edn}">[{edn}]</a>' if edn else ""
    if h3:
        return f"<h3><span class=MaddeChar>Madd&#601; {number}.</span> {escape(title)}{ref}</h3>"
    return (
        "<p class=MsoNormal style='text-align:justify'>"
        f"<b><span>Madd&#601; {number}. {escape(title)}</span></b>{ref}</p>"
    )


def para(text: str, *, edn: int | None = None) -> str:
    ref = f'<a href="#_edn{edn}" name="_ednref{edn}"><span>[{edn}]</span></a>' if edn else ""
    return f"<p class=MsoNormal style='text-align:justify'><span>{escape(text)}</span>{ref}</p>"


def endnote(number: int, text: str) -> str:
    return (
        f"<div id=edn{number}><p class=MsoEndnoteText>"
        f'<a href="#_ednref{number}">[{number}]</a> {escape(text)}</p></div>'
    )


def document(*blocks: str, endnotes: dict[int, str] | None = None) -> str:
    # As in Word's output: the body section, then the endnotes, inside <body>.
    notes = "".join(endnote(n, t) for n, t in (endnotes or {}).items())
    return HEAD + "".join(blocks) + "</div>" + (f"<div>{notes}</div>" if notes else "") + TAIL


def encode(html: str, encoding: str = "windows-1254") -> bytes:
    return html.encode(encoding, errors="xmlcharrefreplace")


ACT_2025 = (
    "30 dekabr 2025-ci il tarixli 332-VIIQD nömrəli Azərbaycan Respublikasının Qanunu "
    "ilə 69-cu maddənin adı yeni redaksiyada verilmişdir."
)
ACT_2007 = (
    "1 oktyabr 2007-ci il tarixli 424-IIIQD nömrəli Azərbaycan Respublikası Qanunu "
    "ilə üçüncü hissəyə sözlər əlavə edilmişdir."
)


def labour_code_excerpt() -> str:
    """Every structural pattern of the Labour Code, in miniature."""
    return document(
        centered("AZƏRBAYCAN RESPUBLİKASININ ƏMƏK MƏCƏLLƏSİ"),
        centered(
            "(Bu Məcəllə 1 fevral 1999-cu il tarixli 618-IQ nömrəli Qanunu ilə təsdiq edilmişdir)",
            bold=False,
        ),
        para("Əmək fərdi və ictimai rifahın əsasıdır."),
        centered("I bölmə"),
        centered("Ümumi normalar"),
        centered("Birinci fəsil"),
        centered("Əsas müddəalar"),
        article("1", "Əmək qanunvericiliyi sistemi"),
        para("Əmək qanunvericiliyi sistemi:"),
        para("bu Məcəllədən ibarətdir."),
        centered("III bölmə"),
        centered("Əmək müqaviləsi"),
        centered("On birinci fəsil"),
        centered("Əmək müqaviləsinə xitam verilməsi"),
        article("69", "Əmək müqaviləsinə işçinin təşəbbüsü ilə xitam verilməsi", edn=204),
        para("1. İşçi bir təqvim ayı qabaqcadan işəgötürəni xəbərdar etməlidir."),
        para("2. Ərizə verildiyi gündən bir ay bitdikdən sonra işçi işə çıxmaya bilər."),
        para("3. Üzrlü səbəblər olduqda müddət qısaldılır:", edn=206),
        para("3.1. təhsilini davam etdirdikdə;"),
        para("3.1.1. əyani təhsil alanda;"),
        para("3.2. yeni yaşayış yerinə köçdükdə."),
        para("3-1. Diplomatik nümayəndəlikdə xidmət keçən işçinin ailə üzvü."),
        para("a) birinci hal;"),
        para("b) ikinci hal."),
        para("Bu hissə ilə müəyyən edilmiş qaydalar tətbiq edilir."),
        article("69-1", "Əlavə maddə", h3=True),
        para("Mətn."),
        centered("Azərbaycan Respublikasının Əmək Məcəlləsinə"),
        centered("ƏLAVƏLƏR"),
        centered("Azərbaycan Respublikasının Əmək Məcəlləsinə"),
        centered("1-ci əlavə"),
        centered("Əmək müqaviləsinin nümunəvi forması"),
        centered("§1. Tərəflər haqqında məlumatlar"),
        para("1.1. İşəgötürən ________"),
        para("1.2. İşçi ________"),
        centered("İSTİFADƏ OLUNMUŞ MƏNBƏ SƏNƏDLƏRİNİN SİYAHISI"),
        para("Məcəlləyə əlavə və dəyişikliklər etmiş qanunlar"),
        para("1. 20 fevral 2001-ci il tarixli 88-IIQD nömrəli Qanun"),
        endnotes={204: ACT_2025, 206: ACT_2007},
    )
