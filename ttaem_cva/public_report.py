"""Explicit reader-only projection and shared, escaped HTML renderer."""
from __future__ import annotations
import hashlib
import html
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit
from .outline.check import validate_structured_manager_outline
from .outline.schema import hms_to_seconds, seconds_to_hms
from .local_files import plain_path

ROOT = Path(__file__).resolve().parents[1]


class _PublicHTMLContract(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.body = {}
        self.primary_links = []
        self.identity = []
        self.iframes = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'body':
            self.body = values
        if tag == 'a' and 'data-editor-entry-primary' in values:
            self.primary_links.append(values)
        if tag == 'div' and values.get('id') == 'summaryPipelineIdentity':
            self.identity.append(values)
        if tag == 'iframe':
            self.iframes += 1

def clean(value, maximum=5000):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("Invalid report text")
    return value

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def clip_thumbnail(value):
    """Permit only public CHZZK clip image URLs, without credentials or queries."""
    if not isinstance(value, str) or len(value) > 2000:
        return ''
    try:
        url = urlsplit(value)
    except ValueError:
        return ''
    if (url.scheme != 'https' or url.netloc != 'video-phinf.pstatic.net'
            or url.query or url.fragment
            or not re.fullmatch(r'/[A-Za-z0-9_./%-]+\.(?:jpg|jpeg|png|webp)', url.path, re.I)):
        return ''
    return value

def project(result):
    metadata = result["metadata"]
    outline = result["outline"]
    duration = metadata["duration"]
    validate_structured_manager_outline(outline, duration_sec=duration)
    number = str(metadata["video_no"])
    if not re.fullmatch(r"\d{1,16}", number) or not isinstance(duration, int) or not 0 < duration < 172800:
        raise ValueError("Invalid VOD metadata")
    ranges = [{k: clean(row.get(k, "")) for k in ("id", "level", "parent_id", "start", "end", "title", "content")} for row in outline["ranges"]]
    points = [{k: clean(row.get(k, "")) for k in ("id", "timestamp", "title", "content")} for row in outline["points"]]
    point_ids = {p["id"] for p in points}
    stories = [{"point_ref": clean(row.get("point_ref", "")), "title": clean(row.get("title", "")),
                "description": clean(row.get("why_notable", ""))} for row in result.get("stories", []) if row.get("point_ref") in point_ids]
    highlights = []
    for row in result.get("highlights", {}).get("highlights", []):
        spans = []
        for span in row.get("source_spans", []):
            start, end = span.get("start_sec"), span.get("end_sec")
            if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in (start,end)) or not 0 <= start < end <= duration:
                raise ValueError("Highlight outside source duration")
            if spans and start < spans[-1]["end_sec"]:
                raise ValueError("Overlapping highlight spans")
            spans.append({"start_sec": start, "end_sec": end})
        if not spans:
            raise ValueError("Highlight has no source spans")
        refs = row.get("point_refs", [])
        if not isinstance(refs, list) or not refs or not set(refs) <= point_ids:
            raise ValueError("Highlight references missing Point")
        highlights.append({"id": clean(row["highlight_id"], 120), "title": clean(row.get("title", "")),
                           "summary": clean(row.get("story_summary", "")), "point_refs": list(refs), "source_spans": spans})
    if any(len(v) > 1000 for v in (ranges, points, stories, highlights)):
        raise ValueError("Report contains too many items")
    buckets=[]
    for row in result.get('chat_projection', {}).get('buckets', []):
        start, count = row.get('start_sec'), row.get('count')
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (start,count)) or not 0 <= start <= duration or not 0 <= count <= 1000000:
            raise ValueError('Invalid aggregate chat bucket')
        if buckets and start <= buckets[-1]['start_sec']:
            raise ValueError('Chat buckets must be ordered')
        buckets.append({'start_sec':start,'end_sec':min(duration,start+10),'count':count})
    if len(buckets)>17280:raise ValueError('Too many aggregate buckets')
    chat_status=result.get('chat_projection',{}).get('status','unavailable')
    if chat_status not in ('complete','partial','unavailable'):raise ValueError('Invalid chat state')
    clips=[]
    for row in result.get('viewer_clips',[]):
        if row.get('offset_status') != 'ok' or str(row.get('video_no')) != number:
            continue
        uid=row.get('clip_uid');second=row.get('offset_sec')
        if not isinstance(uid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',uid):
            raise ValueError('Invalid viewer clip ID')
        if isinstance(second,bool) or not isinstance(second,(float,int)) or not math.isfinite(second) or not 0 <= second <= duration:
            raise ValueError('Viewer clip outside source duration')
        clip={'clip_uid':uid,'title':clean(row.get('title',''),500),'offset_sec':second}
        clip['thumbnail_url'] = clip_thumbnail(row.get('thumbnail_url'))
        for key in ('like_count','play_count'):
            value=row.get(key)
            if value is not None and (isinstance(value,bool) or not isinstance(value,int) or not 0 <= value <= 10**12):
                raise ValueError('Invalid clip engagement')
            clip[key]=value
        clips.append(clip)
    if len(clips)>30:raise ValueError('Too many viewer clips')
    return {"schema": "ttaem.public-report.v1", "video_no": number, "title": clean(metadata["title"], 500),
            "channel_name": clean(metadata["channel_name"], 200), "duration": duration,
            "summary": clean(outline.get("read_only", {}).get("short_summary", "")),
            "uncertainty": clean(outline.get("read_only", {}).get("unknown_or_missing", "")),
            "ranges": ranges, "points": points, "stories": stories, "highlights": highlights,
            "published_at":clean(metadata.get('publish_date',''),100), "chat_status":chat_status,
            "chat_count":sum(b['count'] for b in buckets) if chat_status != 'unavailable' else None,
            "chat_buckets":buckets, "viewer_clips":clips}

def render(data):
    from .report_template import render_report
    return render_report(data)


def validate_bundle_files(files, *, video_no):
    """Fail closed when the reviewed static page loses its public UI contract."""
    required = {'index.html', 'report.json', 'report.css', 'report.js',
                'chart.umd.min.js', 'TTaem_CVA_logo.svg'}
    if set(files) != required:
        raise ValueError('Public bundle file set changed')
    try:
        page = files['index.html'].decode('utf-8', errors='strict')
        script = files['report.js'].decode('utf-8', errors='strict')
        style = files['report.css'].decode('utf-8', errors='strict')
        report = json.loads(files['report.json'].decode('utf-8', errors='strict'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Public bundle is not valid UTF-8/JSON') from exc
    number = str(video_no)
    if report.get('schema') != 'ttaem.public-report.v1' or str(report.get('video_no')) != number:
        raise ValueError('Public report JSON does not match the reviewed VOD')

    parsed = _PublicHTMLContract()
    parsed.feed(page)
    if parsed.body.get('data-default-editor-mode') != 'companion':
        raise ValueError('Public report must default to video companion mode')
    if len(parsed.identity) != 1 or page.count('>요약 방식: Loki 2.9.4</div>') != 1:
        raise ValueError('Public report is missing the Loki 2.9.4 identity')
    identity = parsed.identity[0]
    if identity.get('data-summary-mechanism') != 'Loki' or identity.get('data-summary-mechanism-version') != '2.9.4':
        raise ValueError('Public report has an invalid summary identity')
    if len(parsed.primary_links) != 1:
        raise ValueError('Public report must contain one video connection link')
    link = parsed.primary_links[0]
    expected_url = f'https://chzzk.naver.com/video/{number}'
    rel = set((link.get('rel') or '').split())
    if (link.get('href') != expected_url or link.get('target') != 'ttaem-chzzk-companion'
            or 'opener' not in rel or {'noopener', 'noreferrer'} & rel):
        raise ValueError('Public report video connection link is unsafe or non-reusable')
    if parsed.iframes or 'data-editor-split=' in page or 'data-report-editor=' in page:
        raise ValueError('Legacy embedded video mode is not publishable')
    if 'TTaem_CVA_logo.svg' not in page or 'href="https://ttaem.com"' not in page:
        raise ValueError('Public report footer branding is missing')

    script_requirements = (
        "CHZZK_COMPANION_WINDOW_NAME = 'ttaem-chzzk-companion'",
        'function initEditorCompanionMode()',
        'function navigateChzzkCompanion(value)',
        'Ctrl+Alt+왼쪽 두 번 클릭',
        'window.open(url, `${CHZZK_COMPANION_WINDOW_NAME}-${reportScope}`)',
    )
    style_requirements = ('.summary-pipeline-identity', '.editor-companion-guide',
                          '.editor-companion-workspace-guide', '.editor-companion-status')
    if any(value not in script for value in script_requirements):
        raise ValueError('Public report video connection script is incomplete')
    if any(value not in style for value in style_requirements):
        raise ValueError('Public report video connection style is incomplete')
    forbidden = ('initEditorSplitMode', 'data-editor-chzzk-frame', 'editor-split-shell')
    if any(value in page or value in script or value in style for value in forbidden):
        raise ValueError('Legacy embedded video code remains in the public bundle')


def bundle_files(data):
    files = {'index.html': render(data).encode(),
             'report.json': json.dumps(data, ensure_ascii=False, indent=2).encode(),
             'report.css': (ROOT/'ttaem_cva/web/static/report.css').read_bytes(),
             'report.js': (ROOT/'ttaem_cva/web/static/report.js').read_bytes(),
             'chart.umd.min.js': (ROOT/'ttaem_cva/web/static/chart.umd.min.js').read_bytes(),
             'TTaem_CVA_logo.svg': (ROOT/'assets/branding/TTaem_CVA_logo.svg').read_bytes()}
    validate_bundle_files(files, video_no=data['video_no'])
    return files


def bundle_fingerprint(data):
    return digest({name:hashlib.sha256(content).hexdigest() for name,content in bundle_files(data).items()})


def write_bundle(data, destination):
    """Only reviewed public data, original viewer assets and branding are exported."""
    destination = plain_path(destination)
    files = bundle_files(data)
    if destination.exists():
        if {p.name for p in destination.iterdir()} != set(files):
            raise ValueError('Existing export contains unexpected files; use a new destination')
        for name, content in files.items():
            if plain_path(destination/name).read_bytes() != content:
                raise ValueError('Existing export changed; use a new destination')
    else:
        destination.mkdir(parents=True, exist_ok=False)
        for name, content in files.items():
            plain_path(destination/name).write_bytes(content)
    return {name:hashlib.sha256(content).hexdigest() for name,content in files.items()}
