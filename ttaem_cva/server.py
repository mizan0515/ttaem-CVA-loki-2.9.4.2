"""Loopback-only review server. No credentials, global files or deployment APIs."""
from __future__ import annotations
import copy
from datetime import datetime, timezone
import html
from pathlib import Path
import re
import threading
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
from flask import Flask, abort, jsonify, request, send_file
from .acquire import fetch, fetch_json, save_json
from .public_report import ROOT, digest, project, render, write_bundle, bundle_fingerprint
from .outline.render import project_manager_outline_markdown
from .local_files import plain_path, read_json
from .admin_views import NAV, dashboard, library

TTAEM_REQUEST_URL = 'https://ttaem.com/community/'
TTAEM_POLICY_URL = 'https://ttaem.com/community/policy/'


def create_app(root=ROOT):
    root = Path(root).resolve()
    app = Flask(__name__, static_folder=str(ROOT/'ttaem_cva/web/static'))
    app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
    lock = threading.RLock()

    def run_path(base):
        if not isinstance(base, str) or not re.fullmatch(r'\d{1,16}', base):
            abort(400, 'Invalid report ID')
        path = root/'runs'/base
        for part in (root, root/'runs', path):
            if part.exists() and (part.is_symlink() or getattr(part.stat(), 'st_file_attributes', 0) & 0x400):
                abort(400, 'Linked data paths are not supported')
        return path

    def load(base):
        path = run_path(base)
        current = path/'current.json'
        candidate = current if current.exists() else path/'result-private.json'
        if not candidate.is_file():
            abort(404, '아직 생성이 완료되지 않았습니다.')
        return read_json(candidate)

    def report(base):
        result = load(base)
        data = project(result)
        outline = copy.deepcopy(result['outline'])
        outline['duration_sec'] = data['duration']
        story_by_point = {s['point_ref']: s for s in result.get('stories', [])}
        stories = []
        for point in outline['points']:
            story = story_by_point.get(point['id'], {})
            point['story_ref'] = story.get('story_packet_id', 'manual-'+point['id'])
            stories.append({'story_ref':point['story_ref'], 'title':story.get('title',point['title']),
                            'why_notable':story.get('why_notable','')})
        highlights = [{'highlight_id':h['id'], 'title':h['title'], 'story_summary':h['summary'],
                       'point_refs':h['point_refs'], 'source_spans':h['source_spans']} for h in data['highlights']]
        history = []
        directory = plain_path(run_path(base)/'revisions')
        if directory.exists():
            for p in sorted(directory.glob('*.json'), reverse=True):
                plain_path(p)
                if re.fullmatch(r'[a-f0-9]{64}', p.stem):
                    history.append({'revision_id':p.stem, 'created_at':datetime.fromtimestamp(p.stat().st_mtime,timezone.utc).isoformat()})
        generated=read_json(run_path(base)/'result-private.json')
        generated_revision=digest(generated)
        current_path=run_path(base)/'current.json'
        return {'base':base, 'video_no':base, 'title':data['title'], 'kind':'vod',
                'generated_revision':generated_revision,
                'generated_differs':current_path.is_file() and generated_revision != digest(result),
                'revision':digest(result), 'structured_outline':outline,
                'manager_markdown':project_manager_outline_markdown(result['outline'],duration_sec=data['duration']),
                'manager_outline_revisions':history,
                'qg1_review_packet':{'broadcast_map':outline,'stories':stories,'editorial_highlights':highlights}}

    @app.before_request
    def local_only():
        if request.remote_addr not in ('127.0.0.1','::1'):
            abort(403)
        if request.host.split(':')[0] not in ('127.0.0.1','localhost'):
            abort(403)
        if request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('X-CVA-Local') != '1' or not request.is_json:
                abort(403)
            if not isinstance(request.get_json(), dict):
                abort(400, 'Expected a JSON object')
            origin = request.headers.get('Origin')
            if origin and origin != 'http://'+request.host:
                abort(403)
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            abort(403)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cache-Control']='no-store'
        frame_source = "'self' https://chzzk.naver.com" if request.path.startswith('/preview/') else "'self'"
        response.headers['Content-Security-Policy']=f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https://video-phinf.pstatic.net; media-src 'self' https: blob:; connect-src 'self' https://*.naver.com https://*.pstatic.net https://*.navercdn.com; worker-src 'self' blob:; frame-src {frame_source}; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
        return response

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def http_error(error):
        return jsonify(error=str(error.description)), error.code

    @app.errorhandler(ValueError)
    def value_error(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(KeyError)
    @app.errorhandler(TypeError)
    def malformed_input(error):
        return jsonify(error='Invalid or incomplete report data'), 400

    def catalog():
        rows=[]
        directory=plain_path(root/'runs')
        for p in sorted(directory.iterdir(),reverse=True) if directory.exists() else []:
            if re.fullmatch(r'\d{1,16}',p.name) and (p/'result-private.json').is_file():
                data=project(load(p.name))
                rows.append({key:data[key] for key in ('video_no','title','channel_name','published_at')} | {'point_count':len(data['points']),'highlight_count':len(data['highlights'])})
        return sorted(rows,key=lambda r:(r['published_at'],int(r['video_no'])),reverse=True)

    @app.get('/api/reports')
    def list_reports():
        return jsonify(catalog())

    @app.get('/')
    @app.get('/reports')
    def index():
        base=request.args.get('publish')
        if base:
            data=project(load(base));revision=digest(load(base))
            body=f'''<header class="publish-header">
              <p class="eyebrow">PUBLICATION</p>
              <h1>게시 준비</h1>
              <p class="publish-lead">검토한 저장본으로 공개용 파일을 만듭니다. 파일 준비만으로 외부 사이트에 게시되지는 않습니다.</p>
            </header>
            <section class="publish-summary" aria-labelledby="publishReportTitle">
              <div><span class="publish-kicker">선택한 리포트</span><h2 id="publishReportTitle">{html.escape(data["title"])}</h2></div>
              <a class="button ghost" href="/preview/{base}/index.html" target="_blank" rel="noopener">저장본 미리보기 ↗</a>
            </section>
            <section class="publish-card" aria-labelledby="publishDestinationTitle">
              <div class="publish-step"><span aria-hidden="true">1</span><div><h2 id="publishDestinationTitle">게시 목적지 선택</h2><p>목적지에 맞는 공개 파일만 준비합니다.</p></div></div>
              <label class="publish-field" for="destination"><span>목적지</span><select id="destination"><option value="self">내 사이트에 올릴 정적 파일</option><option value="ttaem">ttaem.com 게시 요청용 report.json</option></select></label>
              <p id="destinationHelp" class="publish-help">내 사이트에 올릴 공개 파일 여섯 개를 준비합니다.</p>
            </section>
            <section class="publish-card" aria-labelledby="publishPrepareTitle">
              <div class="publish-step"><span aria-hidden="true">2</span><div><h2 id="publishPrepareTitle">파일 준비</h2><p>현재 저장본과 revision이 일치할 때만 파일을 만듭니다.</p></div></div>
              <div class="publish-actions"><button class="button primary" id="exportButton" data-base="{base}" data-revision="{revision}">이 저장본으로 파일 준비</button><a class="policy-link" href="{TTAEM_POLICY_URL}" target="_blank" rel="noopener">이용·게시 정책 보기 ↗</a></div>
              <p id="exportStatus" class="publish-status" role="status" aria-live="polite"></p>
              <div class="publish-next"><div><strong>파일을 준비한 다음</strong><span>ttaem.com을 선택한 경우 공식 양식에서 생성된 report.json을 첨부합니다.</span></div><a id="ttaemRequestLink" class="button primary" href="{TTAEM_REQUEST_URL}" target="_blank" rel="noopener" hidden>ttaem.com 게시 요청 양식 열기 ↗</a></div>
            </section>
            <script src="/static/publish.js"></script>'''
        else:
            return dashboard(catalog()) if request.path=='/' else library()
        return '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TTaem CVA · 게시 준비</title><link rel="stylesheet" href="/static/admin/report_workspace.css"><link rel="stylesheet" href="/static/admin/app_shell.css"><body>'+NAV+'<main class="workspace-shell publish-shell">'+body+'</main></body></html>'

    @app.get('/report-workspace')
    def workspace():
        return (ROOT/'ttaem_cva/web/templates/report_workspace.html').read_text(encoding='utf-8').replace('{{NAV}}',NAV)

    @app.get('/api/report')
    def get_report():
        return jsonify(report(request.args.get('base')))

    @app.post('/api/manager-outline/save')
    def save():
        body=request.get_json();base=body.get('base');path=run_path(base)
        with lock:
            old=load(base)
            if body.get('expected_revision') != digest(old):
                abort(409,'다른 저장본이 생겼습니다. 새로고침 후 다시 검토하세요.')
            new=copy.deepcopy(old);new['outline']=body['outline']
            packet=body.get('editorial_review_packet') or {}
            point_by_story={p.get('story_ref'):p['id'] for p in new['outline'].get('points',[])}
            new['stories']=[{'story_packet_id':s.get('story_ref',''),'point_ref':point_by_story.get(s.get('story_ref'),'') ,'title':s.get('title',''),'why_notable':s.get('why_notable','')} for s in packet.get('stories',[])]
            new['highlights']={'highlights':packet.get('editorial_highlights',[])}
            project(new)
            revisions=plain_path(path/'revisions');revisions.mkdir(exist_ok=True)
            previous=revisions/(digest(old)+'.json')
            if not previous.exists():save_json(previous,old)
            save_json(path/'current.json',new)
        return jsonify(report(base))

    @app.post('/api/manager-outline/restore')
    def restore():
        body=request.get_json();base=body.get('base');path=run_path(base);revision=body.get('revision_id','')
        if not re.fullmatch(r'[a-f0-9]{64}',revision):abort(400)
        with lock:
            old=load(base)
            if body.get('expected_revision') != digest(old):abort(409,'저장본이 변경됐습니다.')
            source=path/'revisions'/(revision+'.json')
            if not source.is_file():abort(404)
            new=read_json(source);project(new)
            save_json(path/'revisions'/(digest(old)+'.json'),old)
            save_json(path/'current.json',new)
        return jsonify(report(base))

    @app.get('/api/saved-html')
    def saved_html():
        base=request.args.get('base');run_path(base)
        from flask import redirect
        return redirect('/preview/'+base+'/index.html')

    @app.post('/api/manager-outline/adopt-generated')
    def adopt_generated():
        body=request.get_json();base=body.get('base');path=run_path(base)
        with lock:
            old=load(base)
            new=read_json(path/'result-private.json')
            if body.get('expected_revision') != digest(old):
                abort(409,'현재 저장본이 변경됐습니다. 다시 확인하세요.')
            if body.get('generated_revision') != digest(new):
                abort(409,'생성본이 변경됐습니다. 새 미리보기를 확인하세요.')
            project(new)
            revisions=plain_path(path/'revisions');revisions.mkdir(exist_ok=True)
            previous=revisions/(digest(old)+'.json')
            if not previous.exists():save_json(previous,old)
            save_json(path/'current.json',new)
        return jsonify(report(base))

    @app.get('/preview/<base>/<name>')
    def preview(base,name):
        if name=='generated.html':
            result=read_json(run_path(base)/'result-private.json')
            if request.args.get('revision') and request.args['revision'] != digest(result):
                abort(409,'생성본이 변경됐습니다. 작업공간을 새로고침하세요.')
            data=project(result)
            return render(data)
        data=project(load(base))
        if name=='index.html':return render(data)
        if name=='evidence.html':
            from .review_signals import render_private_review
            evidence_path=plain_path(run_path(base)/'review-signals.json')
            if not evidence_path.is_file():abort(404,'분석 근거가 아직 생성되지 않았습니다.')
            return render_private_review(data,read_json(evidence_path))
        if name in ('report.css','report.js','chart.umd.min.js'):return send_file(ROOT/'ttaem_cva/web/static'/name)
        if name=='TTaem_CVA_logo.svg':return send_file(ROOT/'assets/branding/TTaem_CVA_logo.svg')
        if name=='report.json':return jsonify(data)
        abort(404)

    def playback_source(base):
        run_path(base)
        metadata=fetch_json(f'https://api.chzzk.naver.com/service/v3/videos/{base}')['content']
        manifest=fetch('https://apis.naver.com/neonplayer/vodplay/v2/playback/'+metadata['videoId']+'?'+urlencode({'key':metadata['inKey']}),headers={'Accept':'application/dash+xml'})
        ns={'m':'urn:mpeg:dash:schema:mpd:2011'};reps=[]
        for rep in ET.fromstring(manifest).findall('.//m:Representation',ns):
            height=int(rep.get('height') or 0)
            source=rep.get('{urn:naver:vod:2020}m3u')
            if height and source and source.startswith('https://'):reps.append((abs(height-720),source))
        if not reps:raise ValueError('원본 사이트에서 확인하세요. 영상 연결 정보가 없습니다.')
        return min(reps)[1]

    @app.get('/api/report-playback')
    def playback():
        base=request.args.get('video_no');run_path(base)
        return jsonify(playback={'original_url':f'https://chzzk.naver.com/video/{base}',
                                'stream_url':'/api/playback-playlist?video_no='+base})

    @app.get('/api/playback-playlist')
    def playlist():
        from .playback import rewrite_playlist
        from flask import Response
        source=playback_source(request.args.get('video_no'))
        return Response(rewrite_playlist(source,fetch(source).decode('utf-8')),mimetype='application/vnd.apple.mpegurl')

    @app.post('/api/export')
    def export():
        body=request.get_json();base=body.get('base');run_path(base)
        with lock:
            result=load(base);revision=digest(result)
            if body.get('revision')!=revision:abort(409,'저장본이 변경됐습니다. 다시 미리보기하세요.')
            destination=body.get('destination')
            if destination not in ('self','ttaem'):abort(400)
            data=project(result);parent=plain_path(root/'exports');parent.mkdir(exist_ok=True)
            target=plain_path(parent/(base+'-'+bundle_fingerprint(data)[:16]))
            write_bundle(data,target)
        name='report.json' if destination=='ttaem' else 'index.html'
        response=dict(path=str(target/name),preview_url='/preview/'+base+'/index.html',published=False,
                      message='공개용 파일을 준비했습니다. 아직 외부에 게시되지 않았습니다.')
        if destination=='ttaem':
            response['submission_url']=TTAEM_REQUEST_URL
        return jsonify(response)

    return app

def serve(port=8767):
    create_app().run(host='127.0.0.1',port=port,debug=False,use_reloader=False)
