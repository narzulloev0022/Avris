"""Avris Patient ID — the human-facing global patient identifier.

Format: ``AV-XXXX-XXXX`` over an unambiguous alphabet (no 0/O, 1/I/L) so the
ID survives being read out loud at a reception desk or typed from a card.
8 chars over a 31-symbol alphabet ≈ 8.5e11 combinations — collisions are
guarded by the DB unique constraint; callers retry on IntegrityError.
"""
import secrets

_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def new_avris_patient_id() -> str:
    chars = [secrets.choice(_ALPHABET) for _ in range(8)]
    return f"AV-{''.join(chars[:4])}-{''.join(chars[4:])}"
