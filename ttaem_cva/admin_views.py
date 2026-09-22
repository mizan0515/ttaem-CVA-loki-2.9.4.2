"""Selected current admin layouts; no private operational dashboard APIs."""
import html
from pathlib import Path

WEB = Path(__file__).resolve().parent/'web'
NAV = '<nav class="app-shell-nav" aria-label="관리자 메뉴"><a class="app-shell-brand" href="/"><span aria-hidden="true"></span>TTaem CVA</a><div class="app-shell-groups"><a class="app-shell-tab" href="/">홈</a><a class="app-shell-tab" href="/reports">리포트</a></div></nav>'

def library():
    return (WEB/'templates/home.html').read_text(encoding='utf-8').replace('{{NAV}}', NAV)

def dashboard(reports):
    e = html.escape
    latest = ''.join(f'<a class="latest-row" href="/report-workspace?base={r["video_no"]}"><span><strong>{e(r["title"])}</strong><small>{e(r["channel_name"])} · {e(r["published_at"])}</small></span><span>검토 →</span></a>' for r in reports[:6])
    if not latest:
        latest = '<p class="empty-state">완성된 요약이 없습니다. 이 폴더의 Codex에 VOD 링크로 요약 생성을 요청하세요.</p>'
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>운영 홈 — Report Admin</title><link rel="stylesheet" href="/static/admin/dashboard.css"><link rel="stylesheet" href="/static/admin/app_shell.css"></head><body><main class="page">{NAV}
    <header class="hero" data-testid="manager-home-focus"><div><p class="eyebrow">MANAGER HOME</p><h1>오늘 확인할 일</h1><p class="lead">최근 생성된 요약을 검토하고 게시할 저장본을 준비합니다.</p></div><div class="service-state" data-state="ok"><span aria-hidden="true"></span><strong>로컬 검토</strong></div></header>
    <div class="dashboard-columns"><section class="panel products" aria-labelledby="productsTitle"><div class="section-head compact"><div><p class="section-kicker">제품</p><h2 id="productsTitle">관리할 콘텐츠</h2></div></div><a class="product-row" href="/reports"><span class="product-icon" aria-hidden="true">📋</span><span><strong>리포트</strong><small>방송 요약을 찾고 검토·편집합니다.</small></span><span class="product-count">{len(reports)}개</span></a></section>
    <section class="panel latest" aria-labelledby="latestTitle"><div class="section-head compact"><div><p class="section-kicker">최근 결과</p><h2 id="latestTitle">최근 생성된 요약</h2></div><a class="text-link" href="/reports">전체 보기</a></div><div class="latest-list">{latest}</div></section></div>
    </main></body></html>'''
