# Návrh architektury exportní vrstvy pro Databricks - Parquet tool

## Kontext

Potřebujeme realizovat export dat z primárních systémů do Databricks jako součást migrace do cloudu.

### Zdrojové systémy

- Oracle
- Microsoft SQL Server
- PostgreSQL
- MySQL
- Elasticsearch
- Kafka

### Cíl

Generování datových souborů z interních systémů v KB ve formátu Parquet  a jejich ukládání do interního S3 úložiště.

### Bezpečnostní požadavek

Z bezpečnostních důvodů není možné dát Databricks přímý přístup do primárních systémů. Architektura řešení proto předpokládá:

- Generování Parquetů **přímo v On-Premise prostředí primárních systémů KB** 
- Přenos hotových Parquetů přes firewall do AWS

### Předpokládaný objem

- cca 500 různých exportů
- maximální velikost jednoho exportu přibližně menší desítky GB
- celkem cca 480 tisíc loadů za 3 měsíce z Informatiky
- více než 98 % loadů z Informatiky obsahuje méně než 10 milionů záznamů
- v rámci jednoho loadu (z Informatiky i Adoki) se aktuálně průměrně přenese asi 13 objektů

### Migrace dosavadních procesů

V rámci migrace do cloudu bude nutné do generátoru parquetů převést:

- cca 400 loadů dosud generovaných přes Informatické workflow z primárních systémů
- cca 100 importů dat dosud řešených přes Offloader/Adoki jako import do Cloudery

---

# Hlavní požadavky

1. Umožnit vývoj nových exportů pouze pomocí konfigurace.
2. Zachovat ownership business flow a orchestrace pro jednotlivé squady.
3. Zajistit dobrou škálovatelnost pro případné zvládnutí objemných přenosů dat.
4. Minimalizovat složitost migrace z dosavadních Informatických workflow a Cloudera importů.
5. Parquety by měly být generované 1:1 vůči zdroji. 
6. Tool musí umožnit generování inkrementů.

---

# Navrhovaná architektura

```text
            Enterprise Orchestration Platform
                          │
                          ▼
            Squad Business Orchestration
                          │
                          ▼
            Invoke Generic Export Process
                          │
                          ▼
                  Generic Export Framework
                          │
                          ▼
                        Parquet
                          │
                          ▼
                        AWS
                          │
                          ▼
                      Databricks
                          │
                          ▼
                Ingest + Transformation
```

---

# Rozdělení odpovědností

## Business Squad - vlastník business procesu injestu

Squad vlastní business proces. Definuje scheduling injestů. Tam kde nestačí definovat časový plán, tam definuje spouštěcí podmínky, kterými lze na primárním systému ověřit dostupnost nových dat. 

**Squad má operační možnosti**:
- **Dokončit nedokončený export** - Pokud export selhal nebo se zasekl uprostřed běhu, squad si může vynutit dokončit zbývající část bez ztráty již zpracovaných dat (watermark uchová pozici)
- **Ručně spustit nový export** - Squad si může kdykoliv vynutit nový běh exportu (i mimo plán), ať už jako FULL nebo DELTA (podle konfigurace)
- **Znovu spuštění běhu - i s jinými parametry** - Squad si může upravit `exports.yaml` a znovu spustit s novými parametry (např. jiný watermark, jiný load_mode)

**Squad je vlastníkem konfigurace**:
- Definuje `exports.yaml` (co se exportuje, jak se exportuje)
- Definuje `*.schema.yaml` (strukturu dat ze zdrojů)
- Spravuje konfiguraci ve svém squad repository
- Rozhoduje o `load_mode`, `validate_schema`, `watermark_column`, apod.

**Squad odpovídá za**:
- Správnost konfigurace exportů
- Řešení problémů způsobených chybnou konfigurací (špatný sloupec, nesprávný watermark, atd.)
- Detekci a řešení neočekávaných změn v primárním systému (schémata, dostupnost dat)
- Komunikaci se zdrojem dat při změnách datové struktury
- Aktualnost schémat a watermark resetování v případě chyb

---

## Parquet tool - tým

Parquet tool tým vlastní Generic Export Framework.

Odpovídá za:

- Oracle exporter
- MSSQL exporter
- PostgreSQL exporter
- MySQL exporter
- Elasticsearch exporter
- Kafka exporter
- Zpracování FULL i INCREMENTAL exportů (včetně řízení watermarků)
- Parquet writer
- společné logování
- monitoring
- retry logiku

Klíčová schopnost: Framework musí umět zpracovat inkrementální čtení ze zdrojů (pokud je k dispozici watermark_column) a generovat pouze relevantní delty. Framework dělá přesně to, co je konfigurováno - **nerozhoduje za squad** o změně load_mode či jiných režimů.

---

# Export Jobs

Joby, které budou generovat parquety musí být na základě YAML konfigurace vygenerované jako samostatné flow.
Flow je ohraničené jednou connectionou a sadou dat, která se přesouvají ve stejný moment.  
Každé toto flow by mělo být ve vlastnictví daného squadu z pohledu operátorů.
Squady by však neměly mít možnost zasahovat manuálně do těchto flow. 

