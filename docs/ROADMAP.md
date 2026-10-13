# 🏛️ Mizan — Tam Roadmap (24 həftə)

> **Mizan** — Azərbaycan qanunvericiliyi üzrə agentli, ölçülə bilən və production səviyyəli hüquqi tədqiqat platforması.
> RAG + Multi-agent + Observability + Knowledge Graph + öz ML modelləri.

---

## 📌 Əsas prinsiplər

1. **Əvvəl ölç, sonra mürəkkəbləşdir.** Hər yeni komponent eval-da baseline-dan yaxşı olduğunu sübut etməlidir.
2. **Hər fazanın "Definition of Done"-u var.** Meyar ödənməyibsə, növbəti fazaya keçilmir.
3. **Hər əhəmiyyətli qərar ADR-də sənədləşdirilir.**
4. **Hər faza sonunda işlək demo olur.** Yarımçıq branch yox, `main`-də işləyən sistem.

---

## 🧱 Texnologiya stack-i

| Qat | Texnologiya |
|---|---|
| Dil / tooling | Python 3.12, `uv`, `ruff`, `mypy --strict`, `pytest` |
| API | FastAPI, Pydantic v2, SSE streaming |
| Async işlər | Redis + Arq → (F9) Temporal |
| Verilənlər | PostgreSQL 16 (+ RLS), Qdrant, Neo4j, ClickHouse (traces) |
| LLM | Claude API (Haiku / Sonnet / Opus routing) |
| ML | PyTorch, `sentence-transformers`, `transformers`, `peft`, `bitsandbytes`, vLLM |
| Eval | Öz eval framework-ün + RAGAS, W&B (təcrübə izləmə) |
| Observability | OpenTelemetry (GenAI semantic conventions), Grafana |
| Frontend | Next.js (chat + dashboard) |
| Infra | Docker Compose → Kubernetes / Fly.io, GitHub Actions |
| Agent ekosistemi | MCP server (Python SDK) |

---

## 🗺️ Ümumi baxış

| Faza | Həftə | Mövzu |
|---|---|---|
| F0 | 1 | Təməl və mühəndislik infrastrukturu |
| F1 | 2–3 | Ingestion pipeline + temporal versiyalama |
| F2 | 4–5 | Baseline retrieval (hybrid + rerank) |
| F3 | 6–7 | Eval təməli: golden dataset, CI gate, judge kalibrasiyası |
| F4 | 8–9 | Knowledge Graph + məhkəmə qərarları |
| F5 | 10–12 | Multi-agent sistemi |
| F6 | 13–14 | Observability platforması |
| F7 | 15–17 | Embedding fine-tune + reranker distillation |
| F8 | 18–20 | LLM fine-tune (verifier, citation extractor, query rewriter) |
| F9 | 21–22 | Platforma: MCP, durable execution, cache, shadow deploy |
| F10 | 23 | Müqavilə analizi + təhlükəsizlik |
| F11 | 24 | AzLegalBench buraxılışı, load test, launch |

---

## F0 — Təməl (Həftə 1)

**Məqsəd:** kod yazmağa başlamazdan əvvəl "senior skeleti" qurmaq.

- [x] Monorepo strukturu (`apps/`, `packages/`, `evals/`, `docs/adr/`, `infra/`)
- [x] `uv` workspace, `ruff`, `mypy --strict`, `pre-commit`
- [x] Docker Compose: Postgres, Qdrant, Redis, Neo4j
- [x] GitHub Actions: lint → type check → test
- [x] Konfiqurasiya: `pydantic-settings`, `.env.example`, secrets heç vaxt repoda olmur
- [x] Strukturlaşdırılmış logging (`structlog`, JSON format)
- [x] Tracing SDK skeleti: `trace_id` sorğu başından sonuna qədər ötürülür
- [x] **ADR-001:** Monorepo və texnologiya seçimi
- [x] **ADR-002:** Vektor DB seçimi (Qdrant vs pgvector)

**✅ DoD:** `docker compose up` ilə hər şey qalxır, CI yaşıldır, `/health` endpoint-i trace yaradır.

