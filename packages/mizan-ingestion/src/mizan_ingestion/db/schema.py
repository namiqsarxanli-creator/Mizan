"""Temporal schema (ADR-0003).

legal_documents      one row per act (e.g. the Labour Code)
document_versions    one row per consolidated text, valid on [valid_from, valid_to)
provisions           the structure of one version: key, kind, number, position
provision_texts      content-addressed text, shared by every version where it is unchanged
endnotes             amendment history as printed in each version

"Article 69 as of 2020-01-01" is a join on the version whose interval contains the date.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Deterministic constraint names: Alembic migrations stay reviewable and reversible.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class LegalDocument(Base):
    __tablename__ = "legal_documents"
    __table_args__ = (UniqueConstraint("source", "source_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str | None] = mapped_column(String(64))
    accepted_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    versions: Mapped[list[DocumentVersion]] = relationship(back_populates="document")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "raw_sha256"),
        Index("ix_document_versions_validity", "document_id", "valid_from", "valid_to"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("legal_documents.id", ondelete="CASCADE"))
    # e-qanun's id for historical versions; NULL for the current consolidated text.
    source_version_id: Mapped[int | None] = mapped_column(BigInteger)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[date] = mapped_column(Date)
    # True when the source does not state the date and we derived it (see ADR-0003).
    valid_from_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text)
    raw_sha256: Mapped[str] = mapped_column(String(64))
    encoding: Mapped[str] = mapped_column(String(32))
    parser_version: Mapped[int] = mapped_column(SmallInteger)
    parse_warnings: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    document: Mapped[LegalDocument] = relationship(back_populates="versions")


class ProvisionText(Base):
    __tablename__ = "provision_texts"

    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    heading: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)


class Provision(Base):
    __tablename__ = "provisions"
    __table_args__ = (
        UniqueConstraint("version_id", "key"),
        Index("ix_provisions_key", "key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(255))
    parent_key: Mapped[str | None] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))
    number: Mapped[str | None] = mapped_column(String(32))
    ordinal: Mapped[int] = mapped_column(Integer)
    depth: Mapped[int] = mapped_column(SmallInteger)
    content_hash: Mapped[str] = mapped_column(ForeignKey("provision_texts.content_hash"))
    endnote_refs: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)

    content: Mapped[ProvisionText] = relationship(lazy="joined")


class Endnote(Base):
    __tablename__ = "endnotes"
    __table_args__ = (UniqueConstraint("version_id", "number"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    act_number: Mapped[str | None] = mapped_column(String(64), index=True)
    act_adopted_on: Mapped[date | None] = mapped_column(Date)
