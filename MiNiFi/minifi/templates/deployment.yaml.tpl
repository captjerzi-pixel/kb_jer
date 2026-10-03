apiVersion: apps/v1
kind: Deployment
metadata:
  name: minifi-java-opb-task-inst-run
  namespace: ci
  labels:
    app.kubernetes.io/name: minifi-java-parquet-ingestion
    app.kubernetes.io/component: opb-task-inst-run-poc
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: minifi-java-parquet-ingestion
      app.kubernetes.io/component: opb-task-inst-run-poc
  template:
    metadata:
      labels:
        app.kubernetes.io/name: minifi-java-parquet-ingestion
        app.kubernetes.io/component: opb-task-inst-run-poc
    spec:
      restartPolicy: Always
      terminationGracePeriodSeconds: 60
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
              mkdir -p "${conf_dir}"
              start_tcp_proxy
              s3_endpoint="${S3_HOST#http://}"
              s3_endpoint="${s3_endpoint#https://}"
              s3_endpoint="${s3_endpoint%%/*}"
              echo "Preparing S3 truststore for ${s3_endpoint}"
              keytool -printcert -rfc -sslserver "${s3_endpoint}" > /tmp/s3-cert-chain.pem
              keytool -importcert -noprompt -storetype "${S3_TRUSTSTORE_TYPE}" -keystore "${S3_TRUSTSTORE_PATH}" -storepass "${S3_TRUSTSTORE_PASSWORD}" -alias s3-object-store -file /tmp/s3-cert-chain.pem >/dev/null
              render_vars='${DATABASE_URL} ${DATABASE_DRIVER_CLASS} ${DATABASE_DRIVER_LOCATION} ${DATABASE_USER} ${DATABASE_PASSWORD} ${DATABASE_VALIDATION_QUERY} ${SQL_SELECT_QUERY} ${FETCH_SIZE} ${MAX_ROWS_PER_FLOW_FILE} ${OUTPUT_BATCH_SIZE} ${MAX_TIMER_DRIVEN_THREAD_COUNT} ${EXECUTE_SQL_CONCURRENCY} ${UPDATE_ATTRIBUTE_CONCURRENCY} ${PUT_PARQUET_CONCURRENCY} ${S3_UPLOAD_CONCURRENCY} ${S3_HOST} ${S3_ACCESS_KEY} ${S3_ACCESS_SECRET} ${S3_BUCKET_NAME} ${S3_REGION_SELECTOR} ${S3_REGION} ${S3_PREFIX} ${S3_COMMUNICATIONS_TIMEOUT} ${S3_MULTIPART_THRESHOLD} ${S3_MULTIPART_PART_SIZE} ${S3_USE_CHUNKED_ENCODING} ${S3_USE_PATH_STYLE_ACCESS} ${S3_TRUSTSTORE_PATH} ${S3_TRUSTSTORE_PASSWORD} ${S3_TRUSTSTORE_TYPE} ${S3_RETRY_COUNT} ${RUN_TIMESTAMP} ${TABLE_SCHEMA} ${TABLE_NAME} ${PARQUET_ROW_GROUP_SIZE} ${PARQUET_PAGE_SIZE} ${RUN_SCHEDULE} ${OUTPUT_FILENAME_PREFIX} ${PARQUET_COMPRESSION} ${HADOOP_CONFIGURATION_RESOURCES} ${NIFI_SENSITIVE_PROPS_KEY}'
              envsubst "${render_vars}" < /opt/minifi/templates/bootstrap.conf.template > "${conf_dir}/bootstrap.conf"
              envsubst "${render_vars}" < /opt/minifi/templates/minifi.properties.template > "${conf_dir}/minifi.properties"
              envsubst "${render_vars}" < /opt/minifi/templates/minifi-env.properties.template > "${conf_dir}/minifi-env.properties"
              sed -i "s|<root level=\"INFO\">|<root level=\"${MINIFI_ROOT_LOG_LEVEL:-INFO}\">|" "${conf_dir}/logback.xml"
              sed -i "s|<logger name=\"org.apache.nifi.processors\" level=\"WARN\"/>|<logger name=\"org.apache.nifi.processors\" level=\"${MINIFI_PROCESSOR_LOG_LEVEL:-DEBUG}\"/>|" "${conf_dir}/logback.xml"
              envsubst "${render_vars}" < /opt/minifi/templates/flow-jdbc-to-s3-parquet.json.raw.template > "${conf_dir}/flow.json.raw"
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
              echo "Output S3 prefix: ${S3_PREFIX}/${RUN_TIMESTAMP}/${TABLE_SCHEMA}/${TABLE_NAME}/data"
              /opt/minifi/minifi-current/bin/minifi.sh start
              tail -n +1 -F /opt/minifi/minifi-current/logs/minifi-app.log /opt/minifi/minifi-current/logs/minifi-bootstrap.log &
              tail_pid=$!
              while true; do
                sleep "${MINIFI_STATUS_PERIOD_SECONDS:-60}"
                echo "--- MiNiFi flow status $(date -Iseconds) ---"
                printf '%s\n' "${MINIFI_STATUS_QUERIES:-instance:health}" | while IFS= read -r query; do
                  [ -n "${query}" ] || continue
                  echo "Flow status query: ${query}"
                  /opt/minifi/minifi-current/bin/minifi.sh flowStatus "${query}" || true
                done
                /opt/minifi/minifi-current/bin/minifi.sh status >/tmp/minifi-status.log 2>&1 || {
                  cat /tmp/minifi-status.log
                  kill "${tail_pid}" 2>/dev/null || true
                  exit 1
                }
              done
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
