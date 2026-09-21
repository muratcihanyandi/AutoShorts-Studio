from enum import IntEnum, StrEnum

PUNCTUATIONS = (
    "?",
    ",",
    ".",
    "、",
    ";",
    ":",
    "!",
    "…",
    "？",
    "，",
    "。",
    "；",
    "：",
    "！",
    # 阿拉伯语常用标点也应作为自然断句点，避免脚本文本和 edge-tts
    # 返回的字幕停顿边界不一致，导致后续逐行匹配失败。
    "،",
    "؛",
    "؟",
)


class TaskState(IntEnum):
    FAILED = -1
    COMPLETE = 1
    PROCESSING = 4


# Backward-compatible aliases for existing code that references the old names.
TASK_STATE_FAILED = TaskState.FAILED
TASK_STATE_COMPLETE = TaskState.COMPLETE
TASK_STATE_PROCESSING = TaskState.PROCESSING


class CrossPostState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


# Backward-compatible aliases.
CROSS_POST_STATE_PENDING = CrossPostState.PENDING
CROSS_POST_STATE_PROCESSING = CrossPostState.PROCESSING
CROSS_POST_STATE_COMPLETE = CrossPostState.COMPLETE
CROSS_POST_STATE_FAILED = CrossPostState.FAILED

FILE_TYPE_VIDEOS = ("mp4", "mov", "mkv", "webm")
FILE_TYPE_IMAGES = ("jpg", "jpeg", "png", "bmp")
