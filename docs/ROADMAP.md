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
| F5 | 10–12 | Multi-agent sistemi (DoD 1–2 ✅, 3 ❌ — sənədləşdirilib) |
| F6 | 13–14 | Observability platforması |
| F7 | 15–17 | Embedding fine-tune + reranker distillation (hər iki gate ❌ — sənədləşdirilib, ADR-0009) |
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

## F2 — Baseline retrieval (Həftə 4–5) ✅

**Məqsəd:** ölçülə bilən, güclü retrieval baseline-ı.

### Həftə 4
- [x] Dense axtarış: `bge-m3` və `multilingual-e5-large` müqayisəsi (CPU, embedding cache ilə)
- [x] Sparse axtarış: BM25, Azərbaycan dili analizatoru + prefix stemming (5/6/7 hərf müqayisəsi)
- [x] Hybrid: **Reciprocal Rank Fusion** (Qdrant Query API)
- [x] Temporal filter: `as_of` parametri, hər chunk öz qüvvədə olduğu intervalla

### Həftə 5
- [x] Reranker: `bge-reranker-v2-m3`
- [x] Parent-child retrieval: kiçik chunk ilə axtarış, LLM-ə bütöv maddə verilir
- [x] Query rewriting: Groq `gpt-oss-20b` ilə (Claude yox, istifadəçi seçimi)
- [x] Eval: 70 sual (silver), 17 variant, bootstrap etibar intervalları
- [x] RAG endpoint-ləri: `/v1/search` və `/v1/ask` (Groq `gpt-oss-120b`, hərfi sitat yoxlaması ilə istinadlar)
- [x] **ADR-0004:** Retrieval konfiqurasiyası (ölçmə ilə seçilib)

**✅ DoD:** Recall@5 ≥ 0.75. Hər chunking və axtarış variantının nəticəsi cədvəldə qeyd olunub.

**Nəticə:** bge-m3 dense, maddə chunk-ları: **Recall@5 0.929 [0.87, 0.98], MRR 0.831, p50 376 ms (CPU)**.
Gözlənilməz tapıntılar: hybrid (RRF) MRR-i 0.831 → 0.741 **aşağı salır**; reranker +0.03 MRR üçün gecikməni 45 dəfə artırır;
query rewriting statistik fayda vermir. Hamısı konfiqurasiya ilə açıla bilir; F3-də hüquqşünas datasetində yenidən yoxlanacaq.

---

## F3 — Eval təməli (Həftə 6–7) ✅ (judge kalibrasiyası insan qiymətləndirməsini gözləyir)

**Məqsəd:** bundan sonrakı hər qərarı rəqəmlə əsaslandırmaq.

### Həftə 6 — Dataset
- [x] Dataset v1: **200 sual**: single 101, multi 35, exact_term 20, temporal 19, no_answer 15, false_premise 10
- [x] Hər gold maddə sualın tarixində yoxlanılıb; false-premise faktları mətndə təsdiqlənib (q197 bu yoxlamada rədd edilib, əvəzlənib)
- [x] Dataset versiyalanır (`evals/datasets/v0`, `v1`) və sənədləşdirilib
- [ ] Hüquqşünas yoxlaması (silver → gold)