Squady Mohou ovlivnit pouze konfiguraci na základě které se flow přegeneruje nebo posledního hodnotu Watermarku v případě, že je nutné přegenerovat inkrementální parquet.

---


# Metadata-driven přístup

Přidání nového exportu **by nemělo vyžadovat nový orchestrační proces ani nový vývoj v žádné technologii** (Python, NiFi, Informatika apod.).

Squad je **vlastníkem metadat** exportu:

- Definiuje, jaká data z kterého systému se mají exportovat
- Spravuje konfiguraci exportu ve svém GIT repository
- Framework pro implementaci je navržen jako technologicky agnostický

Výsledek:
- Squad rozhoduje **CO** se exportuje
- Framework zajišťuje **JAK** se exportuje (bez závislosti na konkrétní technologii)

## Konfigurační tabulka

Konfigurace se spravuje ve formátu YAML v GIT repository jednotlivého squadu.

Příklad konfigurace (`exports.yaml`):

```yaml
version: "1.0"

# Společné nastavení pro všechny exporty v tomto configu
export_id: CRM_EXPORTS
connection_id: CRM_DB
source_type: oracle

# VÝCHOZÍ hodnoty pro všechny objekty (lze přebít v jednotlivých objektech)
load_mode: incremental
validate_schema: breaking-changes
active: true

# Jednotlivé objekty z jednoho connectingu
exports:
  - source_object: CRM.CUSTOMER
    watermark_column: UPDATE_TS
    schema_ref: crm-customer
    # Dědí: load_mode=incremental, validate_schema=breaking-changes, active=true

  - source_object: CRM.CUSTOMER_CONTACTS
    watermark_column: MODIFIED_DATE
    target_path: /aws/customer_contacts
    load_mode: full  # PŘEBITÍ výchozího (je full, ne incremental)
    validate_schema: strict  # PŘEBITÍ výchozího
    schema_ref: crm-customer-contacts

  - source_object: CRM.ADDRESSES
    validate_schema: none  # PŘEBITÍ výchozího
    schema_ref: crm-addresses
    # Dědí: load_mode=incremental, active=true
```
```

**Klíčové aspekty:**

- `version` - verze YAML schématu (zajišťuje kompatibilitu s verzí frameworku)
- `export_id` - identifikátor exportu/datasetu na úrovni configu (např. "CRM_EXPORTS")
- `connection_id` + `source_type` - společné pro všechny objekty v tomto configu
- **Default hodnoty** na úrovni exportu (které se dědí do všech objektů):
  - `load_mode` - výchozí mód (lze přebít v jednotlivých objektech)
  - `validate_schema` - výchozí režim validace (lze přebít)
  - `active` - výchozí stav (lze přebít)
- `source_object` - identifikátor konkrétní tabulky/objektu (běží spolu s `export_id`)
- `watermark_column` - **povinný pouze pro `load_mode: incremental`**
- `target_path` - **(volitelné)** cesta v AWS; pokud chybí, vygeneruje se z `source_object` (např. `CRM.CUSTOMER` → `/aws/crm/customer`)
- `validate_schema` - `strict` (všechny změny = selhání), `breaking-changes` (jen destruktivní), `none` (žádná validace)
- `schema_ref` - odkaz na externí schéma (pokud je `validate_schema` nastaveno na cokoliv jiného než `none`)
- Jedním souborem se konfiguruje **více objektů ze stejného connectingu** (v praxi desítky položek per squad)
- **Dědičnost**: Parametry na úrovni exportu se automaticky aplikují na všechny objekty; jednotlivé objekty je mohou přebít
- Squad spravuje celý config ve svém GIT
- Framework čte tento YAML a orchestruje jednotlivé exporty
- Yaml by mohl být součástí konfigurace DAGu v Aiflow SOGE

---

# Schéma – samostatná konfigurace

Schémata jsou oddělena od `exports.yaml` do vlastního adresáře `schemas/`.

## Struktura schémat

```
squad-git-repo/
├── exports.yaml
└── schemas/
    ├── crm-customer.schema.yaml
    ├── crm-customer-contacts.schema.yaml
    └── crm-addresses.schema.yaml
```

Schémata jsou definována podle **Apache Arrow Schema** standardu: https://arrow.apache.org/docs/python/api/datatypes.html

## Příklad schématu (`schemas/crm-customer.schema.yaml`):

```yaml
version: "1.0"
name: crm/customer
source: CRM.CUSTOMER

columns:
  - name: CUSTOMER_ID
    type: bigint
    
  - name: NAME
    type: string
    
  - name: EMAIL
    type: string
    
  - name: UPDATE_TS
    type: timestamp
```

**Poznámka:** Schéma následuje Apache Arrow standard pro datové typy, který je kompatibilní s Parquet formátem a Databricks.

# Řízení inkrementálních exportů

## Watermark Management

Framework si automaticky spravuje **poslední známou hodnotu watermarku** pro každý export.
Framework si musí **persistentně uchovávat poslední watermark hodnotu** pro každý export a zajistit **idempotentní chování**.

Pokud squad zjistí, že v nějakém loadu byla chyba (např. špatná transformace, duplikáty, nebo chybná data) a potřebuje zopakovat určitý rozsah, musí mít možnost **ručně upravit poslední watermark**.


