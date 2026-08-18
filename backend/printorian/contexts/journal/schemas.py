"""What crosses the wire.

The block union is the interesting part. Each kind is its own model with its own
required fields, discriminated on `kind`, so an editor that sends a code listing
without any code is rejected at the edge with a field error rather than producing
an article with an empty grey box in it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from printorian.contexts.journal.policies import Section

# --------------------------------------------------------------- body blocks


class Heading(BaseModel):
    kind: Literal["heading"] = "heading"
    text: str = Field(min_length=1, max_length=200)


class Paragraph(BaseModel):
    kind: Literal["paragraph"] = "paragraph"
    #: Carries `**bold**` and `` `code` `` and nothing else. The client renders
    #: those two marks itself, which is why no markup can reach it (ADR-0012 in
    #: spirit: the backend sends content, the client decides how it looks).
    text: str = Field(min_length=1, max_length=4000)


class ListBlock(BaseModel):
    kind: Literal["list"] = "list"
    items: list[str] = Field(min_length=1, max_length=30)


class Callout(BaseModel):
    kind: Literal["callout"] = "callout"
    title: str = Field(default="", max_length=160)
    text: str = Field(min_length=1, max_length=1200)
    #: `live` is the kit's accented callout, used for the rule a report turns on.
    tone: Literal["plain", "live"] = "plain"


class Quote(BaseModel):
    kind: Literal["quote"] = "quote"
    text: str = Field(min_length=1, max_length=800)
    #: «ОТЧЁТ #52 · КАРТА ОБСЛУЖИВАНИЯ» — where the pulled line came from.
    cite: str = Field(default="", max_length=160)


class Code(BaseModel):
    kind: Literal["code"] = "code"
    #: The listing's header bar, left and right — «PRICING/ENGINE.PY» / «ФРАГМЕНТ».
    label: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=40)
    code: str = Field(min_length=1, max_length=6000)


class TableBlock(BaseModel):
    kind: Literal["table"] = "table"
    head: list[str] = Field(min_length=1, max_length=8)
    rows: list[list[str]] = Field(min_length=1, max_length=60)
    #: Per column, so a figures column can sit right where the kit puts it.
    align: list[Literal["start", "end"]] = Field(default_factory=list)


class FigureRow(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=60)
    tone: Literal["plain", "good", "warn", "bad"] = "plain"


class Figures(BaseModel):
    """The kit's «Итог отчёта в цифрах» panel."""

    kind: Literal["figures"] = "figures"
    title: str = Field(default="", max_length=120)
    aside: str = Field(default="", max_length=80)
    rows: list[FigureRow] = Field(min_length=1, max_length=20)
    total_label: str = Field(default="", max_length=80)
    total_value: str = Field(default="", max_length=60)
    note: str = Field(default="", max_length=160)


Block = Annotated[
    Heading | Paragraph | ListBlock | Callout | Quote | Code | TableBlock | Figures,
    Field(discriminator="kind"),
]


# ------------------------------------------------------------------- reading


class PostCard(BaseModel):
    """A report as the index grid and the archive rows show it."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    number: int
    title: str
    excerpt: str
    section: Section
    author: str
    read_minutes: int
    is_published: bool
    published_at: datetime | None


class TocEntry(BaseModel):
    """One line of «Содержание», and the anchor it scrolls to."""

    anchor: str
    text: str


class Neighbour(BaseModel):
    """A sibling report, for the article's «Другие отчёты» rail."""

    slug: str
    number: int
    title: str


class PostView(PostCard):
    """One report, in full."""

    lede: str
    data_note: str
    blocks: list[Block]
    #: Derived from the headings, so a contents list can never disagree with the
    #: article it describes.
    toc: list[TocEntry] = Field(default_factory=list)
    #: The three most recent other reports. Composed at read time rather than
    #: stored, or every new report would have to rewrite its predecessors.
    neighbours: list[Neighbour] = Field(default_factory=list)


class Subscribe(BaseModel):
    """An address asking to hear about new reports."""

    email: EmailStr


class Subscription(BaseModel):
    """What the form is told back.

    Deliberately says nothing about whether the address was already on the list.
    A different answer for "added" and "already there" turns this endpoint into a
    way of asking whether somebody subscribed, which is not a question a stranger
    gets to ask about another person.
    """

    accepted: bool = True


class SectionCount(BaseModel):
    """A filter chip and its tally, for the kit's «Все 18 · Себестоимость 5»."""

    section: Section
    count: int


class JournalIndex(BaseModel):
    rows: list[PostCard]
    counts: list[SectionCount]
    #: Across the whole journal, not this page — the chips describe the journal.
    #: Follows the caller's permission, so an editor's own drafts are counted in
    #: the filter chips they can actually filter by.
    total: int
    #: Issues that have actually come out. Never counts drafts, whoever is asking,
    #: because the lead block's «N ВЫПУСКОВ» is a claim about the farm rather than
    #: about the current query.
    published_total: int = 0
    #: Reports per week, measured from the real publication dates. `None` while
    #: the journal has no rhythm yet — see `policies.weekly_rate`.
    weekly_rate: Decimal | None = None


# ------------------------------------------------------------------- writing


class CreatePost(BaseModel):
    """A new report.

    No `number`: the series numbers itself. A client that chose its own would
    eventually choose one that already exists, and the failure would land on
    whoever pressed save rather than on whoever wrote the client.
    """

    title: str = Field(min_length=1, max_length=300)
    section: Section
    lede: str = Field(default="", max_length=600)
    excerpt: str = Field(default="", max_length=600)
    author: str = Field(default="", max_length=160)
    data_note: str = Field(default="", max_length=200)
    blocks: list[Block] = Field(default_factory=list)
    #: Drafts are the default. Publishing is a separate decision, and making it the
    #: default would mean a half-written report reaches the storefront on save.
    is_published: bool = False


class UpdatePost(BaseModel):
    """A partial edit. Anything omitted is left alone."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    section: Section | None = None
    lede: str | None = Field(default=None, max_length=600)
    excerpt: str | None = Field(default=None, max_length=600)
    author: str | None = Field(default=None, max_length=160)
    data_note: str | None = Field(default=None, max_length=200)
    blocks: list[Block] | None = None
    is_published: bool | None = None
