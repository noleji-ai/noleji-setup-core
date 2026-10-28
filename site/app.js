
const body=document.body,kind=body.dataset.kind;
const out=document.querySelector('#example-output'),msg=document.querySelector('#demo-message');
let stage=0,revision=1,edited=false;
function draw(note){
 const record=kind==='harness'?{example:true,checkpoint:['initialized','planned','checks-recorded','review-ready'][stage],completedSteps:stage,liveModelCalled:false,releaseVerified:false}:{example:true,managedRevision:revision,userEdited:edited,canUpdate:!edited,deviceFilesChanged:false};
 out.textContent=JSON.stringify(record,null,2);msg.textContent=note;return record;
}
draw(kind==='harness'?'초기 상태입니다. 다음 체크포인트를 확인하세요.':'변경 전: 관리 버전 1입니다.');
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>{
 const action=button.dataset.action;
 if(action==='reset'){stage=0;revision=1;edited=false;draw('예시를 처음 상태로 되돌렸습니다.');}
 if(action==='next'){stage=Math.min(3,stage+1);draw(['초기 상태입니다.','계획을 기록했습니다. 다음은 검사 결과 확인입니다.','검사 기록이 추가됐습니다. 실제 검사 실행은 아닙니다.','검토할 예시 기록을 모았습니다. 실제 출시를 뜻하지 않습니다.'][stage]);}
 if(action==='update'){if(edited)draw('충돌: 내 수정이 있어 교체를 중단했습니다.');else{revision=2;draw('예시 관리 버전 2를 적용했습니다.');}}
 if(action==='restore'){if(edited)draw('충돌: 내 수정을 덮어쓰지 않습니다. 예시 초기화로 다시 시작하세요.');else{revision=1;draw('예시 관리 버전 1로 복원했습니다.');}}
 if(action==='edit'){edited=true;draw('내 수정을 추가했습니다. 업데이트를 누르면 보호 동작을 확인할 수 있습니다.');}
}));
document.querySelector('#download-example').addEventListener('click',()=>{
 const link=document.createElement('a');const url=URL.createObjectURL(new Blob([out.textContent+'\n'],{type:'application/json'}));
 link.href=url;link.download=kind+'-explanation-example.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 document.querySelector('#download-status').textContent='설명용 예시 기록을 내려받았습니다.';
});
document.querySelector('#copy-command').addEventListener('click',async()=>{
 const status=document.querySelector('#copy-status');
 try{await navigator.clipboard.writeText(document.querySelector('#install-command').textContent);status.textContent='명령을 복사했습니다. 실행 전에 내용을 확인하세요.';}
 catch{const range=document.createRange();range.selectNodeContents(document.querySelector('#install-command'));const selection=getSelection();selection.removeAllRanges();selection.addRange(range);status.textContent='복사 권한이 없습니다. 선택된 명령을 직접 복사하세요.';}
});
document.querySelector('#motion').addEventListener('click',event=>{
 const reduced=document.documentElement.classList.toggle('reduced');event.currentTarget.setAttribute('aria-pressed',String(reduced));event.currentTarget.textContent=reduced?'기본 움직임 사용':'움직임 줄이기';
});
