"""Collect bounded libcurl response bytes without retaining them in diagnostics."""

from dataclasses import dataclass, field

from curl_cffi.curl import CURL_WRITEFUNC_ERROR


@dataclass
class BoundedResponseBody:
    """Abort a native HTTP transfer before its response exceeds the byte allowance."""

    limit: int
    chunks: list[bytes] = field(default_factory=list, repr=False)
    size: int = 0
    exceeded: bool = False

    def write(self, chunk: bytes) -> int:
        """Collect one bounded chunk or signal that libcurl must stop the transfer."""
        self.size += len(chunk)
        if self.size > self.limit:
            self.exceeded = True
            return CURL_WRITEFUNC_ERROR
        self.chunks.append(chunk)
        return len(chunk)
