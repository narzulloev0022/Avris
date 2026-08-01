"""Заголовок Content-Disposition для файлов с не-латинскими именами.

HTTP-заголовки кодируются latin-1, а лаборатория называет сканы по-русски
(«Анализ крови.pdf») или по-таджикски. На таком имени Starlette падал с
UnicodeEncodeError — пациент получал 500 вместо своего анализа, а врач не мог
открыть загруженный лабораторией файл.

RFC 6266 решает это парой: ASCII-запасное имя для древних клиентов и
``filename*`` в UTF-8 для всех остальных.
"""
from urllib.parse import quote


def content_disposition(filename: str, *, inline: bool = True) -> str:
    cleaned = (filename or "").replace('"', "").replace("\r", " ").replace("\n", " ").strip()
    if not cleaned:
        cleaned = "file"

    stem, dot, ext = cleaned.rpartition(".")
    if not dot:
        stem, ext = cleaned, ""
    ascii_stem = stem.encode("ascii", "ignore").decode().strip()
    ascii_ext = ext.lower() if ext.isascii() and ext.isalnum() else ""
    if not any(ch.isalnum() for ch in ascii_stem):
        # Имя целиком не-латинское: расширение сохраняем, чтобы система знала,
        # чем открывать, а основу заменяем родовой.
        ascii_stem = "file"
    ascii_name = f"{ascii_stem}.{ascii_ext}" if ascii_ext else ascii_stem

    disposition = "inline" if inline else "attachment"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(cleaned, safe='')}"
