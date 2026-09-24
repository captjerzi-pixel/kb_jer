# MiNiFi + K8s — obsah prezentace (pracovní draft)

> Status: sekce A, B, C navrženy. D, E čekají na rozpracování.
> Publikum: širší architektonické/rozhodovací, publikum už zná kontext výběru ingest toolu.

---

## A) Rekapitulace (4 snímky)

### 1. Titulní snímek
**Nadpis:** Ingest Tool pro DataMesh
**Podnadpis:** Cílová architektura — MiNiFi na platformě Kubernetes

### 2. Rekapitulace předchozího rozhodování
**Nadpis:** Rekapitulace předchozího rozhodování
**Podnadpis:** Výchozí stav před aktuálním hodnocením

**Text snímku:**
- V předchozí fázi hodnocení bylo jako cílové řešení doporučeno Airflow + Python (AirPy)
- Na základě podnětu dodavatele (Vendor) byla do hodnocení doplněna alternativa Meltano
- Alternativa Meltano byla následně ověřena formou Proof of Concept

### 3. Vyřazení alternativy Meltano
**Nadpis:** Vyřazení alternativy Meltano
**Podnadpis:** Výsledky Proof of Concept

**Text snímku:**
- Export 80 milionů záznamů trval v případě Meltano přibližně 4 hodiny
- Ostatní hodnocené alternativy dosáhly výrazně kratších časů: AirPy 5 minut, Informatica 8 minut, Apache NiFi 15 minut
- Výkon alternativy Meltano je o řád nižší než u ostatních hodnocených řešení
- Na základě těchto výsledků byla alternativa Meltano z dalšího hodnocení vyřazena

### 4. Architektura nezávislá na volbě nástroje
**Nadpis:** Architektura zůstává nezávislá na volbě konkrétního nástroje
**Podnadpis:** Princip Versioned Template Library (varianta B2)

**Text snímku:**
- Zvolený architektonický model zůstává platný bez ohledu na výsledek hodnocení konkrétního nástroje
- Business squad nasazuje verzovanou kopii šablony a vlastní konfiguraci ve svém Git repozitáři
- Business squad odpovídá za nasazení, provoz a rozhodnutí o upgradu
- Konfigurační komponenty zůstávají neměnné: Object Schema, Export Configuration, Run Conditions
- Volba konkrétního nástroje (AirPy, MiNiFi) ovlivňuje pouze implementaci, nikoli způsob, jakým business squady tuto architekturu využívají

---

## B) Co je MiNiFi (2 snímky)

### 5. Co je MiNiFi
**Nadpis:** Co je MiNiFi
**Podnadpis:** Lehký agent postavený na platformě Apache NiFi

**Text snímku:**
- Nástroj existuje ve dvou variantách: agent Java a agent C++
- Pro navrhované řešení je použit agent Java — varianta C++ neobsahuje writer pro formát Parquet, což je nezbytný požadavek
- Jde o agenta postaveného na platformě Java, který je součástí projektu Apache NiFi
- Je součástí každého vydání NiFi od verze 2.0
- Využívá stejné procesory jako NiFi — funkční rozsah je totožný
- Neobsahuje webové uživatelské rozhraní — definice flow je uložena jako soubor ve verzovacím systému (Git)

### 6. Proč MiNiFi vyhovuje našemu případu
**Nadpis:** Proč MiNiFi vyhovuje našemu případu
**Podnadpis:** Využití hotových komponent namísto vlastního vývoje

**Text snímku:**
- Databáze — přístup k Oracle, MS SQL Server a PostgreSQL prostřednictvím JDBC, s podporou inkrementální extrakce
- Kafka a Elasticsearch — nativní procesory pro obě technologie; řešení jedním nástrojem namísto kombinace dvou (AirPy a NiFi)
- Parquet a S3 — integrovaná podpora čtení i zápisu formátu Parquet, přímý přístup k úložišti S3
- Flow jako kód — definice flow je uložena jako soubor, verzovaná v Gitu, procházející revizí formou pull requestů

---

## C) Hodnocení (4 snímky, přeuspořádáno)

### 7. Srovnání NiFi a MiNiFi
**Nadpis:** Srovnání NiFi a MiNiFi
**Podnadpis:** Zdroj zlepšení hodnocení a jeho příčina

