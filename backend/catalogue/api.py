"""
Catalogue API — designed for consumption by Astro Content Layer loaders.

Each endpoint returns a JSON array of entries where every entry has:
  - `id`     (string) — unique identifier, required by Astro content collections
  - `digest` (string) — changes when the data changes; Astro uses this to skip
                         unchanged entries during incremental builds

All endpoints support `?updated_after=<ISO datetime>` so that an Astro object
loader can store the last sync time in its metadata store, then only fetch
entries that changed since the previous build.
"""

import hashlib
import json
from datetime import datetime

from django.db.models import Max, Prefetch, Q
from ninja import NinjaAPI, Query, Schema

from catalogue.models import Comedian, Segment, SegmentComedian, Video

api = NinjaAPI(
    title="Little Jokebook Catalogue",
    version="1.0.0",
    urls_namespace="catalogue_api",
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TranscriptLineSchema(Schema):
    start: float
    end: float
    text: str


class SegmentEntrySchema(Schema):
    id: str
    digest: str
    video_id: int
    video_url: str
    start_time: int
    end_time: int
    segment_type: str
    description: str
    summary: str
    transcript: list[TranscriptLineSchema] | list
    comedians: list[str]
    updated_at: datetime


class VideoEntrySchema(Schema):
    id: str
    digest: str
    url: str
    segments: list[SegmentEntrySchema]
    updated_at: datetime


class ComedianEntrySchema(Schema):
    id: str
    digest: str
    name: str
    segments: list[SegmentEntrySchema]
    updated_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_digest(updated_at: datetime, *extras: str) -> str:
    """Non-cryptographic content digest from timestamp + optional extras."""
    raw = updated_at.isoformat() + "|" + "|".join(extras)
    return hashlib.md5(raw.encode()).hexdigest()


def _segment_entry(seg: Segment, video_url: str | None = None) -> SegmentEntrySchema:
    comedian_names = sorted(sc.comedian.name for sc in seg.segment_comedians.all())
    return SegmentEntrySchema(
        id=f"segment-{seg.id}",
        digest=_make_digest(seg.updated_at, *comedian_names),
        video_id=seg.video_id,
        video_url=video_url or seg.video.url,
        start_time=seg.start_time,
        end_time=seg.end_time,
        segment_type=seg.segment_type,
        description=seg.description,
        summary=seg.summary,
        transcript=seg.transcript,
        comedians=comedian_names,
        updated_at=seg.updated_at,
    )


def _prefetched_segments_qs() -> Prefetch:
    """Reusable prefetch for segments with their comedians."""
    return Prefetch(
        "segments",
        queryset=Segment.objects.prefetch_related(
            Prefetch(
                "segment_comedians",
                queryset=SegmentComedian.objects.select_related("comedian"),
            )
        ),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@api.get("/segments", response=list[SegmentEntrySchema], tags=["content-layer"])
def list_segments(request, updated_after: datetime = Query(None)):
    """
    Flat list of all segments with denormalized video URL and comedian names.
    Each segment is a single Astro content entry — ideal when each segment
    is its own page.
    """
    qs = Segment.objects.select_related("video").prefetch_related(
        Prefetch(
            "segment_comedians",
            queryset=SegmentComedian.objects.select_related("comedian"),
        )
    )
    if updated_after:
        qs = qs.filter(updated_at__gte=updated_after)

    return [_segment_entry(s) for s in qs]


@api.get("/videos", response=list[VideoEntrySchema], tags=["content-layer"])
def list_videos(request, updated_after: datetime = Query(None)):
    """
    All videos with their segments inlined.

    A video is considered updated if the video itself *or* any of its
    segments changed after `updated_after`.
    """
    qs = Video.objects.prefetch_related(_prefetched_segments_qs())

    if updated_after:
        qs = qs.annotate(
            latest_segment_change=Max("segments__updated_at"),
        ).filter(
            Q(updated_at__gte=updated_after)
            | Q(latest_segment_change__gte=updated_after)
        ).distinct()

    items = []
    for v in qs:
        segs = [_segment_entry(s, video_url=v.url) for s in v.segments.all()]
        # Digest covers the video and the freshest segment
        latest = max(
            [v.updated_at] + [s.updated_at for s in v.segments.all()]
        )
        items.append(
            VideoEntrySchema(
                id=f"video-{v.id}",
                digest=_make_digest(latest),
                url=v.url,
                updated_at=v.updated_at,
                segments=segs,
            )
        )
    return items


@api.get("/comedians", response=list[ComedianEntrySchema], tags=["content-layer"])
def list_comedians(request, updated_after: datetime = Query(None)):
    """
    All comedians with their segments inlined.

    A comedian is considered updated if the comedian record *or* any linked
    segment changed after `updated_after`.
    """
    qs = Comedian.objects.prefetch_related(
        Prefetch(
            "segment_comedians",
            queryset=SegmentComedian.objects.select_related(
                "segment", "segment__video",
            ).prefetch_related(
                Prefetch(
                    "segment__segment_comedians",
                    queryset=SegmentComedian.objects.select_related("comedian"),
                )
            ),
        ),
    )

    if updated_after:
        qs = qs.annotate(
            latest_segment_change=Max("segment_comedians__segment__updated_at"),
        ).filter(
            Q(updated_at__gte=updated_after)
            | Q(latest_segment_change__gte=updated_after)
        ).distinct()

    items = []
    for c in qs:
        segs = [
            _segment_entry(sc.segment, video_url=sc.segment.video.url)
            for sc in c.segment_comedians.all()
        ]
        latest = max(
            [c.updated_at] + [sc.segment.updated_at for sc in c.segment_comedians.all()]
        )
        items.append(
            ComedianEntrySchema(
                id=f"comedian-{c.id}",
                digest=_make_digest(latest),
                name=c.name,
                updated_at=c.updated_at,
                segments=segs,
            )
        )
    return items
