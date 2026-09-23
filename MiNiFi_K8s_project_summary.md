# Ingest Tool / MiNiFi + Kubernetes — souhrn projektu

> Referenční dokument pro obnovu kontextu (ne osnova prezentace — ta je v `MiNiFi_presentation_outline.md`).
> Poslední aktualizace: v rámci této session, 2026-09.

---

## 1. Cíl projektu

KB (Komerční banka) migruje do cloudu (DataMesh). Potřebuje **Ingest Tool**, který generuje Parquet soubory z primárních systémů (Oracle, MSSQL, PostgreSQL, MySQL, Elasticsearch, Kafka) a ukládá je do interního S3, odkud se přes DMZ Transfer Agent přenáší do AWS S3 Landing Zone a dál do Databricks.

**Bezpečnostní požadavek:** Databricks nesmí mít přímý přístup do primárních systémů → Parquety se musí generovat **on-prem v KB**, hotové soubory se přenášejí přes firewall do AWS.

### Objemy
- ~500 různých exportů, max desítky GB na export
- ~480 tisíc loadů za 3 měsíce z Informatiky (~5 300/den)
- 98 %+ loadů z Informatiky má < 10 mil. záznamů
- V průměru ~13 objektů na jeden load
- Migrace: ~400 Informatica workflows + ~100 Adoki/Offloader importů do Cloudery

---

## 2. Vybraná architektura: Versioned Template Library (B2)

Ze 4 zvažovaných konceptů (`wiki_concept.html`):
- A) Centralizovaný parametrizovaný tool
- B1) Generátor plně samostatných flow
- **B2) Versioned Template Library — VYBRÁNO**
- C) Framework s generovanými thin klienty

**Princip B2:** Platform Squad vyvíjí a verzuje šablony (Full/Delta/Kafka). Business Squad si nasadí kopii šablony + vlastní YAML config do svého Git repa. Squad vlastní nasazení, provoz a rozhoduje o upgradu (kromě povinných security fixů).

### Konfigurační komponenty (`wiki_templates.html`)
1. **Object Schema** — struktura exportovaných dat (Arrow-style typy)
2. **Export Configuration** (`exports.yaml`) — `connection_id`, `source_type`, `load_mode`, `validate_schema`, seznam `exports` s `watermark_column`, `schema_ref`, `export_location`. `load_mode` slouží jen k validaci proti vybrané šabloně, ne k dynamickému větvení.
3. **Run Conditions** — SQL vracející boolean, ověřuje dostupnost dat před startem

### Ownership Model
| Artefakt | Vlastník |
|---|---|
| Templates Library | Platform Squad |
| Object Schema, Export Config, Run Conditions | Business Squad |
| Deployed Ingest Flow | Business Squad |
| Template Version Inventory | Platform Squad |

---

## 3. Historie hodnocení nástrojů

**Pořadí událostí:** AirPy doporučeno jako první → Vendor navrhl zkusit **Meltano** → Meltano v PoC selhalo (export 80 mil. řádků ~4 h, řádově pomalejší než ostatní) → při hledání náhrady se objevilo **MiNiFi** → nové srovnání.

Alternativy v `wiki_eval.html`: AirPy, NiFi, Informatica PowerCenter (Infa), IDMC, Adoki, PNJ (PNJ a Adoki brzy vyřazeny), později přidány Meltano a MiNiFi.

### Finální skóre — sekce 3 Functional Capabilities (0=ideál, 9=nepřijatelné)
| Kritérium | AirPy | Infa | Meltano | NiFi | MiNiFi |
|---|---|---|---|---|---|
| Export do Parquet | 1 | 1 | 1 | 1 | 1 |
| Import z Parquet | 2 | 3 | 3 | 1 | 1 |
| Elasticsearch, Kafka | 5 | 6 | 6 | 1 | 1 |
| Více zdrojů | 1 | 3 | 1 | 1 | 1 |
| Teradata | 2 | 1 | 7 | 3 | 3 |
| SQL run conditions | 1 | 4 | 6 | 3 | 2 |
| Inkrementální loady | 5 | 6 | 1 | 0 | 2 |
| Přepsání checkpointu | 2 | 2 | 3 | 5 | 2 |
| CDC | 10 | 6 | 1 | 9 | 9 |
| Škálování napříč loady | 0 | 1 | 5-6 | 5 | 1 (with K8s) |
| Škálování jednoho loadu | 1 | 5 | 5 | 2 | 2 |
| Amount of custom code | — | — | — | — | **NEVYPLNĚNO** (řádek přidán, hodnoty nikdy nezapsány) |

