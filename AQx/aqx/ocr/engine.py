from __future__ import annotations

import re
from typing import Optional

import Vision

# Glyphs Vision can still misread as letters even with language correction off,
# because in many UI fonts the shapes are genuinely near-identical to a digit
# (0/O, 1/I/l, 5/S, 8/B, 2/Z, 6/G).
_DIGIT_CONFUSIONS = str.maketrans({
    "O": "0", "o": "0",
    "I": "1", "l": "1", "|": "1",
    "S": "5", "s": "5",
    "B": "8",
    "Z": "2",
    "G": "6",
})
# A token only gets corrected if, once the confusable letters above are treated
# as digits, every character in it is a digit or common counter punctuation
# (/, :, ., ,, %, -) AND it contains at least one actual digit already - so a
# real word like "So" or "Is" (no digit present) is left alone, and a mixed
# token like "iPhone12" (has letters outside the confusable set) never matches.
_NUMERIC_TOKEN = re.compile(r"^[0-9OoIl|SsBZG/:.,%-]+$")


def _fix_digit_confusions(text: str) -> str:
    def fix_token(token: str) -> str:
        if any(c.isdigit() for c in token) and _NUMERIC_TOKEN.match(token):
            return token.translate(_DIGIT_CONFUSIONS)
        return token

    return "\n".join(
        " ".join(fix_token(tok) for tok in line.split(" "))
        for line in text.split("\n")
    )


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
    # Vision's language-correction model "fixes" recognized text against real
    # English words/spelling - which is exactly what turns a lone digit like "0"
    # into "O" in UI counters (e.g. "0/10") that aren't real words to begin with.
    request.setUsesLanguageCorrection_(False)

    request_handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image_ref, None)
    ok, error = request_handler.performRequests_error_([request], None)
    if not ok:
        return None

    text = results.get("text", "").strip()
    if not text:
        return None
    return _fix_digit_confusions(text)
