"""
Storage service for managing weekly analytics reports using Google Cloud Storage.

This service provides functionality to:
- Store weekly analytics reports in Google Cloud Storage
- Retrieve stored reports for analysis
- Manage report metadata and timestamps
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from google.cloud import storage
from google.cloud.exceptions import NotFound

logger = logging.getLogger(__name__)

class StorageService:
    """Service for managing analytics report storage using Google Cloud Storage."""
    
    def __init__(self, bucket_name: Optional[str] = None):
        """Initialize the storage service with Google Cloud Storage.
        
        Always uses Application Default Credentials (Cloud Run service account).
        The Cloud Run SA must have storage.objectAdmin and storage.legacyBucketReader roles.
        """
        self.bucket_name = bucket_name or os.environ.get("STORAGE_BUCKET_NAME", "bigas-analytics-reports")
        
        # Always use Application Default Credentials (Cloud Run service account)
        # This works in both Standalone and SaaS modes
        self.client = storage.Client()  # Uses ADC automatically
        self.bucket = self._get_or_create_bucket()
        logger.info(f"StorageService initialized with bucket: {self.bucket.name}")
        
    def _get_or_create_bucket(self):
        """Get or create the storage bucket."""
        try:
            bucket = self.client.bucket(self.bucket_name)
            # Check if bucket exists
            bucket.reload()
            logger.info(f"Using existing bucket: {self.bucket_name}")
        except NotFound:
            # Create bucket if it doesn't exist
            bucket = self.client.create_bucket(self.bucket_name)
            logger.info(f"Created new bucket: {self.bucket_name}")
        return bucket
    
    def store_weekly_report(self, report_data: Dict[str, Any], report_date: Optional[str] = None) -> str:
        """
        Store a weekly analytics report in Google Cloud Storage.
        
        Args:
            report_data: The complete report data to store
            report_date: Optional date string (YYYY-MM-DD). Defaults to today.
            
        Returns:
            str: The blob name (file path) where the report was stored
        """
        if report_date is None:
            report_date = datetime.now().strftime("%Y-%m-%d")
            
        # Create metadata for the report
        metadata = {
            "report_date": report_date,
            "stored_at": datetime.now().isoformat(),
            "report_type": "weekly_analytics",
            "version": "1.0"
        }
        
        # Combine report data with metadata
        full_data = {
            "metadata": metadata,
            "report": report_data
        }
        
        # Create blob name with date
        blob_name = f"weekly_reports/{report_date}/report.json"
        
        # Upload to Google Cloud Storage
        blob = self.bucket.blob(blob_name)
        blob.upload_from_string(
            json.dumps(full_data, indent=2),
            content_type='application/json'
        )
        
        logger.info(f"Stored weekly report for {report_date} at {blob_name}")
        return blob_name
    
    def get_latest_weekly_report(self) -> Optional[Dict[str, Any]]:
        """
        Retrieve the most recent weekly analytics report.
        
        Returns:
            Dict containing the report data and metadata, or None if no report found
        """
        try:
            # List all weekly report blobs
            blobs = list(self.bucket.list_blobs(prefix="weekly_reports/"))
            
            if not blobs:
                logger.info("No weekly reports found in storage")
                return None
            
            # Find the most recent report
            latest_blob = max(blobs, key=lambda b: b.name)
            
            # Download and parse the report
            content = latest_blob.download_as_text()
            report_data = json.loads(content)
            
            logger.info(f"Retrieved latest weekly report: {latest_blob.name}")
            return report_data
            
        except Exception as e:
            logger.error(f"Error retrieving latest weekly report: {e}")
            return None
    
    def get_weekly_report_by_date(self, report_date: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific weekly report by date.
        
        Args:
            report_date: Date string in YYYY-MM-DD format
            
        Returns:
            Dict containing the report data and metadata, or None if not found
        """
        try:
            blob_name = f"weekly_reports/{report_date}/report.json"
            blob = self.bucket.blob(blob_name)
            
            # Check if blob exists
            if not blob.exists():
                logger.info(f"No report found for date: {report_date}")
                return None
            
            # Download and parse the report
            content = blob.download_as_text()
            report_data = json.loads(content)
            
            logger.info(f"Retrieved weekly report for {report_date}")
            return report_data
            
        except Exception as e:
            logger.error(f"Error retrieving report for {report_date}: {e}")
            return None
    
    def list_available_reports(self) -> List[Dict[str, str]]:
        """
        List all available weekly reports with their dates and metadata.
        
        Returns:
            List of dictionaries containing report information
        """
        try:
            blobs = list(self.bucket.list_blobs(prefix="weekly_reports/"))
            reports = []
            
            for blob in blobs:
                if blob.name.endswith("/report.json"):
                    # Extract date from blob name
                    date_part = blob.name.split("/")[1]  # weekly_reports/YYYY-MM-DD/report.json
                    
                    reports.append({
                        "date": date_part,
                        "blob_name": blob.name,
                        "size": blob.size,
                        "updated": blob.updated.isoformat() if blob.updated else None
                    })
            
            # Sort by date (newest first)
            reports.sort(key=lambda x: x["date"], reverse=True)
            
            return reports
            
        except Exception as e:
            logger.error(f"Error listing available reports: {e}")
            return []
    
    def delete_old_reports(self, keep_days: int = 30, max_reports_to_delete: int = 50) -> int:
        """
        Delete weekly reports older than the specified number of days.
        
        Args:
            keep_days: Number of days to keep reports (default: 30)
            max_reports_to_delete: Maximum number of reports to delete in one operation (default: 50)
            
        Returns:
            int: Number of reports deleted
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=keep_days)
            blobs = list(self.bucket.list_blobs(prefix="weekly_reports/"))
            deleted_count = 0
            
            # Sort blobs by date (oldest first) to prioritize deleting the oldest reports
            old_blobs = []
            for blob in blobs:
                if blob.name.endswith("/report.json"):
                    # Extract date from blob name
                    date_part = blob.name.split("/")[1]
                    
                    try:
                        report_date = datetime.strptime(date_part, "%Y-%m-%d")
                        if report_date < cutoff_date:
                            old_blobs.append((blob, report_date))
                    except ValueError:
                        # Skip blobs with invalid date format
                        continue
            
            # Sort by date (oldest first) and limit the number to delete
            old_blobs.sort(key=lambda x: x[1])
            blobs_to_delete = old_blobs[:max_reports_to_delete]
            
            for blob, report_date in blobs_to_delete:
                blob.delete()
                deleted_count += 1
                logger.info(f"Deleted old report: {blob.name} (from {report_date.strftime('%Y-%m-%d')})")
            
            logger.info(f"Deleted {deleted_count} old reports (limited to {max_reports_to_delete})")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error deleting old reports: {e}")
            return 0
    
    def get_report_summary(self, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract key summary information from a weekly report.
        
        Args:
            report_data: The complete report data
            
        Returns:
            Dict containing summary information
        """
        try:
            metadata = report_data.get("metadata", {})
            report = report_data.get("report", {})
            
            # Extract key metrics from the report
            summary = {
                "report_date": metadata.get("report_date"),
                "stored_at": metadata.get("stored_at"),
                "total_questions": len(report.get("questions", [])),
                "has_summary": "summary" in report,
                "has_underperforming_pages": False,
                "underperforming_pages": []
            }
            
            # Look for underperforming pages in the report
            for question_data in report.get("questions", []):
                if "underperforming" in question_data.get("question", "").lower():
                    answer = question_data.get("answer", "")
                    raw_data = question_data.get("raw_data", {})
                    
                    # Extract page information from the answer
                    # This is a simple extraction - could be enhanced with better parsing
                    if "pages" in answer.lower():
                        summary["has_underperforming_pages"] = True
                        
                        # Extract URLs from raw data if available
                        page_urls = self._extract_page_urls_from_raw_data(raw_data)
                        
                        # Add the full answer and URLs for further analysis
                        summary["underperforming_pages"].append({
                            "question": question_data.get("question"),
                            "answer": answer,
                            "page_urls": page_urls
                        })
                        
                        # Also add page URLs to the original question data for the analysis endpoint
                        question_data["page_urls"] = page_urls
            
            return summary
            
        except Exception as e:
            logger.error(f"Error creating report summary: {e}")
            return {"error": str(e)}
    
    def _extract_page_urls_from_raw_data(self, raw_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract page URLs and metrics from raw GA4 data.
        
        Args:
            raw_data: Raw GA4 data from the template query
            
        Returns:
            List of dictionaries containing page URLs and their metrics
        """
        try:
            if not raw_data or not raw_data.get("rows"):
                return []
            
            page_urls = []
            rows = raw_data.get("rows", [])
            
            # Get dimension/metric headers to understand the data structure
            dimension_headers = raw_data.get("dimension_headers", [])
            metric_headers = raw_data.get("metric_headers", [])
            
            # Find the indices for pagePath and hostName
            page_path_index = None
            host_name_index = None
            
            for i, header in enumerate(dimension_headers):
                if header == "pagePath":
                    page_path_index = i
                elif header == "hostName":
                    host_name_index = i
            
            for row in rows:
                try:
                    # Extract page path and hostname from dimensions
                    dimension_values = row.get("dimension_values", [])
                    metric_values = row.get("metric_values", [])
                    
                    if dimension_values and len(dimension_values) > 0:
                        # Extract page path
                        page_path = dimension_values[page_path_index] if page_path_index is not None and page_path_index < len(dimension_values) else ""
                        
                        # Extract hostname (domain)
                        hostname = dimension_values[host_name_index] if host_name_index is not None and host_name_index < len(dimension_values) else ""
                        
                    # Extract metrics by header name when possible (the templates may use keyEvents instead of conversions)
                    sessions = 0
                    key_events = 0
                    conversions = 0

                    def _metric_idx(name: str) -> Optional[int]:
                        try:
                            return metric_headers.index(name)
                        except ValueError:
                            return None

                    idx_sessions = _metric_idx("sessions")
                    idx_key_events = _metric_idx("keyEvents")
                    idx_conversions = _metric_idx("conversions")

                    if idx_sessions is not None and idx_sessions < len(metric_values):
                        sessions = int(metric_values[idx_sessions])
                    elif len(metric_values) > 0:
                        # Fallback: first metric is usually sessions
                        sessions = int(metric_values[0])

                    if idx_key_events is not None and idx_key_events < len(metric_values):
                        key_events = int(metric_values[idx_key_events])

                    if idx_conversions is not None and idx_conversions < len(metric_values):
                        conversions = int(metric_values[idx_conversions])

                    # Back-compat: if template uses keyEvents as the "conversion-like" metric,
                    # mirror it into conversions so older consumers don't show 0 incorrectly.
                    if conversions == 0 and key_events > 0:
                        conversions = key_events
                        
                        # Check if this page is underperforming (high traffic, low conversions)
                        is_underperforming = row.get("underperforming", False)
                        
                        # Construct the full URL
                        if page_path and hostname:
                            # If page path starts with /, combine with hostname
                            if page_path.startswith("/"):
                                full_url = f"https://{hostname}{page_path}"
                            else:
                                # If page path is already a full URL, use it as is
                                full_url = page_path
                        else:
                            # Fallback if we don't have hostname
                            full_url = page_path if page_path else ""
                        
                        page_urls.append({
                            "page_path": page_path,
                            "hostname": hostname,
                            "page_url": full_url,
                            "sessions": sessions,
                        # Keep both fields to avoid confusion between GA4 "key events" and legacy "conversions".
                        "key_events": key_events,
                        "conversions": conversions,
                        "key_event_rate": (key_events / sessions * 100) if sessions > 0 else 0,
                        "conversion_rate": (conversions / sessions * 100) if sessions > 0 else 0,
                            "is_underperforming": is_underperforming
                        })
                        
                except (ValueError, IndexError, TypeError) as e:
                    logger.warning(f"Error extracting page URL from row: {e}")
                    continue
            
            return page_urls
            
        except Exception as e:
            logger.error(f"Error extracting page URLs from raw data: {e}")
            return [] 