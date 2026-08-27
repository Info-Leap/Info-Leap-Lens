"""
Google Drive backend for InfoLeap Pulse.

Folder structure in Drive:
  Info-Leap Lens/
  ├── quant/
  │   ├── project_1__elec_appliances/   ← oxdata.db, mapping_workbook.xlsx, raw data
  │   └── akshayakalpa__dairy/
  └── qual/
      ├── coindcx__concept_test/         ← transcripts, pageindex_trees/, registry.json
      └── mixer__ethnographic/

Usage:
  from gdrive_backend.client import DriveClient
  client = DriveClient()
  client.download_db("project_1__elec_appliances", "/tmp/oxdata.db")
  client.upload_db("project_1__elec_appliances", "/tmp/oxdata.db")
"""

import os
import io
import json
import tempfile
from pathlib import Path
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# ── Folder IDs (from .env) ────────────────────────────────────────────────────
ROOT_ID  = os.environ.get("GDRIVE_ROOT_FOLDER_ID",  "1KivpF_wTTxc8F2CHMFZBC8lHAD4R2z-E")
QUANT_ID = os.environ.get("GDRIVE_QUANT_FOLDER_ID", "1ba22lbMa95C32mmAJJXtCqjv0MirK3Hx")  # Quantitative/
QUAL_ID  = os.environ.get("GDRIVE_QUAL_FOLDER_ID",  "1hTnXwVYAtg_p_8qWs12gO0fPxEV-hwsu")  # Qualitative/

FOLDER_MIME = "application/vnd.google-apps.folder"
SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveClient:
    """Thin wrapper around Drive API for InfoLeap project storage."""

    def __init__(self):
        cred_path = os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS",
            str(Path(__file__).parent.parent / "oxdata" / "config" / "infoleap_service_account.json"),
        )
        creds = service_account.Credentials.from_service_account_file(cred_path, scopes=SCOPES)
        self._svc = build("drive", "v3", credentials=creds)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _list_children(self, parent_id: str) -> list[dict]:
        results = self._svc.files().list(
            q=f"'{parent_id}' in parents and trashed=false",
            fields="files(id,name,mimeType,modifiedTime,size)",
            pageSize=200,
        ).execute()
        return results.get("files", [])

    def _find(self, name: str, parent_id: str) -> Optional[dict]:
        for f in self._list_children(parent_id):
            if f["name"] == name:
                return f
        return None

    def _mkdir(self, name: str, parent_id: str) -> str:
        existing = self._find(name, parent_id)
        if existing:
            return existing["id"]
        f = self._svc.files().create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            fields="id",
        ).execute()
        return f["id"]

    def _project_folder_id(self, project_name: str, kind: str) -> str:
        """Get or create project folder under quant/ or qual/."""
        parent = QUANT_ID if kind == "quant" else QUAL_ID
        return self._mkdir(project_name, parent)

    # ── Project registry ───────────────────────────────────────────────────────

    def list_quant_projects(self) -> list[str]:
        """Return list of quant project folder names."""
        return [f["name"] for f in self._list_children(QUANT_ID) if f["mimeType"] == FOLDER_MIME]

    def list_qual_projects(self) -> list[str]:
        """Return list of qual project folder names."""
        return [f["name"] for f in self._list_children(QUAL_ID) if f["mimeType"] == FOLDER_MIME]

    def list_all_projects(self) -> dict:
        return {
            "quant": self.list_quant_projects(),
            "qual":  self.list_qual_projects(),
        }

    # ── Download ───────────────────────────────────────────────────────────────

    def download_file(self, project_name: str, filename: str, dest_path: str, kind: str = "quant") -> bool:
        """Download a file from a project folder. Returns True on success."""
        folder_id = self._project_folder_id(project_name, kind)
        file_meta = self._find(filename, folder_id)
        if not file_meta:
            return False
        request = self._svc.files().get_media(fileId=file_meta["id"])
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return True

    def download_db(self, project_name: str, dest_path: str) -> bool:
        """Download oxdata.db for a quant project."""
        return self.download_file(project_name, "oxdata.db", dest_path, kind="quant")

    def download_mapping_workbook(self, project_name: str, dest_path: str) -> bool:
        """Download mapping_workbook.xlsx for a quant project."""
        return self.download_file(project_name, "mapping_workbook.xlsx", dest_path, kind="quant")

    # ── Upload ─────────────────────────────────────────────────────────────────

    def upload_file(self, project_name: str, local_path: str, filename: str, kind: str = "quant") -> str:
        """Upload a file to a project folder. Returns file ID."""
        folder_id = self._project_folder_id(project_name, kind)
        existing  = self._find(filename, folder_id)
        mime = _guess_mime(filename)
        media = MediaFileUpload(local_path, mimetype=mime, resumable=True)
        if existing:
            # Update in place
            f = self._svc.files().update(
                fileId=existing["id"],
                media_body=media,
                fields="id",
            ).execute()
        else:
            f = self._svc.files().create(
                body={"name": filename, "parents": [folder_id]},
                media_body=media,
                fields="id",
            ).execute()
        return f["id"]

    def upload_db(self, project_name: str, local_path: str) -> str:
        """Upload oxdata.db for a quant project. Returns Drive file ID."""
        return self.upload_file(project_name, local_path, "oxdata.db", kind="quant")

    def upload_mapping_workbook(self, project_name: str, local_path: str) -> str:
        return self.upload_file(project_name, local_path, "mapping_workbook.xlsx", kind="quant")

    def upload_raw_data(self, project_name: str, local_path: str, filename: str, kind: str = "quant") -> str:
        """Upload any raw file (codebook, data xlsx, etc.)."""
        return self.upload_file(project_name, local_path, filename, kind=kind)

    # ── Qual-specific ──────────────────────────────────────────────────────────

    def upload_qual_file(self, project_name: str, local_path: str, filename: str) -> str:
        """Upload a qual project file (transcript, registry.json, etc.)."""
        return self.upload_file(project_name, local_path, filename, kind="qual")

    def download_qual_file(self, project_name: str, filename: str, dest_path: str) -> bool:
        return self.download_file(project_name, filename, dest_path, kind="qual")

    # ── List project files ─────────────────────────────────────────────────────

    def list_project_files(self, project_name: str, kind: str = "quant") -> list[dict]:
        """List files inside a project folder."""
        folder_id = self._project_folder_id(project_name, kind)
        return [
            {"name": f["name"], "id": f["id"], "size": f.get("size"), "modified": f.get("modifiedTime")}
            for f in self._list_children(folder_id)
            if f["mimeType"] != FOLDER_MIME
        ]

    # ── Create new project folder ──────────────────────────────────────────────

    def create_project_folder(self, project_name: str, kind: str = "quant") -> str:
        """Create a new project folder. Returns folder ID."""
        return self._project_folder_id(project_name, kind)


def _guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".db":    "application/octet-stream",
        ".xlsx":  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls":   "application/vnd.ms-excel",
        ".json":  "application/json",
        ".csv":   "text/csv",
        ".pdf":   "application/pdf",
        ".docx":  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc":   "application/msword",
        ".pptx":  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(ext, "application/octet-stream")