---

## F1 — Ingestion pipeline (Həftə 2–3) ✅

**Məqsəd:** hüquqi mətnləri strukturlu, versiyalanmış və təkrar emal oluna bilən şəkildə saxlamaq.

### Həftə 2 — Parse və struktur
- [x] Mənbə: e-qanun.az. `robots.txt` yoxdur; sayt yalnız hotlink qoruması tətbiq edir. Dürüst User-Agent, 1.5 s rate limit, tarixi versiyalar üçün disk cache
- [x] Başlanğıc korpusu: **Əmək Məcəlləsi**, 1999–2026 arası 14 versiya *(Mülki, Cinayət, Konstitusiya: `tracked_documents`-ə ID əlavə etmək kifayətdir)*
- [x] İyerarxik parser: Bölmə → Fəsil → Maddə → Hissə → Bənd → Yarımbənd → hərfli bənd (a, b) + Əlavələr (§)
- [x] Data modeli: stabil açarlar (`art:69/part:3-1/item:a`), `parent_key`, sıra, dərinlik
- [x] Unit testlər: real markup nümunələrindən fixture-lar (kodlaşdırma, struktur, endnote-lar, chunking)

### Həftə 3 — Versiyalama və pipeline
- [x] **Temporal model:** `valid_from` / `valid_to` (törəmə), tarixsiz cari mətn üçün `valid_from_estimated`, endnote-lardan dəyişdirən akt (nömrə + tarix)
- [x] SHA-256 ilə idempotent ingestion + `PARSER_VERSION` artanda avtomatik reparse + yetim mətnlərin təmizlənməsi
- [x] Arq worker: gecəlik cron + tələb üzrə `sync_document` *(embed → index F2-yə keçdi: model eval ilə seçilir, ADR-0003)*
- [~] Retry siyasəti: HTTP səviyyəsində backoff + jitter, job səviyyəsində 3 cəhd. *Ayrıca DLQ → F9 (Temporal)*
- [x] Chunking strategiyaları: maddə (parent-child), hissə, fixed (baseline). *Semantic chunking embedding tələb edir → F2*
- [x] **ADR-0003:** Temporal versiyalama modeli

**✅ DoD:** Əmək Məcəlləsinin bütün maddələri versiyaları ilə DB-dədir, pipeline ikinci dəfə işə salındıqda heç nəyi təkrar emal etmir.

**Nəticə:** 14 versiya, 23 101 provision, 2 487 unikal mətn (89% dedup). Təkrar sync: `{'unchanged': 14}`, yalnız cari mətn yüklənir. `mizan-ingest as-of 46943 2021-06-01 --key art:69` həmin tarixdə qüvvədə olan mətni qaytarır.

---

## F2 — Baseline retrieval (Həftə 4–5)

**Məqsəd:** ölçülə bilən, güclü retrieval baseline-ı.

### Həftə 4
- [ ] Dense axtarış: `bge-m3` və `multilingual-e5-large` müqayisəsi
- [ ] Sparse axtarış: BM25 (Azərbaycan dili üçün tokenizasiya və stemming problemləri)
- [ ] Hybrid: **Reciprocal Rank Fusion**
- [ ] Temporal filter: `as_of_date` parametri

### Həftə 5
- [ ] Reranker: `bge-reranker-v2-m3`
- [ ] Parent-child retrieval: kiçik chunk ilə axtarış, LLM-ə bütöv maddə verilir
- [ ] Query rewriting (hələlik Claude Haiku ilə)
- [ ] İlk 50 sual üzərində mini-eval: Recall@5, MRR
- [ ] Sadə RAG endpoint-i: `/ask` (single-shot, istinadlarla)

**✅ DoD:** Recall@5 ≥ 0.75. Hər chunking və axtarış variantının nəticəsi cədvəldə qeyd olunub.

---

## F3 — Eval təməli (Həftə 6–7)

**Məqsəd:** bundan sonrakı hər qərarı rəqəmlə əsaslandırmaq.