### Həftə 7 — Framework və CI
- [x] Retrieval metrikləri: Recall@k, MRR, nDCG, Hit@k
- [x] Generasiya metrikləri: `answer_correct` (judge), `cites_gold` və `citation_valid` (deterministik), abstention
- [x] Bootstrap CI + **paired bootstrap** müqayisə
- [x] **CI eval gate:** dondurulmuş korpus snapshot-u, cache-lənmiş model və embedding-lər; baseline-dan əhəmiyyətli düşüşdə PR bloklanır
- [x] Gate nəticəsi CI job summary-də cədvəl kimi
- [~] **Judge kalibrasiyası:** alət hazırdır (`mizan-eval label`, kor qiymətləndirmə, Cohen's κ); insan qiymətləri gözlənilir
- [x] **ADR-0005:** Eval metodologiyası

**✅ DoD:** `mizan-eval` tam hesabat verir ✅, CI gate işləyir ✅ (lokal və GitHub Actions-da), judge-insan uyğunluğu κ ≥ 0.6 ⏳.

**Nəticələr:**
- Generasiya (60 sual, judge hələ kalibrlənməyib): düzgün cavab **0.907** [0.81, 0.98], gold maddəyə istinad **0.889**, əhatədən kənar suallarda imtina **4/4**, yanlış fərziyyə **3/3**, p50 2.6 s.
- Retrieval v1 (185 sual): dense bge-m3 MRR **0.894** vs hybrid 0.829. Amma hüquqi terminli suallarda BM25/hybrid **1.00**, dense 0.95 → F5 planner üçün sual növünə görə marşrutlaşdırma ideyası.
- Judge-ın səhv saydığı 5 cavabın çoxu gold maddədən kənar, amma düzgün məlumat əlavə edir: judge həddindən artıq sərt ola bilər, κ bunu göstərəcək.

---

## F4 — Knowledge Graph + məhkəmə qərarları (Həftə 8–9) ✅

**Məqsəd:** oxşar mətndən əlavə, normalar arasındakı əlaqələri də anlamaq.

### Həftə 8 — Qraf ✅
- [x] Citation extraction: `"70-ci maddəyə uyğun"` → `{act, article}`. LLM əvəzinə **qrammatika**: cari mətndə 633 istinad, 0 həll olunmayan (ADR-0006). LLM başqa aktlar və məhkəmə qərarları üçün qalır.
- [x] Neo4j sxemi: `(:Article)-[:REFERENCES {valid_from, valid_to}]->(:Article)`, `(:Act)`; zaman intervallı kənarlar. `[:AMENDS]`, `[:INTERPRETS]` → Həftə 9
- [x] Graph-aware retrieval: top maddənin istinad etdiyi ən oxşar maddəyə top-5-də bir yer ayrılır; sualda adı çəkilən maddə birinci gəlir; Neo4j əlçatmazdırsa dense-ə qayıdır
- [x] Eval: **əvvəlcədən qeydə alınmış** test (30 yeni multi-hop sual): Recall@5 0.700 → **0.817 (+16.7%)**, v1-də zərər yoxdur. İlk avtomatik seçim protokolu "winner's curse" səbəbindən uğursuz oldu və bu sənədləşdirilib
- [x] Parser düzəlişi: 2021-ci ildən sonrakı versiyalarda düzəlişlərin siyahısı 2-ci əlavəyə qarışırdı (50k simvol), PARSER_VERSION 4
- [x] CI gate artıq serving yolunu (dense + qraf) qoruyur; qraf snapshot-dan qurulur, Neo4j tələb olunmur

### Həftə 9 — Case law və ziddiyyətlər ✅
- [x] Konstitusiya Məhkəməsinin qərarlarının ingestion-u: 11 qərar, əsaslandırma və nəticə hissəsi, nəticənin növü (şərh / uyğundur / uyğun deyil). *Ali Məhkəmə qərarları əvəzinə: KMQ qərarları normativ xarakterli aktdır (NHA 4.1.1) və e-qanun-da hüquqi baxımdan açıq mənbədir; Ali Məhkəmə qərarları üçün açıq və strukturlu mənbə tapılmayıb, sonraya saxlanılıb.*
- [x] `(:CourtDecision)-[:INTERPRETS]->(:Article)`: 22 əlaqə (başlıqdan və nəticə hissəsindən); aktlar reyestri xarici istinadları e-qanun id-lərinə bağlayır (8/8)
- [x] **Ziddiyyətlərin həlli:** "Normativ hüquqi aktlar haqqında" Konstitusiya Qanununun 2.3–2.8, 10.2–10.6 və 11-ci maddələri; lex specialis üçün mətndəki "istisna olmaqla" kənarları (19); 23 ssenari testi
- [x] Amendment diff: `GET /v1/diff?key=art:69&from=2020-01-01&to=2026-10-01` (söz səviyyəsində diff + düzəliş edən aktlar)
- [x] **Cavabda KMQ qərarları:** düzgünlük 0.27 → **0.91**, cütlənmiş fərq +0.64 [+0.36, +0.91], 7 sual düzəlib, 0 pozulub (`evals/results/f4_decisions`). Səbəb: 144-cü maddənin 3-cü hissəsinin tətbiqi 18.02.2026-da dayandırılıb, rəsmi mətn isə hələ dəyişməyib
- [x] **ADR-0006:** Qraf: Neo4j (törəmə, yenidən qurula bilən data) vs Postgres recursive CTE

**✅ DoD:** Multi-hop suallarda Recall@5 baseline-dan ≥ 10% yaxşıdır ✅ (+16.7%, əvvəlcədən qeydə alınmış test). Ziddiyyətlərin həlli üzrə 20 test ssenarisi keçir ✅ (23).

---

## F5 — Multi-agent sistemi (Həftə 10–12)

**Məqsəd:** mürəkkəb hüquqi sualları planlaşdıran, araşdıran və özünü yoxlayan sistem.

### Həftə 10 — Orkestrasiya ✅
- [x] State machine: `Planner → Researcher(s) → Drafter → Verifier → Judge`, "yenidən axtar" döngüsü ilə
- [x] Agentlər arası məlumat ötürülməsi yalnız Pydantic sxemləri ilə (`extra="forbid"`; hər çağırışa bir "təmir" cəhdi)
- [x] Hər addım üçün limitlər: maksimum iterasiya, timeout, run başına token büdcəsi, paralel researcher limiti
- [x] **ADR-0007:** öz tipli state machine (LangGraph yox); LLM yalnız mühakimə tələb edən addımlarda

### Həftə 11 — Tool-lar və agentlər ✅
- [x] Tool-lar: `search_law`, `get_article`, `get_related_articles` (qraf), `get_court_decisions`, `check_conflicts`
- [x] Researcher-lərin paralel işləməsi (`asyncio.gather` + semaphore); orijinal sual həmişə araşdırılır
- [x] Verifier: əvvəl mexaniki sitat yoxlaması, sonra `supported / contradicted / not_enough_info` (tək batch çağırış)
- [x] Judge: cavab vermək, imtina etmək ("əmin deyiləm") və ya daha geniş yenidən axtarış

### Həftə 12 — LLM Gateway və etibarlılıq
- [x] Model routing: `gpt-oss-20b` (planner, verifier), `gpt-oss-120b` (drafter) *(Claude əvəzinə Groq: istifadəçinin seçimi)*
- [~] Prompt caching: dəyişməyən sistem promptu hər zaman əvvəldə gəlir (prefix caching üçün); Groq tərəfində ölçülməyib
- [x] Retry + exponential backoff + jitter (`retry-after` üstündür), circuit breaker
- [x] Graceful degradation: planner, verifier, qraf və drafter çökəndə sistemin davranışı müəyyəndir (ADR-0007 §6)
- [x] Tenant başına gündəlik xərc büdcəsi (Redis, 429 + `Retry-After`); run başına token büdcəsi
- [x] SSE ilə addım-addım streaming (`/v1/agent/ask/stream`); müştəri ayrılanda run dayandırılır
- [ ] **Kalibrə olunmuş inam:** confidence score + imtina həddi (0.5) var; kalibrasiya eval nəticəsinə bağlıdır

**✅ DoD:** Faithfulness ≥ 0.90, citation accuracy ≥ 0.90. Agent versiyası baseline RAG ilə müqayisə cədvəlində statistik əhəmiyyətli şəkildə üstündür.

**Nəticə (pre-registered, 51 sual):** faithfulness 0.912 ✅, citation accuracy 0.920 ✅ (baseline 0.644),
amma `correct` agent − baseline = −0.118 [−0.235, −0.02] ❌. Agent default deyil; `/v1/agent`
opt-in rejim kimi qalır. Səhv təhlili və növbəti addımlar: `evals/results/f5_agent/README.md`.

---

## F6 — Observability platforması (Həftə 13–14) ✅

**Məqsəd:** production-da nə baş verdiyini görmək və keyfiyyət düşüşünü avtomatik aşkarlamaq.

### Həftə 13 — Tracing və saxlama ✅
- [x] **OpenTelemetry GenAI semantic conventions:** `invoke_agent`, `execute_tool`, `chat` span-ları, `gen_ai.*` atributları
- [x] Trace → span iyerarxiyası: sorğu → agent addımı → LLM / tool çağırışı (hər retry ayrıca span)
- [x] Hər span-da: token, xərc (`mizan.cost.usd`, konfiqurasiya olunan qiymət cədvəli), latency, model, prompt versiyası (SHA-256)
- [x] OTel Collector → ClickHouse, 30 günlük TTL; health probe span-ları Collector-da atılır
- [x] PII redaction iki qatda: kodda (`redact`) və Collector-da (`redaction` processor)

### Həftə 14 — Dashboard və online eval ✅
- [x] Dashboard (kod kimi, Grafana provisioning): trace explorer, xərc / latency / token / nəticə qrafikləri
- [x] Online eval: cavabların 5%-i (cavab id-sinə görə deterministik) müstəqil modellə faithfulness üzrə qiymətləndirilir, cavabı gecikdirmədən
- [x] İstifadəçi feedback-i: `POST /v1/feedback` (👍/👎 + şərh), hər cavabda `answer_id`; `answers` cədvəli data flywheel üçündür
- [x] Drift alert-ləri: 7 günlük orta əvvəlki 28 günlük ortadan 10%-dən çox aşağıdırsa və ya SLO (0.90) pozulursa; `mizan-eval drift` (exit 1 + webhook) və Grafana alert qaydası
- [~] Prompt versiyalama ✅ (hash hər span-da və hər cavabda); A/B təyinatı → F9
- [x] SLO panelləri: agent p95 < 8s, availability 99.5%, faithfulness ≥ 0.9
- [x] **ADR-0008:** Observability

**✅ DoD:** İstənilən sorğunun bütün addımları dashboard-da görünür ✅ (trace explorer), sintetik keyfiyyət düşüşü yaradıldıqda alert gəlir ✅ (inteqrasiya testi).

---

## F7 — Embedding fine-tune + reranker distillation (Həftə 15–17)

**Məqsəd:** Azərbaycan hüquqi domain-i üçün öz retrieval modellərin.

### Həftə 15 — Data
- [x] Hər maddə üçün 4 sintetik sual → (sual, maddə) cütləri (Gemini, pulsuz kvota; 329-dan 218 maddə)
- [x] Keyfiyyət filtri: hər sintetik sual round-trip yoxlamasından keçir (başqa model ailəsi)
- [x] **Hard negative mining:** base model ilə tapılan, amma yanlış olan maddələr (sitat qonşuları istisna)
- [x] Train / val / test bölgüsü maddə səviyyəsində (**data leakage yoxdur:** char 3-gram Jaccard guard)
- [ ] Production feedback datası da əlavə olunur (F6 flywheel)

### Həftə 16 — Embedding fine-tune
- [x] `bge-m3` + MNRL (same-article masking, hard negative), RunPod RTX 3090, ~$0.14
- [x] Hiperparametr axtarışı (iki run, seçim val MRR@10 ilə; W&B əvəzinə JSON report)
- [x] Base vs fine-tuned: Recall@5, MRR, multi-hop ayrıca (`mizan-train compare`)
- [x] Yenidən indeksləmə (blue-green, `bge-m3-mizan` əlavə vektor)
- **Nəticə: gate keçmədi** — sintetik test-də MRR@10 +0.09, insan yazdığı eval-də Recall@5 +0.4%,
  MRR@10 −0.042 (əhəmiyyətli). Model promote olunmadı. Bax `evals/results/f7_embed/README.md`.

### Həftə 17 — Reranker distillation
- [x] Teacher: `bge-reranker-v2-m3` *(LLM label-ləri əvəzinə: pulsuz kvota darboğazdır, distillation-ın məqsədi zatən bu modeli ucuzlaşdırmaqdır)*
- [x] Student: `mmarco-mMiniLMv2-L12` (118M), dense top-20 üzərində listwise KL; RunPod RTX 3090, ~$0.10
- [x] Müqayisə: keyfiyyət / latency / xərc (`mizan-train rerank-report`, pre-registered)
- [x] ONNX export + dinamik int8 kvantizasiya (CPU-da inference)
- [x] **ADR-009:** Model serving strategiyası

**✅ DoD:** Fine-tuned embedding Recall@5-də base modeldən ≥ 8% yaxşıdır, distilled reranker teacher keyfiyyətinin ≥ 95%-ni saxlayır.

**Nəticə: hər iki gate keçmədi** — embedding (yuxarıda); reranker student teacher-in 93%-ni saxlayır
və dense-dən əhəmiyyətli dərəcədə pisdir, teacher-in özünün qazancı da əhəmiyyətli deyil. Serving:
dense `bge-m3`, reranker-siz (ADR-0009). Bax `evals/results/f7_rerank/README.md`.

---

## F8 — LLM fine-tune (Həftə 18–20) ✅ (verifier; gate keçdi)

**Məqsəd:** dar və tez-tez təkrarlanan tapşırıqları ucuz, sürətli və öz idarənlə olan modellərə vermək.

**Nəticə:** [`evals/results/f8_verifier`](../evals/results/f8_verifier/README.md). Qwen3.5-2B + LoRA verifier API-nin (`gpt-oss-20b`) macro-F1-inin **98.4%**-ni saxlayır (0.935 vs 0.951) və iddia başına **7.3×** ucuzdur (214 ms vs 1.59 s). Hər iki pre-registered gate keçdi. Retention intervalı [0.91, 1.05]-dir; test iddiaları sintetikdir.

### Həftə 18 — Data və tokenizer analizi
- [x] Tokenizer analizi: Azərbaycan mətni üçün Qwen / Llama / Gemma-nın token effektivliyi ([`f8_tokenizers`](../evals/results/f8_tokenizers/README.md))
- [x] Base model seçimi: `Qwen/Qwen3.5-2B` (248k vocab, Qwen2.5-dən 22% az token)
- [x] Verifier datası: label-i konstruksiya ilə məlum olan iddialar (restated / altered / absent / extended), başqa model ailəsindən hakim filtri; F5 trace-ləri əvəzinə (gold label yoxdur)
- [x] Sinif balansı, deduplikasiya, keyfiyyət filtri; maddə üzrə split (test maddələri train-də yoxdur)

### Həftə 19 — Training
- [x] **LoRA** (r=16, bütün linear qatlar) ilə verifier — `supported / contradicted / not_enough_info`. QLoRA lazım olmadı: 2B model fp16-da T4-ə sığır
- [ ] Citation extractor və query rewriter — öyrədilmədi; ADR-010 qaydası ilə API-də qalır
- [x] GPU: pulsuz Colab T4 (`infra/gpu/verifier_notebook.ipynb`); RunPod pod-ları özünü silir (`infra/gpu/run.sh`)
- [x] Report: `evals/results/f8_verifier/` (W&B əvəzinə repoda JSON + README)

### Həftə 20 — Serving və inteqrasiya
- [ ] vLLM ilə multi-LoRA serving — ADR-010-a görə təxirə salındı (GPU yalnız trafik break-even-i keçəndə)
- [ ] LLM Gateway routing qaydası (`task=verify` → lokal model, fallback → API) — eyni səbəbdən
- [x] Eval: keyfiyyət / xərc / latency (API ilə müqayisədə)
- [ ] Shadow rejimdə test (F9 `ShadowRunner` hazırdır; lokal modelin serving-i ilə birlikdə)
- [x] **ADR-010:** Hansı tapşırıqlar lokal modelə, hansılar API-yə gedir

**✅ DoD:** Lokal verifier API-nin keyfiyyətinin ≥ 95%-ni saxlayır, sorğu başına xərc ≥ 5 dəfə azalır. — **keçdi** (98.4%, 7.3×)

---

## F9 — Platforma (Həftə 21–22)

**Məqsəd:** sistemi genişlənə bilən və digər agentlərin istifadə edə biləcəyi platformaya çevirmək.

### Həftə 21
- [x] **MCP server:** `search_law`, `get_article`, `check_conflicts`, `ask_mizan` tool-ları (`apps/mcp`, API-nin nazik klienti; stdio üzərindən end-to-end yoxlanılıb). Claude Desktop konfiqurasiyası README-də — istifadəçi tərəfindən qoşulmalıdır
- [x] **Temporal** ilə durable agent execution (server yenidən başlasa belə axın qaldığı yerdən davam edir): eyni state machine, addımlar activity kimi, heartbeat, `Idempotency-Key`. End-to-end yoxlanılıb: işçi draft zamanı öldürüldü, yeni işçi `draft`-ı 2-ci cəhddə bitirdi, plan təkrarlanmadı. Bax **ADR-011**
- [x] Human-in-the-loop: `reviews` növbəsi (confidence < 0.7, imtina, 👎, faithfulness < 0.8), `GET/POST /v1/reviews` (reviewer token)

### Həftə 22
- [x] **Semantic cache:** similarity threshold (kalibrə: 0.9431) + TTL + evidence-fingerprint ilə temporal invalidation — gate keçmədi (23.9% < 25%), default söndürülüb; bax `evals/results/f9_cache`
- [x] **Shadow deployment:** `ShadowRunner` (namizəd model trafik nümunəsində eyni mənbələrlə, istifadəçiyə görünmədən), `shadow_runs`, `mizan-eval shadow` hesabatı + Grafana paneli
- [~] Feature flags: konfiqurasiya bayraqları (`MIZAN_CACHE__ENABLED`, `RETRIEVAL__GRAPH`, `RETRIEVAL__COURT_DECISIONS`); tenant-səviyyəli tədricən açılma hələ yoxdur
- [x] Multi-tenancy: Postgres RLS (`answers`, rol `mizan_app`, `mizan.tenant`; fail-closed), tenant başına gündəlik token kvotası (Redis). Məhdudiyyət: API hələ owner rolu ilə qoşulur və `SET LOCAL ROLE` edir. Production-da API birbaşa `mizan_app` kimi qoşulmalıdır (README)

**✅ DoD:** Mizan Claude Desktop-dan MCP ilə istifadə olunur, semantic cache xərci ≥ 25% azaldır, shadow test hesabatı avtomatik yaranır.

---

## F10 — Müqavilə analizi + təhlükəsizlik (Həftə 23)

### Məhsul funksiyası
- [x] PDF/DOCX/TXT/foto yükləmə; mətn qatı olmayan (skan) səhifələr vision model ilə OCR edilir (`mizan_contracts.document`, `ocr`)
- [x] Müqaviləni bəndlərə bölmək, hər bəndi qanunla yoxlamaq: rəqəmsal limitlər üçün deterministik qaydalar (12 qayda, 14 maddə), qalanı üçün LLM yoxlaması; LLM-in hər iddiası maddədən hərfi sitatla yoxlanılır. Bax **ADR-013**
- [x] Risk hesabatı: *"7.2-ci bənd Əmək Məcəlləsinin 77-ci maddəsinə ziddir"* (`POST /v1/contracts/analyze`); müqavilədəki gizli təlimatlar `injection` kimi göstərilir, modelə göndərilmir
- [x] UI: bəndlər rəng kodu ilə + istinadlar (`/contracts`, yalnız mətn kimi render)

### Təhlükəsizlik
- [x] **OWASP LLM Top 10** üzrə audit: 8/10 nəzarətdə, 2-si qismən (supply chain scan, mənbənin müstəqil yoxlanması); bax `docs/security/owasp-llm-top10.md`
- [x] Prompt injection müdafiəsi: sualların skrininqi (400 + audit `injection`), etibarsız mətnin neytrallaşdırılması, mənbələr "data" kimi işarələnir, read-only tool allowlist, JSON/quote output validation, sistem promptunun sızmasının bloklanması
- [x] Avtomatik red-teaming dataset-i CI-də işləyir (yüklənən müqavilələrdə gizli təlimatlar da daxil): pre-registered gate test-də **keçdi**: hücumların 98.2%-i bloklandı, 0/281 yanlış bloklama. Skrininqi keçən 20 hücumun heç birinə model itaət etmədi. Bax `evals/results/f10_redteam`
- [x] Auth (JWT, scope-lar `ask`/`review`; tenant yalnız token-dən), rate limiting (subject başına dəqiqəlik, Redis), append-only audit log. Staging/production auth-suz başlamır. OAuth/OIDC real istifadəçilər gələnə qədər açıq qalır. Bax **ADR-012**
- [x] Hüquqi disclaimer və məsuliyyət sərhədləri UI-da və hər hesabatda

**✅ DoD:** Red-team dataset-inin ≥ 95%-i bloklanır ✅, müqavilə analizi 10 real nümunədə düzgün işləyir ❌ (real müqavilə tapılmadı; müstəqil müəllifin 3 görünməmiş dəsti üzrə əvvəlcədən dondurulmuş ölçmə: dəqiqlik 1.00 / 0.67 / 0.59, tapma faizi 0.28 / 0.50 / 0.61, hədd 0.80 / 0.70 — keçmədi. Funksiya "tədqiqat vasitəsi" kimi qalır, dəqiqlik iddiası edilmir. Bax `evals/results/f10_contracts/test3`, ADR-013).

---

## F11 — Launch (Həftə 24)

- [ ] **AzLegalBench**: golden + adversarial dataset Hugging Face-də dərc olunur (dataset card, lisenziya, baseline nəticələr)
- [ ] Fine-tuned modellər Hugging Face-də (model card-larla)
- [x] **Load test** (Locust): 10 / 50 / 100 eyni vaxtda istifadəçi, p50 / p95 / p99 — 0 xəta; bir proses ~25 istifadəçiyə qədər rahatdır, darboğaz CPU-da bge-m3 sorğu embedding-idir (88 ms, ~11/s); micro-batching (default açıq) yük altında ötürməni ~10.5 sorğu/s-də saxlayır (batching-siz 5.5–7-yə düşür). Bax `evals/results/f11_load`
- [ ] Production deploy + monitoring
- [x] **Texniki hesabat** (`docs/REPORT.md`): metodologiya, bütün nəticələr, mənfi nəticələr və trade-off-lar
- [ ] README: arxitektura diaqramı ✅, nəticə cədvəli ✅, demo GIF (demo video ilə birlikdə)
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
| 004 | Retrieval konfiqurasiyası (ölçmə ilə seçilib) |
| 005 | Eval metodologiyası və CI gate |
| 006 | Qraf DB: Neo4j vs Postgres |
| 007 | Agent orkestrasiyası: LangGraph vs öz implementasiya |
| 008 | Observability: OTel GenAI span-ları, ClickHouse, Postgres-də keyfiyyət dövrəsi |
| 009 | Model serving strategiyası |
| 010 | Lokal model vs API routing |
| 011 | Durable agent runs (Temporal) |
| 012 | Tenancy və giriş: JWT, RLS, rate limit, audit log |

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
