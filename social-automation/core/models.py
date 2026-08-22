from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SourceType(str, Enum):
    GOVERNMENT = "government"
    PUBLIC_INSTITUTION = "public_institution"
    OFFICIAL_OPERATOR = "official_operator"
    NEWS_MEDIA = "news_media"
    OTHER = "other"


AUTHORITATIVE_SOURCE_TYPES = {
    SourceType.GOVERNMENT,
    SourceType.PUBLIC_INSTITUTION,
    SourceType.OFFICIAL_OPERATOR,
}


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"


class ClaimType(str, Enum):
    ELIGIBILITY = "eligibility"
    AMOUNT = "amount"
    DEADLINE = "deadline"
    REQUIREMENTS = "requirements"
    EXCLUSIONS = "exclusions"
    REQUIRED_DOCUMENTS = "required_documents"
    ACTION_METHOD = "action_method"
    OTHER = "other"


CRITICAL_CLAIM_TYPES = {
    ClaimType.ELIGIBILITY,
    ClaimType.AMOUNT,
    ClaimType.DEADLINE,
    ClaimType.REQUIREMENTS,
    ClaimType.EXCLUSIONS,
    ClaimType.REQUIRED_DOCUMENTS,
    ClaimType.ACTION_METHOD,
}


class QAStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class Source:
    source_id: str
    url: str
    source_type: SourceType
    publisher: str
    published_at: Optional[str] = None
    retrieved_at: Optional[str] = None

    @property
    def is_authoritative(self) -> bool:
        return self.source_type in AUTHORITATIVE_SOURCE_TYPES


@dataclass
class Claim:
    claim_id: str
    claim_type: ClaimType
    text: str
    source_ids: list = field(default_factory=list)
    verified_at: Optional[str] = None
    status: VerificationStatus = VerificationStatus.UNVERIFIED

    @property
    def is_critical(self) -> bool:
        return self.claim_type in CRITICAL_CLAIM_TYPES


@dataclass
class SourceExcerpt:
    """A deterministically-extracted, relevant slice of a source page --
    the unit actually handed to the semantic layer, instead of a full raw
    page (context minimization). extracted_fields holds structured pulls
    (amounts/dates/eligibility text/etc) found by deterministic extraction.
    """

    excerpt_id: str
    source_id: str
    text: str
    extracted_fields: dict = field(default_factory=dict)
    excerpt_hash: str = ""


@dataclass
class TopicCandidate:
    candidate_id: str
    topic: str
    category: str
    summary: str
    urgent: bool = False
    timeliness_signal: float = 0.0
    practical_value_signal: float = 0.0
    population_reach_signal: float = 0.0
    verification_availability_signal: float = 0.0
    save_share_signal: float = 0.0
    duplication_penalty_signal: float = 0.0
    has_authoritative_source: bool = False


@dataclass
class ScoreBreakdown:
    timeliness: float
    practical_value: float
    population_reach: float
    verification_availability: float
    save_share: float
    duplication_balance: float

    @property
    def total(self) -> float:
        return (
            self.timeliness
            + self.practical_value
            + self.population_reach
            + self.verification_availability
            + self.save_share
            + self.duplication_balance
        )


@dataclass
class FactSheet:
    content_id: str
    topic: str
    reader_value: str
    affected_audience: str
    event_or_policy: str
    why_it_matters: str
    eligibility: str
    exclusions: str
    amount_or_benefit: str
    deadline: str
    action_steps: list
    required_documents: list
    exceptions_and_warnings: list
    claims: list
    sources: list
    image_rights: str
    risk_flags: list = field(default_factory=list)
    verified_at: Optional[str] = None
    volatile_fields: list = field(default_factory=list)


@dataclass
class CarouselPage:
    page_number: int
    role: str
    headline: str
    body: str
    visual_ref: str
    visual_data: dict = field(default_factory=dict)


@dataclass
class InstagramContent:
    pages: list
    caption: str


@dataclass
class ThreadsContent:
    text: str
    cta: str


@dataclass
class CanonicalContent:
    content_id: str
    fact_sheet: FactSheet
    page_count: int
    page_plan: list
    pages: list = field(default_factory=list)
    instagram: Optional[InstagramContent] = None
    threads: Optional[ThreadsContent] = None


@dataclass
class RenderPackage:
    """The rendered-page contract handed to the PNG renderer: one entry per
    page (page_number/role/layout_variant/width/height/html), plus enough
    identity to trace it back to a run.
    """

    content_id: str
    canvas_width: int
    canvas_height: int
    pages: list  # list[dict], as produced by renderer.html_renderer.build_renderer_input


@dataclass
class QAResult:
    status: QAStatus
    checks_passed: list = field(default_factory=list)
    checks_failed: list = field(default_factory=list)
    notes: list = field(default_factory=list)


@dataclass
class ContentPackage:
    account_id: str
    content_id: str
    canonical_content: Optional[CanonicalContent]
    instagram_caption: Optional[str]
    threads_text: Optional[str]
    qa_result: Optional[QAResult]
    status: str = "IN_PROGRESS"


@dataclass
class PipelineStageState:
    account_id: str
    content_id: str
    stage: str
    status: str
    input_hash: str
    output_hash: Optional[str] = None
    error: Optional[str] = None
    retry_count: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
