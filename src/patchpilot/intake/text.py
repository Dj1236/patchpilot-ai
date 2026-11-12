"""Handling of text that PatchPilot did not write.

Issue titles, issue bodies and repository file contents are untrusted data.
Two things are provided here:

- ``UntrustedText`` makes that status part of the type. It never turns into
  a string by accident: formatting it gives a placeholder, and code has to
  call ``reveal()`` to get the content. A test keeps the list of modules
  allowed to do that short.
- ``capture`` keeps a bounded copy of the original for provenance and builds
  a separate, cleaned presentation form for people to read.

Cleaning is for readability and size. **It does not prevent prompt
injection.** Text that survives cleaning can still contain instructions aimed
at a language model. The defense against that is structural and lives
elsewhere: no shell, a fixed set of validated tools, an allowlist, path and
budget checks in code, and approval gates.
"""

import hashlib
import unicodedata

from pydantic import Field

from patchpilot.sandbox.models import FrozenModel

# Invisible or direction-changing characters that can make text read
# differently from what it contains.
_INVISIBLE = frozenset(
    {
        0x00AD,
        0x061C,
        0x180E,
        0xFEFF,
        *range(0x200B, 0x2010),  # zero-width characters and direction marks
        *range(0x202A, 0x202F),  # embedding and override controls
        *range(0x2060, 0x2065),  # word joiner and invisible operators
        *range(0x2066, 0x206A),  # isolate controls
        *range(0xE0000, 0xE0080),  # tag characters
    }
)
_KEPT_CONTROLS = frozenset("\n\t")
_COMMENT_OPEN = "<!--"
_COMMENT_CLOSE = "-->"


class UntrustedText(FrozenModel):
    """Text from outside PatchPilot. Data only, never instructions."""

    untrusted_value: str

    def reveal(self) -> str:
        """Return the content. Every caller is deliberately handling untrusted data."""
        return self.untrusted_value

    def __len__(self) -> int:
        return len(self.untrusted_value)

    def _placeholder(self) -> str:
        return f"<untrusted text, {len(self.untrusted_value)} characters>"

    def __str__(self) -> str:
        return self._placeholder()

    def __repr__(self) -> str:
        return self._placeholder()

    def __format__(self, format_spec: str) -> str:
        return self._placeholder()


class CleaningNotes(FrozenModel):
    """What cleaning removed from the presentation form."""

    control_characters: int = 0
    invisible_characters: int = 0
    html_comments: int = 0
    truncated: bool = False


class CapturedText(FrozenModel):
    """A piece of outside text, kept for provenance and prepared for display.

    ``complete_sha256`` and ``complete_length`` describe the whole text as it
    was received, even when only part of it is kept. ``captured_sha256``
    describes exactly the characters stored in ``original``. The two hashes
    are equal only when ``captured_is_complete`` is true.
    """

    original: UntrustedText
    complete_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete_length: int = Field(ge=0)
    captured_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_is_complete: bool
    presentation: UntrustedText
    cleaning: CleaningNotes


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def strip_html_comments(text: str) -> tuple[str, int]:
    """Remove ``<!-- ... -->`` blocks, which GitHub hides from readers.

    An unterminated comment hides everything after it on GitHub, so it is
    removed to the end of the text. Runs in linear time.
    """
    pieces: list[str] = []
    removed = 0
    position = 0
    while True:
        start = text.find(_COMMENT_OPEN, position)
        if start < 0:
            pieces.append(text[position:])
            break
        pieces.append(text[position:start])
        removed += 1
        end = text.find(_COMMENT_CLOSE, start + len(_COMMENT_OPEN))
        if end < 0:
            break
        position = end + len(_COMMENT_CLOSE)
    return "".join(pieces), removed


def clean_for_presentation(
    text: str, *, max_chars: int, remove_html_comments: bool = False
) -> tuple[str, CleaningNotes]:
    """Return a readable, bounded form of ``text`` and what was removed.

    This makes text safe to display and compare. It does not make it safe to
    obey: see the module docstring.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    comments = 0
    if remove_html_comments:
        text, comments = strip_html_comments(text)

    kept: list[str] = []
    controls = 0
    invisible = 0
    for char in text:
        if ord(char) in _INVISIBLE:
            invisible += 1
        elif char in _KEPT_CONTROLS:
            kept.append(char)
        elif unicodedata.category(char) in ("Cc", "Cs", "Co", "Cn"):
            controls += 1
        else:
            kept.append(char)
    cleaned = "".join(kept)
    truncated = len(cleaned) > max_chars
    notes = CleaningNotes(
        control_characters=controls,
        invisible_characters=invisible,
        html_comments=comments,
        truncated=truncated,
    )
    return cleaned[:max_chars], notes


def capture(text: str, *, max_chars: int, remove_html_comments: bool = False) -> CapturedText:
    """Record ``text`` for provenance and build its presentation form.

    At most ``max_chars`` characters of the original are stored. The hash and
    length of the complete text are always recorded, so a truncated capture
    can still be matched against the full original later.
    """
    captured = text[:max_chars]
    presentation, notes = clean_for_presentation(
        text, max_chars=max_chars, remove_html_comments=remove_html_comments
    )
    return CapturedText(
        original=UntrustedText(untrusted_value=captured),
        complete_sha256=_sha256(text),
        complete_length=len(text),
        captured_sha256=_sha256(captured),
        captured_is_complete=len(captured) == len(text),
        presentation=UntrustedText(untrusted_value=presentation),
        cleaning=notes,
    )
