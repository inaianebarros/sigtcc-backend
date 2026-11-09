"""Small valid files of every allowed type, for the delivery tests."""

import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile

PDF = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
DOC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16


def zip_bytes(names=("nota.txt",)):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "conteúdo")
    return buffer.getvalue()


ZIP = zip_bytes()
DOCX = zip_bytes(("[Content_Types].xml", "word/document.xml"))

VALID_FILES = {
    "relatorio.pdf": (PDF, "application/pdf"),
    "capitulo.doc": (DOC, "application/msword"),
    "capitulo.docx": (
        DOCX,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "codigo.zip": (ZIP, "application/zip"),
    "grafico.png": (PNG, "image/png"),
    "foto.jpg": (JPG, "image/jpeg"),
}


def upload(name, content, content_type="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=content_type)
