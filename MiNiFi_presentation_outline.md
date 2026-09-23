# MiNiFi + K8s — obsah prezentace (pracovní draft)

> Status: sekce A, B, C navrženy. D, E čekají na rozpracování.
> Publikum: širší architektonické/rozhodovací, publikum už zná kontext výběru ingest toolu.

---

## A) Rekapitulace (4 snímky)

### 1. Titulní snímek
- Název: *Ingest Tool for DataMesh — MiNiFi on Kubernetes*
- Podtitul: návaznost na předchozí rozhodování (AirPy → Meltano → MiNiFi)

### 2. Kde jsme skončili
- Minule doporučeno: **AirPy** (Airflow + Python)
- Vendor navrhl vyzkoušet **Meltano** jako alternativu
- → prošli jsme Meltano PoC

### 3. Proč jsme Meltano vyřadili
- Export 80 mil. řádků: **~4 hodiny** (AirPy 5 min, Infa 8 min, NiFi 15 min)
- Řádově pomalejší než všechny ostatní zvažované nástroje
- Meltano dál mimo hru

### 4. Co zůstává fixní bez ohledu na vítězný nástroj
- **Versioned Template Library** (model B2) — squad si nasazuje verzovanou kopii šablony + config do vlastního repa
- Konfigurace: **Object Schema**, **Export Configuration**, **Run Conditions**
- Tohle se nemění, ať vyhraje AirPy nebo MiNiFi — týká se to *jak se squady zapojují*, ne *co běží uvnitř*

---

## B) Co je MiNiFi (2 snímky — hotovo z pilotu)

### 5. What is MiNiFi?
- Two variants exist: **Java agent** and **C++ agent**
- We use the **Java agent** — the C++ variant has no Parquet writer, which is a hard requirement for us
- Java-based agent, part of the Apache NiFi project
- Included in every NiFi release since NiFi 2.0
- Runs the same processors as NiFi — same functional coverage
- No web UI — the flow is a definition file, kept in Git

### 6. Why MiNiFi fits our case
- **Databases** — Oracle, MS SQL Server, PostgreSQL via JDBC; incremental extraction built in
- **Kafka & Elasticsearch** — native processors for both; one tool instead of two (AirPy + NiFi)
- **Parquet & S3** — built-in reader/writer; direct S3 read/write
- **Flow as code** — flow definition is a file, versioned in Git, reviewed in pull requests

---

## C) Hodnocení (4 snímky, přeuspořádáno)

### 7. NiFi vs. MiNiFi — kde a proč se MiNiFi zlepšila
Stejné procesory NiFi, jiný provozní model (pod na běh z Airflow, flow jako soubor v Gitu, secrety přes Airflow/Conjur) — **zlepšení jde za modelem nasazení, ne za jádrem nástroje**.

| Kritérium | NiFi | MiNiFi | Rozdíl |
|---|---|---|---|
| CI/CD — Yaml config consistency | 8 | 1 | **-7** |
| CI/CD — Git Support | 6 | 1 | **-5** |
| Alignment with Target Cloud Architecture | 7 | 3 (in K8s) | **-4** |
| Reuse in other entities potential | 5 (DI?) | 1 (DI) | **-4** |
| Horizontal Scalability (Cross-Load) | 5 | 1 (with K8s) | **-4** |
| CI/CD — Easy environment promotion | 5 | 2 | **-3** |
| Airflow SOGE Integration | 6 (PJE-REST) | 3 | **-3** |
| CyberArk / Vault Integration | 5 | 3 | **-2** |
| Squad autonomy | 4 | 2 | **-2** |

Poctivě i to, kde je MiNiFi horší:
- Support for Incremental Loads: NiFi 0 → MiNiFi 2 (**+2**, protože nepoužíváme nativní stav procesoru, ale vlastní watermark logiku)

*(Pozn. k přípravě: čísla jsou z wiki_eval.html, sekce 3–6. Až budeme dělat vizuál, zvážit graf/waterfall místo tabulky.)*

### 8. Proč ne AirPy, i když vychází dobře
- AirPy vede v několika kategoriích (výkon, provoz, expertíza, Greenbook)
- Ale: vyžaduje **vlastní framework** v Airflow (watermark, schema validace, Parquet generování) — přesně to, čeho se migrační projekt bojí
- Kafka a Elasticsearch by řešil **druhý nástroj** (NiFi) → dva toolset, dva CI/CD, dva provozní modely
- MiNiFi = **jeden tool pro všechny zdroje**, hotové komponenty místo vlastního kódu

### 9. Výsledky PoC
| | AirPy | Infa | NiFi | MiNiFi |
|---|---|---|---|---|
| Export 80 mil. řádků | 5 min | 8 min | 15 min | 10 min |
| CyberArk / rotace hesel | ✅ validováno | ✅ validováno | ❌ problémy | – (neověřeno) |

