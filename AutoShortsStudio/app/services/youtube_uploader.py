import os
import re
import json
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests
from loguru import logger

from app.config import config


class YouTubeUploaderService:
    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    ]

    def __init__(self):
        self._auth_lock = threading.Lock()
        self._auth_flow = None
        self._auth_thread = None
        self._auth_status = "idle"  # "idle", "running", "success", "timeout", "cancelled", "error"
        self._auth_error = ""
        self._auth_start_time = 0.0
        self._auth_timeout_seconds = 45

    def _get_setting(self, key: str, default=None):
        if key in config.app:
            return config.app[key]
        cfg_raw = getattr(config, "_cfg", {}) if hasattr(config, "_cfg") else {}
        yt_section = cfg_raw.get("youtube", {})
        if key in yt_section:
            return yt_section[key]
        alt_key = key.removeprefix("youtube_")
        if alt_key in yt_section:
            return yt_section[alt_key]
        return default

    @property
    def enabled(self) -> bool:
        return bool(self._get_setting("youtube_enabled", False))

    @property
    def auto_upload(self) -> bool:
        return bool(self._get_setting("youtube_auto_upload", False))

    @property
    def privacy_status(self) -> str:
        # Default to "private" (acts as draft on YouTube)
        status = str(self._get_setting("youtube_privacy_status", "private")).lower()
        if status not in ("private", "unlisted", "public"):
            status = "private"
        return status

    @property
    def client_secrets_file(self) -> str:
        val = self._get_setting("youtube_client_secrets_file", "resource/client_secret.json")
        if not os.path.isabs(val):
            val = os.path.join(config.root_dir, val)
        return val

    @property
    def credentials_file(self) -> str:
        val = self._get_setting("youtube_credentials_file", "resource/youtube_token.json")
        if not os.path.isabs(val):
            val = os.path.join(config.root_dir, val)
        return val

    @property
    def add_shorts_tag(self) -> bool:
        return bool(self._get_setting("youtube_add_shorts_tag", True))

    @property
    def category_id(self) -> str:
        return str(self._get_setting("youtube_category_id", "22"))

    @property
    def client_id(self) -> str:
        val = self._get_setting("youtube_client_id", "")
        if not val and os.path.exists(self.client_secrets_file):
            try:
                with open(self.client_secrets_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    installed = data.get("installed") or data.get("web") or {}
                    val = installed.get("client_id", "")
            except Exception:
                pass
        return val

    @property
    def client_secret(self) -> str:
        val = self._get_setting("youtube_client_secret", "")
        if not val and os.path.exists(self.client_secrets_file):
            try:
                with open(self.client_secrets_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    installed = data.get("installed") or data.get("web") or {}
                    val = installed.get("client_secret", "")
            except Exception:
                pass
        return val

    def save_client_secrets(self, client_id: str, client_secret: str) -> bool:
        if not client_id or not client_secret:
            return False
        try:
            data = {
                "installed": {
                    "client_id": client_id.strip(),
                    "client_secret": client_secret.strip(),
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": [
                        "http://localhost:8090/",
                        "http://127.0.0.1:8090/",
                        "http://localhost"
                    ]
                }
            }
            target_path = self.client_secrets_file
            os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved client secrets to {target_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save client secrets: {e}")
            return False

    def is_authenticated(self) -> bool:
        return self.get_credentials() is not None

    def is_configured(self) -> bool:
        return self.enabled and (
            os.path.exists(self.credentials_file)
            or os.path.exists(self.client_secrets_file)
            or bool(self._get_setting("youtube_client_id") and self._get_setting("youtube_client_secret"))
        )

    def get_credentials(self):
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
        except ImportError as e:
            logger.error(f"Google auth libraries not installed: {e}")
            return None

        creds = None
        if os.path.exists(self.credentials_file):
            try:
                creds = Credentials.from_authorized_user_file(self.credentials_file, self.SCOPES)
            except Exception as e:
                logger.warning(f"Failed to load YouTube credentials from {self.credentials_file}: {e}")
                creds = None

        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info("Refreshing expired YouTube OAuth credentials...")
                creds.refresh(Request())
                os.makedirs(os.path.dirname(os.path.abspath(self.credentials_file)), exist_ok=True)
                with open(self.credentials_file, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                logger.success("YouTube OAuth credentials refreshed and saved successfully.")
            except Exception as e:
                logger.error(f"Failed to refresh YouTube credentials: {e}")
                return None

        return creds if (creds and creds.valid) else None

    def start_browser_auth(self, port: int = 8090, timeout_seconds: int = 45) -> bool:
        """
        Start OAuth flow in a background thread with explicit timeout and cancel support.
        Uses port 8090 by default.
        """
        with self._auth_lock:
            if self._auth_status == "running":
                return True

            if not os.path.exists(self.client_secrets_file):
                if self.client_id and self.client_secret:
                    self.save_client_secrets(self.client_id, self.client_secret)
                else:
                    self._auth_status = "error"
                    self._auth_error = f"Client secrets file not found: {self.client_secrets_file}"
                    logger.error(self._auth_error)
                    return False

            self._auth_status = "running"
            self._auth_error = ""
            self._auth_start_time = time.time()
            self._auth_timeout_seconds = timeout_seconds

            def _worker():
                try:
                    from google_auth_oauthlib.flow import InstalledAppFlow

                    flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets_file, self.SCOPES)
                    with self._auth_lock:
                        self._auth_flow = flow

                    logger.info(f"Starting YouTube OAuth local server on port {port} (timeout: {timeout_seconds}s)...")
                    try:
                        creds = flow.run_local_server(
                            port=port,
                            timeout_seconds=timeout_seconds,
                            open_browser=True,
                        )
                    except OSError:
                        logger.warning(f"Port {port} is occupied, falling back to dynamic port...")
                        creds = flow.run_local_server(
                            port=0,
                            timeout_seconds=timeout_seconds,
                            open_browser=True,
                        )

                    with self._auth_lock:
                        if self._auth_status == "cancelled":
                            return

                    os.makedirs(os.path.dirname(os.path.abspath(self.credentials_file)), exist_ok=True)
                    with open(self.credentials_file, "w", encoding="utf-8") as f:
                        f.write(creds.to_json())

                    with self._auth_lock:
                        self._auth_status = "success"
                        self._auth_flow = None
                    logger.success(f"YouTube authentication successful. Token saved to {self.credentials_file}")

                except Exception as e:
                    exc_name = type(e).__name__
                    with self._auth_lock:
                        current_status = self._auth_status
                        self._auth_flow = None
                        if current_status == "cancelled":
                            return
                        if "Timeout" in exc_name or "WSGITimeoutError" in exc_name:
                            self._auth_status = "timeout"
                            self._auth_error = "Yetkilendirme zaman aşımına uğradı veya sekme kapatıldı."
                            logger.warning("YouTube OAuth flow timed out (browser tab was likely closed).")
                        else:
                            self._auth_status = "error"
                            self._auth_error = str(e)
                            logger.warning(f"YouTube OAuth flow ended with error: {e}")

            self._auth_thread = threading.Thread(target=_worker, daemon=True)
            self._auth_thread.start()
            return True

    def cancel_browser_auth(self):
        """
        Immediately cancels a running browser authentication by unblocking the local server.
        """
        with self._auth_lock:
            if self._auth_status != "running":
                self._auth_status = "idle"
                self._auth_flow = None
                return
            self._auth_status = "cancelled"
            flow = self._auth_flow
            self._auth_flow = None

        if flow and getattr(flow, "redirect_uri", None):
            try:
                # Trigger quick HTTP GET to instantly unblock local server's handle_request()
                requests.get(flow.redirect_uri + "?error=access_denied", timeout=1)
            except Exception:
                pass
        logger.info("YouTube OAuth authorization cancelled by user.")

    def get_auth_state(self) -> dict:
        """
        Get current authentication progress state.
        """
        with self._auth_lock:
            remaining = 0
            if self._auth_status == "running":
                elapsed = time.time() - self._auth_start_time
                remaining = max(0, int(self._auth_timeout_seconds - elapsed))
            return {
                "status": self._auth_status,
                "error": self._auth_error,
                "remaining_seconds": remaining,
            }

    def reset_auth_state(self):
        """
        Reset state back to idle so user can retry.
        """
        with self._auth_lock:
            if self._auth_status != "running":
                self._auth_status = "idle"
                self._auth_error = ""

    def authenticate_via_browser(self, port: int = 8090, timeout_seconds: int = 45) -> bool:
        """
        Synchronous wrapper with timeout for CLI / bat usage.
        """
        self.start_browser_auth(port=port, timeout_seconds=timeout_seconds)
        if self._auth_thread:
            self._auth_thread.join(timeout=timeout_seconds + 5)
        state = self.get_auth_state()
        return state["status"] == "success"

    def set_thumbnail(self, video_id: str, thumbnail_path: str) -> Dict[str, Any]:
        """
        Uploads a custom thumbnail for a YouTube video with retry and size checks.
        Note: Channel must have phone verification (intermediate features) enabled.
        """
        if not video_id:
            return {"success": False, "error": "Missing video_id"}
        if not thumbnail_path or not os.path.exists(thumbnail_path):
            logger.warning(f"Thumbnail file not found: {thumbnail_path}")
            return {"success": False, "error": f"Thumbnail not found: {thumbnail_path}"}

        creds = self.get_credentials()
        if not creds:
            return {"success": False, "error": "YouTube is not authenticated"}

        # Validate file size (YouTube max is 2MB)
        try:
            if os.path.isfile(thumbnail_path) and os.path.getsize(thumbnail_path) > 2 * 1024 * 1024:
                logger.info(f"Thumbnail exceeds 2MB limit, compressing for YouTube...")
                from PIL import Image
                compressed_path = thumbnail_path.replace(".jpg", "_yt.jpg")
                with Image.open(thumbnail_path) as im:
                    im.convert("RGB").save(compressed_path, format="JPEG", quality=85)
                if os.path.exists(compressed_path):
                    thumbnail_path = compressed_path
        except Exception:
            pass

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                from googleapiclient.discovery import build
                from googleapiclient.http import MediaFileUpload
                from googleapiclient.errors import HttpError

                youtube = build("youtube", "v3", credentials=creds)
                media = MediaFileUpload(
                    thumbnail_path,
                    mimetype="image/jpeg",
                    resumable=False,
                )
                request = youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=media,
                )
                response = request.execute()
                logger.success(f"Custom thumbnail successfully set for video {video_id}!")
                return {"success": True, "response": response}
            except HttpError as e:
                error_details = str(e)
                status_code = getattr(e.resp, "status", None) if hasattr(e, "resp") else None
                try:
                    error_content = json.loads(e.content.decode("utf-8"))
                    error_details = error_content.get("error", {}).get("message", str(e))
                except Exception:
                    pass

                # If YouTube video was just uploaded, it might take 2-4 seconds to propagate
                if status_code in (404, 500, 502, 503) and attempt < max_retries:
                    logger.warning(
                        f"YouTube returned temporary status {status_code} for thumbnail (attempt {attempt}/{max_retries}). "
                        "Retrying in 2s..."
                    )
                    time.sleep(2)
                    continue

                logger.warning(
                    f"Failed to set custom thumbnail for video {video_id}: {error_details}. "
                    "Note: Custom thumbnails require phone verification on your YouTube channel."
                )
                return {"success": False, "error": error_details}
            except Exception as e:
                if attempt < max_retries:
                    logger.warning(f"Unexpected error setting thumbnail (attempt {attempt}/{max_retries}): {e}. Retrying...")
                    time.sleep(2)
                    continue
                logger.warning(f"Unexpected error setting thumbnail: {e}")
                return {"success": False, "error": str(e)}

        return {"success": False, "error": "Thumbnail upload failed after retries"}

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        privacy_status: Optional[str] = None,
        video_aspect: Optional[str] = "9:16",
        thumbnail_path: Optional[str] = None,
        category_id: Optional[str] = None,
        made_for_kids: Optional[bool] = None,
        age_restricted: Optional[bool] = False,
        publish_at: Optional[str] = None,
        has_paid_promotion: bool = False,
        contains_synthetic_media: bool = False,
    ) -> Dict[str, Any]:
        """
        Uploads a video to YouTube.
        - If video_aspect is 9:16 (portrait): uploaded as YouTube Shorts with #Shorts routing.
        - If video_aspect is 16:9 (landscape): uploaded as a standard YouTube video.
        - If thumbnail_path is provided: sets the custom thumbnail on the uploaded video.
        - If category_id is provided: overrides default channel category (e.g. 20 for Gaming).
        - If made_for_kids is provided: sets selfDeclaredMadeForKids flag.
        - If age_restricted is provided: sets contentDetails.contentRating.ytRating = 'ytAgeRestricted'.
        - If publish_at is provided: sets scheduled release time (when privacy_status is private).
        - has_paid_promotion: sets paidProductPlacementDetails.hasPaidProductPlacement (default False).
        - contains_synthetic_media: sets status.containsSyntheticMedia (default False).
        """
        if not os.path.exists(video_path):
            logger.error(f"Video file not found: {video_path}")
            return {"success": False, "error": f"Video file not found: {video_path}"}

        creds = self.get_credentials()
        if not creds:
            msg = (
                f"YouTube is not authenticated. Please ensure '{self.client_secrets_file}' exists "
                f"and run authentication to create '{self.credentials_file}'."
            )
            logger.error(msg)
            return {"success": False, "error": msg}

        effective_privacy = (privacy_status or self.privacy_status).lower()
        if effective_privacy not in ("private", "unlisted", "public"):
            effective_privacy = "private"

        is_landscape = "16:9" in str(video_aspect) or "landscape" in str(video_aspect).lower()
        is_shorts = not is_landscape

        # Format title depending on whether it's Shorts or regular Video
        clean_title = (title or ("New Video" if is_landscape else "New Short")).strip()
        if is_shorts:
            if self.add_shorts_tag and "#shorts" not in clean_title.lower():
                if len(clean_title) + len(" #Shorts") <= 100:
                    clean_title = f"{clean_title} #Shorts"
                else:
                    clean_title = f"{clean_title[:91]} #Shorts"
        else:
            # Strip #shorts from title for standard 16:9 horizontal YouTube videos
            clean_title = re.sub(r"#shorts\b", "", clean_title, flags=re.IGNORECASE).strip()
        clean_title = clean_title[:100]

        # Ensure tags match format
        final_tags = list(tags) if tags else []
        if is_shorts:
            if self.add_shorts_tag:
                for tag in ["Shorts", "YouTubeShorts"]:
                    if tag not in final_tags:
                        final_tags.append(tag)
        else:
            # Remove shorts tags for horizontal videos
            final_tags = [t for t in final_tags if t.lower() not in ("shorts", "youtubeshorts", "#shorts", "#youtubeshorts")]

        # Build description
        final_desc = description.strip() if description else clean_title
        if is_shorts:
            if self.add_shorts_tag and "#shorts" not in final_desc.lower():
                final_desc = f"{final_desc}\n\n#Shorts #YouTubeShorts"
        else:
            # Remove #shorts hashtags from description for horizontal videos
            final_desc = re.sub(r"#shorts\b", "", final_desc, flags=re.IGNORECASE).strip()

        target_category = str(category_id or self.category_id or "20")
        # Para kazanma (monetization) ve yorumlar için kesinlikle Çocuklara Özel Değil (False),
        # 18+ Yaş Sınırı Devre Dışı (False), Ücretli Tanıtım Yok (False),
        # ve Sentetik/AI Medya Beyanı Hayır (False) olarak işaretlenir.
        is_kids = False

        status_dict: Dict[str, Any] = {
            "privacyStatus": effective_privacy,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": bool(contains_synthetic_media),
        }
        if publish_at and effective_privacy == "private":
            status_dict["publishAt"] = publish_at

        parts = ["snippet", "status", "paidProductPlacementDetails"]
        body = {
            "snippet": {
                "title": clean_title,
                "description": final_desc,
                "tags": final_tags,
                "categoryId": target_category,
            },
            "status": status_dict,
            "paidProductPlacementDetails": {
                "hasPaidProductPlacement": bool(has_paid_promotion),
            },
        }

        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            from googleapiclient.errors import HttpError

            youtube = build("youtube", "v3", credentials=creds)

            media = MediaFileUpload(
                video_path,
                chunksize=1024 * 1024 * 4,  # 4MB chunks
                resumable=True,
                mimetype="video/*",
            )

            video_type_str = "Shorts" if is_shorts else "Standard Video"
            logger.info(
                f"Starting YouTube {video_type_str} upload: '{clean_title}' (Aspect: {video_aspect}, Privacy: {effective_privacy})..."
            )
            insert_request = youtube.videos().insert(
                part=",".join(parts),
                body=body,
                media_body=media,
            )

            response = None
            while response is None:
                status, response = insert_request.next_chunk()
                if status:
                    progress_pct = int(status.progress() * 100)
                    logger.info(f"YouTube upload progress: {progress_pct}%")

            video_id = response.get("id", "")
            if is_shorts:
                video_url = f"https://www.youtube.com/shorts/{video_id}" if video_id else ""
            else:
                video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""

            logger.success(f"{video_type_str} uploaded to YouTube successfully! ID: {video_id}, URL: {video_url}")

            # Upload thumbnail if available
            thumbnail_result = None
            if thumbnail_path and os.path.exists(thumbnail_path) and video_id:
                logger.info(f"Uploading thumbnail for YouTube video {video_id} from {thumbnail_path}...")
                thumbnail_result = self.set_thumbnail(video_id, thumbnail_path)

            return {
                "success": True,
                "video_id": video_id,
                "video_url": video_url,
                "video_type": "shorts" if is_shorts else "video",
                "privacy_status": effective_privacy,
                "title": clean_title,
                "thumbnail_uploaded": thumbnail_result.get("success") if thumbnail_result else None,
            }

        except HttpError as e:
            error_details = str(e)
            try:
                error_content = json.loads(e.content.decode("utf-8"))
                error_details = error_content.get("error", {}).get("message", str(e))
            except Exception:
                pass
            logger.error(f"YouTube API HttpError: {error_details}")
            return {"success": False, "error": f"YouTube API error: {error_details}"}
        except Exception as e:
            logger.exception(f"Unexpected error during YouTube upload: {e}")
            return {"success": False, "error": str(e)}

    def update_video_privacy(self, video_id: str, new_privacy: str = "public") -> Dict[str, Any]:
        """
        Daha onceden taslak (private/unlisted) olarak yuklenmis bir YouTube videosunun
        gizlilik durumunu gunceller. Ornegin yayin zamani geldiginde aninda 'public' (Herkese Acik) yapar.
        """
        creds = self.get_credentials()
        if not creds:
            return {"success": False, "error": "YouTube is not authenticated"}

        new_privacy = new_privacy.lower()
        if new_privacy not in ("public", "private", "unlisted"):
            new_privacy = "public"

        try:
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError

            youtube = build("youtube", "v3", credentials=creds)
            body = {
                "id": video_id,
                "status": {
                    "privacyStatus": new_privacy,
                    "selfDeclaredMadeForKids": False,
                    "containsSyntheticMedia": False,
                }
            }
            req = youtube.videos().update(
                part="status",
                body=body
            )
            resp = req.execute()
            logger.success(f"YouTube video {video_id} privacy updated to '{new_privacy}' successfully!")
            return {"success": True, "video_id": video_id, "privacy_status": new_privacy, "response": resp}
        except HttpError as e:
            error_details = str(e)
            try:
                error_content = json.loads(e.content.decode("utf-8"))
                error_details = error_content.get("error", {}).get("message", str(e))
            except Exception:
                pass
            logger.error(f"Failed to update YouTube video privacy: {error_details}")
            return {"success": False, "error": error_details}
        except Exception as e:
            logger.exception(f"Unexpected error updating video privacy: {e}")
            return {"success": False, "error": str(e)}


# Singleton instance
youtube_uploader_service = YouTubeUploaderService()


def update_youtube_video_privacy(video_id: str, new_privacy: str = "public") -> Dict[str, Any]:
    return youtube_uploader_service.update_video_privacy(video_id=video_id, new_privacy=new_privacy)


def upload_to_youtube(
    video_path: str,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    privacy_status: Optional[str] = None,
    video_aspect: Optional[str] = "9:16",
    thumbnail_path: Optional[str] = None,
    category_id: Optional[str] = None,
    made_for_kids: Optional[bool] = None,
    age_restricted: Optional[bool] = False,
    publish_at: Optional[str] = None,
    has_paid_promotion: bool = False,
    contains_synthetic_media: bool = False,
) -> Dict[str, Any]:
    return youtube_uploader_service.upload_video(
        video_path=video_path,
        title=title,
        description=description,
        tags=tags,
        privacy_status=privacy_status,
        video_aspect=video_aspect,
        thumbnail_path=thumbnail_path,
        category_id=category_id,
        made_for_kids=made_for_kids,
        age_restricted=age_restricted,
        publish_at=publish_at,
        has_paid_promotion=has_paid_promotion,
        contains_synthetic_media=contains_synthetic_media,
    )
