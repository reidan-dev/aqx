from __future__ import annotations

from typing import Optional

import Vision


def read_text_from_cgimage(image_ref) -> Optional[str]:
    """Runs on-device OCR (Apple's Vision framework) on a CGImageRef and returns the
    recognized text, or None if nothing was detected."""
    results: dict = {}

    def handler(request, error):
        observations = request.results()
        lines = []
        if observations:
            for obs in observations:
                candidates = obs.topCandidates_(1)
                if candidates:
                    lines.append(str(candidates[0].string()))
        results["text"] = "\n".join(lines)

    request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(handler)
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)

    request_handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image_ref, None)
    ok, error = request_handler.performRequests_error_([request], None)
    if not ok:
        return None

    text = results.get("text", "").strip()
    return text or None
