"""Local adapter for baseline community evidence. No accounts or proxy API."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from .models import CommunityPost
from .acquire import fetch, save_json
from .local_files import plain_path, read_json
from .community_parser import (
    _should_skip_fmkorea, _build_search_url, _parse_search_results, _parse_iso,
    _select_top_diverse, filter_posts_by_broadcast_time,
)


def community_evidence(run, vod, *, refresh=False):
    target = plain_path(run / 'community.json')
    manual = plain_path(run / 'community.manual.json')
    source = 'public_http'
    cached = read_json(target) if target.is_file() else None
    if cached and cached.get('video_no') != str(vod.video_no):
        raise ValueError('Community cache belongs to another VOD')
    if manual.is_file():
        data = read_json(manual)
        if data.get('video_no') != str(vod.video_no):
            raise ValueError('Community input belongs to another VOD')
        rows = data.get('posts', [])
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError('Community manual input exceeds 100 posts')
        posts = [CommunityPost(**{k:row[k] for k in CommunityPost.__dataclass_fields__
                                  if k in row and k != 'author'}) for row in rows]
        status, source = 'manual', 'user_prepared'
    elif cached and not refresh and (
        cached.get('status') in ('complete', 'age_skipped')
        or cached.get('retry_after', 0) > time.time()
    ):
        return [CommunityPost(**row) for row in cached.get('posts', [])], cached
    else:
        skip, _ = _should_skip_fmkorea(vod.publish_date, 48)
        posts, candidates = [], []
        status = 'age_skipped' if skip else 'complete'
        cooldown = plain_path(run / 'community-cooldown.json')
        blocked_until = read_json(cooldown).get('until', 0) if cooldown.is_file() else 0
        if not skip and blocked_until > time.time():
            status = 'blocked_cooldown'
        elif not skip and vod.channel_name:
            for page in range(1, 4):
                try:
                    text = fetch(_build_search_url(vod.channel_name, page),
                                 headers={'Referer':'https://www.fmkorea.com/'}).decode('utf-8')
                    if re.search(r'captcha|access denied|verify you are human', text, re.I):
                        status = 'blocked'
                        save_json(cooldown, {'until':time.time() + 3*3600})
                        break
                    batch = _parse_search_results(text)
                    if not batch:
                        if 'bd_lst' not in text:
                            status = 'unrecognized_page'
                        break
                    candidates.extend(batch)
                except HTTPError as exc:
                    status = 'blocked' if exc.code in (403, 429, 430) else 'fetch_failed'
                    if status == 'blocked':
                        save_json(cooldown, {'until':time.time() + 3*3600})
                    break
                except (OSError, ValueError):
                    status = 'fetch_failed'
                    break
                if page < 3:
                    time.sleep(8)
            start = _parse_iso(vod.publish_date)
            unique = {r['url']:r for r in candidates
                      if urlparse(r['url']).hostname in ('www.fmkorea.com','fmkorea.com')}
            rows = [r for r in unique.values() if not start or not r.get('timestamp_parsed')
                    or start-timedelta(hours=24) <= r['timestamp_parsed'] <= start+timedelta(hours=24)]
            for row in _select_top_diverse(rows, 20, start):
                posts.append(CommunityPost(title=row['title'], url=row['url'], body_preview=row['body_preview'],
                    timestamp=row['timestamp'], publish_date=row['timestamp_parsed'].isoformat() if row.get('timestamp_parsed') else '',
                    views=row['views'], comments=row['comments'], likes=row['likes']))
            if status != 'complete' and posts:
                status = 'partial_' + status
        elif not skip:
            status = 'channel_unavailable'
    for post in posts:
        if not isinstance(post.title, str) or not isinstance(post.body_preview, str):
            raise ValueError('Invalid community text')
        if len(post.title) > 1000 or len(post.body_preview) > 8000:
            raise ValueError('Community text too long')
        if urlparse(post.url).scheme != 'https' or urlparse(post.url).hostname not in ('www.fmkorea.com','fmkorea.com'):
            raise ValueError('Community source must be a public FMKorea HTTPS URL')
        post.author = ''
    posts = filter_posts_by_broadcast_time(posts, vod.publish_date, vod.duration)
    data = {'video_no':str(vod.video_no), 'status':status, 'source':source,
            'posts':[asdict(p) for p in posts], 'fetched_at':datetime.now(timezone.utc).isoformat()}
    if status not in ('complete', 'age_skipped', 'manual'):
        data['retry_after'] = time.time() + 180
    save_json(target, data)
    return posts, data