**Text snímku (úvod):**
Obě řešení využívají shodné procesory Apache NiFi. Rozdíl je v modelu nasazení — MiNiFi běží jako samostatný pod spouštěný z Airflow, definice flow je uložena jako soubor v Gitu a přístup k přihlašovacím údajům zajišťuje kombinace Airflow a Kubernetes. Zlepšení hodnocení tedy vychází z modelu nasazení, nikoli ze samotného jádra nástroje.

| Kritérium | NiFi | MiNiFi | Rozdíl |
|---|---|---|---|
| Konzistence YAML konfigurace v CI/CD | 8 | 1 | **-7** |
| Podpora Gitu v CI/CD | 6 | 1 | **-5** |
| Soulad s cílovou cloudovou architekturou | 7 | 3 (v K8s) | **-4** |
| Potenciál znovupoužití v jiných entitách | 5 (DI?) | 1 (DI) | **-4** |
| Horizontální škálovatelnost (napříč loady) | 5 | 1 (s K8s) | **-4** |
| Snadnost promotion mezi prostředími v CI/CD | 5 | 2 | **-3** |
| Integrace s Airflow SOGE | 6 (PJE-REST) | 3 | **-3** |
| Integrace s CyberArk / Vault | 5 | 3 | **-2** |
| Autonomie squadu | 4 | 2 | **-2** |

**Text snímku (poctivě i zhoršení):**
V oblasti podpory inkrementálních loadů dochází k mírnému zhoršení hodnocení (z 0 na 2) — řešení nevyužívá nativní stav procesoru NiFi, ale vlastní implementaci správy watermarku.

*(Pozn. k přípravě: čísla jsou z wiki_eval.html, sekce 3–6. Při tvorbě vizuálu zvážit graf/waterfall místo tabulky.)*

### 8. Zdůvodnění — proč nebyla vybrána alternativa AirPy
**Nadpis:** Zdůvodnění — proč nebyla vybrána alternativa AirPy
**Podnadpis:** Silné hodnocení nemusí znamenat nejvhodnější volbu

**Text snímku:**
- Alternativa AirPy dosahuje nejlepších výsledků v několika kategoriích — výkon, provozní parametry, dostupnost interních znalostí, soulad s požadavky Greenbook
- Vyžaduje však vývoj a údržbu vlastního frameworku v prostředí Airflow (správa watermarku, validace schémat, generování formátu Parquet)
- Pro podporu Kafka a Elasticsearch by bylo nutné doplnit druhý nástroj (NiFi), což by znamenalo provoz dvou technologických sad a dvou CI/CD procesů
- Řešení MiNiFi naopak pokrývá všechny zdrojové systémy jedním nástrojem s využitím hotových komponent namísto vlastního vývoje

### 9. Výsledky Proof of Concept
**Nadpis:** Výsledky Proof of Concept
**Podnadpis:** Srovnání zbývajících alternativ

| | AirPy | Informatica | NiFi | MiNiFi |
|---|---|---|---|---|
| Export 80 milionů záznamů | 5 minut | 8 minut | 15 minut | 10 minut |
| Integrace CyberArk a rotace hesel | validováno | validováno | zjištěny problémy | neověřeno |

### 10. Decision Summary
*(zařazeno až sem, za PoC — tabulka z wiki_eval_ch9_draft.html, sekce Decision Summary. Obsah k převzetí a přeformulování do formálního tónu: Runtime, Kafka/ES, Custom Code, Flow Generation, CI/CD, Operations, Incremental, Security, Squad Adoption, Licensing, Architecture, Scalability, Overall.)*

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

**Hlavní myšlenka:** `KubernetesPodOperator` v Airflow komunikuje s K8s API serverem prostřednictvím **standardního REST API** (`POST /api/v1/namespaces/{ns}/pods`, následně dotazování stavu a `GET .../pods/{name}/log`). Propojení SOGE Airflow a Kubernetes v prostředí KB je tedy realizováno výhradně formou **HTTPS REST volání**, bez potřeby speciálního protokolu.

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
- AirPy zůstává technicky silnou alternativou, respektive náhradním řešením, pokud se validační body ze snímku 19 nepodaří naplnit
- **Požadovaný krok:** schválit MiNiFi jako cílové řešení a pokračovat ověřením otevřených bodů

### 21. Plánované další kroky

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
