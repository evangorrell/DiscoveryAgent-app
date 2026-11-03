(function () {
  'use strict';
  const CFG = window.roundWorkspaceConfig || {};
  const ReportFigures = window.DiscoveryReportFigures;
  const TabScrollbar = window.RoundWorkspaceTabScrollbar;
  if (!ReportFigures) throw new Error('Shared report figure renderer is unavailable');
  const $ = (id) => document.getElementById(id);
  const activeRoundStorageKey = 'rwActiveRound:' + (CFG.sessionScope || 'session') + ':' + (CFG.projectSlug || 'project');
  const panelTabStorageKey = 'rwPanelTab:' + (CFG.sessionScope || 'session') + ':' + (CFG.projectSlug || 'project');
  const panelCollapsedStorageKey = 'rwPanelCollapsed:' + (CFG.sessionScope || 'session') + ':' + (CFG.projectSlug || 'project');
  const viewerStorageKey = 'rwViewer:' + (CFG.sessionScope || 'session') + ':' + (CFG.projectSlug || 'project');
  const activityExpandedStorageKey = 'rwActivityExpanded:' + (CFG.sessionScope || 'session') + ':' + (CFG.projectSlug || 'project');
  const runningBouncePeriodMs = 4000;
  const entryParams = new URLSearchParams(window.location.search);
  const requestedRoundRaw = entryParams.get('round');
  const requestedRound = requestedRoundRaw === null ? null : Number(requestedRoundRaw);
  const explicitRoundEntry = requestedRoundRaw !== null;
  const exactRoundEntry = Number.isInteger(requestedRound) && requestedRound >= 0;
  const reviewRound = requestedRound;
  const reviewEntry = entryParams.get('review') === '1' && exactRoundEntry;
  const requestedReportView = entryParams.get('report_view') === 'full' ? 'full' : 'summary';
  const auditFigureDragMime = 'application/x-discoveryagent-audit-figure+json';
  const executionLogResponseBytes = 64 * 1024;
  const executionLogRetainedChars = 1024 * 1024;
  const executionLogPolls = new Set();
  const startedRound = requestedRound;
  const startedEntry = entryParams.get('started') === '1' && Number.isFinite(startedRound);
  const storedActiveRoundRaw = sessionStorage.getItem(activeRoundStorageKey);
  const storedActiveRound = storedActiveRoundRaw === null ? null : Number(storedActiveRoundRaw);
  const storedPanelTab = sessionStorage.getItem(panelTabStorageKey);
  const restoredViewer = startedEntry ? {panes:[],activePane:0,orientation:'horizontal',paneRatio:50} : restoreViewer();
  let initialPanelLoadStarted = false;
  let explorerLoading = false;
  function restoreActivityExpanded(){try{const saved=JSON.parse(sessionStorage.getItem(activityExpandedStorageKey)||'[]');return new Set((Array.isArray(saved)?saved:[]).filter(key=>typeof key==='string').slice(-200));}catch(_error){return new Set();}}
  const state = {
    rounds: [], codeTree: null, activeRound: explicitRoundEntry ? (exactRoundEntry ? requestedRound : null) : (storedActiveRound !== null && Number.isFinite(storedActiveRound) ? storedActiveRound : null),
    expanded: new Set(), panes: restoredViewer.panes, activePane: restoredViewer.activePane, orientation: restoredViewer.orientation, paneRatio: restoredViewer.paneRatio,
    panelTab: startedEntry || reviewEntry ? 'activity' : (storedPanelTab === 'chat' ? 'chat' : 'activity'), panelWidth: startedEntry || reviewEntry ? 25 : (Number(localStorage.getItem('rwPanelWidth')) || 25),
    panelCollapsed: startedEntry || reviewEntry ? false : sessionStorage.getItem(panelCollapsedStorageKey)==='true', activityExpanded: restoreActivityExpanded(), activityRequestToken: 0, runStatuses: new Map(), dragFile: null, dragSource: null, dragKind: null, dropDirection: null, dropTargetPane: 0, initialDefaultApplied: false,
  };
  const els = { list: $('rw-round-list'), status: $('rw-tree-status'), panes: $('rw-panes'), empty: $('rw-viewer-empty'),
    viewer: $('rw-viewer'), overlay: $('rw-drop-overlay'), inspector: $('rw-inspector'), divider: $('rw-main-divider'),
    collapse: $('rw-panel-collapse'), restore: $('rw-panel-restore'),
    activity: $('rw-activity-content') };
  const tabScrollbarControllers = [];
  const standaloneFigureViewCleanups = [];
  function registerStandaloneFigureCleanup(cleanup){
    standaloneFigureViewCleanups.push(cleanup);
  }
  function clearStandaloneFigureViews(){
    while(standaloneFigureViewCleanups.length){
      const cleanup=standaloneFigureViewCleanups.pop();
      try{cleanup();}
      catch(error){if(window.console&&typeof window.console.warn==='function')window.console.warn('[round-workspace] Standalone figure cleanup failed:',error);}
    }
  }
  function reportTabScrollbarFailure(error){
    if(window.console&&typeof window.console.warn==='function'){
      window.console.warn('[round-workspace] Optional artifact tab scrollbar disabled:',error);
    }
  }
  function clearTabScrollbarControllers(){
    while(tabScrollbarControllers.length){
      const controller=tabScrollbarControllers.pop();
      try{if(controller&&typeof controller.destroy==='function')controller.destroy();}
      catch(error){reportTabScrollbarFailure(error);}
    }
  }
  function persistActivityExpanded(){try{sessionStorage.setItem(activityExpandedStorageKey,JSON.stringify([...state.activityExpanded].slice(-200)));}catch(_error){}}
  function validViewerFile(file){return file&&(Number.isFinite(file.round)||file.sourceKind==='project-code')&&typeof file.path==='string'&&typeof file.name==='string'&&!(file.sourceKind==='project-code'&&file.isDirectory===true);}
  function restoreViewer(){try{const saved=JSON.parse(sessionStorage.getItem(viewerStorageKey)||'{}');let panes=(Array.isArray(saved.panes)?saved.panes:[]).slice(0,2).map(pane=>{if(validViewerFile(pane))return{tabs:[pane],activeTab:0};const tabs=Array.isArray(pane&&pane.tabs)?pane.tabs.filter(validViewerFile):[];return{tabs,activeTab:Math.max(0,Math.min(Number(pane&&pane.activeTab)||0,Math.max(0,tabs.length-1)))};});if(!panes.some(pane=>pane.tabs.length))panes=[];else if(panes.length===1&&!panes[0].tabs.length)panes=[];return{panes,activePane:Math.max(0,Math.min(Number(saved.activePane)||0,panes.length-1)),orientation:saved.orientation==='vertical'?'vertical':'horizontal',paneRatio:Math.max(25,Math.min(75,Number(saved.paneRatio)||50))};}catch(_error){return{panes:[],activePane:0,orientation:'horizontal',paneRatio:50};}}
  function persistViewer(){try{sessionStorage.setItem(viewerStorageKey,JSON.stringify({panes:state.panes,activePane:state.activePane,orientation:state.orientation,paneRatio:state.paneRatio}));}catch(_error){}}
  function compactEmptyPanes(){const populated=state.panes.filter(pane=>pane&&Array.isArray(pane.tabs)&&pane.tabs.length);if(populated.length===state.panes.length)return;state.panes=populated;state.activePane=Math.max(0,Math.min(state.activePane,state.panes.length-1));if(state.panes.length<2)state.paneRatio=50;}

  async function api(url, body, signal) {
    const res = await fetch(url, { method: body ? 'POST' : 'GET', credentials: 'include',
      headers: { Accept: 'application/json', ...(body ? {'Content-Type': 'application/json'} : {}) },
      body: body ? JSON.stringify(body) : undefined, signal });
    if (res.status === 401) location.href = '/';
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false || data.ok === false) throw new Error(data.message || data.error || 'Request failed');
    return data;
  }
  const projectQuery = () => CFG.projectSlug ? '&project=' + encodeURIComponent(CFG.projectSlug) : '';
  const roundUrl = (path, round = state.activeRound) => path + '?round=' + encodeURIComponent(round) + projectQuery();
  const roundIdentity = (data, round = state.activeRound) => ({
    project_id: CFG.projectId || '',
    round,
    run_id: data && data.run_id || '',
  });
  const roundStatusUrl = (round, runId = '') => {
    const params = new URLSearchParams({project_id: CFG.projectId || '', round: String(round)});
    if(runId)params.set('run_id',runId);
    return '/api/rounds/status?' + params.toString() + projectQuery();
  };
  const artifactUrl = (file) => file.sourceKind==='project-code'
    ? '/api/round_workspace_code_artifact?path=' + encodeURIComponent(file.artifactPath || '') + projectQuery()
    : file.sourceKind==='external'
      ? '/api/round_workspace_artifact?round=' + encodeURIComponent(file.round) + '&source=' + encodeURIComponent(file.source || '') + '&path=' + encodeURIComponent(file.artifactPath || file.path || '') + projectQuery()
      : '/checkpoint_assets/round_' + encodeURIComponent(file.round) + '/' + String(file.path || '').split('/').map(encodeURIComponent).join('/') + (CFG.projectSlug ? '?project=' + encodeURIComponent(CFG.projectSlug) : '');
  const codeLocationUrl = (file) => '/api/round_workspace_code_location?path=' + encodeURIComponent(file.artifactPath || '') + projectQuery();
  const reportFiles={round:{full:'round_report.md',summary:'round_report_summary.md'},memory:{full:'memory_report.md',summary:'memory_report_summary.md'}};
  const viewerReportKind = (file) => {if(!file||file.sourceKind==='project-code')return null;if(file.reportKind==='round'||file.path==='round_report.md')return'round';if(file.reportKind==='memory'||file.path==='memory_report.md')return'memory';return null;};
  const isReport = (file) => viewerReportKind(file)!==null;
  const isRoundReport = (file) => viewerReportKind(file)==='round';
  const reportArtifactPath = (file) => {const kind=viewerReportKind(file);if(!kind)return file&&file.artifactPath||file&&file.path||'';return reportFiles[kind][file.reportView==='summary'?'summary':'full'];};
  const viewerContextString = (value, limit=500) => typeof value==='string'?value.trim().slice(0,limit):'';
  function viewerArtifactType(file){const ext=fileExt(file&&file.name||file&&file.path||'');if(isFigureFile(file&&file.name||''))return'image';if(ext==='.pdf')return'pdf';if(['.md','.markdown'].includes(ext))return'markdown';if(['.txt','.log','.py','.r','.js','.ts','.json','.jsonl','.csv','.tsv','.yaml','.yml','.xml','.tex','.sh','.sql'].includes(ext)||!ext)return'code';if(['.html','.htm'].includes(ext))return'html';return'other';}
  function buildViewerContextSnapshot(panes,paneViews,projectSlug,capturedAt){const artifacts=[];(Array.isArray(panes)?panes:[]).slice(0,2).forEach((pane,paneIndex)=>{const view=Array.isArray(paneViews)?paneViews[paneIndex]:null;if(!view||view.visible!==true||!pane||!Array.isArray(pane.tabs))return;const activeIndex=Math.max(0,Math.min(Number(pane.activeTab)||0,pane.tabs.length-1));const file=pane.tabs[activeIndex];if(!validViewerFile(file))return;const reportKind=viewerReportKind(file);const queryArtifact=['query_input','query_report','execution_log'].includes(file.artifactRole)&&Number.isInteger(file.queryNumber)&&file.queryNumber>0;const artifact={pane:paneIndex,project:viewerContextString(projectSlug,200),round:Number.isFinite(file.round)?file.round:null,sourceKind:viewerContextString(file.sourceKind||'checkpoint',32),source:viewerContextString(file.source,160),path:viewerContextString(file.path,1000),artifactPath:viewerContextString(reportArtifactPath(file),1000),artifactType:viewerArtifactType(file),reportKind,reportView:reportKind?(file.reportView==='summary'?'summary':'full'):null,artifactRole:queryArtifact?file.artifactRole:null,queryNumber:queryArtifact?file.queryNumber:null,figures:(Array.isArray(view.figures)?view.figures:[]).map(value=>({index:Number(value&&value.index)})).filter(value=>Number.isInteger(value.index)&&value.index>=0).slice(0,4)};if(artifact.artifactType==='pdf'&&Number.isInteger(view.pdfPage)&&view.pdfPage>0)artifact.pdfPage=view.pdfPage;artifacts.push(artifact);});if(!artifacts.length)return null;return{version:1,capturedAt:Number.isFinite(capturedAt)?capturedAt:Date.now(),project:viewerContextString(projectSlug,200),artifacts};}
  function visiblyDisplayed(element){if(!element||element.hidden||element.getAttribute&&element.getAttribute('aria-hidden')==='true')return false;const style=window.getComputedStyle?window.getComputedStyle(element):null;if(style&&(style.display==='none'||style.visibility==='hidden'))return false;const rect=element.getBoundingClientRect?element.getBoundingClientRect():null;return!rect||(rect.width>0&&rect.height>0);}
  function visibleFigureIndexes(paneElement){const body=paneElement&&paneElement.querySelector('.rw-pane-body');if(!body||!visiblyDisplayed(body))return[];const bounds=body.getBoundingClientRect();return Array.from(body.querySelectorAll('details.rw-embedded-figure[open]')).map(details=>{const index=Number(details.dataset.rwFigureIndex);const image=details.querySelector('img');if(!Number.isInteger(index)||index<0||!image||!visiblyDisplayed(details)||!visiblyDisplayed(image))return null;const rect=details.getBoundingClientRect();const intersects=rect.right>bounds.left&&rect.left<bounds.right&&rect.bottom>bounds.top&&rect.top<bounds.bottom;return intersects?{index}:null;}).filter(Boolean).slice(0,4);}
  function visiblePdfPage(paneElement){const frame=paneElement&&paneElement.querySelector('iframe.rw-artifact-frame');const src=frame&&frame.getAttribute('src')||'';const match=src.match(/(?:#|[?&])page=(\d+)/i);return match?Number(match[1]):null;}
  function captureViewerContextSnapshot(){if(!els.viewer||!els.panes||els.panes.hidden||!visiblyDisplayed(els.viewer)||!visiblyDisplayed(els.panes))return null;const paneElements=Array.from(els.panes.querySelectorAll('.rw-pane'));const paneViews=state.panes.slice(0,2).map((_pane,index)=>{const paneElement=paneElements[index];return{visible:visiblyDisplayed(paneElement),figures:visibleFigureIndexes(paneElement),pdfPage:visiblePdfPage(paneElement)};});return buildViewerContextSnapshot(state.panes,paneViews,CFG.projectSlug,Date.now());}
  window.DiscoveryRoundWorkspaceViewerContext=Object.freeze({captureSnapshot:captureViewerContextSnapshot});
  const markdownDisplayUrl = (file) => '/api/round_workspace_markdown?round=' + encodeURIComponent(file.round)
    + (file.sourceKind==='external'?'&source=' + encodeURIComponent(file.source || ''):'') + '&path=' + encodeURIComponent(reportArtifactPath(file)) + projectQuery();
  const reportDownloadUrl = (file) => '/projects/' + encodeURIComponent(CFG.projectSlug || '') + '/rounds/' + encodeURIComponent(file.round) + '/report/download?view=' + encodeURIComponent(file.reportView==='summary'?'summary':'full') + (viewerReportKind(file)==='memory'?'&type=memory':'');
  const isDownloadableMarkdown = (file) => file&&file.sourceKind==='checkpoint'&&['.md','.markdown'].includes(fileExt(file.name||file.path||''));
  const isExecutionLog = (file) => Boolean(file&&file.sourceKind==='checkpoint'&&file.artifactRole==='execution_log'&&Number.isInteger(file.queryNumber)&&file.queryNumber>0);
  const markdownArtifactDownloadUrl = (file) => '/projects/' + encodeURIComponent(CFG.projectSlug || '') + '/rounds/' + encodeURIComponent(file.round) + '/artifacts/markdown/download?path=' + encodeURIComponent(file.artifactPath || file.path || '');
  const withDownload = (url) => url + (url.includes('?')?'&':'?') + 'download=1';
  const node = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  function disclosureCaret(tag = 'span', className = 'rw-tree-caret') {
    const caret = node(tag, className);
    caret.innerHTML = '<svg class="rw-tree-caret-icon" viewBox="0 0 12 12" width="10" height="10" aria-hidden="true" focusable="false"><path d="M4 2.5 7.5 6 4 9.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    return caret;
  }
  function formatFileSize(bytes){if(!Number.isFinite(bytes))return'';if(bytes<1024)return bytes+' B';if(bytes<1048576)return Math.round(bytes/1024)+' KB';return(bytes/1048576).toFixed(1)+' MB';}
  function entryTooltip(item,owner){const location=owner.sourceScope==='code'?'Code':'Round '+owner.round_index;const bits=[location+' / '+(item.path||item.name||'')];bits.push(item.type==='dir'?'Folder':((fileExt(item.name).slice(1)||'file').toUpperCase()+' file'));if(item.type==='file'&&Number.isFinite(item.size))bits.push(formatFileSize(item.size));if(item.last_modified){const date=new Date(item.last_modified*1000);if(!isNaN(date.getTime()))bits.push('Modified '+date.toLocaleString());}return bits.join('\n');}
  function visibleResearchTreeNode(item){if(!item||item.visibility!=='research')return null;if(item.type!=='dir')return item;const children=(item.children||[]).map(visibleResearchTreeNode).filter(Boolean);if(!children.length)return null;return{...item,children};}
  function findTreeFile(children,path){for(const item of children||[]){if(item.path===path)return item;if(item.type==='dir'){const found=findTreeFile(item.children,path);if(found)return found;}}return null;}
  function reportViewAvailable(file,view){return view==='summary'?file&&file.hasReportSummary===true:view==='full'&&file&&file.hasReportFull===true;}
  function mergeViewerFile(existing,incoming){const merged={...existing,...incoming};if(isReport(existing)&&isReport(incoming)&&viewerReportKind(existing)===viewerReportKind(incoming)&&reportViewAvailable(incoming,existing.reportView))merged.reportView=existing.reportView;return merged;}
  function hydrateRestoredViewerSources(){let changed=false;state.panes.forEach(pane=>{const tabs=[];pane.tabs.forEach(file=>{const isCode=file.sourceKind==='project-code';const round=state.rounds.find(item=>item.round_index===file.round);const children=isCode?(state.codeTree&&state.codeTree.children):(round&&round.tree&&round.tree.children);const lookupPath=isCode?String(file.artifactPath||file.path||'').replace(/^code\/?/,''):file.path;const item=isCode&&!lookupPath?state.codeTree:findTreeFile(children,lookupPath);if(isCode&&item&&item.type==='dir'){changed=true;return;}if(!item){tabs.push(file);return;}const nextFile=mergeViewerFile(file,workspaceFile(item,isCode?{sourceScope:'code',label:'Code'}:round));if(file.source===nextFile.source&&file.sourceKind===nextFile.sourceKind&&(file.artifactPath||'')===(nextFile.artifactPath||'')&&file.path===nextFile.path&&file.isDirectory===nextFile.isDirectory&&file.reportKind===nextFile.reportKind&&file.hasReportSummary===nextFile.hasReportSummary&&file.hasReportFull===nextFile.hasReportFull&&file.reportView===nextFile.reportView){file.size=item.size??file.size;tabs.push(file);return;}Object.assign(file,nextFile);tabs.push(file);changed=true;});if(tabs.length!==pane.tabs.length){pane.tabs=tabs;pane.activeTab=Math.max(0,Math.min(pane.activeTab,tabs.length-1));}});if(changed)compactEmptyPanes();return changed;}
  function normalizeLegacyRoundTree(legacy) {
    const match = String((legacy.root||{}).name||'').match(/^round_(\d+)$/);
    const index = match ? Number(match[1]) : (state.activeRound || 0);
    return {current_round_index:index, rounds:[{round_index:index,label:'Round '+index,status:'active',tree:legacy.root||{children:[]}}]};
  }

  function workspaceFile(item, owner) {
    const isCode=owner.sourceScope==='code';const codeRelative=isCode?String(item.path||''):'';
    const file = { round: isCode?null:(Number.isFinite(item.storage_round_index)?item.storage_round_index:owner.round_index), source: item.source, sourceKind: item.source_kind || (isCode?'project-code':'checkpoint'), artifactPath: isCode?codeRelative:(item.artifact_path || item.path), path: isCode?(codeRelative?'code/'+codeRelative:'code/'):(item.path), name: item.name, size: item.size, isDirectory:item.type==='dir' };
    const reportKind=item.report_kind||(item.name==='round_report.md'?'round':(item.name==='memory_report.md'?'memory':null));
    if(reportKind){file.reportKind=reportKind;file.hasReportSummary=item.has_report_summary??Boolean(owner['has_'+reportKind+'_report_summary']);file.hasReportFull=item.has_report_full??Boolean(owner['has_'+reportKind+'_report_full']??owner['has_'+reportKind+'_report']);file.reportView=file.hasReportSummary?'summary':'full';}
    if(['query_input','query_report','execution_log'].includes(item.artifact_role)&&Number.isInteger(item.query_number)&&item.query_number>0){file.artifactRole=item.artifact_role;file.queryNumber=item.query_number;}
    return file;
  }

  function safeArtifactIdentityPath(value) {
    if(typeof value!=='string'||!value||value.length>1000||value.startsWith('/')||value.includes('\\')||value.includes('\0'))return null;
    const parts=value.split('/');
    if(parts.some(part=>!part||part==='.'||part==='..'))return null;
    return value;
  }
  function findTreeArtifact(children, identity) {
    for(const item of children||[]){
      if(item.type==='dir'){const found=findTreeArtifact(item.children,identity);if(found)return found;continue;}
      if(item.type!=='file'||item.visibility!=='research')continue;
      const sourceKind=item.source_kind||identity.defaultSourceKind;
      const artifactPath=item.artifact_path||item.path;
      if(sourceKind===identity.sourceKind&&String(item.source||'')===identity.source&&artifactPath===identity.artifactPath)return item;
    }
    return null;
  }
  function canonicalAuditFigureFile(raw) {
    if(!raw||typeof raw!=='object'||raw.version!==1||typeof CFG.projectSlug!=='string'||!CFG.projectSlug||raw.project!==CFG.projectSlug)return null;
    const artifactPath=safeArtifactIdentityPath(raw.artifactPath);
    const sourceKind=typeof raw.sourceKind==='string'?raw.sourceKind:'';
    const source=typeof raw.source==='string'&&raw.source.length<=160?raw.source:'';
    if(!artifactPath||!['checkpoint','external','project-code'].includes(sourceKind)||!isFigureFile(artifactPath))return null;
    if(sourceKind==='project-code'){
      if(raw.round!==null||source)return null;
      const children=state.codeTree&&state.codeTree.children;
      const item=findTreeArtifact(children,{sourceKind,source:'',artifactPath,defaultSourceKind:'project-code'});
      return item&&isFigureFile(item.name)?workspaceFile(item,{sourceScope:'code',label:'Code'}):null;
    }
    if(!Number.isInteger(raw.round)||raw.round<0)return null;
    const round=state.rounds.find(entry=>entry.round_index===raw.round);
    if(!round)return null;
    const item=findTreeArtifact(round.tree&&round.tree.children,{sourceKind,source,artifactPath,defaultSourceKind:'checkpoint'});
    return item&&isFigureFile(item.name)?workspaceFile(item,round):null;
  }
  function sameFileIdentity(left,right){return Boolean(left&&right&&left.round===right.round&&left.sourceKind===right.sourceKind&&String(left.source||'')===String(right.source||'')&&(left.artifactPath||left.path)===(right.artifactPath||right.path));}
  function auditFigureDragPayload(file){return{version:1,project:CFG.projectSlug,round:file.round==null?null:file.round,sourceKind:file.sourceKind,source:String(file.source||''),artifactPath:file.artifactPath||file.path};}
  function startAuditFigureDrag(event, raw) {
    const file=canonicalAuditFigureFile({...raw,version:1});
    if(!file||!event||!event.dataTransfer)return false;
    try{
      event.dataTransfer.clearData();
      event.dataTransfer.effectAllowed='copy';
      event.dataTransfer.setData(auditFigureDragMime,JSON.stringify(auditFigureDragPayload(file)));
    }catch(_error){return false;}
    state.dragFile=file;state.dragSource=null;state.dragKind='audit-figure';
    return true;
  }
  function endAuditFigureDrag(){hideDropOverlay();}
  function auditFigureFileFromDrop(dataTransfer){
    if(!dataTransfer)return null;
    try{return canonicalAuditFigureFile(JSON.parse(dataTransfer.getData(auditFigureDragMime)||''));}catch(_error){return null;}
  }

  function collectRoundFiles(items, owner, files) {
    (items || []).forEach(item => {
      if(!item || item.visibility!=='research')return;
      if(item.type==='dir')collectRoundFiles(item.children,owner,files);
      else files.push(workspaceFile(item,owner));
    });
  }

  function renderTreeNode(item, owner, depth) {
    const wrap = node('div', 'rw-tree-node' + (item.type === 'dir' ? ' is-dir' : ' is-file'));
    const row = node(item.type === 'dir' ? 'button' : 'div', 'rw-tree-row');
    row.title=entryTooltip(item,owner);
    row.style.paddingLeft = '8px';
    const key = (owner.sourceScope==='code'?'code':owner.round_index) + ':' + item.path;
    const disclosure = node('span', 'rw-tree-disclosure-slot');
    row.append(disclosure);
    if (item.type === 'dir') {
      const open = state.expanded.has(key); wrap.classList.toggle('is-collapsed', !open);
      row.type = 'button'; row.setAttribute('aria-expanded', String(open)); disclosure.append(disclosureCaret());
      row.addEventListener('click', () => { open ? state.expanded.delete(key) : state.expanded.add(key); renderExplorer(); });
    } else {
      disclosure.setAttribute('aria-hidden','true');if(item.primary_output){disclosure.classList.add('rw-primary-star');disclosure.innerHTML='<svg viewBox="0 0 16 16" width="10" height="10" focusable="false"><path d="M8 1.5 9.9 5.35l4.25.62-3.08 3 .73 4.23L8 11.2l-3.8 2 .73-4.23-3.08-3 4.25-.62L8 1.5Z" fill="currentColor"/></svg>';}row.draggable = true;
      const file = workspaceFile(item, owner);
      if(file.round!=null)row.dataset.round = String(file.round);row.dataset.path = file.path;
      row.addEventListener('click', () => openFile(file, false));
      row.addEventListener('dragstart', (e) => { state.dragFile = file;state.dragSource=null;state.dragKind='workspace-file'; e.dataTransfer.effectAllowed = 'copy'; e.dataTransfer.setData('application/json', JSON.stringify(file)); });
      row.addEventListener('dragend', hideDropOverlay);
    }
    row.append(node('span', 'rw-tree-name', item.name)); wrap.append(row);
    if (item.type === 'dir') { const children = node('div', 'rw-tree-children'); (item.children || []).forEach(c => children.append(renderTreeNode(c, owner, depth + 1))); wrap.append(children); }
    return wrap;
  }

  function renderCodeTreeNode(item, owner) {
    const wrap=node('div','rw-tree-node is-code '+(item.type==='dir'?'is-dir':'is-file'));
    const row=node('div','rw-tree-row');row.title=entryTooltip(item,owner);row.style.paddingLeft='8px';
    const key='code:'+item.path;
    if(item.type==='dir'){
      const open=state.expanded.has(key);wrap.classList.toggle('is-collapsed',!open);
      const disclosure=disclosureCaret('button','rw-code-disclosure rw-tree-caret');disclosure.type='button';disclosure.setAttribute('aria-label',(open?'Collapse ':'Expand ')+item.name);disclosure.setAttribute('aria-expanded',String(open));disclosure.addEventListener('click',()=>{open?state.expanded.delete(key):state.expanded.add(key);renderExplorer();});row.append(disclosure);
    }else{const spacer=node('span','rw-tree-disclosure-slot');spacer.setAttribute('aria-hidden','true');row.append(spacer);}
    const select=node('button','rw-code-tree-select',item.name);select.type='button';if(item.type==='dir'){const open=state.expanded.has(key);select.setAttribute('aria-label',(open?'Collapse ':'Expand ')+item.name);select.setAttribute('aria-expanded',String(open));select.addEventListener('click',()=>{open?state.expanded.delete(key):state.expanded.add(key);renderExplorer();});}else{select.setAttribute('aria-label','Open '+item.name);select.addEventListener('click',()=>openFile(workspaceFile(item,owner),false));}row.append(select);wrap.append(row);
    if(item.type==='dir'){const children=node('div','rw-tree-children');(item.children||[]).forEach(child=>children.append(renderCodeTreeNode(child,owner)));wrap.append(children);}
    return wrap;
  }

  function renderExplorer() {
    els.list.replaceChildren();
    state.rounds.slice().sort((a,b) => b.round_index-a.round_index).forEach(round => {
      const section = node('section', 'rw-round-section' + (round.round_index === state.activeRound ? ' is-current' : ''));
      section.dataset.status=round.status||'unknown';
      const key = 'round:' + round.round_index; const open = state.expanded.has(key);
      section.classList.toggle('is-collapsed', !open);
      const header = node('div', 'rw-round-header');
      const caret = disclosureCaret('button', 'v2-round-row-leading rw-round-caret rw-tree-caret'); caret.type = 'button'; caret.setAttribute('aria-label', 'Toggle ' + round.label);
      caret.addEventListener('click', (e) => { e.stopPropagation(); open ? state.expanded.delete(key) : state.expanded.add(key); renderExplorer(); });
      const select = node('button', 'rw-round-select'); select.type = 'button'; select.append(node('span', 'v2-round-row-title rw-round-title', round.label));
      const rawStatus=round.status||'unknown';const status=node('span','v2-round-row-status rw-round-status '+rawStatus);
      const statusLabel=rawStatus==='interrupted'?'stopped':rawStatus;
      if(rawStatus==='running'){
        status.classList.add('is-bouncing');status.setAttribute('aria-label',statusLabel);
        status.style.setProperty('--rw-bounce-phase','-'+String(Date.now()%runningBouncePeriodMs)+'ms');
        Array.from(statusLabel).forEach((letter,index)=>{const glyph=node('span','rw-round-status-letter',letter);glyph.setAttribute('aria-hidden','true');glyph.style.setProperty('--rw-letter-delay',String(index*70)+'ms');status.append(glyph);});
      }else status.append(document.createTextNode(statusLabel));
      select.append(status);
      select.addEventListener('click', () => {open ? state.expanded.delete(key) : state.expanded.add(key);if(state.activeRound===round.round_index)renderExplorer();else setActiveRound(round.round_index);});
      header.append(caret, select); section.append(header);
      const tree = node('div', 'rw-file-tree');(round.tree && round.tree.children || []).map(visibleResearchTreeNode).filter(Boolean).forEach(c => tree.append(renderTreeNode(c, round, 0))); section.append(tree); els.list.append(section);
    });
    if(state.codeTree){const owner={sourceScope:'code',label:'Code'};const section=node('section','rw-round-section rw-code-section');const key='code:root';const open=state.expanded.has(key);section.classList.toggle('is-collapsed',!open);const toggle=()=>{open?state.expanded.delete(key):state.expanded.add(key);renderExplorer();};const header=node('div','rw-round-header');const caret=disclosureCaret('button','v2-round-row-leading rw-round-caret rw-tree-caret');caret.type='button';caret.setAttribute('aria-label',(open?'Collapse ':'Expand ')+'Code');caret.setAttribute('aria-expanded',String(open));caret.addEventListener('click',toggle);const title=node('button','rw-round-select');title.type='button';title.append(node('span','v2-round-row-title rw-round-title','Code'));title.setAttribute('aria-label',(open?'Collapse ':'Expand ')+'Code');title.setAttribute('aria-expanded',String(open));title.addEventListener('click',toggle);header.append(caret,title);section.append(header);const tree=node('div','rw-file-tree');(state.codeTree.children||[]).forEach(child=>tree.append(renderCodeTreeNode(child,owner)));section.append(tree);els.list.append(section);}
  }

  async function loadExplorer(options) {
    const silent = Boolean(options&&options.silent);
    if(explorerLoading)return;
    explorerLoading=true;
    const roundBeforeLoad = state.activeRound;
    if(!silent){els.status.textContent = 'Loading…'; els.status.classList.add('is-visible');}
    try { let data; try { data = await api('/api/round_workspace_tree'); } catch (primary) {
        data = normalizeLegacyRoundTree(await api('/api/round_tree'));
      } state.rounds = data.rounds || [];state.codeTree=data.code_tree||null;const viewerIdentityChanged=hydrateRestoredViewerSources();
      const availableRoundIndexes = new Set(state.rounds.map(round => round.round_index));
      if (!explicitRoundEntry && (state.activeRound == null || !availableRoundIndexes.has(state.activeRound))) {
        state.activeRound = state.rounds.reduce((latest, round) => latest == null || round.round_index > latest ? round.round_index : latest, null);
      }
      if (state.activeRound != null) sessionStorage.setItem(activeRoundStorageKey, String(state.activeRound));
      if (state.expanded.size === 0 && state.activeRound != null) state.expanded.add('round:' + state.activeRound);
      els.status.classList.remove('is-visible');renderExplorer();if(viewerIdentityChanged)renderPanes(); openReviewRoundReport();if((!initialPanelLoadStarted||roundBeforeLoad!==state.activeRound)&&state.activeRound!=null){renderActivitySelection(state.activeRound);refreshActivity();}else if(renderActivityQueryStream.lastData&&renderActivityQueryStream.lastRound===state.activeRound)renderActivity(renderActivityQueryStream.lastData,state.activeRound);initialPanelLoadStarted=true;
    } catch (e) { if(!silent){els.status.textContent = e.message;els.status.classList.add('is-visible');} }
    finally{explorerLoading=false;}
  }

  function setActiveRound(round) { if (state.activeRound === round) return; state.activeRound = round;state.activityRequestToken+=1; sessionStorage.setItem(activeRoundStorageKey,String(round)); renderExplorer();renderActivitySelection(round); refreshActivity(); }
  function reportFile(roundIndex,reportKind,reportView='summary'){const round=state.rounds.find(item=>item.round_index===roundIndex);if(!round)return null;const report=(round.tree&&round.tree.children||[]).find(item=>item.type==='file'&&viewerReportKind(item)===reportKind);if(!report)return null;const file=workspaceFile(report,round);if(reportViewAvailable(file,reportView))file.reportView=reportView;return file;}
  function roundReportFile(roundIndex=state.activeRound,reportView='summary'){return reportFile(roundIndex,'round',reportView);}
  function activityRoundReportFile(data,roundIndex){return roundReportFile(roundIndex)||(data.has_round_report?{round:roundIndex,sourceKind:'checkpoint',artifactPath:'round_report.md',path:'round_report.md',name:'round_report.md',reportKind:'round',reportView:'full',hasReportFull:true,hasReportSummary:false}:null);}
  function openReviewRoundReport(){if(state.initialDefaultApplied||!reviewEntry)return;state.initialDefaultApplied=true;const report=roundReportFile(reviewRound,requestedReportView);if(report)openFile(report,false);}
  function fileExt(name) { const i = name.lastIndexOf('.'); return i < 0 ? '' : name.slice(i).toLowerCase(); }
  function isFigureFile(name){return['.png','.jpg','.jpeg','.gif','.svg','.webp','.tif','.tiff'].includes(fileExt(name));}
  function fileLocation(file){return file.sourceKind==='project-code'?'Code':'Round '+file.round;}
  const renderMarkdown = (element, text) => ReportFigures.renderMarkdown(element, text);
  const renderReportMarkdown = (element, text, figures) => (
    ReportFigures.renderReportMarkdown(element, text, figures)
  );
  async function copyCodePath(pathText,status) {
    let copied=false;
    try {
      if(!navigator.clipboard||typeof navigator.clipboard.writeText!=='function')throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(pathText);copied=true;
    } catch(_error) {
      let field=null;
      try {
        field=document.createElement('textarea');field.value=pathText;field.setAttribute('readonly','');field.setAttribute('aria-hidden','true');Object.assign(field.style,{position:'fixed',left:'-9999px',opacity:'0',pointerEvents:'none'});document.body.append(field);field.select();copied=Boolean(document.execCommand&&document.execCommand('copy'));
      } catch(_fallbackError) { copied=false; }
      finally { if(field)field.remove(); }
    }
    status.textContent=copied?'Copied.':'Copy unavailable.';
    if(copied&&typeof window.setTimeout==='function')window.setTimeout(()=>{if(status.textContent==='Copied.')status.textContent='';},1800);
    return copied;
  }
  function stopExecutionLogPolls(){executionLogPolls.forEach(controller=>controller.stop());executionLogPolls.clear();}
  function executionLogCursorUrl(file,cursor){return '/api/round_workspace_execution_log?round='+encodeURIComponent(file.round)+'&query='+encodeURIComponent(file.queryNumber)+'&path='+encodeURIComponent(file.artifactPath||file.path||'')+'&cursor='+encodeURIComponent(cursor)+'&count='+encodeURIComponent(executionLogResponseBytes)+projectQuery();}
  function renderExecutionLogBody(body,file){
    body.replaceChildren();body.classList.add('rw-execution-log-body');
    const shell=node('section','rw-execution-log');shell.setAttribute('aria-label','Read-only Execution Agent log');
    const toolbar=node('div','rw-execution-log-toolbar');const stateLabel=node('span','rw-execution-log-state','Loading…');stateLabel.setAttribute('role','status');stateLabel.setAttribute('aria-live','polite');
    const latest=node('button','rw-execution-log-latest','Latest');latest.type='button';latest.hidden=true;
    const viewport=node('div','rw-execution-log-scroll');viewport.tabIndex=0;viewport.setAttribute('aria-label','Execution log output');
    const output=node('pre','rw-execution-log-output');viewport.append(output);toolbar.append(stateLabel,latest);shell.append(toolbar,viewport);body.append(shell);
    let cursor=0;let retained='';let nearBottom=true;let timer=null;let request=null;let stopped=false;let inFlight=false;let delay=750;let terminalIdlePolls=0;
    const followLatest=()=>{viewport.scrollTop=viewport.scrollHeight;nearBottom=true;latest.hidden=true;};
    const updateFollowState=()=>{nearBottom=viewport.scrollHeight-viewport.scrollTop-viewport.clientHeight<=48;latest.hidden=nearBottom;};
    viewport.addEventListener('scroll',updateFollowState,{passive:true});latest.addEventListener('click',followLatest);
    const controller={stop(){if(stopped)return;stopped=true;if(timer!==null)window.clearTimeout(timer);timer=null;if(request)request.abort();executionLogPolls.delete(controller);}};executionLogPolls.add(controller);
    const schedule=(milliseconds)=>{if(stopped)return;if(timer!==null)window.clearTimeout(timer);timer=window.setTimeout(poll,milliseconds);};
    const append=(text)=>{if(!text)return;const shouldFollow=nearBottom;retained+=text;if(retained.length>executionLogRetainedChars)retained=retained.slice(-executionLogRetainedChars);output.textContent=retained;if(shouldFollow)window.requestAnimationFrame?window.requestAnimationFrame(followLatest):followLatest();else latest.hidden=false;};
    const poll=async()=>{if(stopped||inFlight)return;if(document.hidden){schedule(Math.min(8000,delay*2));return;}inFlight=true;request=new AbortController();try{const res=await fetch(executionLogCursorUrl(file,cursor),{cache:'no-store',credentials:'include',headers:{Accept:'application/json'},signal:request.signal});const payload=await res.json().catch(()=>({}));if(!res.ok||payload.success===false)throw new Error(payload.error||'Unable to read execution log');if(Number(payload.cursor)!==cursor||!Number.isInteger(payload.next_cursor)||payload.next_cursor<cursor)throw new Error('Invalid execution log cursor response');append(typeof payload.content==='string'?payload.content:'');cursor=payload.next_cursor;const live=payload.live===true;stateLabel.textContent=live?'Live':'Complete';stateLabel.classList.toggle('is-live',live);if(live){terminalIdlePolls=0;}else if(payload.complete===true){terminalIdlePolls=payload.content?0:terminalIdlePolls+1;if(terminalIdlePolls>=2){controller.stop();return;}schedule(500);return;}delay=payload.content?750:Math.min(5000,Math.round(delay*1.5));schedule(payload.content&&cursor<Number(payload.file_size)?0:delay);}catch(error){if(stopped||error.name==='AbortError')return;stateLabel.textContent='Reconnecting…';stateLabel.classList.remove('is-live');delay=Math.min(8000,Math.max(1500,delay*2));schedule(delay);}finally{inFlight=false;request=null;}};
    poll();
  }
  function renderStandaloneImage(body,file) {
    const shell=node('div','rw-figure-resize-shell rw-standalone-figure-resize-shell');
    const stage=node('div','rw-figure-image-stage rw-standalone-figure-image-stage');
    const img=node('img','rw-artifact-image');
    img.alt=file.name;
    img.draggable=false;
    stage.append(img);
    shell.append(stage);
    body.append(shell);

    let disposed=false;
    let controller=null;
    const installResizer=()=>{
      if(disposed||controller||!shell.isConnected||!img.naturalWidth)return;
      const available=Math.max(1,body.getBoundingClientRect().width-40);
      const initial=Math.min(img.naturalWidth,available);
      shell.style.width=initial>=available-1?'100%':Math.round(initial)+'px';
      controller=ReportFigures.attachFigureResizer(
        shell,stage,{horizontalGutter:40},
      );
    };
    img.addEventListener('load',installResizer);
    registerStandaloneFigureCleanup(()=>{
      disposed=true;
      img.removeEventListener('load',installResizer);
      if(controller&&typeof controller.destroy==='function')controller.destroy();
      controller=null;
    });
    img.src=artifactUrl(file);
    if(img.complete)installResizer();
  }

  async function renderFileBody(body, file) {
    const ext = fileExt(file.name); body.replaceChildren();
    if(isExecutionLog(file)){renderExecutionLogBody(body,file);return;}
    if(isFigureFile(file.name)){renderStandaloneImage(body,file);return;}
    if (ext === '.pdf' || ext === '.html' || ext === '.htm') { const frame = node('iframe','rw-artifact-frame'); frame.src = artifactUrl(file); frame.title = file.name; if (ext !== '.pdf') frame.setAttribute('sandbox','allow-same-origin'); body.append(frame); return; }
    if (['.md','.markdown','.txt','.log','.py','.r','.js','.ts','.json','.jsonl','.csv','.tsv','.yaml','.yml','.xml','.tex','.sh','.sql'].includes(ext) || !ext) {
      try { const markdownFile=ext === '.md'||ext === '.markdown';const trustedMarkdownPayload=markdownFile&&file.sourceKind!=='project-code';const res=await fetch(trustedMarkdownPayload?markdownDisplayUrl(file):artifactUrl(file),{cache:'default'});if(!res.ok)throw new Error('Unable to load file ('+res.status+')');let text,figures=[];if(trustedMarkdownPayload){const payload=await res.json();text=payload.display_text??payload.markdown??'';figures=payload.figures||[];}else{text=await res.text();}body.replaceChildren();
        if (ext === '.md' || ext === '.markdown') { const article = node('article','rw-markdown rw-markdown-document v2-report-markdown'); if(figures.length)renderReportMarkdown(article,text,figures);else renderMarkdown(article,text);Array.from(article.querySelectorAll('details.rw-embedded-figure')).forEach((details,index)=>{if(figures[index]&&figures[index].available)details.dataset.rwFigureIndex=String(index);}); body.append(article); }
        else { const pre = node('pre','rw-code'); pre.textContent = text; body.append(pre); }
      } catch(e) { body.replaceChildren(node('div','rw-unsupported',e.message)); } return;
    }
    const fallback = node('div','rw-unsupported'); fallback.append(node('strong','', 'Preview unavailable'), node('p','', 'This artifact can be downloaded and opened in its native application.')); body.append(fallback);
  }

  function applyPaneRatio(){const panes=els.panes.querySelectorAll('.rw-pane');if(panes.length!==2)return;panes[0].style.flex='0 0 '+state.paneRatio+'%';panes[1].style.flex='0 0 '+(100-state.paneRatio)+'%';const divider=els.panes.querySelector('.rw-pane-divider');if(divider)divider.setAttribute('aria-valuenow',String(Math.round(state.paneRatio)));}
  function resizePaneRatio(value){state.paneRatio=Math.max(25,Math.min(75,value));applyPaneRatio();persistViewer();}
  function createPaneDivider(){const divider=node('div','rw-pane-divider');divider.setAttribute('role','separator');divider.tabIndex=0;divider.setAttribute('aria-label','Resize file panes');divider.setAttribute('aria-valuemin','25');divider.setAttribute('aria-valuemax','75');divider.setAttribute('aria-orientation',state.orientation==='vertical'?'horizontal':'vertical');divider.addEventListener('pointerdown',e=>{e.preventDefault();const move=ev=>{const rect=els.panes.getBoundingClientRect();const ratio=state.orientation==='vertical'?((ev.clientY-rect.top)/rect.height)*100:((ev.clientX-rect.left)/rect.width)*100;resizePaneRatio(ratio);};const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);};window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);});divider.addEventListener('keydown',e=>{const decrease=state.orientation==='vertical'?'ArrowUp':'ArrowLeft';const increase=state.orientation==='vertical'?'ArrowDown':'ArrowRight';if(e.key!==decrease&&e.key!==increase)return;e.preventDefault();resizePaneRatio(state.paneRatio+(e.key===increase?2:-2));});return divider;}
  function setReportView(file,view){if(!isReport(file)||!reportViewAvailable(file,view))return;file.reportView=view;const params=new URLSearchParams(window.location.search);if(view==='full')params.set('report_view','full');else params.delete('report_view');window.history.replaceState(null,'',window.location.pathname+(params.toString()?'?'+params.toString():'')+window.location.hash);renderPanes();}
  function clearReportViewUrl(){const params=new URLSearchParams(window.location.search);if(!params.has('report_view'))return;params.delete('report_view');window.history.replaceState(null,'',window.location.pathname+(params.toString()?'?'+params.toString():'')+window.location.hash);}
  function codePathControl(file){const action=node('span','rw-code-path-action');const copy=node('button','rw-report-download rw-code-path-copy');copy.type='button';copy.setAttribute('aria-label','Copy Absolute Path');copy.innerHTML='<svg class="rw-report-download-icon rw-code-path-copy-icon" viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" focusable="false"><rect x="6.4" y="6.4" width="9.1" height="9.1" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M13.1 6.4V5c0-.8-.6-1.4-1.4-1.4H5c-.8 0-1.4.6-1.4 1.4v6.7c0 .8.6 1.4 1.4 1.4h1.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg><span>Copy Absolute Path</span>';const status=node('span','rw-code-path-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');copy.addEventListener('click',async event=>{event.stopPropagation();copy.disabled=true;try{const res=await fetch(codeLocationUrl(file),{cache:'no-store'});if(!res.ok)throw new Error('Unable to load code file path');const payload=await res.json();if(!payload.success||typeof payload.absolute_path!=='string'||!payload.absolute_path.startsWith('/'))throw new Error('Unable to load code file path');await copyCodePath(payload.absolute_path,status);}catch(_error){status.textContent='Unable to copy.';}finally{copy.disabled=false;}});action.append(copy,status);return action;}
  function downloadControl(file,current){const download=node('a','rw-report-download');download.innerHTML='<svg class="rw-report-download-icon" viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" focusable="false"><path d="M10 2.8c.5 0 .9.4.9.9v7.1l2.4-2.4c.3-.3.9-.3 1.2 0 .3.3.3.9 0 1.2l-3.9 3.9c-.3.3-.9.3-1.2 0L5.5 9.6c-.3-.3-.3-.9 0-1.2.3-.3.9-.3 1.2 0l2.4 2.4V3.7c0-.5.4-.9.9-.9ZM4.2 15.4c0-.5.4-.9.9-.9h9.8c.5 0 .9.4.9.9s-.4.9-.9.9H5.1c-.5 0-.9-.4-.9-.9Z" fill="currentColor"/></svg><span>Download</span>';const report=isReport(file);const direct=file.sourceKind==='project-code'||isFigureFile(file.name)||fileExt(file.name)==='.pdf'||isExecutionLog(file);download.href=report?reportDownloadUrl({...file,reportView:current}):(direct?withDownload(artifactUrl(file)):markdownArtifactDownloadUrl(file));download.setAttribute('download','');const kind=viewerReportKind(file);download.setAttribute('aria-label',report?'Download '+kind+' report '+(current==='summary'?'summary':'full report')+' for Round '+file.round:(file.sourceKind==='project-code'?'Download '+file.name:'Download '+file.name+' from Round '+file.round));download.addEventListener('click',event=>event.stopPropagation());return download;}
  function artifactViewControls(file){const report=isReport(file);const code=file&&file.sourceKind==='project-code'&&file.isDirectory!==true;const figure=file&&(isFigureFile(file.name)||fileExt(file.name)==='.pdf');const downloadable=isDownloadableMarkdown(file)||code||figure||isExecutionLog(file);if(!report&&!downloadable)return null;const current=report?(reportViewAvailable(file,file.reportView)?file.reportView:(file.hasReportSummary?'summary':'full')):'full';const controls=node('div','rw-report-controls');if(code)controls.append(codePathControl(file));if(report&&file.hasReportSummary&&file.hasReportFull){const choices=node('div','rw-report-view-toggle');choices.setAttribute('role','group');choices.setAttribute('aria-label',(viewerReportKind(file)==='memory'?'Memory':'Round')+' report view');[['summary','Summary'],['full','Full Report']].forEach(([value,label])=>{const button=node('button','rw-report-view-option'+(current===value?' is-active':''),label);button.type='button';button.setAttribute('aria-pressed',String(current===value));button.addEventListener('click',event=>{event.stopPropagation();setReportView(file,value);});choices.append(button);});controls.append(choices);}controls.append(downloadControl(file,current));return controls;}
  function installTabScrollbars(){
    if(!els.panes||!TabScrollbar||typeof TabScrollbar.create!=='function')return;
    try{
      els.panes.querySelectorAll('.rw-pane-header').forEach((header,index)=>{
        let track=null;
        try{
          const tabs=header.querySelector('.rw-file-tabs');
          if(!tabs)return;
          tabs.id='rw-file-tabs-'+index;
          track=node('div','rw-tab-scrollbar');
          track.hidden=true;
          track.tabIndex=-1;
          track.setAttribute('role','scrollbar');
          track.setAttribute('aria-hidden','true');
          track.setAttribute('aria-label','Scroll open artifact tabs');
          track.setAttribute('aria-orientation','horizontal');
          track.setAttribute('aria-controls',tabs.id);
          track.setAttribute('aria-valuemin','0');
          track.setAttribute('aria-valuemax','0');
          track.setAttribute('aria-valuenow','0');
          const thumb=node('span','rw-tab-scrollbar-thumb');
          thumb.setAttribute('aria-hidden','true');
          track.append(thumb);
          header.prepend(track);
          const controller=TabScrollbar.create(tabs,track,thumb);
          if(controller){tabScrollbarControllers.push(controller);controller.scrollActive(tabs.querySelector('.rw-file-tab.is-active'));}
        }catch(error){
          if(track&&typeof track.remove==='function')track.remove();
          reportTabScrollbarFailure(error);
        }
      });
    }catch(error){reportTabScrollbarFailure(error);}
  }
  function focusPane(index){
    // Moving focus between split viewers must not rebuild them; a rebuild
    // resets loaded content, scroll position, and execution-log polling.
    if(state.activePane===index)return;
    state.activePane=index;
    els.panes.querySelectorAll('.rw-pane').forEach((pane,paneIndex)=>pane.classList.toggle('is-active',paneIndex===index));
    persistViewer();
  }
  function renderPanes() {
    clearStandaloneFigureViews();
    clearTabScrollbarControllers();
    compactEmptyPanes();
    stopExecutionLogPolls();
    els.panes.replaceChildren(); els.empty.hidden = state.panes.length > 0; els.panes.hidden = state.panes.length === 0;
    els.panes.className = 'rw-panes ' + (state.orientation === 'vertical' ? 'is-vertical' : 'is-horizontal');
    state.panes.forEach((paneModel, i) => { const file=paneModel.tabs[paneModel.activeTab];const pane = node('section','rw-pane' + (i === state.activePane ? ' is-active' : '') + (!file?' is-empty':'')); pane.addEventListener('click',()=>focusPane(i));
      const head=node('header','rw-pane-header');const tabs=node('div','rw-file-tabs');paneModel.tabs.forEach((tabFile,tabIndex)=>{const tabIsActive=tabIndex===paneModel.activeTab;const tab=node('div','rw-file-tab'+(tabIsActive?' is-active':''));tab.title=fileLocation(tabFile)+' / '+tabFile.path;tab.draggable=true;tab.addEventListener('dragstart',e=>{state.dragFile=tabFile;state.dragSource={pane:i,tab:tabIndex};state.dragKind='workspace-file';e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('application/json',JSON.stringify(tabFile));tab.classList.add('is-dragging');});tab.addEventListener('dragend',()=>{tab.classList.remove('is-dragging');hideDropOverlay();});const label=node('button','rw-file-tab-label',tabFile.name);label.type='button';label.addEventListener('click',e=>{e.stopPropagation();state.activePane=i;paneModel.activeTab=tabIndex;renderPanes();});const close=node('button','rw-file-tab-close','×');close.type='button';close.tabIndex=tabIsActive?0:-1;if(!tabIsActive)close.setAttribute('aria-hidden','true');close.setAttribute('aria-label','Close '+tabFile.name);close.addEventListener('click',e=>{e.stopPropagation();const wasActive=tabIndex===paneModel.activeTab;if(isReport(tabFile))clearReportViewUrl();paneModel.tabs.splice(tabIndex,1);if(!paneModel.tabs.length){state.panes.splice(i,1);state.activePane=0;state.paneRatio=50;}else if(tabIndex<paneModel.activeTab){paneModel.activeTab-=1;}else if(wasActive){paneModel.activeTab=Math.min(tabIndex,paneModel.tabs.length-1);}renderPanes();});tab.append(label,close);tabs.append(tab);});head.append(tabs);
      if(!file){const emptyBody=node('div','rw-pane-empty','Open a file from the round explorer.');pane.append(head,emptyBody);if(state.panes.length===2)pane.style.flex='0 0 '+(i===0?state.paneRatio:100-state.paneRatio)+'%';els.panes.append(pane);if(i===0&&state.panes.length===2)els.panes.append(createPaneDivider());return;}
      const codeSelection=file.sourceKind==='project-code';const breadcrumb=node('nav','rw-pane-breadcrumb');breadcrumb.setAttribute('aria-label','File path');breadcrumb.title=codeSelection?file.path:fileLocation(file)+' / '+file.path;const breadcrumbPath=node('div','rw-pane-breadcrumb-path');if(!codeSelection)breadcrumbPath.append(node('span','rw-breadcrumb-round',fileLocation(file)));String(file.path||file.name).split('/').filter(Boolean).forEach((part,index)=>{if(!codeSelection||index>0){const separator=node('span','rw-breadcrumb-separator','›');separator.setAttribute('aria-hidden','true');breadcrumbPath.append(separator);}breadcrumbPath.append(node('span','rw-breadcrumb-part',part));});breadcrumb.append(breadcrumbPath);const viewControls=artifactViewControls(file);if(viewControls)breadcrumb.append(viewControls);
      const body=node('div','rw-pane-body'); pane.append(head,breadcrumb,body);if(state.panes.length===2)pane.style.flex='0 0 '+(i===0?state.paneRatio:100-state.paneRatio)+'%';els.panes.append(pane);if(i===0&&state.panes.length===2)els.panes.append(createPaneDivider());renderFileBody(body,file);
    });
    installTabScrollbars();
    persistViewer();
  }
  function openFile(file, split, direction, replace, focusExistingAcrossPanes) {
    if(file&&file.sourceKind==='project-code'&&file.isDirectory===true)return;
    if(focusExistingAcrossPanes&&!split&&!replace){for(let paneIndex=0;paneIndex<state.panes.length;paneIndex+=1){const pane=state.panes[paneIndex];const existing=pane.tabs.findIndex(tab=>sameFileIdentity(tab,file));if(existing>=0){state.activePane=paneIndex;pane.tabs[existing]=mergeViewerFile(pane.tabs[existing],file);pane.activeTab=existing;renderPanes();return;}}}
    if (!state.panes.length) { state.panes=[{tabs:[file],activeTab:0}]; state.activePane=0; }
    else if (split && state.panes.length < 2) { state.orientation=['top','bottom'].includes(direction)?'vertical':'horizontal';state.paneRatio=50; const before=['left','top'].includes(direction); state.panes.splice(before?state.activePane:state.activePane+1,0,{tabs:[file],activeTab:0}); state.activePane=before?state.activePane:state.activePane+1; }
    else {const pane=state.panes[state.activePane];if(replace){pane.tabs[pane.activeTab]=sameFileIdentity(pane.tabs[pane.activeTab],file)?mergeViewerFile(pane.tabs[pane.activeTab],file):file;}else{const existing=pane.tabs.findIndex(tab=>sameFileIdentity(tab,file));if(existing>=0){pane.tabs[existing]=mergeViewerFile(pane.tabs[existing],file);pane.activeTab=existing;}else{pane.tabs.push(file);pane.activeTab=pane.tabs.length-1;}}}
    renderPanes();
  }
  function detachDraggedTab(source){const pane=state.panes[source.pane];if(!pane||!pane.tabs[source.tab])return null;const file=pane.tabs[source.tab];const wasActive=source.tab===pane.activeTab;pane.tabs.splice(source.tab,1);if(pane.tabs.length){if(source.tab<pane.activeTab)pane.activeTab-=1;else if(wasActive)pane.activeTab=Math.min(source.tab,pane.tabs.length-1);}else{state.panes.splice(source.pane,1);}return{file,pane};}
  function showDropOverlay(e){if(!state.dragFile)return;e.preventDefault();const rect=els.viewer.getBoundingClientRect();const preview=els.overlay.querySelector('.rw-drop-preview');if(state.dragKind==='audit-figure'){const panes=els.panes.querySelectorAll('.rw-pane');const active=panes[state.activePane];const target=active?active.getBoundingClientRect():rect;state.dropDirection='center';state.dropTargetPane=state.activePane;Object.assign(preview.style,{left:(target.left-rect.left)+'px',top:(target.top-rect.top)+'px',width:target.width+'px',height:target.height+'px'});preview.dataset.direction='center';els.overlay.hidden=false;return;}const x=(e.clientX-rect.left)/Math.max(1,rect.width);const y=(e.clientY-rect.top)/Math.max(1,rect.height);const dx=x-.5,dy=y-.5;let centerTarget=-1;if(state.dragSource&&state.panes.length===2){els.panes.querySelectorAll('.rw-pane').forEach((pane,index)=>{if(index===state.dragSource.pane)return;const box=pane.getBoundingClientRect();const px=(e.clientX-box.left)/Math.max(1,box.width);const py=(e.clientY-box.top)/Math.max(1,box.height);if(px>=.2&&px<=.8&&py>=.2&&py<=.8)centerTarget=index;});}const explorerMayReplace=!state.dragSource&&state.panes.length<2&&x>=.25&&x<=.75&&y>=.25&&y<=.75;const direction=centerTarget>=0||explorerMayReplace?'center':(Math.abs(dx)>Math.abs(dy)?(dx<0?'left':'right'):(dy<0?'top':'bottom'));state.dropDirection=direction;state.dropTargetPane=centerTarget>=0?centerTarget:(['left','top'].includes(direction)?0:Math.min(1,state.panes.length-1));let left=0,top=0,width=rect.width,height=rect.height;if(direction==='center'&&centerTarget>=0){const targetBox=els.panes.querySelectorAll('.rw-pane')[centerTarget].getBoundingClientRect();left=targetBox.left-rect.left;top=targetBox.top-rect.top;width=targetBox.width;height=targetBox.height;}else{if(direction==='left')width/=2;if(direction==='right'){left=width/2;width/=2;}if(direction==='top')height/=2;if(direction==='bottom'){top=height/2;height/=2;}}Object.assign(preview.style,{left:left+'px',top:top+'px',width:width+'px',height:height+'px'});preview.dataset.direction=direction;els.overlay.hidden=false;}
  function hideDropOverlay(){els.overlay.hidden=true;state.dropDirection=null;state.dragFile=null;state.dragSource=null;state.dragKind=null;}
  els.viewer.addEventListener('dragover',showDropOverlay);els.viewer.addEventListener('dragleave',e=>{if(!els.viewer.contains(e.relatedTarget))hideDropOverlay();});
  els.viewer.addEventListener('drop',e=>{e.preventDefault();if(!state.dragFile)return;if(state.dragKind==='audit-figure'){const file=auditFigureFileFromDrop(e.dataTransfer);if(!file||!sameFileIdentity(file,state.dragFile)){hideDropOverlay();return;}openFile(file,false,'center',false,true);hideDropOverlay();return;}const file=state.dragFile;const source=state.dragSource;const direction=state.dropDirection||'right';if(direction==='center'&&source&&state.panes.length===2){const targetPane=state.panes[state.dropTargetPane];const sourcePane=state.panes[source.pane];if(!targetPane||!sourcePane||targetPane===sourcePane){hideDropOverlay();return;}const moved=detachDraggedTab(source);if(!moved){hideDropOverlay();return;}state.activePane=state.panes.indexOf(targetPane);openFile(moved.file,false,direction);hideDropOverlay();return;}if(direction==='center'){openFile(file,false,direction);hideDropOverlay();return;}if(source&&state.panes.length<2){const sourcePane=state.panes[source.pane];if(!sourcePane||sourcePane.tabs.length<2){hideDropOverlay();return;}const moved=detachDraggedTab(source);if(!moved){hideDropOverlay();return;}state.activePane=state.panes.indexOf(moved.pane);openFile(moved.file,true,direction);hideDropOverlay();return;}if(source&&state.panes.length===2){const sourcePane=state.panes[source.pane];const otherPane=state.panes[1-source.pane];if(!sourcePane||!otherPane){hideDropOverlay();return;}state.orientation=['top','bottom'].includes(direction)?'vertical':'horizontal';state.paneRatio=50;const before=['left','top'].includes(direction);if(sourcePane.tabs.length===1){state.panes=before?[sourcePane,otherPane]:[otherPane,sourcePane];state.activePane=state.panes.indexOf(sourcePane);renderPanes();hideDropOverlay();return;}const moved=detachDraggedTab(source);if(!moved){hideDropOverlay();return;}const targetPane=otherPane;state.activePane=state.panes.indexOf(targetPane);openFile(moved.file,false,direction);state.panes=before?[targetPane,state.panes.find(pane=>pane!==targetPane)]:[state.panes.find(pane=>pane!==targetPane),targetPane];state.activePane=state.panes.indexOf(targetPane);renderPanes();hideDropOverlay();return;}if(state.panes.length<2){openFile(file,true,direction);}else{state.orientation=['top','bottom'].includes(direction)?'vertical':'horizontal';state.paneRatio=50;state.activePane=['left','top'].includes(direction)?0:1;openFile(file,false,direction);}hideDropOverlay();});

  function activityStatusClass(status){const value=String(status||'unknown').toLowerCase();if(['complete','completed'].includes(value))return'completed';if(['failed','interrupted','stopped'].includes(value))return'failed';if(['running','in progress','started','selected','exploring'].includes(value))return'active';if(value==='waiting'||value==='pending')return'waiting';return'unknown';}
  function canonicalRoundStatus(status){const value=String(status||'unavailable').trim().toLowerCase();if(['complete','completed'].includes(value))return'complete';if(value==='interrupted')return'interrupted';if(value==='stopped')return'stopped';if(value==='failed')return'failed';if(['running','in progress','started','selected','exploring','pending','waiting'].includes(value))return'in progress';return'status unavailable';}
  function roundStatusTitle(status,roundIndex){return'Round '+roundIndex+' '+canonicalRoundStatus(status);}
  function queryNumber(item,index){const match=String(item.query_id||item.query||'').match(/(?:query[_-]?)?0*(\d+)$/i);return match?Number(match[1]):index+1;}
  function activityMilestoneType(row){if(row.milestone_type)return row.milestone_type;const summary=String(row.summary||row.message||'').trim().toLowerCase();if(summary==='round started.'||summary.startsWith('discoveryagent run started'))return'round_started';if(summary==='round resumed.')return'round_resumed';if(summary==='report writing started.')return'report_writing_started';if(summary==='round report saved.')return'round_report_saved';if(summary==='memory report saved.')return'memory_report_saved';if(summary==='workflow completed.')return'workflow_completed';if(summary.startsWith('error encountered:'))return'round_error';return null;}
  function normalizeActivity(data){const rows=Array.isArray(data.discovery_activity)?data.discovery_activity:[];const roundStatus=String(data.execution_status||data.round_status||'').toLowerCase();return rows.map((row,index)=>{if(row.kind==='query'){const calls=Array.isArray(row.subagent_calls)?row.subagent_calls:[];let status=String(row.status||'unknown').toLowerCase();let summary=row.query_summary||row.summary||'';const unfinished=!['completed','failed','interrupted','stopped'].includes(status);if(unfinished&&roundStatus==='interrupted'){status='interrupted';summary='Query interrupted.';}else if(unfinished&&roundStatus==='stopped'){status='stopped';summary='Query stopped.';}else if(unfinished&&roundStatus==='failed'){status='failed';summary='Query failed.';}const normalizedCalls=calls.map(call=>call&&call.execution_log?({...call,execution_log_live:call.execution_log_live===true&&!['completed','failed','interrupted','stopped'].includes(status)}):call);const objective=row.query_objective||row.objective||summary;const input=row.query_input&&typeof row.query_input==='object'?row.query_input:null;const output=row.query_report&&typeof row.query_report==='object'?row.query_report:null;return{kind:'query',key:row.query_id||'query-'+index,number:queryNumber(row,index),status,objective,summary,input,output,time:row.timestamp||'',details:normalizedCalls}}const milestoneType=activityMilestoneType(row);return milestoneType?{kind:'milestone',key:milestoneType+':'+(row.timestamp||index),milestoneType,summary:row.summary||row.message||'',time:row.timestamp||''}:null;}).filter(Boolean);}
  function activityQueryArtifactFile(item,requestedRound,role){const descriptor=role==='query_input'?item&&item.input:item&&item.output;if(!descriptor||descriptor.artifact_role!==role||descriptor.query_number!==item.number||typeof descriptor.path!=='string')return null;const round=state.rounds.find(candidate=>candidate.round_index===requestedRound);const tree=round&&round.tree&&round.tree.children;if(!Array.isArray(tree))return null;const artifact=findTreeFile(tree,descriptor.path);if(!artifact||artifact.type!=='file'||artifact.visibility!=='research'||artifact.artifact_role!==role||artifact.query_number!==item.number||artifact.path!==descriptor.path)return null;return workspaceFile(artifact,round);}
  function activityExecutionLogFile(detail,item,requestedRound){const descriptor=detail&&detail.execution_log;if(!descriptor||descriptor.artifact_role!=='execution_log'||descriptor.query_number!==item.number||typeof descriptor.path!=='string')return null;const round=state.rounds.find(candidate=>candidate.round_index===requestedRound);const tree=round&&round.tree&&round.tree.children;if(!Array.isArray(tree))return null;const artifact=findTreeFile(tree,descriptor.path);if(!artifact||artifact.type!=='file'||artifact.visibility!=='research'||artifact.artifact_role!=='execution_log'||artifact.query_number!==item.number||artifact.path!==descriptor.path)return null;return workspaceFile(artifact,round);}
  function activityDataWithRoundFallback(data,requestedRound){const round=state.rounds.find(item=>item.round_index===requestedRound);if(!round)return data;const children=round.tree&&Array.isArray(round.tree.children)?round.tree.children:[];const validFile=name=>children.some(item=>item.type==='file'&&item.name===name&&Number(item.size)>0);return{...data,round_status:data.round_status||round.status||'unavailable',has_round_report:data.has_round_report??Boolean(roundReportFile(requestedRound)),has_memory_report:data.has_memory_report??validFile('memory_report.md')};}
  function activeActivityQuery(items,roundStatus){const queries=items.filter(item=>item.kind==='query');const explicit=[...queries].reverse().find(item=>['active'].includes(activityStatusClass(item.status)));if(explicit)return explicit;if(roundStatus==='running'){const latest=queries[queries.length-1];if(latest&&!['completed','failed','waiting'].includes(activityStatusClass(latest.status)))return latest;}return null;}
  function completedQueryCount(count){return count+' '+(count===1?'query':'queries')+' completed';}
  function activityReportState(data){if(data.has_round_report&&data.has_memory_report)return'Round report and memory report saved';if(data.has_round_report)return'Round report saved';if(data.has_memory_report)return'Memory report saved';return'';}
  function confirmRoundAction({title,copy,confirmLabel,confirmClass,cancelLabel='Cancel'}){return window.V2Dialog.confirm({title,message:copy,confirmLabel,cancelLabel,danger:confirmClass.includes('stop')});}
  function confirmStopRun(){return confirmRoundAction({title:'Stop this round?',copy:'DiscoveryAgent will stop the current run. Completed checkpoints and generated files will be preserved.',confirmLabel:'Stop Run',confirmClass:'rw-stop-run',cancelLabel:'Keep Running'});}
  function confirmResubmitRound(){return confirmRoundAction({title:'Resubmit this round?',copy:'DiscoveryAgent will resubmit this round and run it again from the beginning.',confirmLabel:'Resubmit Round',confirmClass:'rw-resubmit-round'});}
  async function stopActiveRound(button,data){if(!await confirmStopRun())return;button.disabled=true;try{await api('/api/rounds/stop',roundIdentity(data));await refreshActivity();await loadExplorer({silent:true});}catch(error){button.disabled=false;button.title=error.message;}}
  function showRoundActionError(button,error){const controls=button.closest('.rw-run-controls')||button.parentElement;let message=controls&&controls.querySelector('.rw-run-action-error');if(!message&&controls){message=node('div','rw-run-action-error');message.setAttribute('role','alert');controls.append(message);}if(message)message.textContent=error&&error.message?error.message:'The round could not be launched.';button.title=error&&error.message?error.message:'';}
  async function resumeActiveRound(button,data){button.disabled=true;const prior=button.parentElement&&button.parentElement.querySelector('.rw-run-action-error');if(prior)prior.remove();try{await api('/api/rounds/resume',roundIdentity(data));await refreshActivity();await loadExplorer({silent:true});}catch(error){button.disabled=false;showRoundActionError(button,error);}}
  async function resubmitActiveRound(button,data){if(!await confirmResubmitRound())return;button.disabled=true;try{await api('/api/rounds/resubmit',roundIdentity(data));await refreshActivity();await loadExplorer({silent:true});}catch(error){button.disabled=false;button.title=error.message;}}
  function appendRunControl(body,roundStatus,data){if(roundStatus==='running'){const button=node('button','rw-run-action rw-stop-run','Stop Run');button.type='button';button.addEventListener('click',()=>stopActiveRound(button,data));body.append(button);}else if(['interrupted','stopped','failed'].includes(roundStatus)){const controls=node('div','rw-run-controls');if(data.resumable){const button=node('button','rw-run-action rw-resume-round','Resume Round');button.type='button';button.addEventListener('click',()=>resumeActiveRound(button,data));controls.append(button);}else{const button=node('button','rw-run-action rw-resubmit-round','Resubmit Round');button.type='button';button.addEventListener('click',()=>resubmitActiveRound(button,data));controls.append(button);}body.append(controls);}}
  function renderActivitySummary(data,items,requestedRound){const section=node('section','rw-activity-summary');section.setAttribute('aria-label','Round status summary');const roundStatus=data.execution_status||data.round_status||'unavailable';const queries=items.filter(item=>item.kind==='query');const completedCount=queries.filter(item=>activityStatusClass(item.status)==='completed').length;const active=activeActivityQuery(items,roundStatus);const title=node('div','rw-activity-summary-title');const body=node('div','rw-activity-summary-body');if(roundStatus==='completed'){title.append(node('span','rw-activity-state-icon completed','✓'),node('strong','', 'Round complete'));body.append(node('div','',completedQueryCount(queries.length)));const reportState=activityReportState(data);if(reportState)body.append(node('div','',reportState));const report=activityRoundReportFile(data,requestedRound);if(report){const action=node('button','rw-open-round-report','Open Round Report');action.type='button';action.addEventListener('click',()=>openFile(report,false));body.append(action);}}else if(roundStatus==='interrupted'){title.append(node('span','rw-activity-state-icon failed','■'),node('strong','', 'Round interrupted'));body.append(node('div','rw-activity-completed-count',completedQueryCount(completedCount)),node('div','rw-activity-current-summary',data.resumable?'Completed checkpoints and generated files were preserved.':'The round stopped before a resumable checkpoint was written. You can resubmit it from the beginning.'));}else if(roundStatus==='failed'){title.append(node('span','rw-activity-state-icon failed','!'),node('strong','', 'Round '+requestedRound+' failed'));body.append(node('div','rw-activity-completed-count',completedQueryCount(completedCount)),node('div','rw-activity-current-summary',data.resumable?'A native checkpoint is available. You can resume this round.':'Please resubmit this round and try again.'));}else if(['running','selected','exploring','pending'].includes(roundStatus)){title.append(node('span','rw-activity-state-icon active','●'),node('strong','', 'Round in progress'));body.append(node('div','rw-activity-completed-count',completedQueryCount(completedCount)));if(!queries.length){body.append(node('div','rw-activity-current-summary','Preparing the first research query…'));}else if(active){const planned=Number.isInteger(data.planned_query_count)&&data.planned_query_count>0?data.planned_query_count:null;body.append(node('div','rw-activity-query-count',planned?'Query '+active.number+' of '+planned:'Query '+active.number+' in progress'),node('div','rw-activity-current-summary',active.summary));const progress=node('div','rw-activity-progress'+(planned?'':' is-indeterminate'));progress.setAttribute('role','progressbar');progress.setAttribute('aria-label','Round query progress');if(planned){const value=Math.min(planned,Math.max(0,active.number));progress.setAttribute('aria-valuemin','0');progress.setAttribute('aria-valuemax',String(planned));progress.setAttribute('aria-valuenow',String(value));const track=node('span','rw-activity-progress-track');const fill=node('span','rw-activity-progress-fill');fill.style.width=(value/planned*100)+'%';track.append(fill);progress.append(track,node('span','rw-activity-progress-label',value+'/'+planned));}else{progress.append(node('span','rw-activity-progress-track'),node('span','rw-activity-progress-label',queries.length+' created'));}body.append(progress);}else{body.append(node('div','rw-activity-current-summary','Waiting for the next research query…'));}}else{title.append(node('span','rw-activity-state-icon failed','!'),node('strong','', 'Round status unavailable'));if(data.message)body.append(node('div','',data.message));}appendRunControl(body,roundStatus,data);section.append(title,body);return section;}
  function renderActivityQuery(item,requestedRound){const article=node('article','rw-activity-query '+activityStatusClass(item.status));const expansionKey=requestedRound+':'+item.key;const expanded=state.activityExpanded.has(expansionKey);const row=node('div','rw-activity-query-row');const caret=node('button','rw-activity-caret',expanded?'⌄':'›');caret.type='button';caret.setAttribute('aria-label',(expanded?'Collapse ':'Expand ')+'Query '+item.number+' details');caret.setAttribute('aria-expanded',String(expanded));caret.addEventListener('click',()=>{expanded?state.activityExpanded.delete(expansionKey):state.activityExpanded.add(expansionKey);persistActivityExpanded();renderActivityQueryStream.lastData&&renderActivity(renderActivityQueryStream.lastData,requestedRound);});const statusClass=activityStatusClass(item.status);const statusGlyph=statusClass==='completed'?'✓':item.status==='interrupted'?'■':statusClass==='failed'?'!':'●';const indicator=node('span','rw-activity-query-indicator '+statusClass,statusGlyph);indicator.setAttribute('aria-label',item.status);const copy=node('div','rw-activity-query-copy');copy.append(node('strong','rw-activity-query-label','Query '+item.number),node('div','rw-activity-query-summary',item.summary));row.append(caret,indicator,copy);article.append(row);if(expanded){const details=node('div','rw-activity-query-details');const inputSection=node('section','rw-activity-query-section');inputSection.append(node('strong','rw-activity-detail-heading','Input'));const input=activityQueryArtifactFile(item,requestedRound,'query_input');if(input){const action=node('button','rw-activity-artifact-link','Query input ↗');action.type='button';action.setAttribute('aria-label','Open Query '+item.number+' input in file viewer');action.addEventListener('click',()=>openFile(input,false,'center',false,true));inputSection.append(action);}else inputSection.append(node('div','rw-activity-detail-empty','Exact input artifact unavailable.'));const outputSection=node('section','rw-activity-query-section');outputSection.append(node('strong','rw-activity-detail-heading','Output'));const report=activityQueryArtifactFile(item,requestedRound,'query_report');if(report){const action=node('button','rw-activity-artifact-link','Query report ↗');action.type='button';action.setAttribute('aria-label','Open Query '+item.number+' report in file viewer');action.addEventListener('click',()=>openFile(report,false,'center',false,true));outputSection.append(action);}else{let message='Query report unavailable.';if(['pending','running','waiting','started','in progress'].includes(item.status))message='Query report will be available after completion.';else if(['failed','interrupted'].includes(item.status))message='No completed query report is available.';outputSection.append(node('div','rw-activity-detail-empty',message));}details.append(inputSection,outputSection);if(item.details.length){const calls=node('div','rw-activity-subagents');calls.append(node('strong','rw-activity-detail-heading','Sub-agent call'));item.details.forEach(detail=>{const call=node('div','rw-activity-subagent');if(detail.id)call.dataset.callId=detail.id;const callIndicator=node('span','rw-activity-subagent-indicator '+activityStatusClass(detail.status),activityStatusClass(detail.status)==='completed'?'✓':activityStatusClass(detail.status)==='failed'?'!':'●');const callCopy=node('div','');callCopy.append(node('strong','',(detail.sequence?detail.sequence+'. ':'')+(detail.agent_role||'')),node('div','',detail.task_summary||''));const logFile=activityExecutionLogFile(detail,item,requestedRound);if(logFile){const logAction=node('button','rw-activity-artifact-link rw-execution-log-link','Execution log'+(detail.execution_log_live?' · Live':'')+' ↗');logAction.type='button';logAction.setAttribute('aria-label','Open Query '+item.number+' Execution Agent log in file viewer'+(detail.execution_log_live?' (live)':''));logAction.addEventListener('click',()=>openFile(logFile,false,'center',false,true));callCopy.append(logAction);}call.append(callIndicator,callCopy);calls.append(call);});details.append(calls);}article.append(details);}return article;}
  function renderActivityQueryStream(items,requestedRound,loading=false){const section=node('section','rw-activity-query-progress');const heading=node('h2','','QUERY PROGRESS');const stream=node('div','rw-activity-stream');items.forEach(item=>{if(item.kind==='query')stream.append(renderActivityQuery(item,requestedRound));else if(item.summary){const milestone=node('div','rw-activity-milestone');milestone.dataset.milestoneType=item.milestoneType;milestone.title=item.time?'Recorded '+item.time:'';milestone.append(node('span','rw-activity-milestone-icon','○'),node('span','',item.summary));stream.append(milestone);}});if(!stream.children.length)stream.append(node('div','rw-panel-empty',loading?'Loading Round '+requestedRound+' activity…':'No queries or milestones yet.'));section.append(heading,stream);return section;}
  function finalizeActivitySummary(section,data,items,requestedRound){const roundStatus=String(data.execution_status||data.round_status||'unavailable').toLowerCase();const title=section.querySelector('.rw-activity-summary-title strong');if(title)title.textContent=roundStatusTitle(roundStatus,requestedRound);const body=section.querySelector('.rw-activity-summary-body');if(data.loading&&body){body.replaceChildren(node('div','rw-activity-current-summary','Loading selected-round status…'));return section;}if(roundStatus==='stopped'&&body){const queries=items.filter(item=>item.kind==='query');const completedCount=queries.filter(item=>activityStatusClass(item.status)==='completed').length;body.replaceChildren(node('div','rw-activity-completed-count',completedQueryCount(completedCount)),node('div','rw-activity-current-summary','The round was stopped. Completed checkpoints and generated files were preserved when available.'));appendRunControl(body,roundStatus,data);}return section;}
  function renderActivity(data,requestedRound){const compatibleData=activityDataWithRoundFallback(data,requestedRound);const items=normalizeActivity(compatibleData);renderActivityQueryStream.lastData=compatibleData;renderActivityQueryStream.lastRound=requestedRound;const summary=finalizeActivitySummary(renderActivitySummary(compatibleData,items,requestedRound),compatibleData,items,requestedRound);els.activity.replaceChildren(summary,renderActivityQueryStream(items,requestedRound,compatibleData.loading===true));}
  function renderActivitySelection(requestedRound){const round=state.rounds.find(item=>item.round_index===requestedRound);renderActivity({round_index:requestedRound,round_status:round&&round.status||'unavailable',loading:true,discovery_activity:[]},requestedRound);}
  function runResponseMatches(data,requestedRound,runId=null){return Boolean(data&&data.project_id===CFG.projectId&&Number(data.round_index??data.round)===requestedRound&&(!runId||data.run_id===runId));}
  function acceptRunStatus(data){const runId=String(data&&data.run_id||'');if(!runId)return true;const status=canonicalRoundStatus(data.execution_status||data.round_status||data.status);const prior=state.runStatuses.get(runId);const finalStates=new Set(['complete','failed','interrupted','stopped']);if(prior&&finalStates.has(prior)&&!finalStates.has(status))return false;if(finalStates.has(status))state.runStatuses.set(runId,status);else if(!prior)state.runStatuses.set(runId,status);return true;}
  async function refreshActivity(){if(state.activeRound==null)return;const requestedRound=state.activeRound;const requestToken=++state.activityRequestToken;try{const data=await api(roundUrl('/api/round_activity',requestedRound));if(!runResponseMatches(data,requestedRound))return;let execution=null;try{execution=await api(roundStatusUrl(requestedRound,data.run_id));}catch(_error){}if(execution&&runResponseMatches(execution,requestedRound,data.run_id||null)){data.execution_status=execution.status;data.resumable=execution.resumable===true;data.run_id=execution.run_id||data.run_id;}if(state.activeRound!==requestedRound||requestToken!==state.activityRequestToken)return;if(!runResponseMatches(data,requestedRound)||!acceptRunStatus(data))return;renderActivity(data,requestedRound);}catch(error){if(state.activeRound!==requestedRound||requestToken!==state.activityRequestToken)return;renderActivity({project_id:CFG.projectId,round_index:requestedRound,round_status:'unavailable',message:error.message,discovery_activity:[]},requestedRound);}}

  function notifyAuditLifecycle(transition){window.dispatchEvent(new CustomEvent('roundworkspace:audit-lifecycle',{detail:{transition}}));}
  function applyPanelTab(){document.querySelectorAll('[data-panel-tab]').forEach(x=>x.classList.toggle('is-active',x.dataset.panelTab===state.panelTab));document.querySelectorAll('[data-panel-view]').forEach(x=>x.classList.toggle('is-active',x.dataset.panelView===state.panelTab));}
  function selectPanelTab(nextTab){const previousTab=state.panelTab;if(nextTab===previousTab)return;state.panelTab=nextTab;sessionStorage.setItem(panelTabStorageKey,state.panelTab);applyPanelTab();if(previousTab==='chat'&&nextTab!=='chat')notifyAuditLifecycle('hidden');else if(previousTab!=='chat'&&nextTab==='chat')notifyAuditLifecycle('shown');}
  document.querySelectorAll('[data-panel-tab]').forEach(b=>b.addEventListener('click',()=>{selectPanelTab(b.dataset.panelTab);}));
  function applyPanel(){sessionStorage.setItem(panelCollapsedStorageKey,String(state.panelCollapsed));els.inspector.hidden=state.panelCollapsed;els.divider.hidden=state.panelCollapsed;els.restore.hidden=!state.panelCollapsed;if(!state.panelCollapsed)els.inspector.style.flexBasis=state.panelWidth+'%';}
  function setPanelCollapsed(collapsed){const next=collapsed===true;if(state.panelCollapsed===next)return;state.panelCollapsed=next;applyPanel();notifyAuditLifecycle(next?'closed':'reopened');}
  els.collapse.addEventListener('click',()=>{setPanelCollapsed(true);}); els.restore.addEventListener('click',()=>{setPanelCollapsed(false);});
  els.divider.addEventListener('pointerdown',e=>{e.preventDefault();const collapseThreshold=12.5;const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',up);};const move=ev=>{const box=els.viewer.parentElement.getBoundingClientRect();const requested=((box.right-ev.clientX)/box.width)*100;if(requested<collapseThreshold){setPanelCollapsed(true);return;}setPanelCollapsed(false);state.panelWidth=Math.max(25,Math.min(75,requested));localStorage.setItem('rwPanelWidth',state.panelWidth);applyPanel();};window.addEventListener('pointermove',move);window.addEventListener('pointerup',up);window.addEventListener('pointercancel',up);});
  window.roundWorkspace={state,reloadTree:loadExplorer,setActiveRound,openFile,startAuditFigureDrag,endAuditFigureDrag}; applyPanel(); applyPanelTab(); renderPanes();if(state.activeRound!=null){initialPanelLoadStarted=true;refreshActivity();}loadExplorer();
  window.addEventListener('beforeunload',stopExecutionLogPolls);
  window.addEventListener('beforeunload',clearStandaloneFigureViews);
  setInterval(()=>{if(!document.hidden){refreshActivity();loadExplorer({silent:true});}},5000);
})();
