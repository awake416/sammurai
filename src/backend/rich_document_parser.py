"""Rich document parser: hybrid OCR pipeline for WhatsApp attachments.

Pipeline:
  PDF → pdfplumber (native text) → if sparse → docTR or EasyOCR → Ollama cleanup
  Image (.jpg/.png) → EasyOCR directly → Ollama cleanup
"""

import io
import logging
import os
from pathlib import Path
from typing import Optional

import pdfplumber

from src.backend.models import Message
from src.backend.utils import redact_pii

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

# Thresholds from LEARNINGS.md
NATIVE_TEXT_MIN_CHARS = 100   # use pdfplumber result if >= this
SCANNED_TEXT_THRESHOLD = 50   # try OCR if native text < this
OCR_MIN_CHARS = 30            # fallback to other engine if result < this
OLLAMA_CLEANUP_MIN_CHARS = 20 # skip Ollama for very short OCR output

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"

# Hindi/bilingual filename hints → prefer EasyOCR
HINDI_HINTS = {"aadhaar", "adhar", "aadhar", "gst", "udyam", "electricity", "partnership"}


class RichDocumentParser:
    """Hybrid OCR parser: pdfplumber → docTR/EasyOCR → Ollama cleanup."""

    def __init__(self, use_ollama_cleanup: bool = True):
        self.use_ollama_cleanup = use_ollama_cleanup
        self._doctr_model = None   # lazy-loaded
        self._easyocr_reader = None  # lazy-loaded

    # ------------------------------------------------------------------
    # Public interface (backward-compatible with DocumentParser)
    # ------------------------------------------------------------------

    def extract_text(self, file_path: str) -> str:
        """Extract text from PDF or image file."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = Path(file_path).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported file type: {ext}")

        if ext in {".jpg", ".jpeg", ".png"}:
            return self._ocr_image(file_path)

        return self._extract_pdf(file_path)

    def get_document_content(self, message: Message) -> Optional[str]:
        """Extract text from a message attachment (PDF or image)."""
        if not message.local_path:
            return None

        ext = Path(message.local_path).suffix.lower()
        is_supported = ext in SUPPORTED_EXTENSIONS

        if not is_supported:
            return None

        if not os.path.exists(message.local_path):
            logger.warning("Media file missing at %s", redact_pii(message.local_path))
            return None

        try:
            return self.extract_text(message.local_path)
        except (FileNotFoundError, ValueError) as e:
            logger.error("Failed to extract from message %s: %s", message.id, e)
            return None

    # ------------------------------------------------------------------
    # PDF extraction
    # ------------------------------------------------------------------

    def _extract_pdf(self, file_path: str) -> str:
        """Try pdfplumber native text first; fall through to OCR if sparse."""
        native_text = ""
        try:
            with pdfplumber.open(file_path) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
                native_text = "\n".join(pages)
        except Exception as e:
            logger.warning("pdfplumber failed on %s: %s — trying OCR", file_path, e)

        if len(native_text) >= NATIVE_TEXT_MIN_CHARS:
            return native_text

        logger.debug("Native text sparse (%d chars), falling back to OCR", len(native_text))
        return self._ocr_pdf(file_path)

    def _ocr_pdf(self, file_path: str, hint: str = "auto") -> str:
        """Run OCR on a PDF. hint: 'english' | 'hindi' | 'auto'."""
        images = self._pdf_to_images(file_path)
        if not images:
            return ""

        prefer_hindi = (
            hint == "hindi"
            or any(h in Path(file_path).stem.lower() for h in HINDI_HINTS)
        )

        if prefer_hindi:
            text = self._run_easyocr(images)
            if len(text) < OCR_MIN_CHARS:
                logger.debug("EasyOCR sparse (%d chars), trying docTR fallback", len(text))
                fallback = self._run_doctr(images)
                if len(fallback) >= OCR_MIN_CHARS:
                    text = fallback
                elif not text:
                    text = fallback
        else:
            text = self._run_doctr(images)
            if len(text) < OCR_MIN_CHARS:
                logger.debug("docTR sparse (%d chars), trying EasyOCR fallback", len(text))
                fallback = self._run_easyocr(images)
                if len(fallback) >= OCR_MIN_CHARS:
                    text = fallback
                elif not text:
                    text = fallback

        # Both engines failed to produce sufficient text
        if len(text) < OCR_MIN_CHARS:
            logger.warning("Both OCR engines produced insufficient text (%d chars)", len(text))
            return ""

        return self._postprocess_ocr(text)

    def _ocr_image(self, file_path: str) -> str:
        """Run EasyOCR on a standalone image file."""
        try:
            from PIL import Image
            import numpy as np
            img = Image.open(file_path).convert("RGB")
            images = [np.array(img)]
        except Exception as e:
            logger.error("Failed to load image %s: %s", file_path, e)
            return ""

        text = self._run_easyocr(images)
        return self._postprocess_ocr(text)

    # ------------------------------------------------------------------
    # OCR engines (lazy-loaded)
    # ------------------------------------------------------------------

    def _pdf_to_images(self, file_path: str) -> list:
        """Render PDF pages to numpy arrays for OCR."""
        try:
            import fitz  # PyMuPDF
            import numpy as np
            from PIL import Image

            doc = fitz.open(file_path)
            images = []
            for page in doc:
                pix = page.get_pixmap(dpi=200)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                images.append(np.array(img))
            doc.close()
            return images
        except ImportError:
            logger.warning("PyMuPDF (fitz) not installed — cannot render PDF to images")
            return []
        except Exception as e:
            logger.error("PDF rendering failed: %s", e)
            return []

    def _run_doctr(self, images: list) -> str:
        """Run docTR OCR on a list of images."""
        try:
            from doctr.models import ocr_predictor
            import numpy as np

            if self._doctr_model is None:
                logger.info("Loading docTR model (first use)...")
                self._doctr_model = ocr_predictor(
                    "db_resnet50", "crnn_vgg16_bn",
                    pretrained=True,
                    assume_straight_pages=True,
                )

            from doctr.io import DocumentFile
            # docTR expects a list of numpy arrays
            result = self._doctr_model(images)
            lines = []
            for page in result.pages:
                for block in page.blocks:
                    for line in block.lines:
                        text = " ".join(w.value for w in line.words)
                        if text.strip():
                            lines.append(text)
            return "\n".join(lines)
        except ImportError:
            logger.warning("doctr not installed — skipping docTR OCR")
            return ""
        except Exception as e:
            logger.error("docTR OCR failed: %s", e)
            return ""

    def _run_easyocr(self, images: list) -> str:
        """Run EasyOCR on a list of images."""
        try:
            import easyocr
            import numpy as np

            if self._easyocr_reader is None:
                logger.info("Loading EasyOCR model (first use)...")
                self._easyocr_reader = easyocr.Reader(
                    ["en", "hi"], gpu=True, verbose=False
                )

            all_text = []
            for img in images:
                results = self._easyocr_reader.readtext(img, detail=1, paragraph=False)
                # Sort by Y-position (top-to-bottom, left-to-right)
                ordered = sorted(
                    results,
                    key=lambda r: (min(p[1] for p in r[0]) // 30, min(p[0] for p in r[0]))
                )
                all_text.extend(t[1] for t in ordered if t[1].strip())

            return " ".join(all_text)
        except ImportError:
            logger.warning("easyocr not installed — skipping EasyOCR")
            return ""
        except Exception as e:
            logger.error("EasyOCR failed: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Ollama cleanup
    # ------------------------------------------------------------------

    def _postprocess_ocr(self, text: str) -> str:
        """Apply Ollama cleanup if enabled and text is long enough."""
        if not self.use_ollama_cleanup:
            return text
        if len(text) < OLLAMA_CLEANUP_MIN_CHARS:
            return text
        return self._ollama_cleanup(text)

    def _ollama_cleanup(self, raw_text: str) -> str:
        """Use qwen2.5:7b to clean OCR errors and normalize text."""
        import requests

        prompt = (
            "You are cleaning up OCR text from scanned Indian documents. "
            "Fix common OCR errors: 0↔O, 8↔B, 1↔I, 5↔S, 2↔Z in appropriate positions. "
            "Preserve all names, numbers, dates, and amounts exactly. "
            "Do NOT add or invent information. Return only the cleaned text, nothing else.\n\n"
            f"OCR text:\n{raw_text[:3000]}"
        )

        try:
            resp = requests.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 800},
                },
                timeout=45,
            )
            cleaned = resp.json().get("response", "").strip()
            return cleaned if cleaned else raw_text
        except Exception as e:
            logger.warning("Ollama cleanup failed (%s) — returning raw OCR text", e)
            return raw_text