### Həftə 6 — Dataset
- [ ] Golden dataset: **200+ sual** (mümkünsə hüquqşünasla yoxlanılır)
  - Sadə faktiki / multi-hop / temporal / cavabı olmayan / tələ suallar
- [ ] Hər sual üçün: gözlənilən maddələr, istinad cavabı, çətinlik dərəcəsi
- [ ] Dataset versiyalanır (`evals/datasets/v1/`)

### Həftə 7 — Framework və CI
- [ ] Retrieval metrikləri: Recall@k, MRR, nDCG
- [ ] Generation metrikləri: faithfulness, citation accuracy, answer correctness
- [ ] **Judge kalibrasiyası:** 100 cavab həm insan, həm LLM tərəfindən qiymətləndirilir, sonra Cohen's κ hesablanır
- [ ] Bootstrap confidence interval (statistik əhəmiyyət)
- [ ] **CI eval gate:** faithfulness və ya Recall@5 baseline-dan statistik əhəmiyyətli şəkildə düşərsə, PR bloklanır
- [ ] Eval nəticələri PR-a avtomatik şərh kimi yazılır
- [ ] **ADR-004:** Eval metodologiyası

**✅ DoD:** `make eval` tam hesabat verir, CI gate işləyir, judge-insan uyğunluğu κ ≥ 0.6.

---

## F4 — Knowledge Graph + məhkəmə qərarları (Həftə 8–9)

**Məqsəd:** oxşar mətndən əlavə, normalar arasındakı əlaqələri də anlamaq.

### Həftə 8 — Qraf
- [ ] Citation extraction (hələlik Claude ilə, F8-də öz modelin əvəz edəcək): `"70-ci maddəyə uyğun"` → `{code, article}`
- [ ] Neo4j sxemi: `(:Article)-[:REFERENCES]->(:Article)`, `[:AMENDS]`, `[:SUPERSEDES]`
- [ ] Graph-aware retrieval: tapılan maddələrin 1–2 hop qonşuları da gətirilir
- [ ] Eval: multi-hop suallarda F2 baseline ilə müqayisə

### Həftə 9 — Case law və ziddiyyətlər
- [ ] Ali Məhkəmə qərarlarının ingestion-u (açıq mənbələrdən)
- [ ] `(:CourtDecision)-[:INTERPRETS]->(:Article)` əlaqələri
- [ ] **Ziddiyyət aşkarlama:** *lex specialis* və *lex posterior* qaydaları
- [ ] Amendment diff: `/diff?code=ƏM&from=2022&to=2025`
- [ ] **ADR-005:** Niyə ayrıca qraf DB (Neo4j vs Postgres recursive CTE)

**✅ DoD:** Multi-hop suallarda Recall@5 baseline-dan ≥ 10% yaxşıdır. Ziddiyyət aşkarlamanın 20 test ssenarisi keçir.

---

## F5 — Multi-agent sistemi (Həftə 10–12)

**Məqsəd:** mürəkkəb hüquqi sualları planlaşdıran, araşdıran və özünü yoxlayan sistem.

### Həftə 10 — Orkestrasiya
- [ ] State machine: `Planner → Researcher(s) → Drafter → Verifier → Judge`
- [ ] Agentlər arası məlumat ötürülməsi yalnız Pydantic sxemləri ilə
- [ ] Hər addım üçün limitlər: max iterasiya, timeout, token büdcəsi
- [ ] **ADR-006:** LangGraph vs öz orkestratorun

### Həftə 11 — Tool-lar və agentlər
- [ ] Tool-lar: `search_law`, `get_article`, `get_related_articles` (qraf), `get_court_decisions`, `check_conflicts`
- [ ] Researcher-lərin paralel işləməsi (`asyncio.gather` + semaphore)
- [ ] Verifier: claim → evidence uyğunlaşdırması, `supported / contradicted / not_enough_info`
- [ ] Judge: cavab vermək, imtina etmək ("əmin deyiləm") və ya yenidən axtarış

