# Base Java MiNiFi image with JDBC, Kafka, Parquet, and AWS/S3 extensions.
# Jenkins builds this from the repository root using the internal KB base image.
# Reuse this image for MiNiFi pipelines by mounting flow/config templates at runtime.

FROM dockerhub.kb.cz/ps-sq002-pub/python:3.11-slim

LABEL applicationId="one-data-platform"
LABEL componentId="minifi-java-base"
LABEL companyId="kb"

ARG MINIFI_VERSION=2.12.0
ARG NIFI_VERSION=${MINIFI_VERSION}
ARG MSSQL_JDBC_VERSION=12.10.1.jre11
ARG POSTGRES_JDBC_VERSION=42.7.7
ARG ORACLE_JDBC_VERSION=23.26.3.0.0
ARG TERADATA_JDBC_VERSION=20.00.00.60
ARG MAVEN_REPOSITORY_URL=https://artifactory.kb.cz/artifactory/public-maven
ARG JENKINS_TECH_USER
ARG JENKINS_TECH_PASS
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy
ARG NO_PROXY
ARG no_proxy

COPY minifi/runtime/tcp_proxy.py /opt/minifi/bin/tcp_proxy.py

RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gettext-base gnupg procps unzip; \
    curl --insecure --connect-timeout 30 --max-time 300 --retry 3 --retry-delay 5 -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg; \
    echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main' > /etc/apt/sources.list.d/microsoft-prod.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends msopenjdk-21; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --system minifi; \
    useradd --system --gid minifi --home-dir /opt/minifi --shell /usr/sbin/nologin minifi; \
    mkdir -p /opt/minifi /opt/minifi/bin /opt/minifi/jdbc-drivers /opt/minifi/extra-nars; \
    auth_header="Authorization: Basic $(printf '%s:%s' "${JENKINS_TECH_USER}" "${JENKINS_TECH_PASS}" | base64 | tr -d '\n')"; \
    curl --connect-timeout 30 --max-time 900 --retry 3 --retry-delay 10 -fsSL \
      -H "${auth_header}" \
      "http://jenkins:8080/userContent/minifi/minifi-${MINIFI_VERSION}-bin.zip" \
      -o /tmp/minifi.zip; \
    unzip -q /tmp/minifi.zip -d /opt/minifi; \
    rm /tmp/minifi.zip; \
    ln -s "/opt/minifi/minifi-${MINIFI_VERSION}" /opt/minifi/minifi-current; \
    curl --insecure --connect-timeout 30 --max-time 300 --retry 3 --retry-delay 5 -fsSL \
      -H "${auth_header}" \
      "${MAVEN_REPOSITORY_URL}/com/microsoft/sqlserver/mssql-jdbc/${MSSQL_JDBC_VERSION}/mssql-jdbc-${MSSQL_JDBC_VERSION}.jar" \
      -o /opt/minifi/jdbc-drivers/mssql-jdbc.jar; \
    curl --insecure --connect-timeout 30 --max-time 300 --retry 3 --retry-delay 5 -fsSL \
      -H "${auth_header}" \
      "${MAVEN_REPOSITORY_URL}/org/postgresql/postgresql/${POSTGRES_JDBC_VERSION}/postgresql-${POSTGRES_JDBC_VERSION}.jar" \
      -o /opt/minifi/jdbc-drivers/postgresql-jdbc.jar; \
    curl --insecure --connect-timeout 30 --max-time 300 --retry 3 --retry-delay 5 -fsSL \
      -H "${auth_header}" \
      "${MAVEN_REPOSITORY_URL}/com/oracle/database/jdbc/ojdbc11/${ORACLE_JDBC_VERSION}/ojdbc11-${ORACLE_JDBC_VERSION}.jar" \
      -o /opt/minifi/jdbc-drivers/oracle-jdbc.jar; \
    curl --insecure --connect-timeout 30 --max-time 300 --retry 3 --retry-delay 5 -fsSL \
      -H "${auth_header}" \
      "${MAVEN_REPOSITORY_URL}/com/teradata/jdbc/terajdbc/${TERADATA_JDBC_VERSION}/terajdbc-${TERADATA_JDBC_VERSION}.jar" \
      -o /opt/minifi/jdbc-drivers/teradata-jdbc.jar; \
    curl --insecure --connect-timeout 30 --max-time 300 --retry 3 --retry-delay 5 -fsSL \
      -H "${auth_header}" \
      "${MAVEN_REPOSITORY_URL}/org/apache/nifi/nifi-parquet-nar/${NIFI_VERSION}/nifi-parquet-nar-${NIFI_VERSION}.nar" \
      -o /opt/minifi/extra-nars/nifi-parquet-nar.nar; \
    curl --insecure --connect-timeout 30 --max-time 300 --retry 3 --retry-delay 5 -fsSL \
      -H "${auth_header}" \
      "${MAVEN_REPOSITORY_URL}/org/apache/nifi/nifi-hadoop-libraries-nar/${NIFI_VERSION}/nifi-hadoop-libraries-nar-${NIFI_VERSION}.nar" \
      -o /opt/minifi/extra-nars/nifi-hadoop-libraries-nar.nar; \
    curl --insecure --connect-timeout 30 --max-time 300 --retry 3 --retry-delay 5 -fsSL \
      -H "${auth_header}" \
      "${MAVEN_REPOSITORY_URL}/org/apache/nifi/nifi-aws-service-api-nar/${NIFI_VERSION}/nifi-aws-service-api-nar-${NIFI_VERSION}.nar" \
      -o /opt/minifi/extra-nars/nifi-aws-service-api-nar.nar; \
    curl --insecure --connect-timeout 30 --max-time 300 --retry 3 --retry-delay 5 -fsSL \
      -H "${auth_header}" \
      "${MAVEN_REPOSITORY_URL}/org/apache/nifi/nifi-aws-nar/${NIFI_VERSION}/nifi-aws-nar-${NIFI_VERSION}.nar" \
      -o /opt/minifi/extra-nars/nifi-aws-nar.nar; \
    curl --insecure --connect-timeout 30 --max-time 300 --retry 3 --retry-delay 5 -fsSL \
      -H "${auth_header}" \
      "${MAVEN_REPOSITORY_URL}/org/apache/nifi/nifi-kafka-nar/${NIFI_VERSION}/nifi-kafka-nar-${NIFI_VERSION}.nar" \
      -o /opt/minifi/extra-nars/nifi-kafka-nar.nar; \
    cp /opt/minifi/extra-nars/*.nar /opt/minifi/minifi-current/lib/; \
    chmod 0755 /opt/minifi/bin/tcp_proxy.py; \
    chown -R minifi:minifi /opt/minifi

USER minifi
WORKDIR /opt/minifi/minifi-current

ENV JAVA_HOME=/usr/lib/jvm/msopenjdk-21-amd64 \
    JDBC_DRIVER_DIR=/opt/minifi/jdbc-drivers \
    MSSQL_JDBC_JAR=/opt/minifi/jdbc-drivers/mssql-jdbc.jar \
    POSTGRES_JDBC_JAR=/opt/minifi/jdbc-drivers/postgresql-jdbc.jar \
    ORACLE_JDBC_JAR=/opt/minifi/jdbc-drivers/oracle-jdbc.jar \
    TERADATA_JDBC_JAR=/opt/minifi/jdbc-drivers/teradata-jdbc.jar
