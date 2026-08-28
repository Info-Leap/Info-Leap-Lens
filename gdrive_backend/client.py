"""
Google Drive backend for InfoLeap Pulse.

Folder structure in Drive:
  Info-Leap Lens/
  ├── quant/
  │   ├── project_1__elec_appliances/   ← oxdata.db, master_mapping.xlsx, raw data
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
    _GDRIVE_LIBS_AVAILABLE = True
except ImportError:
    _GDRIVE_LIBS_AVAILABLE = False

# ── Folder IDs (from .env) ────────────────────────────────────────────────────
ROOT_ID  = os.environ.get("GDRIVE_ROOT_FOLDER_ID",  "0AHQcUTK8oFvVUk9PVA")
QUANT_ID = os.environ.get("GDRIVE_QUANT_FOLDER_ID", "1smKGRHA8XFZGeO4nFJa2EyQOn7MfV4n0")  # Quantitative/
QUAL_ID  = os.environ.get("GDRIVE_QUAL_FOLDER_ID",  "1gnVu_EXPTNvMGZc9hEggM2gsxmLbZUIU")  # Qualitative/

FOLDER_MIME = "application/vnd.google-apps.folder"
SCOPES = ["https://www.googleapis.com/auth/drive"]


class DriveClient:
    """Thin wrapper around Drive API for InfoLeap project storage."""

    def __init__(self):
        self._svc = None
        if not _GDRIVE_LIBS_AVAILABLE:
            return
        try:
            cred_path = os.environ.get(
                "GOOGLE_APPLICATION_CREDENTIALS",
                str(Path(__file__).parent.parent / "oxdata" / "config" / "infoleap_service_account.json"),
            )
            if cred_path and Path(cred_path).exists():
                creds = service_account.Credentials.from_service_account_file(cred_path, scopes=SCOPES)
                self._svc = build("drive", "v3", credentials=creds)
        except Exception:
            self._svc = None

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _list_children(self, parent_id: str) -> list[dict]:
        if self._svc is None:
            return []
        try:
            results = self._svc.files().list(
                q=f"'{parent_id}' in parents and trashed=false",
                fields="files(id,name,mimeType,modifiedTime,size)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=200,
            ).execute()
            return results.get("files", [])
        except Exception:
            return []

    def _find(self, name: str, parent_id: str) -> Optional[dict]:
        for f in self._list_children(parent_id):
            if f.get("name") == name:
                return f
        return None

    def _mkdir(self, name: str, parent_id: str) -> Optional[str]:
        if self._svc is None:
            return None
        existing = self._find(name, parent_id)
        if existing:
            return existing["id"]
        try:
            f = self._svc.files().create(
                body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
                fields="id",
                supportsAllDrives=True,
            ).execute()
            return f["id"]
        except Exception:
            return None

    def _project_folder_id(self, project_name: str, kind: str) -> Optional[str]:
        """Get or create project folder under quant/ or qual/."""
        parent = QUANT_ID if kind == "quant" else QUAL_ID
        return self._mkdir(project_name, parent)

    # ── Project registry ───────────────────────────────────────────────────────

    def list_quant_projects(self) -> list[str]:
        """Return list of quant project folder names."""
        if self._svc is None:
            return []
        return [f["name"] for f in self._list_children(QUANT_ID) if f.get("mimeType") == FOLDER_MIME]

    def list_qual_projects(self) -> list[str]:
        """Return list of qual project folder names."""
        if self._svc is None:
            return []
        return [f["name"] for f in self._list_children(QUAL_ID) if f.get("mimeType") == FOLDER_MIME]

    def list_all_projects(self) -> dict:
        return {
            "quant": self.list_quant_projects(),
            "qual":  self.list_qual_projects(),
        }

    # ── Download ───────────────────────────────────────────────────────────────

    def download_file(self, project_name: str, filename: str, dest_path: str, kind: str = "quant") -> bool:
        """Download a file from a project folder. Returns True on success."""
        if self._svc is None:
            return False
        folder_id = self._project_folder_id(project_name, kind)
        if not folder_id:
            return False
        file_meta = self._find(filename, folder_id)
        if not file_meta:
            return False
        try:
            request = self._svc.files().get_media(fileId=file_meta["id"])
            Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            return True
        except Exception:
            return False

    def download_db(self, project_name: str, dest_path: str) -> bool:
        """Download oxdata.db for a quant project."""
        return self.download_file(project_name, "oxdata.db", dest_path, kind="quant")

    def download_master_mapping(self, project_name: str, dest_path: str, kind: str = "quant") -> bool:
        """Download master_mapping.xlsx for a project."""
        return self.download_file(project_name, "master_mapping.xlsx", dest_path, kind=kind)

    def download_mapping_workbook(self, project_name: str, dest_path: str) -> bool:
        """Download mapping_workbook.xlsx for a quant project."""
        return self.download_file(project_name, "mapping_workbook.xlsx", dest_path, kind="quant")

    # ── Upload ─────────────────────────────────────────────────────────────────

    def upload_file(self, project_name: str, local_path: str, filename: Optional[str] = None, kind: str = "quant") -> Optional[str]:
        """Upload a file to a project folder. Returns file ID."""
        if self._svc is None:
            return None
        lp = Path(local_path)
        if not lp.exists():
            return None
        fname = filename or lp.name
        folder_id = self._project_folder_id(project_name, kind)
        if not folder_id:
            return None
        existing = self._find(fname, folder_id)
        mime = _guess_mime(fname)
        try:
            media = MediaFileUpload(str(lp), mimetype=mime, resumable=True)
            if existing:
                f = self._svc.files().update(
                    fileId=existing["id"],
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                ).execute()
            else:
                f = self._svc.files().create(
                    body={"name": fname, "parents": [folder_id]},
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                ).execute()
            return f.get("id")
        except Exception:
            return None

    def upload_db(self, project_name: str, local_path: str) -> Optional[str]:
        """Upload oxdata.db for a quant project. Returns Drive file ID."""
        return self.upload_file(project_name, local_path, "oxdata.db", kind="quant")

    def upload_master_mapping(self, project_name: str, local_path: str, kind: str = "quant") -> Optional[str]:
        """Upload master_mapping.xlsx for a project."""
        return self.upload_file(project_name, local_path, "master_mapping.xlsx", kind=kind)

    def upload_mapping_workbook(self, project_name: str, local_path: str) -> Optional[str]:
        return self.upload_file(project_name, local_path, "mapping_workbook.xlsx", kind="quant")

    def upload_raw_data(self, project_name: str, local_path: str, filename: Optional[str] = None, kind: str = "quant") -> Optional[str]:
        """Upload any raw file (codebook, data xlsx, etc.)."""
        return self.upload_file(project_name, local_path, filename or Path(local_path).name, kind=kind)

    # ── Qual-specific ──────────────────────────────────────────────────────────

    def upload_qual_file(self, project_name: str, local_path: str, filename: Optional[str] = None) -> Optional[str]:
        """Upload a qual project file (transcript, registry.json, etc.)."""
        return self.upload_file(project_name, local_path, filename, kind="qual")

    def download_qual_file(self, project_name: str, filename: str, dest_path: str) -> bool:
        return self.download_file(project_name, filename, dest_path, kind="qual")

    # ── List project files ─────────────────────────────────────────────────────

    def list_project_files(self, project_name: str, kind: str = "quant") -> list[dict]:
        """List files inside a project folder."""
        if self._svc is None:
            return []
        folder_id = self._project_folder_id(project_name, kind)
        if not folder_id:
            return []
        return [
            {"name": f["name"], "id": f["id"], "size": f.get("size"), "modified": f.get("modifiedTime")}
            for f in self._list_children(folder_id)
            if f.get("mimeType") != FOLDER_MIME
        ]

    # ── Full project synchronization ───────────────────────────────────────────

    def sync_project_to_drive(self, project_name: str, local_data_dir: str, kind: str = "quant") -> dict[str, str]:
        """
        Upload all project files (oxdata.db, master_mapping.xlsx, raw_data.xlsx, raw_data.csv, project_meta.json)
        from local_data_dir to Drive. Returns dict of {filename: file_id}.
        Skips files that don't exist locally. Updates last_drive_sync timestamp in project_meta.json.
        """
        if self._svc is None:
            return {}
        ld = Path(local_data_dir)
        if not ld.exists() or not ld.is_dir():
            return {}

        now_str = datetime.now(timezone.utc).isoformat()
        meta_p = ld / "project_meta.json"
        meta_dict = {}
        if meta_p.exists():
            try:
                with open(meta_p, "r", encoding="utf-8") as f:
                    meta_dict = json.load(f)
            except Exception:
                pass
        meta_dict["last_drive_sync"] = now_str
        try:
            with open(meta_p, "w", encoding="utf-8") as f:
                json.dump(meta_dict, f, indent=2)
        except Exception:
            pass

        sync_targets = ["oxdata.db", "master_mapping.xlsx", "raw_data.xlsx", "raw_data.csv", "project_meta.json"]
        uploaded = {}
        for target in sync_targets:
            fpath = ld / target
            if fpath.exists():
                fid = self.upload_file(project_name, str(fpath), target, kind=kind)
                if fid:
                    uploaded[target] = fid
        return uploaded

    def sync_project_from_drive(self, project_name: str, local_data_dir: str, kind: str = "quant") -> dict[str, bool]:
        """
        Download all project files from Drive to local_data_dir.
        Returns dict of {filename: True/False (found/downloaded)}.
        """
        if self._svc is None:
            return {}
        ld = Path(local_data_dir)
        ld.mkdir(parents=True, exist_ok=True)
        drive_files = self.list_project_files(project_name, kind=kind)
        results = {}
        for df in drive_files:
            fname = df["name"]
            ok = self.download_file(project_name, fname, str(ld / fname), kind=kind)
            results[fname] = ok

        now_str = datetime.now(timezone.utc).isoformat()
        meta_p = ld / "project_meta.json"
        meta_dict = {}
        if meta_p.exists():
            try:
                with open(meta_p, "r", encoding="utf-8") as f:
                    meta_dict = json.load(f)
            except Exception:
                pass
        meta_dict["last_drive_sync"] = now_str
        try:
            with open(meta_p, "w", encoding="utf-8") as f:
                json.dump(meta_dict, f, indent=2)
        except Exception:
            pass

        return results

    # ── Create new project folder ──────────────────────────────────────────────

    def create_project_folder(self, project_name: str, kind: str = "quant") -> Optional[str]:
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