### Həftə 12 — LLM Gateway və etibarlılıq
- [ ] Model routing: Haiku (planner, verifier), Sonnet (drafter), Opus (çətin hallar)
- [ ] Prompt caching
- [ ] Retry + exponential backoff + jitter, circuit breaker
- [ ] Graceful degradation: komponent çöksə, sistem azaldılmış rejimdə işləməyə davam edir
- [ ] Sorğu və tenant başına xərc büdcəsi
- [ ] SSE ilə addım-addım streaming ("Maddələr axtarılır..." → "Yoxlanılır...")
- [ ] **Kalibrə olunmuş inam:** confidence score + selective prediction (imtina həddi)

**✅ DoD:** Faithfulness ≥ 0.90, citation accuracy ≥ 0.90. Agent versiyası baseline RAG ilə müqayisə cədvəlində statistik əhəmiyyətli şəkildə üstündür.

---

## F6 — Observability platforması (Həftə 13–14)

**Məqsəd:** production-da nə baş verdiyini görmək və keyfiyyət düşüşünü avtomatik aşkarlamaq.

### Həftə 13 — Tracing və saxlama
- [ ] **OpenTelemetry GenAI semantic conventions** (öz formatın əvəzinə)
- [ ] Trace → span iyerarxiyası: sorğu → agent addımı → LLM / tool çağırışı
- [ ] Hər span-da: token, xərc, latency, model, prompt versiyası
- [ ] ClickHouse-da saxlama + retention siyasəti
- [ ] PII redaction (loglarda şəxsi məlumat saxlanılmır)

### Həftə 14 — Dashboard və online eval
- [ ] Dashboard: trace explorer, xərc / latency / keyfiyyət qrafikləri
- [ ] Online eval: trafikin 5%-i LLM-as-judge ilə qiymətləndirilir
- [ ] İstifadəçi feedback-i (👍/👎 + şərh), data flywheel üçün saxlanılır
- [ ] Drift alert-ləri (faithfulness həftəlik ortadan 10% aşağı düşərsə)
- [ ] Prompt versiyalama + A/B test infrastrukturu
- [ ] SLO dashboard: p95 < 8s, availability 99.5%, faithfulness ≥ 0.9

**✅ DoD:** İstənilən sorğunun bütün addımları dashboard-da görünür, sintetik keyfiyyət düşüşü yaradıldıqda alert gəlir.

---

## F7 — Embedding fine-tune + reranker distillation (Həftə 15–17)

**Məqsəd:** Azərbaycan hüquqi domain-i üçün öz retrieval modellərin.

### Həftə 15 — Data
- [ ] Hər maddə üçün Claude ilə 3–5 sintetik sual → (sual, maddə) cütləri
- [ ] Keyfiyyət filtri: hər sintetik sual round-trip yoxlamasından keçir
- [ ] **Hard negative mining:** base model ilə tapılan, amma yanlış olan maddələr
- [ ] Train / val / test bölgüsü (**data leakage yoxdur:** golden eval dataset-dən heç nə train-ə düşmür)
- [ ] Production feedback datası da əlavə olunur (F6 flywheel)

### Həftə 16 — Embedding fine-tune
- [ ] `bge-m3` + `MultipleNegativesRankingLoss` (sentence-transformers)
- [ ] Hiperparametr axtarışı, W&B-də izləmə
- [ ] Base vs fine-tuned: Recall@5, MRR, multi-hop ayrıca
- [ ] Bütün korpusun yeni model ilə yenidən indekslənməsi (blue-green index)

### Həftə 17 — Reranker distillation
- [ ] Teacher: Claude ilə relevance label-ləri (0–3 şkala)
- [ ] Student: kiçik cross-encoder
- [ ] Müqayisə: keyfiyyət / latency / xərc
- [ ] ONNX export + kvantizasiya (CPU-da inference)
- [ ] **ADR-007:** Model serving strategiyası

**✅ DoD:** Fine-tuned embedding Recall@5-də base modeldən ≥ 8% yaxşıdır, distilled reranker teacher keyfiyyətinin ≥ 95%-ni saxlayır.

