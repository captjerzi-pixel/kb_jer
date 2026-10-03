#!/usr/bin/env python3
"""Build one authoritative manifest per dataset from immutable S3 sidecars."""
import datetime as dt
import hashlib
import hmac
import json
import os
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class S3Client:
    def __init__(self):
        endpoint = os.environ["S3_HOST"].rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            endpoint = "https://" + endpoint
        self.endpoint = endpoint
        self.access_key = os.environ["S3_ACCESS_KEY"]
        self.secret = os.environ["S3_ACCESS_SECRET"].encode()
        self.region = os.environ.get("S3_REGION", "us-east-1")
        self.bucket = os.environ["S3_BUCKET_NAME"]
        self.path_style = os.environ.get("S3_USE_PATH_STYLE_ACCESS", "true").lower() == "true"
        cert_chain = "/tmp/s3-cert-chain.pem"
        self.ssl_context = (
            ssl.create_default_context(cafile=cert_chain)
            if os.path.exists(cert_chain)
            else ssl.create_default_context()
        )

    def request(self, method, key="", query=None, body=b"", content_type=None, not_found_is_none=False):
        parsed = urllib.parse.urlsplit(self.endpoint)
        path = parsed.path.rstrip("/")
        if self.path_style:
            path += "/" + urllib.parse.quote(self.bucket, safe="")
        else:
            parsed = parsed._replace(netloc=self.bucket + "." + parsed.netloc)
        if key:
            path += "/" + urllib.parse.quote(key, safe="/")
        query = query or {}
        canonical_query = "&".join(
            f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
            for k, v in sorted(query.items())
        )
        now = dt.datetime.now(dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        headers = {
            "host": parsed.netloc,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if content_type:
            headers["content-type"] = content_type
        signed = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{headers[k].strip()}\n" for k in sorted(headers))
        canonical_request = "\n".join([method, path or "/", canonical_query, canonical_headers, signed, payload_hash])
        scope = f"{date}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
        key_date = hmac.new(b"AWS4" + self.secret, date.encode(), hashlib.sha256).digest()
        key_region = hmac.new(key_date, self.region.encode(), hashlib.sha256).digest()
        key_service = hmac.new(key_region, b"s3", hashlib.sha256).digest()
        signing_key = hmac.new(key_service, b"aws4_request", hashlib.sha256).digest()
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}"
        )
        url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path or "/", canonical_query, ""))
        request = urllib.request.Request(url, data=body or None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120, context=self.ssl_context) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404 and not_found_is_none:
                return None
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"S3 {method} failed for {key}: HTTP {error.code}: {detail[:500]}") from error

    def list_keys(self, prefix):
        keys = []
        continuation = None
        while True:
            query = {"list-type": "2", "prefix": prefix}
            if continuation:
                query["continuation-token"] = continuation
            root = ET.fromstring(self.request("GET", query=query))
            keys.extend(node.text for node in root.findall(".//{*}Key") if node.text)
            truncated = root.findtext(".//{*}IsTruncated") == "true"
            if not truncated:
                return keys
            continuation = root.findtext(".//{*}NextContinuationToken")
            if not continuation:
                raise RuntimeError("S3 returned a truncated listing without a continuation token")

    def get_json(self, key, not_found_is_none=False):
        body = self.request("GET", key, not_found_is_none=not_found_is_none)
        return json.loads(body.decode("utf-8")) if body is not None else None

    def put_json(self, key, value):
        body = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        self.request("PUT", key, body=body, content_type="application/json")


def main():
    client = S3Client()
    run = os.environ["RUN_TIMESTAMP"]
    with open("/tmp/minifi-active-tables.json", encoding="utf-8") as handle:
        tables = json.load(handle)["tables"]
    finalized = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for table in tables:
        dataset = f"{table['schema']}.{table['name']}"
        prefix = f"{os.environ['S3_PREFIX'].strip('/')}/{run}/{table['schema']}/{table['name']}/metadata/"
        keys = client.list_keys(prefix)
        sidecars = []
        for key in keys:
            if not key.endswith(".json") or key.endswith("/manifest.json"):
                continue
            sidecar = client.get_json(key)
            if sidecar.get("status") != "SUCCESS":
                raise RuntimeError(f"Invalid sidecar status for {dataset}: {key}")
            if sidecar.get("batch_id") != run or sidecar.get("dataset") != dataset:
                raise RuntimeError(f"Sidecar identity mismatch for {dataset}: {key}")
            sidecars.append(sidecar)
        if not sidecars:
            raise RuntimeError(f"No immutable sidecars found for {dataset} under {prefix}")
        manifest = {
            "manifest_version": "1.0",
            "schema_version": "1.0",
            "source_system": table["sourceSystem"],
            "dataset": dataset,
            "batch_id": run,
            "load_type": table["loadType"],
            "status": "SUCCESS",
            "format": "parquet",
            "finalized_at_utc": finalized,
            "files": sidecars,
        }
        if os.environ.get("FINALIZER_TEST_FAIL", "false").lower() == "true":
            # Deliberately fail after all data and sidecars are uploaded, but before
            # manifest publication. This validates that an incomplete run is not
            # reported as successful and that the manifest remains absent.
            raise RuntimeError("Intentional finalizer failure requested by FINALIZER_TEST_FAIL")
        manifest_key = f"{os.environ['S3_PREFIX'].strip('/')}/{run}/{table['schema']}/{table['name']}/manifest.json"
        client.put_json(manifest_key, manifest)
        print(f"S3 manifest finalized: {manifest_key} files={len(sidecars)}")


if __name__ == "__main__":
    main()
