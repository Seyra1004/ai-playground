from __future__ import annotations

from core.models import CanonicalContent, ThreadsContent


def build_threads_text(canonical: CanonicalContent) -> str:
    fs = canonical.fact_sheet
    if not fs.why_it_matters or not fs.deadline:
        raise ValueError(
            "cannot build Threads text: fact sheet is missing why_it_matters/deadline "
            "(canonical content copy generation has not occurred)"
        )
    return f"{fs.why_it_matters} {fs.amount_or_benefit} 마감: {fs.deadline}".strip()


def build_threads_content(canonical: CanonicalContent, text: str = None) -> ThreadsContent:
    """Map the shared CanonicalContent into Threads' concise text model.

    Deliberately composed from different fact-sheet fields/structure than the
    Instagram caption so Threads copy is never just the carousel text pasted
    over (product rule 18). `text`, when provided, is the already-authored
    (semantic-layer) Threads copy for this canonical content.
    """
    if not canonical.pages:
        raise ValueError(
            "Threads adapter requires the shared canonical content to be finalized; "
            "no pages found (copy generation has not occurred)"
        )

    if text is None:
        text = build_threads_text(canonical)
    cta = "링크 저장하고 가족에게 공유하세요" if canonical.fact_sheet.action_steps else "지금 확인하세요"
    return ThreadsContent(text=text, cta=cta)