---

## F8 — LLM fine-tune (Həftə 18–20)

**Məqsəd:** dar və tez-tez təkrarlanan tapşırıqları ucuz, sürətli və öz idarənlə olan modellərə vermək.

### Həftə 18 — Data və tokenizer analizi
- [ ] Tokenizer analizi: Azərbaycan mətni üçün Qwen / Llama / Gemma-nın token effektivliyi
- [ ] Base model seçimi (Qwen 1.5–3B tövsiyə olunur)
- [ ] F5-F6 trace-lərindən distillation datası: verifier qərarları, query rewrite-lar, citation extraction-lar
- [ ] Sinif balansı, deduplikasiya, keyfiyyət filtri

### Həftə 19 — Training
- [ ] **QLoRA** (4-bit) ilə üç adapter:
  1. **Verifier** — `supported / contradicted / not_enough_info`
  2. **Citation extractor** — strukturlu JSON çıxışı
  3. **Query rewriter** — gündəlik dildən hüquqi terminologiyaya
- [ ] GPU: Kaggle / Colab (pulsuz) və ya RunPod
- [ ] Hər adapter üçün W&B report

### Həftə 20 — Serving və inteqrasiya
- [ ] vLLM ilə **multi-LoRA serving** (bir base model, üç adapter)
- [ ] LLM Gateway-ə əlavə: routing qaydası (`task=verify` → lokal model, fallback → Claude)
- [ ] Eval: keyfiyyət / xərc / latency (Claude ilə müqayisədə)
- [ ] Shadow rejimdə test (növbəti fazanın infrastrukturu ilə)
- [ ] **ADR-008:** Hansı tapşırıqlar lokal modelə, hansılar API-yə gedir

**✅ DoD:** Lokal verifier Claude-un keyfiyyətinin ≥ 95%-ni saxlayır, sorğu başına xərc ≥ 5 dəfə azalır.

---

## F9 — Platforma (Həftə 21–22)

**Məqsəd:** sistemi genişlənə bilən və digər agentlərin istifadə edə biləcəyi platformaya çevirmək.

### Həftə 21
- [ ] **MCP server:** `search_law`, `get_article`, `check_conflicts`, `ask_mizan` tool-ları. Claude Desktop-da test olunur
- [ ] **Temporal** ilə durable agent execution (server yenidən başlasa belə axın qaldığı yerdən davam edir)
- [ ] Human-in-the-loop: aşağı confidence olduqda manual review queue

### Həftə 22
- [ ] **Semantic cache:** oxşar suallar üçün (similarity threshold + TTL + temporal invalidation)
- [ ] **Shadow deployment:** yeni prompt və ya model real trafikdə paralel işləyir, nəticələr müqayisə olunur
- [ ] Feature flags (yeni komponentlərin tədricən açılması)
- [ ] Multi-tenancy: Postgres RLS, tenant başına kvota

**✅ DoD:** Mizan Claude Desktop-dan MCP ilə istifadə olunur, semantic cache xərci ≥ 25% azaldır, shadow test hesabatı avtomatik yaranır.

---

## F10 — Müqavilə analizi + təhlükəsizlik (Həftə 23)

### Məhsul funksiyası
- [ ] PDF yükləmə + OCR / vision model (skan edilmiş sənədlər üçün)
- [ ] Müqaviləni bəndlərə bölmək, hər bəndi qanunla yoxlamaq
- [ ] Risk hesabatı: *"7.2-ci bənd Əmək Məcəlləsinin 77-ci maddəsinə ziddir"*
- [ ] UI: bəndlər rəng kodu ilə + istinadlar

### Təhlükəsizlik
- [ ] **OWASP LLM Top 10** üzrə audit
- [ ] Prompt injection müdafiəsi: retrieval / sənəd mətni "data" kimi işarələnir, tool allowlist, output validation
- [ ] Avtomatik red-teaming dataset-i CI-də işləyir (yüklənən müqavilələrdə gizli təlimatlar da daxil)
- [ ] Auth (JWT / OAuth), rate limiting, audit log
- [ ] Hüquqi disclaimer və məsuliyyət sərhədləri UI-da

