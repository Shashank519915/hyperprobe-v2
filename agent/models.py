from dataclasses import dataclass
from enum import Enum


class BreakpointType(str, Enum):
    FUNCTION = "function"
    METHOD = "method"
    FILE_LINE = "file_line"


class CaptureMode(str, Enum):
    ENTRY = "ENTRY"
    RETURN = "RETURN"
    BOTH = "BOTH"


@dataclass
class Breakpoint:
    """Registered trace target — see ARCHITECTURE_V2 §5.6."""

    id: str
    type: BreakpointType
    capture_mode: CaptureMode = CaptureMode.ENTRY
    value: str | None = None
    file: str | None = None
    line: int | None = None
