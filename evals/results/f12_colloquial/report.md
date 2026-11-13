# F12 results: everyday wording

- Generated: 2026-11-13 18:35 UTC; protocol: `PROTOCOL.md`.
- Dense `bge-m3`, article chunks, top 10 chunks per query; metrics at article level.
- Δ: paired bootstrap over questions (10000 resamples, seed 0).

## colloquial (n = 60)

| Variant | Hit@5 | Recall@5 | MRR@10 | Δ Recall@5 vs V0 [95% CI] |
|---|---|---|---|---|
| V0 baseline | 0.933 | 0.933 | 0.864 | - |
| V1 refs | 0.933 | 0.933 | 0.864 | +0.000 [+0.000, +0.000] |
| V2 rewrite | 0.900 | 0.900 | 0.783 | -0.033 [-0.083, +0.000] |
| V3 multi-query | 0.933 | 0.933 | 0.825 | +0.000 [-0.050, +0.050] |

**V1 refs: Hit@5 changes vs V0** (0)


**V2 rewrite: Hit@5 changes vs V0** (2)

- broken: `c054` Müdirim hansı hallarda iş yerini bağlayıb, bizi işdən kənarlaşdıra bilər?
- broken: `c058` Məhkəmə işi mən udduqdan sonra müdirim qərarı yerinə yetirməzsə, nə baş verəcək?

**V3 multi-query: Hit@5 changes vs V0** (2)

- fixed: `c002` Müdirim mənimlə hələ də yazılı müqavilə bağlamayıb, bu nə vaxta qədər belə davam edə bilər?
- broken: `c054` Müdirim hansı hallarda iş yerini bağlayıb, bizi işdən kənarlaşdıra bilər?

## v1 (n = 185)

| Variant | Hit@5 | Recall@5 | MRR@10 | Δ Recall@5 vs V0 [95% CI] |
|---|---|---|---|---|
| V0 baseline | 0.973 | 0.946 | 0.894 | - |
| V1 refs | 0.978 | 0.951 | 0.902 | +0.005 [+0.000, +0.016] |
| V2 rewrite | 0.968 | 0.938 | 0.868 | -0.008 [-0.041, +0.024] |
| V3 multi-query | 0.978 | 0.943 | 0.890 | -0.003 [-0.022, +0.019] |

**V1 refs: Hit@5 changes vs V0** (1)

- fixed: `q121` Əmək Məcəlləsinin 69-cu maddəsi nə barədədir?

**V2 rewrite: Hit@5 changes vs V0** (9)

- fixed: `q002` Müdir məni heç bir səbəb göstərmədən işdən çıxara bilərmi?
- fixed: `q014` Uzun illər eyni yerdə işləyənə əlavə istirahət günləri düşürmü?
- broken: `q039` Pozuntunu aşkar edəndən sonra işəgötürən neçə müddət ərzində töhmət verə bilər?
- broken: `q041` Töhmət nə vaxt götürülür və qüvvədən düşür?
- broken: `q056` İşəgötürən işçiləri müəssisəyə buraxmayıb işi dayandıra bilərmi?
- fixed: `q068` İşdən çıxmaq üçün işəgötürəni nə qədər əvvəl xəbərdar etməliyəm?
- fixed: `q079` Qanundakı müddətin son günü istirahət gününə düşərsə, müddət nə vaxt bitir?
- broken: `q147` Töhmət hansı qaydada verilir və nə vaxt götürülür?
- broken: `q194` İşəgötürən maaşın 80 faizini borca görə tuta bilər, eləmi?

**V3 multi-query: Hit@5 changes vs V0** (3)

- fixed: `q014` Uzun illər eyni yerdə işləyənə əlavə istirahət günləri düşürmü?
- broken: `q039` Pozuntunu aşkar edəndən sonra işəgötürən neçə müddət ərzində töhmət verə bilər?
- fixed: `q079` Qanundakı müddətin son günü istirahət gününə düşərsə, müddət nə vaxt bitir?
