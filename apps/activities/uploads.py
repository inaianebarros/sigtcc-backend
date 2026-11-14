import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import IO

from config.errors import FieldValidationError

from .models import MAX_FILE_SIZE_BYTES

MAX_FILES = 5
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

COUNT_MESSAGE = f"Envie de 1 a {MAX_FILES} arquivos por entrega."
REFUSAL_COUNT_MESSAGE = f"Anexe até {MAX_FILES} arquivos à recusa."
NOT_ALLOWED_MESSAGE = "Tipo de arquivo não permitido. Envie PDF, DOC, DOCX, ZIP, PNG ou JPG."
EMPTY_MESSAGE = "O arquivo está vazio."
TOO_BIG_MESSAGE = f"O arquivo excede o limite de {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB."
MISMATCH_MESSAGE = "O conteúdo do arquivo não corresponde ao tipo informado pela extensão."

PDF_SIGNATURE = b"%PDF-"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPG_SIGNATURE = b"\xff\xd8\xff"
OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06")
DOCX_PARTS = {"[Content_Types].xml", "word/document.xml"}


def _is_zip(file, head: bytes) -> bool:
    return head.startswith(ZIP_SIGNATURES) and zipfile.is_zipfile(file)


def _is_docx(file, head: bytes) -> bool:
    if not _is_zip(file, head):
        return False
    with zipfile.ZipFile(file) as archive:
        return DOCX_PARTS <= set(archive.namelist())


def _starts_with(*signatures: bytes) -> Callable[[IO[bytes], bytes], bool]:
    return lambda _file, head: head.startswith(signatures)


@dataclass(frozen=True)
class FileKind:
    """An allowed type: what is stored with the file and how its content is recognised.

    The stored type comes from the extension, never from the sent Content-Type.
    """

    content_type: str
    matches: Callable[[IO[bytes], bytes], bool]


JPEG = FileKind("image/jpeg", _starts_with(JPG_SIGNATURE))
ALLOWED = {
    ".pdf": FileKind("application/pdf", _starts_with(PDF_SIGNATURE)),
    ".doc": FileKind("application/msword", _starts_with(OLE_SIGNATURE)),
    ".docx": FileKind(DOCX_TYPE, _is_docx),
    ".zip": FileKind("application/zip", _is_zip),
    ".png": FileKind("image/png", _starts_with(PNG_SIGNATURE)),
    ".jpg": JPEG,
    ".jpeg": JPEG,
}
# Enough of the start of a file to tell every allowed signature apart.
HEAD_SIZE = max(
    len(signature)
    for signature in (
        PDF_SIGNATURE,
        PNG_SIGNATURE,
        JPG_SIGNATURE,
        OLE_SIGNATURE,
        *ZIP_SIGNATURES,
    )
)


@dataclass(frozen=True)
class CheckedFile:
    file_name: str
    content_type: str


def clean_file_name(raw: str) -> str:
    """The original name, kept only as metadata and never used as a path or a storage key.

    Django already drops any directory part ("/" and "\\") when it parses the upload, so only
    the edges need trimming here.
    """
    return raw.strip()


def extension_of(file_name: str) -> str:
    dot = file_name.rfind(".")
    return file_name[dot:].lower() if dot > 0 else ""


def _problem_with(file, file_name: str) -> str | None:
    """The first problem of one file, or None; the order is type, size, content.

    The name needs no length check: Django cuts it to 255 characters, keeping the extension, and
    drops a file without a name before it reaches the API.
    """
    allowed = ALLOWED.get(extension_of(file_name))
    if allowed is None:
        return NOT_ALLOWED_MESSAGE
    if file.size == 0:
        return EMPTY_MESSAGE
    if file.size > MAX_FILE_SIZE_BYTES:
        return TOO_BIG_MESSAGE
    file.seek(0)
    head = file.read(HEAD_SIZE)
    file.seek(0)
    matches = allowed.matches(file, head)
    file.seek(0)
    return None if matches else MISMATCH_MESSAGE


def check_files(
    files: Sequence, minimum: int = 1, count_message: str = COUNT_MESSAGE
) -> list[CheckedFile]:
    """Validates the whole upload before anything is stored: one bad file rejects all (RF31).

    A delivery needs at least one file; a refusal takes none (RN48), so it passes `minimum=0`."""
    if not minimum <= len(files) <= MAX_FILES:
        raise FieldValidationError({"files": count_message})
    checked: list[CheckedFile] = []
    errors: dict[str, str] = {}
    for index, file in enumerate(files):
        file_name = clean_file_name(file.name)
        problem = _problem_with(file, file_name)
        if problem is not None:
            errors[f"files.{index}"] = problem
        else:
            checked.append(CheckedFile(file_name, ALLOWED[extension_of(file_name)].content_type))
    if errors:
        raise FieldValidationError(errors)
    return checked