### Sekce 4 (Data Mesh) — MiNiFi vs. NiFi klíčové rozdíly
Squad autonomy 2 vs 4, Reuse 1(DI) vs 5(DI?), Autorestarts 2 vs 3, Easy to use 4 vs 5, Documentation 3 vs 2, Industry standard 4 vs 3.

### Sekce 5 (Target Architecture) — MiNiFi vs. NiFi
Cloud alignment 3(in K8s) vs 7, Airflow SOGE Integration 3 vs 6, **CI/CD Git Support 1 vs 6**, **Yaml consistency 1 vs 8**, Easy env promotion 2 vs 5. Greenbook shodně 4 (MiNiFi = předpoklad "stejné jako NiFi", neověřeno).

### Sekce 6 (Security) — MiNiFi vs. NiFi
CyberArk 3 vs 5, RBAC 2 vs 3, Auditability/Compliance shodně 2.

### Sekce 7 (Operations) — MiNiFi vs. NiFi
Monitoring shodně 3, Alerting 3 vs 4, ostatní shodně (In-Flight Recovery 2, Op Effort 4, Reliability 4, SLA 1).

### Sekce 8 (PoC) — export 80 mil. řádků
| | AirPy | Infa | NiFi | Meltano | MiNiFi |
|---|---|---|---|---|---|
| Čas | 5 min | 8 min | 15 min | ~4 h | 10 min |
| CyberArk + rotace | ✅ validováno | ✅ validováno | ❌ problémy (pak přepsáno na „-") | – | – (neověřeno) |

### Klíčové zjištění: PROČ se MiNiFi zlepšila oproti NiFi
Stejné procesory NiFi, jiný **provozní model**: pod na běh z Airflow (K8s), flow jako soubor v Gitu, secrety přes Airflow/K8s Secret. Zlepšení je soustředěné v CI/CD a cloud alignment (Yaml consistency -7, Git Support -5, Cloud alignment -4), ne v jádru nástroje. Regrese: Incremental Loads (+2 horší), protože nepoužíváme nativní stav NiFi procesoru, ale vlastní watermark logiku.

### Proč ne AirPy (i když v mnoha kategoriích vede)
Vyžaduje vlastní framework v Airflow (watermark, schema validace, Parquet generování) — přesně čeho se migrační projekt bojí ("nebudeme programovat, co už je jinde hotové"). Pro Kafku/Elasticsearch by navíc potřeboval druhý nástroj (NiFi) → dva toolsety. MiNiFi řeší vše jedním nástrojem.

### ⚠️ Rozpracované/nedokončené v `wiki_eval.html`
- Sekce 1–2 (Development complexity, Cost/effort v MD) **nebyly rozšířeny o Meltano/MiNiFi** — jen původní AirPy/NiFi/Infa/IDMC/Adoki/PNJ.
- Sekce 9 (Evaluation Summary) v **hlavním souboru je stále stará verze** (doporučuje AirPy) — nová verze s MiNiFi je jen v samostatném draftu `wiki_eval_ch9_draft.html`, **není zpětně mergnutá**.
- `wiki_templates_k8s_ownership_draft.html` (K8s/Conjur ownership) také **není mergnutý** do `wiki_templates.html`.

---

## 4. Architektura MiNiFi na Kubernetes (rozhodnutí z chatu, částečně v diagramech)

### Runtime model
- **MiNiFi Java agent** (ne C++ — C++ verze nemá Parquet writer)
- **Jeden pod na jeden běh** (ephemeral), spouštěný `KubernetesPodOperator` z SOGE on-prem Airflow
- **Pod je bezstavový** — po vygenerování Parquetu zaniká. Watermark/checkpoint **nejde na PVC jako lokální disk**, ale na S3.
- Airflow ↔ K8s = **standardní REST API** volání na K8s API server (žádný speciální protokol) — potenciálně obecný vzor pro propojení SOGE↔KB

### Multi-tenancy
- Jeden **sdílený K8s cluster**, izolace přes **namespace na squad**
- Namespace ≠ fyzické oddělení — pody různých squadů běžně běží na stejném uzlu (proto NetworkPolicy/ResourceQuota, ne jen namespace samo o sobě)

### Secrets — POZOR, dva modely v historii chatu, použít ten druhý!
1. ~~Můj původní návrh: Conjur Kubernetes Authenticator (authn-k8s), init container ověřující se přes SA token, secrety do `emptyDir`~~ — je to v `minifi_conjur_flow.drawio`, **ale neodpovídá tomu, co reálně nakreslili v `jva_secrets.pdf`/`1_aifflow_k8s_MiNiFi.png`**.
2. **Reálný model (potvrzený sketchem i finálním PNG):** CyberArk **syncuje do nativního K8s `Secret` objektu**, ten se do MiNiFi kontejneru dostane jako **env var**. `ConfigMap` se mountuje jako **soubor** (pravděpodobně `exports.yaml`/flow config — neověřeno). Business Squad si po jednorázovém onboarding grantu (permit na celý branch/Safe) **zakládá nové secrety sám** (self-service), bez zásahu security týmu.
3. **TODO:** `minifi_conjur_flow.drawio` je potřeba přepracovat nebo aspoň okomentovat, že ukazuje alternativní/neschválený model.

### Checkpoint / watermark
- Cesta: MiNiFi → **PVC → PV → Internal S3** (potvrzeno v `1_aifflow_k8s_MiNiFi.png`, stejná cesta pro Parquety i checkpoint)
- Aby to sedělo s "pod je bezstavový", **PV musí být podložený S3** (CSI driver) — **není to formálně potvrzené s platformním týmem**, jen logický závěr z návrhu.
- Návrh obsahu checkpoint JSONu (moje syntéza, nikde zapsáno do wiki): per-object watermark, aby částečné selhání flow (13 objektů/flow) neposunulo watermark u objektů, které selhaly:
  ```json
  {
    "export_id": "CRM_EXPORTS",
    "objects": {
      "CRM.CUSTOMER": {"watermark_column": "UPDATE_TS", "last_watermark": "...", "status": "success"},
      "CRM.CUSTOMER_CONTACTS": {"watermark_column": "MODIFIED_DATE", "last_watermark": "...", "status": "failed"}
    }
  }
  ```

### Granularita běhu
- **Doporučeno: pod na flow** (ne na objekt) — ~5 300 podů/den vs. ~69 000 při pod-na-objekt
- Navrženo (nezapsáno do `wiki_templates.html`): volitelný atribut `group` v `exports.yaml`, výchozí = celý flow v jednom podu, squad může zvolit jemnější granularitu

### Výkon / limity K8s
- Skutečný limit je **churn** (rychlost vzniku/zániku podů), ne velikost clusteru — etcd je bottleneck, ne scheduler
- Doporučeno změřit v PoC, rozprostřít starty v Airflow (ne všechny najednou)

### Logy
- `KubernetesPodOperator(get_logs=True)` streamuje pod/container logy (K8s Pod Logs API) přímo do **Airflow task logu** — žádný samostatně pojmenovaný artefakt

### Image
- **Harbor** (container registry) — MiNiFi template image se odtud pulluje při startu podu

### Ownership model K8s (viz `wiki_templates_k8s_ownership_draft.html`, draft nemergnutý)
- **Cluster-level** (jednou pro platformu): K8s cluster (Platform/Infra tým), CyberArk Conjur (Security tým), Airflow platforma, verzovaný MiNiFi image (Platform Squad)
- **Per-squad, jednorázově (onboarding):** Namespace, ServiceAccount, ResourceQuota/LimitRange, NetworkPolicy, Conjur host+permit, RoleBinding — vše zakládá Platform Squad podle vstupů od squadu
- **Per-squad, průběžně:** `exports.yaml`, žádosti o navýšení kvóty/nové připojení, self-service nových secretů v Conjur, sledování logů
- Klíčový argument pro kolegy: „vlastní namespace" ≠ „vlastní cluster" — namespace je zlomek práce cluster-per-squad

---

## 5. Diagramy — inventář

| Soubor | Co ukazuje | Stav |
|---|---|---|
| `1_aifflow.drawio` | Původní architektura (KB on-prem, Airflow, MiNiFi, source cylinder, SQL conditions, Parquets→S3, DMZ→AWS→Databricks) + moje doplnění (Harbor, checkpoint edge, interní pipeline kroky, DDL Registry, Secrets&Config detail) | Doplněno s **TBC poznámkami** u nejistých částí (DDL Registry směr, PVC/PV povaha — před opravou na "stateless, checkpoint na S3") |
| `1_aifflow_k8s_MiNiFi.png` | **Finální, uživatelem dopracovaná verze** — Harbor→MiNiFi component (pull image), ConfigMap (mount as file)/K8s Secret (env var)/CyberArk, PVC→PV→Internal S3 (Parquety i checkpoint), KubernetesPodOperator, kubectl logs → Airflow | Autoritativní zdroj pravdy pro architekturu. Otevřený bod: červená dvoušipka mezi K8s clusterem a DMZ Transfer Agent = uživatelova TODO značka, zda tam Airflow musí aktivně zasahovat |
| `minifi_conjur_flow.drawio` | Můj detailní runtime flow (7 kroků: create pod → authn-k8s SA JWT↔secrets → write to emptyDir → read secrets → extract → write Parquet+watermark → exit code) | **Neodpovídá reálně zvolenému modelu secretů** (viz sekce 4 výše) — potřeba přepracovat nebo odstranit |
| `wiki_templates_k8s_ownership_draft.html` | Ownership tabulky (cluster-level / per-squad onboarding / ongoing / nikdy), psáno technicky pro K8s tým, zmínky Airflow záměrně vyřazeny | Draft, nemergnutý do `wiki_templates.html` |

---

## 6. Prezentace — stav

- **`MiNiFi_presentation_outline.md`** — obsahová osnova (Markdown), ~22 snímků, sekce A–E, průběžně se doplňuje. Publikum: širší architektonické/rozhodovací, znají už kontext AirPy→Meltano.
- **`MiNiFi_pilot.pptx`** — technický pilot v reálné firemní šabloně (`PPT_template_v2_250414.pptx`, 30 layoutů, KB/Komerční banka branding). Obsahuje 2 zkušební snímky (31, 32) navázané na obsah sekce B z osnovy, včetně vloženého oficiálního loga Apache MiNiFi. **Proces psaní přímo do XML je pomalý** → dohodnuto nejdřív domluvit celý obsah, pak stavět snímky.
- **Nástroje nainstalované v této session** (pro budoucí použití): Python 3.12, LibreOffice, Poppler (`pdftoppm`) — plný pipeline `.pptx → PDF → JPEG` běží headless bez oken. **Gotcha:** PowerShell `Compress-Archive` tiše vynechá `[Content_Types].xml` (hranaté závorky = wildcard) → zabalovat přes .NET `ZipArchive` API napřímo.

---

## 7. Otevřené otázky / co ještě není hotové

1. **Secrets model rozpor** — `minifi_conjur_flow.drawio` (authn-k8s) vs. skutečně zvolený model (K8s Secret + env var, self-service po onboarding grantu). Potřeba sjednotit dokumentaci.
2. **"Amount of custom code"** řádek v `wiki_eval.html` sekce 3 — přidán, nikdy nevyplněn.
3. **Sekce 9 v `wiki_eval.html`** — stále stará verze (AirPy doporučeno). Nová verze jen v `wiki_eval_ch9_draft.html`, nemergnuto.
4. **`wiki_templates_k8s_ownership_draft.html`** — nemergnuto do `wiki_templates.html`.
5. **PV = S3-backed** — logický závěr z diagramu, neověřeno s platformním/K8s týmem.
6. **Červená dvoušipka** v `1_aifflow_k8s_MiNiFi.png` (SOGE↔DMZ) — čeká na vyjasnění s kolegou, jestli tam Airflow musí aktivně zasahovat.
7. **DDL Registry** v `1_aifflow.drawio` — směr a význam nejasný, označeno TBC.
8. **Dostupnost procesorů** (Parquet, Kafka, ES, AWS) v distribuci MiNiFi Java — předpoklad, neověřeno.
9. **Wrapper pro životní cyklus podu** — MiNiFi Java nemá dokumentovaný "run-once" režim, potřeba postavit/ověřit (nebo zvážit NiFi Stateless runtime).
10. **Odhad práce v MD pro MiNiFi** — sekce 1–2 `wiki_eval.html` o něj nebyly rozšířeny.
11. **`kubernetes_conn_id` / verze provideru** pro plánované živé demo DAGu v Airflow — potřeba potvrdit proti reálné konfiguraci SOGE Airflow.
12. **Greenbook compliance pro MiNiFi** — hodnota 4 je předpoklad "stejné jako NiFi", neověřeno.

---

## 8. Kde co je (soupis souborů v `kb_jer/`)

- `README.md` — prázdné
- `ingest_req.md` — **zastaralé**, podle uživatele
- `wiki_concept.html`, `wiki_templates.html`, `wiki_eval.html` — hlavní wiki dokumenty (Confluence storage formát), git-trackované
- `wiki_eval_ch9_draft.html`, `wiki_templates_k8s_ownership_draft.html` — drafty, **nemergnuté**
- `1_aifflow.drawio`, `minifi_conjur_flow.drawio` — editovatelné diagramy
- `1_aifflow_k8s_MiNiFi.png` — finální diagram (obrázek, uživatel ho dopracoval mimo Claude)
- `k8s_minifi.pdf`, `jva_secrets.pdf` — ručně kreslené sketche (zdroj pravdy pro K8s/secrets model)
- `PPT_template_v2_250414.pptx` — firemní PPT šablona
- `MiNiFi_pilot.pptx` — technický pilot 2 snímků
- `MiNiFi_presentation_outline.md`, `MiNiFi_K8s_project_summary.md` — tento a osnovový dokument
- Git: repo `kb_jer`, remote `github.com/captjerzi-pixel/kb_jer`, identita `jerzi <capt.jerzi@gmail.com>`
