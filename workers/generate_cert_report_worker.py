import csv
import io
import os

from conductor.client.automator.task_handler import TaskHandler
from conductor.client.configuration.configuration import Configuration
from conductor.client.worker.worker_task import WorkerTask


@WorkerTask(task_definition_name="generate_cert_report", poll_interval_seconds=1)
def generate_cert_report(join_result: dict, expiration_window_days: int = 30) -> dict:
    """Aggregate per-client cert results, classify severity tiers, and generate CSV report."""
    all_details = []
    total_checked = 0
    total_expiring = 0
    worst_severity = "info"

    failed_clients = []

    for key, client_result in join_result.items():
        client_name = client_result.get("client_name") or key

        # Flag clients that had errors or no details
        if client_result.get("error") or not client_result.get("details"):
            failed_clients.append(client_name)
            if not client_result.get("details"):
                continue

        details = client_result["details"]

        for d in details:
            days = d.get("days_remaining", 0)
            error = d.get("error")

            if error or days <= 7:
                status = "critical"
            elif days <= expiration_window_days:
                status = "warning"
            else:
                status = "healthy"

            all_details.append({
                "client_name": client_name,
                "url": d.get("url", ""),
                "hostname": d.get("hostname", ""),
                "expiry_date": d.get("expiry_date", ""),
                "days_remaining": days,
                "status": status,
                "error": error or "",
            })

            if status == "critical":
                worst_severity = "critical"
            elif status == "warning" and worst_severity != "critical":
                worst_severity = "warning"

        total_checked += client_result.get("total_checked", 0)
        total_expiring += client_result.get("total_expiring", 0)

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "client_name", "url", "hostname", "expiry_date",
        "days_remaining", "status", "error"
    ])
    for detail in all_details:
        writer.writerow([
            detail["client_name"],
            detail["url"],
            detail["hostname"],
            detail["expiry_date"],
            detail["days_remaining"],
            detail["status"],
            detail["error"],
        ])
    csv_report = output.getvalue()
    output.close()

    return {
        "all_details": all_details,
        "csv_report": csv_report,
        "total_checked": total_checked,
        "total_expiring": total_expiring,
        "worst_severity": worst_severity,
        "total_rows": len(all_details),
        "failed_clients": failed_clients,
    }


def main():
    server_url = os.getenv("CONDUCTOR_SERVER_URL", "http://localhost:8080/api")
    configuration = Configuration(server_api_url=server_url)
    task_handler = TaskHandler(configuration=configuration)
    task_handler.start_processes()
    print(f"Report worker started, polling {server_url} for 'generate_cert_report' tasks...")
    print("Press Ctrl+C to stop.")
    try:
        task_handler.join_processes()
    except KeyboardInterrupt:
        task_handler.stop_processes()


if __name__ == "__main__":
    main()