**✅ DoD:** Red-team dataset-inin ≥ 95%-i bloklanır, müqavilə analizi 10 real nümunədə düzgün işləyir.

---

## F11 — Launch (Həftə 24)

- [ ] **AzLegalBench**: golden + adversarial dataset Hugging Face-də dərc olunur (dataset card, lisenziya, baseline nəticələr)
- [ ] Fine-tuned modellər Hugging Face-də (model card-larla)
- [ ] **Load test** (k6 / Locust): 10 / 50 / 100 eyni vaxtda istifadəçi, p50 / p95 / p99
- [ ] Production deploy + monitoring
- [ ] **Texniki hesabat** (blog və ya arXiv formatında): metodologiya, nəticələr, trade-off-lar
- [ ] README: arxitektura diaqramı, nəticə cədvəli, demo GIF
- [ ] 3–5 dəqiqəlik demo video

**✅ DoD:** Hər şey açıqdır, təkrarlana bilir və ölçülüb.

---

## 🎯 Hədəf metriklər (yekun)

| Metrik | Hədəf |
|---|---|
| Recall@5 (fine-tuned) | ≥ 0.90 |
| Faithfulness | ≥ 0.94 |
| Citation accuracy | ≥ 0.92 |
| Abstention düzgünlüyü (cavabı olmayan suallar) | ≥ 0.85 |
| Judge–insan uyğunluğu (κ) | ≥ 0.6 |
| p95 latency | < 8s |
| Sorğu başına xərc | < $0.02 |
| Prompt injection blok faizi | ≥ 95% |

---

## 📚 ADR siyahısı

| # | Mövzu |
|---|---|
| 001 | Monorepo və texnologiya stack-i |
| 002 | Vektor DB: Qdrant vs pgvector |
| 003 | Temporal versiyalama modeli |
| 004 | Eval metodologiyası və CI gate |
| 005 | Qraf DB: Neo4j vs Postgres |
| 006 | Agent orkestrasiyası: LangGraph vs öz implementasiya |
| 007 | Model serving strategiyası |
| 008 | Lokal model vs API routing |
| 009 | Semantic cache invalidation |
| 010 | Multi-tenancy və data izolyasiyası |

---

## ⚠️ Risklər və azaltma yolları

| Risk | Azaltma |
|---|---|
| Hüquqi mətnlərin əldə edilməsi çətindir | Kiçik korpusla (1 məcəllə) başla, mənbənin şərtlərinə əməl et |
| Golden dataset üçün hüquqşünas yoxdur | Sintetik və manual yoxlamanı birləşdir, hüquq tələbələri ilə əməkdaşlıq |
| GPU resursu məhduddur | QLoRA, kiçik modellər, Kaggle / Colab, lazım olanda RunPod |
| Scope çox böyükdür | Hər faza müstəqil demo-dur. Lazım olsa, F9–F10 sonraya saxlanıla bilər |
| API xərcləri artır | Prompt caching, Haiku routing, eval-ı nümunə ilə işlətmək, büdcə limitləri |
| Azərbaycan dilində model keyfiyyəti zəifdir | Tokenizer analizi, multilingual modellər, fine-tuning |

---

## 💰 Təxmini büdcə

| Xərc | Məbləğ (təxmini) |
|---|---|
| Claude API (development + eval + distillation datası) | $80–200 |
| GPU (fine-tuning, əsasən pulsuz tier) | $0–100 |
| Hosting (launch-dan sonra, aylıq) | $20–50 |

---

## 🔁 Həftəlik ritm

- **Bazar ertəsi:** həftənin hədəfləri (bu fayldakı checkbox-lar)
- **Hər gün:** kiçik PR-lar, CI yaşıl qalır
- **Cümə:** eval işlədilir, nəticələr `docs/progress.md`-ə yazılır
- **Faza sonu:** demo + DoD yoxlaması + lazım olsa ADR
