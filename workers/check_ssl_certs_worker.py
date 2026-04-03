import json
import os
import ssl
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

import certifi
from conductor.client.automator.task_handler import TaskHandler
from conductor.client.configuration.configuration import Configuration
from conductor.client.worker.worker_task import WorkerTask


def extract_hostname(url: str) -> tuple[str, int]:
    """Extract hostname and port from a URL string."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    hostname = parsed.hostname or url
    port = parsed.port or 443
    return hostname, port


def check_certificate(hostname: str, port: int = 443) -> dict:
    """Connect to a host via SSL and return certificate expiry info."""
    result = {"hostname": hostname, "expiry_date": None, "days_remaining": 0, "error": None}

    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                not_after = cert["notAfter"]
                expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                days_remaining = (expiry - datetime.now(timezone.utc)).days
                result["expiry_date"] = expiry.isoformat()
                result["days_remaining"] = days_remaining
    except ssl.SSLCertVerificationError:
        try:
            context = ssl.create_default_context(cafile=certifi.where())
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            with socket.create_connection((hostname, port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert = ssock.getpeercert(binary_form=True)
                    import cryptography.x509
                    x509_cert = cryptography.x509.load_der_x509_certificate(cert)
                    expiry = x509_cert.not_valid_after_utc
                    days_remaining = (expiry - datetime.now(timezone.utc)).days
                    result["expiry_date"] = expiry.isoformat()
                    result["days_remaining"] = days_remaining
                    result["error"] = "certificate verification failed (expired or invalid)"
        except Exception as inner_e:
            result["error"] = f"certificate verification failed: {inner_e}"
    except socket.timeout:
        result["error"] = "connection timed out"
    except socket.gaierror:
        result["error"] = "DNS resolution failed"
    except Exception as e:
        result["error"] = str(e)

    return result


@WorkerTask(task_definition_name="check_ssl_certs", poll_interval_seconds=1)
def check_ssl_certs(urls: list[str], expiration_window_days: int = 30) -> dict:
    if not isinstance(urls, list) or len(urls) == 0:
        raise ValueError("Input 'urls' must be a non-empty list")

    expiring_urls = []
    details = []

    for url in urls:
        hostname, port = extract_hostname(url)
        cert_info = check_certificate(hostname, port)
        cert_info["url"] = url
        details.append(cert_info)

        if cert_info["error"] or cert_info["days_remaining"] <= expiration_window_days:
            expiring_urls.append(url)

    return {
        "expiring_urls": expiring_urls,
        "details": details,
        "total_checked": len(urls),
        "total_expiring": len(expiring_urls),
    }


def register_definitions(server_url: str) -> None:
    workflows_dir = os.path.join(os.path.dirname(__file__), "..", "workflows")

    def _request(method: str, endpoint: str, payload: list) -> None:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{server_url}{endpoint}",
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            print(f"  Warning: {method} {endpoint} returned {e.code}: {body[:120]}")

    with open(os.path.join(workflows_dir, "check_ssl_certs_taskdef.json")) as f:
        _request("POST", "/metadata/taskdefs", [json.load(f)])

    for wf_file in ("cert_hound_workflow.json", "cert_hound_monitor_workflow.json"):
        with open(os.path.join(workflows_dir, wf_file)) as f:
            _request("PUT", "/metadata/workflow", [json.load(f)])

    print("Registered task definition and workflows.")


def main():
    server_url = os.getenv("CONDUCTOR_SERVER_URL", "http://localhost:8080/api")
    register_definitions(server_url)
    configuration = Configuration(server_api_url=server_url)
    task_handler = TaskHandler(configuration=configuration)
    task_handler.start_processes()
    print(f"SSL cert checker worker started, polling {server_url} for 'check_ssl_certs' tasks...")
    print("Press Ctrl+C to stop.")
    try:
        task_handler.join_processes()
    except KeyboardInterrupt:
        task_handler.stop_processes()


if __name__ == "__main__":
    main()