### 10. Decision Summary
*(zařazeno až sem, za PoC — tabulka z wiki_eval_ch9_draft.html, sekce Decision Summary. Obsah k převzetí: Runtime, Kafka/ES, Custom Code, Flow Generation, CI/CD, Operations, Incremental, Security, Squad Adoption, Licensing, Architecture, Scalability, Overall.)*

---

## D) Cílová architektura

- [ ] 11. Celkový diagram
- [ ] 12. Kubernetes model (zjednodušeně)
- [x] 13. Airflow → Kubernetes přes REST API *(obsah rozpracován níže)*
- [ ] 14. Bezpečnost — CyberArk/Conjur
- [ ] 15. Zpracování dat uvnitř MiNiFi
- [ ] 16. Bezstavovost a checkpoint
- [ ] 17. Vlastnický model šablon

### 13. Airflow → Kubernetes přes REST API

**Hlavní myšlenka:** `KubernetesPodOperator` v Airflow nedělá nic exotického — mluví s K8s API serverem přes **standardní REST API** (`POST /api/v1/namespaces/{ns}/pods`, pak polling stavu a `GET .../pods/{name}/log`). Celé propojení SOGE Airflow ↔ KB Kubernetes je tedy jen **HTTPS REST volání**, ne speciální protokol.

**Proč je to důležité i mimo tento projekt:**
- REST API přes HTTPS bychom měli mít na prostupech mezi SOGE a KB povolené už dnes
- Securita je k REST API obecně naklonění (standardní auth přes token/cert, snadný audit, snadné omezení na konkrétní endpoints)
- → tohle by mohl být **obecný vzor** pro propojení SOGE Airflow s KB světem, nejen pro MiNiFi

**Ukázka kódu (DAG task):**
```python
from airflow import DAG
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from datetime import datetime

with DAG(
    dag_id="minifi_crm_export",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
) as dag:

    run_minifi = KubernetesPodOperator(
        task_id="run_minifi_crm_export",
        name="minifi-crm-export",
        namespace="crm-squad-ns",
        image="harbor.kb.cz/minifi/minifi-template:1.4.0",
        cmds=["/opt/minifi/bin/run.sh"],
        arguments=["--config", "/config/exports.yaml"],
        kubernetes_conn_id="kb_onprem_k8s",   # REST endpoint + token, jako Airflow Connection
        get_logs=True,
        is_delete_operator_pod=True,
    )
```

**Živé demo (na místě, ne na snímku):** spustit v Airflow reálný DAG a ukázat, jak z tasku vytočí pod v K8s a jak se v UI projeví log/stav.

*(TODO: potvrdit `kubernetes_conn_id`/verzi provideru podle skutečné konfigurace SOGE Airflow, než se to ukáže naživo.)*

## E) Rizika a doporučení

- [ ] 18. Rizika a mitigace
- [ ] 19. Otevřené body k ověření
- [x] 20. Doporučení *(obsah rozpracován níže)*
- [x] 21. Plánované next steps *(obsah rozpracován níže)*
- [ ] 22. Závěr

### 20. Doporučení
- **Doporučený nástroj: Apache MiNiFi (Java agent), spouštěný jako pod v Kubernetes, per run z SOGE Airflow** — nahrazuje dříve doporučený AirPy
- Hlavní důvody (shrnutí celé prezentace):
  - Jeden nástroj pro všechny zdroje včetně Kafka/Elasticsearch (žádná kombinace dvou toolů)
  - Hotové komponenty místo vlastního frameworku v Airflow
  - Nejlepší výsledek ve Functional Capabilities, silná CI/CD a Git integrace
  - Žádné licenční náklady, dobrá shoda s cílovou architekturou (K8s, Airflow, S3, Versioned Template Library)
- AirPy zůstává technicky silnou alternativou / fallback, pokud se validační body ze snímku 19 nepodaří naplnit
- **Žádost/next step:** schválit MiNiFi jako cílové řešení a pokračovat ověřením otevřených bodů

### 21. Plánované next steps

**Fáze 1 — technické ověření (PoC)**
- CyberArk integrace + rotace hesel (Airflow → Secret → env var)
- Životní cyklus podu (wrapper / NiFi Stateless) — spolehlivá detekce dokončení běhu
- Dostupnost procesorů (Parquet, Kafka, Elasticsearch, AWS/S3) v distribuci MiNiFi Java

**Fáze 2 — výkon a rozsah**
- Test na větších objemech dat
- Konektivita a výkon pro Teradatu
- Ověření modelu granularity (pod na flow vs. na objekt) v reálném provozu

**Fáze 3 — governance a compliance**
- Potvrzení souladu se Société Générale Greenbook
- Odhad práce v MD (environment setup, upskilling, migrace)
- Onboarding proces pro squady (namespace, ServiceAccount, Conjur) s Platform týmem

**Rozhodovací checkpoint**
- Shrnutí výsledků PoC → go/no-go na plnou migraci

*(TODO: doplnit reálné termíny/vlastníky jednotlivých fází, až budou domluvené.)*
