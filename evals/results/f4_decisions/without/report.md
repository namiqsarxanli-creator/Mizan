# Generation evaluation

Questions: 11 (failed: 0). Judge-based numbers are uncalibrated until `mizan-eval kappa` reaches the target.

| Metric | Value |
|---|---|
| Answer correct (all) | 0.273 [0.00, 0.55] (n=11) |
| Answer correct (in scope) | 0.273 [0.00, 0.55] (n=11) |
| Abstention on out-of-scope | n/a |
| Cites a gold article | 0.909 [0.73, 1.00] (n=11) |
| Citation valid (verbatim) | 0.7727272727272727 |
| p50 latency (ms) | 2821.3 |

## Answer correct by question type

| Type | Correct |
|---|---|
| court_decision | 0.273 [0.00, 0.55] (n=11) |

## Incorrect answers

- `cd01` (court_decision) İşdən çıxarkən istifadə etmədiyim əmək stajına görə əlavə məzuniyyət günləri üçün mənə kompensasiya ödənilməlidirmi? — judge: The answer incorrectly states that compensation is not payable, contrary to the Constitutional Court decision that removed the prohibition.
- `cd02` (court_decision) Uşaqlı qadın kimi istifadə etmədiyim əlavə məzuniyyət günlərinin pulunu işdən çıxanda ala bilərəmmi? — judge: The answer incorrectly states that compensation is not allowed, ignoring the 18 Feb 2026 Constitutional Court ruling that lifts the prohibition on compensating unused additional leave.
- `cd03` (court_decision) Maaşım gecikdirilib və işəgötürənlə mübahisəmiz var. Gecikmiş günlər üçün mənə ödənc düşürmü və nə vaxta qədər məhkəməyə müraciət edə bilərəm? — judge: The answer incorrectly states the claim period as one month instead of the correct one year.
- `cd04` (court_decision) Şirkətin filialı bağlananda filialda işləyənlərin əmək müqaviləsinə hansı əsasla xitam verilir? — judge: The answer incorrectly applies 70(a) for all cases, ignoring the distinction that 70(b) applies when the contract is with the owner or their appointed manager during branch closure.
- `cd05` (court_decision) İşçi iş yerində cinayət törətməkdə şübhəli bilinirsə, işəgötürən onu hansı hallarda işdən kənarlaşdıra bilər? — judge: The answer omits the requirement that the criminal process be underway (suspect/accused status or administrative act) and incorrectly cites unrelated provisions.
- `cd06` (court_decision) Kreditorla bağladığım könüllü razılaşmaya əsasən maaşımdan ən çox neçə faiz tutula bilər? — judge: The answer incorrectly states a 20% limit, whereas the Constitutional Court decision requires a 50% limit for voluntary creditor agreements.
- `cd08` (court_decision) Heç bir xüsusi səbəb olmadan, sadəcə işçi ilə razılaşaraq müddətli əmək müqaviləsi bağlamaq olarmı? — judge: The answer incorrectly denies the possibility of a fixed‑term contract solely by mutual agreement, contradicting Article 47(f) and the Constitutional Court ruling.
- `cd09` (court_decision) Şirkətin əsas fəaliyyətinə aid işi görən şəxslə mülki müqavilə əvəzinə əmək müqaviləsi bağlamaq məcburidirmi? — judge: The answer incorrectly declares the contract mandatory, ignoring the Constitutional Court ruling that requires court determination of the relationship’s nature.
