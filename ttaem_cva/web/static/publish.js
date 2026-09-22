'use strict';
const destination=document.getElementById('destination');
const help=document.getElementById('destinationHelp');
const requestLink=document.getElementById('ttaemRequestLink');
const button=document.getElementById('exportButton');
const status=document.getElementById('exportStatus');

function updateDestination() {
  const ttaem=destination.value==='ttaem';
  help.textContent=ttaem
    ? '검토한 저장본의 report.json 한 개를 준비합니다. 준비가 끝나면 공식 양식에서 이 파일을 첨부하세요.'
    : '내 사이트에 올릴 공개 파일 여섯 개를 준비합니다.';
  button.textContent=ttaem ? 'report.json 준비' : '이 저장본으로 파일 준비';
  status.textContent='';
  requestLink.hidden=true;
}

destination.addEventListener('change',updateDestination);
updateDestination();

button.addEventListener('click', async event => {
  const button=event.currentTarget;
  button.disabled=true;requestLink.hidden=true;
  try {
    const selected=destination.value;
    const response=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json','X-CVA-Local':'1'},body:JSON.stringify({base:button.dataset.base,revision:button.dataset.revision,destination:selected})});
    const result=await response.json();if(!response.ok)throw new Error(result.error);
    status.textContent=result.message+' '+result.path;
    if(selected==='ttaem' && result.submission_url){requestLink.href=result.submission_url;requestLink.hidden=false;}
  } catch(error){status.textContent=error.message;} finally{button.disabled=false;}
});
