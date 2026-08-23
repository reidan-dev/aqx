from __future__ import annotations

import io
import os

import Quartz
from Cocoa import NSMutableData
from PIL import Image

from .region import Region


def _own_window_ids(pid: int = None) -> set:
    """Window IDs owned by this process (AQx's own windows - main window, dialogs,
    overlays, the region-picker toolbar). Excluding these from a capture is what lets
    OCR "see through" AQx to whatever's actually underneath it."""
    pid = os.getpid() if pid is None else pid
    windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    return {w[Quartz.kCGWindowNumber] for w in windows if w.get(Quartz.kCGWindowOwnerPID) == pid}


def capture_cgimage(x: float, y: float, width: float, height: float):
    """Captures a screen rect (in global points) and returns a CGImageRef. Composites
    only windows NOT owned by AQx itself, so if the AQx window happens to overlap the
    requested region, the capture shows whatever app is underneath instead of AQx's
    own UI."""
    rect = Quartz.CGRectMake(x, y, width, height)
    own_ids = _own_window_ids()
    all_windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    window_ids = [w[Quartz.kCGWindowNumber] for w in all_windows if w[Quartz.kCGWindowNumber] not in own_ids]
    return Quartz.CGWindowListCreateImageFromArray(rect, window_ids, Quartz.kCGWindowImageDefault)


def cgimage_to_pil(image_ref) -> Image.Image:
    data = NSMutableData.data()
    dest = Quartz.CGImageDestinationCreateWithData(data, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, image_ref, None)
    Quartz.CGImageDestinationFinalize(dest)
    return Image.open(io.BytesIO(bytes(data)))


def capture_region(region: Region) -> Image.Image:
    image_ref = capture_cgimage(region.x, region.y, region.width, region.height)
    return cgimage_to_pil(image_ref)
