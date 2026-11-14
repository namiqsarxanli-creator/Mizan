# Demo script (about 5 minutes)

A live demo in the browser. Each scene lists what to do, what to point at and what to say.

## Before you start
1. Start Docker Desktop.
2. Run `run_mizan.py` (in PyCharm: right-click, Run). It needs only the standard library. It
   starts the stack, waits until the models are loaded and opens `http://127.0.0.1:18000/`.
   The first start downloads the embedding model (about 2 GB).
3. Ask one question before the audience arrives, so the first answer is not the slow one.
4. Keep `http://127.0.0.1:18000/?intro` ready: `?intro` replays the opening animation.

Answers need an LLM key (`MIZAN_LLM__GROQ_API_KEY`). When Groq's free tier is spent, answers fail
over to the same model on OpenRouter if `MIZAN_LLM__OPENROUTER_API_KEY` is set. `/changes` needs
no LLM at all, so it is the fallback scene if the network fails.

## 1. Opening (0:00–0:20)
**Do:** open `/?intro`.

**Say:** "Mizan answers questions about Azerbaijani labour law with the exact article in force on
a date, and every quote is checked by code."

## 2. A question with verified quotes (0:20–1:30)
**Do:** click the suggestion **"Hamilə işçini ixtisar etmək olarmı?"**, then **Soruş**.

**Point at:**
- the articles found while the answer is written;
- the answer;
- under **Sitatlar**, the quote from Art. 79 and the line saying it matches the law's text
  word for word;
- **Maddənin tam mətni**, which opens the whole article.

**Say:** "A quote that does not occur in the article is dropped. The model cannot invent a
citation."

## 3. A follow-up (1:30–2:00)
**Do:** type **"Bəs uşağı 4 yaşındadırsa?"** and ask.

**Say:** "The follow-up is read together with the previous question. The protection covers
children under three, so the answer changes."

**Then:** show **PDF kimi saxla** (prints the conversation as a report; a sample is
[`demo/Mizan-huquqi-arayis.pdf`](demo/Mizan-huquqi-arayis.pdf)).

## 4. How the law changed (2:00–3:00)
**Do:** open **Qanunun dəyişməsi**, click **"İşçinin işdən çıxması (69)"**.

**Point at:** deleted words in red, added words in green, and the amending acts with their
dates.

**Say:** "Every version since 1999 is stored. This page needs no AI: it is a comparison of the
two texts in force on the two dates."

## 5. Contract check (3:00–4:15)
**Do:** open **Müqavilə yoxlaması**, drop
[`demo/Numune-emek-muqavilesi.pdf`](demo/Numune-emek-muqavilesi.pdf), untick **Ətraflı yoxlama**
(the result then takes seconds), click **Yoxla**, then tick **Yalnız problemli bəndlər**.

**Point at:**
- clause 7.2, the hidden instruction to an AI: flagged in purple, not obeyed;
- the violations in red. The employee is 17, worked out from the birth date, so the rules for
  minors apply: no probation (Art. 52), at most 36 hours a week (Art. 91), no night work
  (Art. 254), 35 days of leave (Art. 119). There are also wages in dollars, a 70% fine, three
  months' notice, and dismissal for pregnancy.

**Say, honestly:** "On contracts written by someone else, it finds about 60% of violations. It
points a lawyer at the clauses to read; it does not replace one."

## 6. Measured, not claimed (4:15–5:00)
**Show:** the README results table, then [`REPORT.md`](REPORT.md) section 4.4 (the multi-agent
system lost to the single-shot baseline).

**Say:** "Every component was measured against a baseline with thresholds set in advance. The
ones that failed are in the report: the agent, two fine-tunes, the cache, contract analysis,
query rewriting."

## Optional: Claude Desktop (MCP)
With the `mizan` server configured (README, *MCP server*), ask Claude Desktop: "Mizan alətindən
istifadə et: sınaq müddəti ən çoxu neçə aydır?" Claude calls `ask_mizan` or `search_law` and
quotes Art. 51.
