"""Prompt-injection defences at the API boundary (F10, see ``mizan_core.security``).

- **In:** a question that tries to instruct the model is refused with 400 before it costs
  anything. The audit log records it as ``injection``. A question that passes is
  ``neutralise``-d (no invisible characters, no forged tags) before it reaches a prompt.
- **Out:** an answer that reproduces a system prompt verbatim is replaced by a refusal and
  logged as an error. The screen is bypassable; this check is the backstop for the one leak
  that matters most.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from mizan_core.logging import get_logger
from mizan_core.security import Channel, leaks_prompt, neutralise, screen

log = get_logger(__name__)

REFUSED = "Bu sorğu təhlükəsizlik siyasətinə görə icra olunmadı."
LEAK_REFUSAL = "Bu sual üzrə cavab verə bilmirəm. Sualı dəqiqləşdirin."


def screened(request: Request, text: str, channel: Channel = "question") -> str:
    """The text, made safe for a prompt; HTTP 400 when it is an injection attempt."""
    verdict = screen(text, channel)
    if verdict.blocked:
        request.state.audit_outcome = "injection"
        log.warning("injection_blocked", channel=channel, reasons=list(verdict.reasons))
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {"message": REFUSED, "reasons": list(verdict.reasons)},
        )
    return neutralise(text)


def leaked(answer: str, *prompts: str) -> bool:
    if any(leaks_prompt(answer, p) for p in prompts):
        log.error("system_prompt_leak_blocked")
        return True
    return False
