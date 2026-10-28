"""Safe parsing of raw RFC 5322 mail: text only, never opens attachments, never fetches anything."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

MAX_BYTES = 2_000_000
MAX_BODY = 8_000
RISKY_EXT = {".exe", ".js", ".vbs", ".scr", ".bat", ".cmd", ".ps1", ".jar", ".msi", ".iso", ".lnk"}
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
INSTRUCTION = re.compile(
    r"(?i)\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|all)\b[^.\n]{0,30}\b(instructions?|rules?)\b|\bset (the )?priority to\b|\bmark (this )?as (resolved|spam)\b"
)


@dataclass
class Mail:
    message_id: str
    sender: str
    domain: str
    subject: str
    body: str
    in_reply_to: str | None
    references: list[str]
    attachments: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def html_to_text(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", _SCRIPT.sub(" ", html))).strip()


def clean_header(v: str | None) -> str:
    return re.sub(r"[\r\n\x00-\x1f]+", " ", v or "").strip()[:200]


def parse(raw: bytes) -> Mail:
    if len(raw) > MAX_BYTES:
        raise ValueError("message too large")
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    name, addr = parseaddr(clean_header(msg["From"]))
    addr = addr.lower()
    if "@" not in addr:
        raise ValueError("no usable sender address")
    flags = []
    if clean_header(msg["Auto-Submitted"]).lower().startswith("auto") or clean_header(
        msg["Precedence"]
    ).lower() in {"bulk", "junk", "list"}:
        flags.append("auto_generated")
    body_part = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body_part is not None:
        content = body_part.get_content()
        text = html_to_text(content) if body_part.get_content_type() == "text/html" else content
        if body_part.get_content_type() == "text/html":
            flags.append("html_only")
    atts = []
    for part in msg.iter_attachments():
        fn = clean_header(part.get_filename() or "")
        ext = "." + fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
        atts.append(
            {
                "filename": fn[:80],
                "type": part.get_content_type(),
                "size": len(part.get_payload(decode=True) or b""),
                "risky": ext in RISKY_EXT,
            }
        )
    if any(a["risky"] for a in atts):
        flags.append("risky_attachment")
    text = re.sub(r"\s+", " ", text).strip()[:MAX_BODY]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if any(
        INSTRUCTION.search(x) for x in sentences
    ):  # drop instruction-like sentences so they cannot steer keywords
        flags.append("suspicious_instruction")
        text = " ".join(x for x in sentences if not INSTRUCTION.search(x)) or "[removed]"
    refs = [r for r in clean_header(msg["References"]).split() if r.startswith("<")]
    return Mail(
        clean_header(msg["Message-ID"]) or f"<none-{hash(raw) & 0xFFFFFF}>",
        addr,
        addr.split("@", 1)[1],
        clean_header(msg["Subject"]),
        text,
        clean_header(msg["In-Reply-To"]) or None,
        refs,
        atts,
        flags,
    )
