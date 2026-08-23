from __future__ import annotations

import io

import Quartz
from Cocoa import NSMutableData
from PIL import Image

from .region import Region


def capture_cgimage(x: float, y: float, width: float, height: float):
    """Captures a screen rect (in global points) and returns a CGImageRef."""
    rect = Quartz.CGRectMake(x, y, width, height)
    return Quartz.CGWindowListCreateImage(
        rect,
        Quartz.kCGWindowListOptionOnScreenOnly,
        Quartz.kCGNullWindowID,
        Quartz.kCGWindowImageDefault,
    )


def cgimage_to_pil(image_ref) -> Image.Image:
    data = NSMutableData.data()
    dest = Quartz.CGImageDestinationCreateWithData(data, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, image_ref, None)
    Quartz.CGImageDestinationFinalize(dest)
    return Image.open(io.BytesIO(bytes(data)))


def capture_region(region: Region) -> Image.Image:
    image_ref = capture_cgimage(region.x, region.y, region.width, region.height)
    return cgimage_to_pil(image_ref)
