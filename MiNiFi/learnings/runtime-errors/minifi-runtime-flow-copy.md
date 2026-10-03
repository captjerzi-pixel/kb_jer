---
title: "Runtime MiNiFi flow lze kopírovat jen z běžícího podu"
date: 2026-10-02
category: runtime-errors
component: minifi-kubernetes
tags: [minifi, kubernetes, kubectl-cp, debugging, flow-json]
file_type: checklist
---

# Získání runtime MiNiFi flow z Kubernetes Jobu

`kubectl cp` používá exec do běžícího kontejneru. Z podu ve fázi `Succeeded` nebo `Completed` už nelze stáhnout `/opt/minifi/minifi-current/conf/flow.json.raw`, i když objekt podu stále existuje.

Pro dočasnou diagnostiku je potřeba po finalizaci S3 manifestu ponechat shell v procesu, například pomocí `sleep 1800`. Flow lze potom stáhnout během fáze `Running`:

- `/opt/minifi/minifi-current/conf/flow.json.raw` je skutečný runtime-rendered flow.
- `/opt/minifi/minifi-current/conf/flow.json.gz` je komprimovaná varianta.
- `ttlSecondsAfterFinished` samo o sobě nestačí; zachová objekt, ale neumožní exec do ukončeného kontejneru.

Po stažení flow je vhodné dočasný debug hold odstranit.
