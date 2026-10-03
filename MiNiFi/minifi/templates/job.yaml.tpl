apiVersion: batch/v1
kind: Job
metadata:
  name: minifi-java-opb-task-inst-run
  namespace: ci
  labels:
    app.kubernetes.io/name: minifi-java-parquet-ingestion
    app.kubernetes.io/component: opb-task-inst-run-poc
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 43200
  ttlSecondsAfterFinished: 86400
  template:
    metadata:
      labels:
        app.kubernetes.io/name: minifi-java-parquet-ingestion
        app.kubernetes.io/component: opb-task-inst-run-poc
    spec:
      restartPolicy: Never
      imagePullSecrets:
        - name: dockerhub
      containers:
        - name: minifi
          image: dockerhub.kb.cz/dpt-sq023/one-data-platform/minifi-java-base:0.1.0-SNAPSHOT
          imagePullPolicy: Always
          command: ["/bin/bash", "-c"]
          args:
            - |
              set -euo pipefail
              tcp_proxy_pid=""
              start_tcp_proxy() {
                if [ "${TCP_PROXY_ENABLED:-false}" != "true" ]; then
                  return 0
                fi
                if [ ! -f /opt/minifi/bin/tcp_proxy.py ]; then
                  echo "TCP proxy requested but /opt/minifi/bin/tcp_proxy.py is not installed in the MiniFi image" >&2
                  exit 1
                fi
                if [ -z "${TCP_PROXY_UPSTREAM_HOST:-}" ] || [ -z "${TCP_PROXY_UPSTREAM_PORT:-}" ] || [ -z "${TCP_PROXY_LISTEN_PORT:-}" ]; then
                  echo "TCP proxy requested but TCP_PROXY_UPSTREAM_HOST, TCP_PROXY_UPSTREAM_PORT, or TCP_PROXY_LISTEN_PORT is empty" >&2
                  exit 1
                fi
                listen_host="${TCP_PROXY_LISTEN_HOST:-127.0.0.1}"
                connect_timeout="${TCP_PROXY_CONNECT_TIMEOUT_SECONDS:-15}"
                echo "Starting embedded Python TCP proxy ${listen_host}:${TCP_PROXY_LISTEN_PORT} -> ${TCP_PROXY_UPSTREAM_HOST}:${TCP_PROXY_UPSTREAM_PORT}"
                python3 /opt/minifi/bin/tcp_proxy.py \
                  --listen-host "${listen_host}" \
                  --listen-port "${TCP_PROXY_LISTEN_PORT}" \
                  --upstream-host "${TCP_PROXY_UPSTREAM_HOST}" \
                  --upstream-port "${TCP_PROXY_UPSTREAM_PORT}" \
                  --connect-timeout "${connect_timeout}" &
                tcp_proxy_pid="$!"
                sleep 1
                if ! kill -0 "${tcp_proxy_pid}" >/dev/null 2>&1; then
                  echo "Embedded Python TCP proxy failed to start" >&2
                  exit 1
                fi
              }
              cleanup_tcp_proxy() {
                if [ -n "${tcp_proxy_pid:-}" ]; then
                  echo "Stopping embedded Python TCP proxy PID ${tcp_proxy_pid}"
                  kill "${tcp_proxy_pid}" >/dev/null 2>&1 || true
                fi
              }
              trap cleanup_tcp_proxy EXIT
              conf_dir=/opt/minifi/minifi-current/conf
              manifest_file=/opt/minifi/templates/table-manifest.json
              active_manifest=/tmp/minifi-active-tables.json
              mkdir -p "${conf_dir}"
              start_tcp_proxy
              s3_endpoint="${S3_HOST#http://}"
              s3_endpoint="${s3_endpoint#https://}"
              s3_endpoint="${s3_endpoint%%/*}"
              echo "Preparing S3 truststore for ${s3_endpoint}"
              keytool -printcert -rfc -sslserver "${s3_endpoint}" > /tmp/s3-cert-chain.pem
              keytool -importcert -noprompt -storetype "${S3_TRUSTSTORE_TYPE}" -keystore "${S3_TRUSTSTORE_PATH}" -storepass "${S3_TRUSTSTORE_PASSWORD}" -alias s3-object-store -file /tmp/s3-cert-chain.pem >/dev/null
              render_vars='${DATABASE_URL} ${DATABASE_DRIVER_CLASS} ${DATABASE_DRIVER_LOCATION} ${DATABASE_USER} ${DATABASE_PASSWORD} ${DATABASE_VALIDATION_QUERY} ${FETCH_SIZE} ${MAX_ROWS_PER_FLOW_FILE} ${OUTPUT_BATCH_SIZE} ${S3_HOST} ${S3_ACCESS_KEY} ${S3_ACCESS_SECRET} ${S3_BUCKET_NAME} ${S3_REGION_SELECTOR} ${S3_REGION} ${S3_PREFIX} ${S3_COMMUNICATIONS_TIMEOUT} ${S3_MULTIPART_THRESHOLD} ${S3_MULTIPART_PART_SIZE} ${S3_USE_CHUNKED_ENCODING} ${S3_USE_PATH_STYLE_ACCESS} ${S3_TRUSTSTORE_PATH} ${S3_TRUSTSTORE_PASSWORD} ${S3_TRUSTSTORE_TYPE} ${RUN_TIMESTAMP} ${PARQUET_ROW_GROUP_SIZE} ${PARQUET_PAGE_SIZE} ${RUN_SCHEDULE} ${PARQUET_COMPRESSION} ${HADOOP_CONFIGURATION_RESOURCES} ${NIFI_SENSITIVE_PROPS_KEY}'
              envsubst "${render_vars}" < /opt/minifi/templates/bootstrap.conf.template > "${conf_dir}/bootstrap.conf"
              envsubst "${render_vars}" < /opt/minifi/templates/minifi.properties.template > "${conf_dir}/minifi.properties"
              envsubst "${render_vars}" < /opt/minifi/templates/minifi-env.properties.template > "${conf_dir}/minifi-env.properties"
              sed -i "s|<root level="INFO">|<root level="${MINIFI_ROOT_LOG_LEVEL:-INFO}">|" "${conf_dir}/logback.xml"
              sed -i "s|<logger name="org.apache.nifi.processors" level="WARN"/>|<logger name="org.apache.nifi.processors" level="${MINIFI_PROCESSOR_LOG_LEVEL:-DEBUG}"/>|" "${conf_dir}/logback.xml"
              envsubst "${render_vars}" < /opt/minifi/templates/flow-jdbc-to-s3-parquet.json.raw.template > /tmp/flow-unfiltered.json.raw
              python3 - <<'PY'
              import json
              import os
              import sys

              sys.path.insert(0, '/opt/minifi/templates')
              from finalize_s3_manifest import S3Client

              with open('/opt/minifi/templates/table-manifest.json', 'r', encoding='utf-8') as handle:
                  manifest = json.load(handle)
              with open('/tmp/flow-unfiltered.json.raw', 'r', encoding='utf-8') as handle:
                  flow = json.load(handle)

              active = []
              skipped = []
              inactive_ids = set()
              s3 = S3Client()
              for table in manifest['tables']:
                  manifest_key = table['manifestKey'].replace('${RUN_TIMESTAMP}', os.environ['RUN_TIMESTAMP'])
                  s3_manifest = s3.get_json(manifest_key, not_found_is_none=True)
                  s3_complete = (
                    isinstance(s3_manifest, dict)
                    and s3_manifest.get('status') == 'SUCCESS'
                    and s3_manifest.get('batch_id') == os.environ['RUN_TIMESTAMP']
                    and s3_manifest.get('dataset') == f"{table['schema']}.{table['name']}"
                  )
                  if s3_manifest is not None and not s3_complete:
                      raise RuntimeError(f"Invalid S3 manifest for {table['schema']}.{table['name']} at {manifest_key}")
                  if s3_complete:
                    skipped.append(table)
                    print(f"S3 manifest found for {table['schema']}.{table['name']} at {manifest_key}; disabling table branch.")
                    slug = table['slug']
                    inactive_ids.update({
                      f"execute-sql-record__{slug}",
                      f"parquet-filename__{slug}",
                      f"put-s3-object__{slug}",
                    })
                  else:
                      active.append(table)

              root = flow['rootGroup']
              root['processors'] = [processor for processor in root['processors'] if processor.get('instanceIdentifier') not in inactive_ids]
              root['connections'] = [
                  connection for connection in root['connections']
                  if connection.get('source', {}).get('name') not in inactive_ids
                  and connection.get('destination', {}).get('name') not in inactive_ids
              ]
              with open('/tmp/minifi-active-tables.json', 'w', encoding='utf-8') as handle:
                  json.dump({'tables': active, 'skipped': skipped}, handle, indent=2)
              with open('/opt/minifi/minifi-current/conf/flow.json.raw', 'w', encoding='utf-8') as handle:
                  json.dump(flow, handle, indent=2)
              print(f"MiniFi table planning: active={len(active)} skipped={len(skipped)}")
              PY
              if [ "$(python3 -c 'import json; print(len(json.load(open("/tmp/minifi-active-tables.json"))["tables"]))')" = "0" ]; then
                echo "All tables already have completed S3 manifests; nothing to run."
                exit 0
              fi
              python3 - <<'PY'
              import json
              with open('/tmp/minifi-active-tables.json', 'r', encoding='utf-8') as handle:
                  active = json.load(handle)
              validation_tables = [table for table in active['tables'] if table.get('schemaDefinition')]
              with open('/tmp/minifi-schema-validation.json', 'w', encoding='utf-8') as handle:
                  json.dump({'tables': validation_tables}, handle)
              print(f"Schema validation planning: tables_with_schema={len(validation_tables)}")
              PY
              if [ "$(python3 -c 'import json; print(len(json.load(open("/tmp/minifi-schema-validation.json"))["tables"]))')" != "0" ]; then
                cat >/tmp/SchemaValidator.java <<'JAVA'
              import com.fasterxml.jackson.databind.JsonNode;
              import com.fasterxml.jackson.databind.ObjectMapper;
              import java.nio.file.Paths;
              import java.sql.Connection;
              import java.sql.DriverManager;
              import java.sql.PreparedStatement;
              import java.sql.ResultSetMetaData;
              import java.util.ArrayList;
              import java.util.HashSet;
              import java.util.List;
              import java.util.Locale;
              import java.util.Set;

              public class SchemaValidator {
                static String normalize(String value) {
                  if (value == null) return "";
                  String type = value.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]", "");
                  if (type.contains("char") || type.equals("text") || type.equals("varchar") || type.equals("nvarchar")) return "string";
                  if (type.contains("int") || type.equals("bigserial") || type.equals("serial")) return "integer";
                  if (type.contains("numeric") || type.contains("decimal") || type.contains("money")) return "decimal";
                  if (type.contains("double") || type.contains("float") || type.contains("real")) return "float";
                  if (type.contains("date") && !type.contains("time")) return "date";
                  if (type.contains("time") || type.contains("timestamp")) return "timestamp";
                  if (type.contains("bool") || type.equals("bit")) return "boolean";
                  return type;
                }

                static String probeQuery(String query) {
                  String stripped = query.trim().replaceAll(";+\s*$", "");
                  return "SELECT * FROM (" + stripped + ") minifi_schema_probe WHERE 1=0";
                }

                public static void main(String[] args) throws Exception {
                  Class.forName(args[0]);
                  ObjectMapper mapper = new ObjectMapper();
                  JsonNode root = mapper.readTree(Paths.get(args[3]).toFile());
                  boolean failed = false;
                  try (Connection connection = DriverManager.getConnection(args[1], args[4], args[5])) {
                    for (JsonNode table : root.path("tables")) {
                      String tableName = table.path("schema").asText() + "." + table.path("name").asText();
                      JsonNode schema = table.path("schemaDefinition");
                      System.out.println("Schema validation starting: " + tableName + " schemaRef=" + schema.path("schemaRef").asText());
                      List<String> actualNames = new ArrayList<>();
                      List<String> actualTypes = new ArrayList<>();
                      try (PreparedStatement statement = connection.prepareStatement(probeQuery(table.path("query").asText()))) {
                        ResultSetMetaData meta = statement.getMetaData();
                        for (int index = 1; index <= meta.getColumnCount(); index++) {
                          actualNames.add(meta.getColumnLabel(index));
                          actualTypes.add(meta.getColumnTypeName(index));
                        }
                      }
                      Set<String> actualLower = new HashSet<>();
                      for (String actualName : actualNames) actualLower.add(actualName.toLowerCase(Locale.ROOT));
                      List<String> expectedNames = new ArrayList<>();
                      for (JsonNode column : schema.path("columns")) {
                        expectedNames.add(column.path("name").asText());
                        boolean required = !column.has("required") || column.path("required").asBoolean(true);
                        String expectedName = column.path("name").asText();
                        if (required && !actualLower.contains(expectedName.toLowerCase(Locale.ROOT))) {
                          System.err.println("SCHEMA_VALIDATION_FAILED missing required column " + expectedName + " for " + tableName);
                          failed = true;
                        }
                        String expectedType = normalize(column.path("type").asText(""));
                        if (!expectedType.isEmpty()) {
                          for (int index = 0; index < actualNames.size(); index++) {
                            if (actualNames.get(index).equalsIgnoreCase(expectedName)) {
                              String actualType = normalize(actualTypes.get(index));
                              if (!actualType.equals(expectedType)) {
                                System.err.println("SCHEMA_VALIDATION_FAILED type mismatch for " + tableName + "." + expectedName + ": expected=" + expectedType + " actual=" + actualType + " rawActual=" + actualTypes.get(index));
                                failed = true;
                              }
                            }
                          }
                        }
                      }
                      if (!schema.path("allowExtraColumns").asBoolean(true)) {
                        Set<String> expectedLower = new HashSet<>();
                        for (String expectedName : expectedNames) expectedLower.add(expectedName.toLowerCase(Locale.ROOT));
                        for (String actualName : actualNames) {
                          if (!expectedLower.contains(actualName.toLowerCase(Locale.ROOT))) {
                            System.err.println("SCHEMA_VALIDATION_FAILED unexpected column " + actualName + " for " + tableName);
                            failed = true;
                          }
                        }
                      }
                      if (schema.path("strictColumnOrder").asBoolean(false)) {
                        if (expectedNames.size() != actualNames.size()) {
                          System.err.println("SCHEMA_VALIDATION_FAILED column count mismatch for " + tableName + ": expected=" + expectedNames.size() + " actual=" + actualNames.size());
                          failed = true;
                        } else {
                          for (int index = 0; index < expectedNames.size(); index++) {
                            if (!expectedNames.get(index).equalsIgnoreCase(actualNames.get(index))) {
                              System.err.println("SCHEMA_VALIDATION_FAILED column order mismatch for " + tableName + " at position " + (index + 1) + ": expected=" + expectedNames.get(index) + " actual=" + actualNames.get(index));
                              failed = true;
                            }
                          }
                        }
                      }
                      System.out.println("Schema validation columns for " + tableName + ": actual=" + actualNames.size() + " expected=" + expectedNames.size());
                    }
                  }
                  if (failed) throw new IllegalStateException("One or more table schemas failed validation");
                  System.out.println("Schema validation completed successfully");
                }
              }
              JAVA
                java -cp "${DATABASE_DRIVER_LOCATION}:/opt/minifi/minifi-current/lib/*" /tmp/SchemaValidator.java "${DATABASE_DRIVER_CLASS}" "${DATABASE_URL}" unused /tmp/minifi-schema-validation.json "${DATABASE_USER}" "${DATABASE_PASSWORD}"
              fi
              cat > "${conf_dir}/core-site.xml" <<EOF
              <?xml version="1.0" encoding="UTF-8"?>
              <configuration>
                <property>
                  <name>fs.defaultFS</name>
                  <value>${HADOOP_DEFAULT_FS:-file:///}</value>
                </property>
              </configuration>
              EOF
              gzip -c "${conf_dir}/flow.json.raw" > "${conf_dir}/flow.json.gz"
              echo "Starting MiNiFi Java JDBC to Parquet sample"
              echo "Database URL: ${DATABASE_URL}"
              echo "Output S3 prefix: ${S3_PREFIX}/${RUN_TIMESTAMP}"
              echo "Active table manifest:"
              cat "${active_manifest}"
              /opt/minifi/minifi-current/bin/minifi.sh start
              tail -n +1 -F /opt/minifi/minifi-current/logs/minifi-app.log /opt/minifi/minifi-current/logs/minifi-bootstrap.log &
              tail_pid=$!
              trap '/opt/minifi/minifi-current/bin/minifi.sh stop 2>/dev/null || true; kill ${tail_pid} 2>/dev/null || true' EXIT
              poll_seconds="${MINIFI_JOB_POLL_SECONDS:-5}"
              required_idle_checks="${MINIFI_JOB_IDLE_CHECKS:-3}"
              idle_checks=0
              idle_started_at=0
              previous_count=-1
              deadline=$(( $(date +%s) + 43200 ))
              while true; do
                sleep "${poll_seconds}"
                now=$(date +%s)
                connection_report=$(/opt/minifi/minifi-current/bin/minifi.sh flowStatus 'connection:all:health' 2>/dev/null | sed -n 's/^.*Command //p' | tail -1 || true)
                processor_report=$(/opt/minifi/minifi-current/bin/minifi.sh flowStatus 'processor:all:stats' 2>/dev/null | sed -n 's/^.*Command //p' | tail -1 || true)
                queued_count=$(python3 -c 'import json,sys; data=json.loads(sys.argv[1]) if len(sys.argv)>1 and sys.argv[1] else {}; print(sum((c.get("connectionHealth") or {}).get("queuedCount",0) for c in (data.get("connectionStatusList") or [])))' "${connection_report}" 2>/dev/null || echo 999999)
                active_threads=$(python3 -c 'import json,sys; data=json.loads(sys.argv[1]) if len(sys.argv)>1 and sys.argv[1] else {}; print(sum((p.get("processorStats") or {}).get("activeThreads",0) for p in (data.get("processorStatusList") or [])))' "${processor_report}" 2>/dev/null || echo 999999)
                s3_sent=$(python3 -c 'import json,sys; data=json.loads(sys.argv[1]) if len(sys.argv)>1 and sys.argv[1] else {}; print(sum((p.get("processorStats") or {}).get("flowfilesSent",0) for p in (data.get("processorStatusList") or []) if str(p.get("id","")).startswith("put-s3-object__")))' "${processor_report}" 2>/dev/null || echo 0)
                s3_bytes=$(python3 -c 'import json,sys; data=json.loads(sys.argv[1]) if len(sys.argv)>1 and sys.argv[1] else {}; print(sum((p.get("processorStats") or {}).get("bytesRead",0) for p in (data.get("processorStatusList") or []) if str(p.get("id","")).startswith("put-s3-object__")))' "${processor_report}" 2>/dev/null || echo 0)
                if grep -qE '(Failed to put|Failed to process|Unable to execute SQL|SQLException|validation.*failed)' /opt/minifi/minifi-current/logs/minifi-app.log 2>/dev/null; then
                  echo "Detected MiniFi processor error in logs" >&2
                  exit 1
                fi
                if [ "${queued_count}" = "0" ] && [ "${active_threads}" = "0" ] && [ "${s3_sent}" = "${previous_count}" ]; then
                  if [ "${idle_checks}" = "0" ]; then
                    idle_started_at="${now}"
                  fi
                  idle_checks=$((idle_checks + 1))
                else
                  idle_checks=0
                  idle_started_at=0
                fi
                previous_count="${s3_sent}"
                idle_elapsed_seconds=0
                if [ "${idle_started_at}" -gt 0 ]; then
                  idle_elapsed_seconds=$((now - idle_started_at))
                fi
                echo "Job monitor: tables=${TABLE_COUNT:-1} s3_sent=${s3_sent} s3_bytes=${s3_bytes} queued_count=${queued_count} active_threads=${active_threads} idle_checks=${idle_checks}/${required_idle_checks} idle_elapsed_seconds=${idle_elapsed_seconds}"
                if [ "${idle_checks}" -ge "${required_idle_checks}" ]; then
                  echo "MiNiFi job is idle and output is stable; stopping."
                  break
                fi
                if [ "${now}" -ge "${deadline}" ]; then
                  echo "Timed out waiting for MiNiFi job completion" >&2
                  exit 124
                fi
                /opt/minifi/minifi-current/bin/minifi.sh status >/tmp/minifi-status.log 2>&1 || { cat /tmp/minifi-status.log; exit 1; }
              done
              export FINISHED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
              echo "Finalizing authoritative S3 manifests"
              python3 /opt/minifi/templates/finalize_s3_manifest.py
          envFrom:
            - configMapRef:
                name: minifi-java-parquet-runtime
          env:
            - name: DATABASE_USER
              valueFrom:
                secretKeyRef:
                  name: minifi-database
                  key: username
            - name: DATABASE_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: minifi-database
                  key: password
            - name: S3_HOST
              valueFrom:
                secretKeyRef:
                  name: minifi-s3
                  key: host
            - name: S3_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: minifi-s3
                  key: accessKey
            - name: S3_ACCESS_SECRET
              valueFrom:
                secretKeyRef:
                  name: minifi-s3
                  key: accessSecret
            - name: S3_BUCKET_NAME
              valueFrom:
                secretKeyRef:
                  name: minifi-s3
                  key: bucketName
          resources:
            requests:
              cpu: "4"
              memory: 8Gi
            limits:
              cpu: "4"
              memory: 8Gi
          volumeMounts:
            - name: minifi-flow-templates
              mountPath: /opt/minifi/templates
              readOnly: true
      volumes:
        - name: minifi-flow-templates
          configMap:
            name: minifi-java-parquet-flow
