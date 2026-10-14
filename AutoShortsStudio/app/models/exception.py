import traceback
from typing import Any

from loguru import logger


class ServiceError(Exception):
    """Base exception for all domain service errors."""


class LLMGenerationError(ServiceError):
    """An LLM provider failed to generate the requested content."""


class LLMQuotaExceededError(LLMGenerationError):
    """The LLM provider refused the request due to quota or rate limits."""


class VoiceSynthesisError(ServiceError):
    """A TTS provider failed to synthesize audio."""


class VideoRenderError(ServiceError):
    """Video encoding or rendering failed."""


class MaterialDownloadError(ServiceError):
    """Downloading stock video/image material failed."""


class HttpException(Exception):
    def __init__(
        self, task_id: str, status_code: int, message: str = "", data: Any = None
    ):
        self.message = message
        self.status_code = status_code
        self.data = data
        # Build a diagnostic string for the caller to log if desired,
        # but do NOT log automatically in __init__ -- this caused noise
        # when exceptions were created in tests or caught cleanly upstream.
        tb_str = traceback.format_exc().strip()
        if not tb_str or tb_str == "NoneType: None":
            self._diagnostic = f"HttpException: {status_code}, {task_id}, {message}"
        else:
            self._diagnostic = (
                f"HttpException: {status_code}, {task_id}, {message}\n{tb_str}"
            )

    @property
    def diagnostic(self) -> str:
        return self._diagnostic


class FileNotFoundException(FileNotFoundError):
    """Kept for backward compatibility; prefer built-in FileNotFoundError."""
