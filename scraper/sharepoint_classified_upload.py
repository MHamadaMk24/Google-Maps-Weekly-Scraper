"""
Convert classified review CSVs to Excel and upload to SharePoint (Documents/Reviews).

Uses Microsoft Graph app credentials from environment variables.
Uploads the same two sheets that go to ClickUp (MAKAN + competitors).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

GRAPH = "https://graph.microsoft.com/v1.0"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def get_graph_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Graph token failed ({resp.status_code}): {resp.text[:300]}")
    return str(resp.json()["access_token"])


def resolve_site_id(headers: dict, site_url: str) -> str:
    """Resolve Microsoft Graph site id from a SharePoint site URL."""
    cleaned = site_url.rstrip("/")
    # https://makanpak.sharepoint.com/sites/SKIDATAFILES
    without_scheme = cleaned.split("://", 1)[-1]
    host, _, path = without_scheme.partition("/")
    site_path = "/" + path if path else ""
    resp = requests.get(
        f"{GRAPH}/sites/{host}:{site_path}",
        headers=headers,
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Resolve site failed ({resp.status_code}): {resp.text[:300]}")
    return str(resp.json()["id"])


def resolve_drive_id(headers: dict, site_id: str, doc_lib: str) -> str:
    resp = requests.get(f"{GRAPH}/sites/{site_id}/drives", headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"List drives failed ({resp.status_code}): {resp.text[:300]}")
    target = doc_lib.strip().lower()
    aliases = {target, "documents", "shared documents"}
    for drive in resp.json().get("value", []):
        name = str(drive.get("name") or "").strip().lower()
        if name in aliases:
            return str(drive["id"])

    # Fallback: site default drive
    default = requests.get(f"{GRAPH}/sites/{site_id}/drive", headers=headers, timeout=60)
    if default.status_code == 200:
        return str(default.json()["id"])
    raise RuntimeError(f"Document library not found: {doc_lib}")


def ensure_folder(headers: dict, drive_id: str, folder: str) -> None:
    folder = folder.strip("/").strip()
    if not folder:
        return
    meta = requests.get(
        f"{GRAPH}/drives/{drive_id}/root:/{folder}",
        headers=headers,
        timeout=60,
    )
    if meta.status_code == 200:
        return
    if meta.status_code != 404:
        raise RuntimeError(f"Check folder failed ({meta.status_code}): {meta.text[:300]}")

    # Create top-level folder only (Reviews). Nested paths not required for this project.
    created = requests.post(
        f"{GRAPH}/drives/{drive_id}/root/children",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "name": folder.split("/")[-1],
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail",
        },
        timeout=60,
    )
    if created.status_code not in (200, 201):
        raise RuntimeError(f"Create folder failed ({created.status_code}): {created.text[:300]}")


def csv_to_excel(csv_path: Path, excel_path: Optional[Path] = None) -> Path:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    out = excel_path or csv_path.with_suffix(".xlsx")
    df = pd.read_csv(csv_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(out, index=False, engine="openpyxl")
    print(f"Converted {csv_path.name} -> {out.name} (rows={len(df)})")
    return out


def upload_file(headers: dict, drive_id: str, folder: str, local_path: Path) -> dict:
    folder = folder.strip("/").strip()
    remote = f"{folder}/{local_path.name}" if folder else local_path.name
    url = f"{GRAPH}/drives/{drive_id}/root:/{remote}:/content"
    data = local_path.read_bytes()
    resp = requests.put(
        url,
        headers={
            **headers,
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
        data=data,
        timeout=120,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Upload failed for {local_path.name} ({resp.status_code}): {resp.text[:300]}"
        )
    body = resp.json()
    print(
        f"Uploaded {local_path.name} -> size={body.get('size')} webUrl={body.get('webUrl')}"
    )
    return body


def upload_classified_excels(
    csv_paths: list[Path],
    tenant_id: str,
    client_id: str,
    client_secret: str,
    site_url: str,
    doc_lib: str,
    folder: str,
) -> list[dict]:
    token = get_graph_token(tenant_id, client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}
    site_id = resolve_site_id(headers, site_url)
    drive_id = resolve_drive_id(headers, site_id, doc_lib)
    ensure_folder(headers, drive_id, folder)

    results: list[dict] = []
    for csv_path in csv_paths:
        xlsx = csv_to_excel(csv_path)
        meta = upload_file(headers, drive_id, folder, xlsx)
        results.append(
            {
                "csv": str(csv_path),
                "excel": str(xlsx),
                "size": meta.get("size"),
                "webUrl": meta.get("webUrl"),
                "id": meta.get("id"),
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert classified CSVs to Excel and upload to SharePoint Reviews folder"
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="Classified CSV path (repeat for multiple files)",
    )
    parser.add_argument(
        "--tenant-id",
        default=_env("TENANT_ID") or _env("SHAREPOINT_TENANT_ID"),
    )
    parser.add_argument(
        "--client-id",
        default=_env("CLIENT_ID") or _env("SHAREPOINT_CLIENT_ID"),
    )
    parser.add_argument(
        "--client-secret",
        default=_env("CLIENT_SECRET") or _env("SHAREPOINT_CLIENT_SECRET"),
    )
    parser.add_argument(
        "--site-url",
        default=_env("SHAREPOINT_SITE_URL")
        or _env("SHAREPOINT_SITE_NAME")
        or "https://makanpak.sharepoint.com/sites/SKIDATAFILES",
    )
    parser.add_argument(
        "--doc-lib",
        default=_env("SHAREPOINT_DOC_LIB") or "Documents",
    )
    parser.add_argument(
        "--folder",
        default=_env("TARGET_FOLDER_PATH") or _env("SHAREPOINT_FOLDER") or "Reviews",
    )
    args = parser.parse_args()

    missing = [
        name
        for name, val in [
            ("TENANT_ID", args.tenant_id),
            ("CLIENT_ID", args.client_id),
            ("CLIENT_SECRET", args.client_secret),
            ("SHAREPOINT_SITE_URL", args.site_url),
        ]
        if not val
    ]
    if missing:
        print(f"Missing required config: {', '.join(missing)}", file=sys.stderr)
        return 1

    paths = [Path(p) for p in args.input]
    results = upload_classified_excels(
        csv_paths=paths,
        tenant_id=args.tenant_id,
        client_id=args.client_id,
        client_secret=args.client_secret,
        site_url=args.site_url,
        doc_lib=args.doc_lib,
        folder=args.folder,
    )
    print(f"SharePoint upload finished: {len(results)} Excel file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
