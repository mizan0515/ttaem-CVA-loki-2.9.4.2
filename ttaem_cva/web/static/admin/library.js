const list = document.getElementById('list'), search = document.getElementById('search');
const meta = document.getElementById('meta'), channelBar = document.getElementById('streamerBar');
let reports = [], channel = '';
const esc = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function render() {
  const query = search.value.trim().toLocaleLowerCase();
  const shown = reports.filter(r => (!channel || r.channel_name === channel) && `${r.title} ${r.channel_name} ${r.video_no}`.toLocaleLowerCase().includes(query));
  list.replaceChildren();
  for (const r of shown) {
    const anchor = document.createElement('a'); anchor.className = 'card';
    anchor.href = '/report-workspace?base=' + encodeURIComponent(r.video_no);
    anchor.innerHTML = `<div class="title">${esc(r.title)}</div><div class="row"><span>${esc(r.video_no)}</span><span class="badge">${esc(r.channel_name)}</span><span>${esc(r.published_at)}</span></div><div class="row status-row"><span class="badge">로컬 저장본</span><span>${r.point_count}개 주요 순간 · ${r.highlight_count}개 Highlight</span></div><span class="arrow" aria-hidden="true">→</span>`;
    list.append(anchor);
  }
  meta.textContent = `${reports.length}개 중 ${shown.length}개 · 최신순`;
  if (!shown.length) {
    const empty = document.createElement('p');
    empty.textContent = reports.length ? '조건에 맞는 요약이 없습니다.' : '완성된 요약이 없습니다. 이 폴더의 Codex에 VOD 링크로 요약 생성을 요청하세요.';
    list.append(empty);
  }
  list.setAttribute('aria-busy', 'false');
}
async function load() {
  document.getElementById('error').textContent = '';
  try {
    const response = await fetch('/api/reports');
    if (!response.ok) throw new Error('목록을 불러오지 못했습니다.');
    reports = await response.json(); channelBar.replaceChildren();
    for (const name of ['', ...new Set(reports.map(r => r.channel_name))]) {
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = name || '전체'; button.className = 'chip' + (channel === name ? ' active' : '');
      button.setAttribute('aria-pressed', String(channel === name));
      button.addEventListener('click', () => {
        channel = name;
        for (const b of channelBar.children) {
          b.setAttribute('aria-pressed', String(b === button)); b.classList.toggle('active', b === button);
        }
        render();
      });
      channelBar.append(button);
    }
    render();
  } catch (error) {
    document.getElementById('error').textContent = error.message;
    list.setAttribute('aria-busy', 'false');
  }
}
search.addEventListener('input', render);
document.getElementById('reloadBtn').addEventListener('click', load);
document.addEventListener('keydown', e => {
  if (e.key === '/' && !['INPUT','TEXTAREA'].includes(e.target.tagName) && !e.target.isContentEditable) {
    e.preventDefault(); search.focus();
  }
});
load();
