import os
import logging
from typing import Optional
import pdfplumber
from src.backend.models import Message

logger = logging.getLogger(__name__)


class DocumentParser:
    """Parser for extracting content from documents."""

    def extract_text(self, file_path: str) -> str:
        """
        Extract text from a PDF file.

        Args:
            file_path: Path to the PDF file.

        Returns:
            Extracted text content.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file is not a PDF or cannot be parsed.
        """
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            raise FileNotFoundError(f"File not found: {file_path}")

        if not file_path.lower().endswith(".pdf"):
            logger.error(f"File is not a PDF: {file_path}")
            raise ValueError(f"File is not a PDF: {file_path}")

        try:
            text_content = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_content.append(page_text)

            return "\n".join(text_content)
        except Exception as e:
            logger.error(f"Error parsing PDF {file_path}: {e}")
            raise ValueError(f"Error parsing PDF {file_path}: {e}")

    def get_document_content(self, message: Message) -> Optional[str]:
        """
        Extract text content from a message's PDF attachment if it exists.

        Args:
            message: The message object potentially containing a document.

        Returns:
            Extracted text content or None if no PDF or file missing.
        """
        # Early exit if no local path
        if not message.local_path:
            return None

        # Early exit if not a PDF (checking both media_type and extension)
        is_pdf = (
            message.media_type == "document"
            and message.filename
            and message.filename.lower().endswith(".pdf")
        ) or (message.local_path.lower().endswith(".pdf"))

        if not is_pdf:
            return None

        # Early exit if file missing
        if not os.path.exists(message.local_path):
            logger.warning(f"Media file missing at {message.local_path}")
            return None

        try:
            return self.extract_text(message.local_path)
        except (FileNotFoundError, ValueError) as e:
            logger.error(f"Failed to extract text from message {message.id}: {e}")
            return None
