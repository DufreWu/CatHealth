'use strict';
const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>v==null?t('missingValue'):Number(v).toFixed(2).replace(/\.00$/,'');
const LANGS=['CN','EN','FI'],LANG_KEY='pawbase.lang';
const stageKeys={adult:'stageAdult',kitten:'stageKitten',senior:'stageSenior',all:'stageAllScope'};
const state={items:[],page:1,total:0,limit:12,selection:new Map(),lenses:new Set(),stats:{},cfg:null,mode:null,route:'/',reviewData:null,modalView:null,request:0,lang:initialLang()};
function initialLang(){try{const v=localStorage.getItem(LANG_KEY);if(LANGS.includes(v))return v;}catch{}return 'CN';}
function t(key,vars){let s=translations[state.lang]?.[key]??translations.CN[key]??key;if(vars)for(const[k,v]of Object.entries(vars))s=s.replaceAll(`{${k}}`,v);return s;}
// 后端中文文本的显示翻译；无对应译文时原样显示。
function tx(text){if(text==null||state.lang==='CN')return text;const table=serverText[state.lang]||{};if(Object.hasOwn(table,text))return table[text];for(const[re,fn]of serverPatterns[state.lang]||[]){const m=String(text).match(re);if(m)return fn(tx,...m.slice(1));}return text;}
function stageName(key){return stageKeys[key]?t(stageKeys[key]):null;}
function applyTranslations(){
  document.documentElement.lang = state.lang === 'FI' ? 'fi' : state.lang === 'EN' ? 'en' : 'zh-CN';
  document.title = t('title');
  const languageSelect = $('#language-select');
  if (languageSelect) languageSelect.value = state.lang;
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-html]').forEach(el => { el.innerHTML = t(el.dataset.i18nHtml); });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  document.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', t(el.dataset.i18nAria)); });
  const modeNote = $('#mode-note');
  if (modeNote && state.mode) modeNote.textContent = state.mode === 'demo' ? t('modeDemo') : t('modeHistory');
  const footerEl = $('#footer');
  if (footerEl && state.cfg) footerEl.textContent = t('footer', {version: state.cfg.framework_version, source: tx(state.cfg.source), reviewed: state.cfg.last_reviewed || t('pendingVerify')});
}
async function api(path){const r=await fetch(path);let data;try{data=await r.json();}catch{throw Error(t('badResponse'));}if(!r.ok)throw Error(tx(data.error)||t('requestFailed'));return data;}
function status(message=''){const statusEl=$('#status'); if(statusEl) statusEl.textContent=message;}
function safeLink(url){try{const u=new URL(url);return u.protocol==='https:'?esc(u.href):'#';}catch{return '#';}}
function order(){const base=['protein_dm_pct','phosphorus_dm_pct','ca_p_ratio'];if(['kidney','senior','urinary'].some(k=>state.lenses.has(k)))return ['phosphorus_dm_pct','ca_p_ratio','protein_dm_pct'];return base;}
// 四级判定 + 未判定；点击展开该级别与参照值的简明说明。
function verdictHtml(p,key){const v=p.verdicts?.[key];if(!v)return '';const ref=v.reference;return `<details class="verdict"><summary><span class="pill verdict-${esc(v.color)}">${esc(tx(v.name))}</span></summary><p>${esc(tx(v.explain))}</p>${ref?`<p>${ref.min!=null?esc(t('verdictMin',{v:fmt(ref.min)})):''} ${ref.nutritional_max!=null?esc(t('verdictNutritionalMax',{v:fmt(ref.nutritional_max)})):''} ${ref.legal_max!=null?esc(t('verdictLegalMax',{v:fmt(ref.legal_max)})):''}</p><p>${esc(tx(ref.basis))}</p>`:''}</details>`;}
function metric(p,key){const d=p.benchmark_dimensions[key],v=p.analysis[key],band=p.bands[key],name=tx(d.name);
 const reason=p.data_suspect||p.needs_review?t('reasonReview'):p.as_fed.moisture==null&&key!=='ca_p_ratio'?t('reasonMoisture'):t('reasonMissing');
 if(v==null)return `<div class="metric"><div class="metric-title">${esc(name)} <small>${esc(d.unit)}</small></div><p class="missing">${esc(reason)}</p>${verdictHtml(p,key)}</div>`;
 const cohort=p.product_type+':'+(p.life_stage||'unknown'),stats=state.stats[cohort]?.[key];
 const lo=Math.min(d.domain[0],v,stats?.min??v),hi=Math.max(d.domain[1],v,stats?.max??v,...d.fediaf_lines.map(l=>l.value));
 const pos=n=>(n-lo)/(hi-lo||1)*100;
 const range=stats&&stats.count>=2?`<span class="range" style="left:${pos(stats.p25)}%;width:${pos(stats.p75)-pos(stats.p25)}%" title="${esc(t('cohortRange',{lo:fmt(stats.p25),hi:fmt(stats.p75),n:stats.count}))}"></span>`:'';
 const ticks=d.fediaf_lines.map(l=>`<span class="tick" style="left:${pos(l.value)}%" title="${esc(tx(l.label)+' · '+tx(l.basis))}"><span>${fmt(l.value)}</span></span>`).join('');
 const bandName=tx(band?.name);
 return `<div class="metric"><div class="metric-title"><span>${esc(name)}</span><b>${fmt(v)} <small>${esc(d.unit)}</small></b></div><div class="scale" role="img" aria-label="${esc(name)} ${fmt(v)} ${esc(d.unit)}, ${esc(bandName)}">${range}${ticks}<span class="point" style="left:${pos(v)}%" title="${esc(t('thisProduct',{v}))}"></span></div><div class="scale-legend"><span>${fmt(lo)}</span><span>${esc(stats?.count>=2?t('cohortBand',{n:stats.count}):t('cohortSmall'))}</span><span>${fmt(hi)}</span></div><details><summary><span class="pill">${esc(bandName||t('referenceValue'))}</span> ${t('viewBasis')}</summary><p>${esc(tx(d.basis))}</p><p>${esc(tx(d.authority))}</p></details>${verdictHtml(p,key)}</div>`;
}
function card(p,actions=true){const scope=p.nutrition_scope.variant;return `<article class="card" data-id="${esc(p.id)}"><div class="card-top"><span>${esc(p.brand||t('brandMissing'))}</span><span>${p.is_demo?t('demo'):esc(p.source.toUpperCase())}</span></div><div class="food-icon" aria-hidden="true">${p.product_type==='wet'?'◒':'◈'}</div><h3>${esc(p.name)}</h3><div class="tags"><span class="pill">${p.product_type==='wet'?t('wet'):t('dry')}</span><span class="pill">${stageName(p.life_stage)||t('stageUnknown')}</span>${p.is_prescription?`<span class="pill">${t('prescription')}</span>`:''}${p.is_grain_free?`<span class="pill">${t('grainFreeTag')}</span>`:''}${p.needs_review?`<span class="pill">${t('needsReview')}</span>`:''}</div><p class="scope">${t('scope')}: ${esc(scope||t('scopeDefault'))} · ${p.is_demo?t('demo'):esc((p.fetched_at||'').slice(0,10))}</p>${order().map(k=>metric(p,k)).join('')}<p class="small-note">${esc(tx(p.stage_note))}</p>${p.context_notes.length?`<details class="context" ${state.lenses.size||p.life_stage==='senior'?'open':''}><summary>${t('contextNotes')} · ${p.context_notes.length}</summary>${p.context_notes.map(n=>`<p class="note-${esc(n.level)}">${esc(tx(n.text))}</p>`).join('')}</details>`:''}${actions?`<div class="card-actions"><button data-action="detail">${t('viewDetail')}</button><button data-action="select" class="${state.selection.has(p.id)?'selected':''}" aria-pressed="${state.selection.has(p.id)}">${state.selection.has(p.id)?t('compareSelected'):t('compareAdd')}</button></div>`:''}</article>`;}
function render(){ $('#products').innerHTML=state.items.length?state.items.map(p=>card(p)).join(''):`<div class="empty">${t('emptyResults')}</div>`;$('#count').textContent=`${state.total} ${t('countUnit')}`;$('#page-label').textContent=t('pageLabel',{page:state.page,pages:Math.max(1,Math.ceil(state.total/state.limit))});$('#prev').disabled=state.page<=1;$('#next').disabled=state.page*state.limit>=state.total;}
async function load(){const req=++state.request;status(t('loadingProducts'));const params=new URLSearchParams({q:$('#search').value,brand:$('#brand').value,type:$('#type').value,stage:$('#stage').value,prescription:$('#prescription').checked,grain_free:$('#grain').checked,page:state.page,limit:state.limit});try{const data=await api('/api/products?'+params);if(req!==state.request)return;state.items=data.items;state.total=data.total;const selected=$('#brand').value;$('#brand').innerHTML=`<option value="" data-i18n="brandAll">${esc(t('brandAll'))}</option>`+data.brands.map(b=>`<option value="${esc(b)}">${esc(b)}</option>`).join('');$('#brand').value=selected;render();status();}catch(e){if(req===state.request){$('#products').innerHTML=`<div class="empty">${t('loadFailed')}<br><button id="retry">${t('retry')}</button></div>`;$('#retry').onclick=load;status(e.message);}}}
function tray(){const n=state.selection.size;$('#compare-tray').hidden=!n;$('#selection-count').textContent=t('selectedCount',{n});$('#compare').disabled=n<2;}
function modal(html){$('#modal-content').innerHTML=html;if(!$('#modal').open)$('#modal').showModal();}
function detailHtml(p){return `<h2>${esc(p.name)}</h2><p class="small-note">${esc(t('frameworkVersion',{version:p.framework_version}))} · ${esc(p.analyzed_at?t('analyzedAt',{at:p.analyzed_at}):t('notAnalyzed'))} · ${esc(t('fetchedAt',{at:p.fetched_at||t('notProvided')}))}</p><div class="detail-grid"><div>${card(p,false)}</div><div><h3>${t('asFedTitle')}</h3><table><thead><tr><th>${t('nutrient')}</th><th>${t('labelValue')}</th></tr></thead><tbody>${Object.entries(p.as_fed).map(([k,v])=>`<tr><td>${esc(t('n_'+k))}</td><td>${fmt(v)}</td></tr>`).join('')}</tbody></table><p class="small-note">${t('taurineDeclared')}${p.taurine_declared===null?t('taurineNone'):p.taurine_declared?t('taurineYes'):t('taurineNo')}</p>${p.review_reasons.length?`<div class="context">${p.review_reasons.map(r=>`<p>${esc(tx(r))}</p>`).join('')}</div>`:''}<h3>${t('offersTitle')}</h3><p class="small-note">${t('offersNote')}</p><table><tbody>${p.offers.map(o=>`<tr><td>${esc(o.size||o.sku)}</td><td>${o.reference_price==null?esc(tx(o.note)):'€ '+fmt(o.reference_price)}</td></tr>`).join('')}</tbody></table>${p.source_url?`<p><a href="${safeLink(p.source_url)}" target="_blank" rel="noopener noreferrer">${esc(t('goToSource',{source:p.source}))}</a></p>`:''}</div></div><details><summary>${t('declarationRaw')}</summary><pre>${esc(p.declaration_raw)}</pre></details><details><summary>${t('ingredientsRaw')}</summary><pre>${esc(p.ingredients_raw||t('notProvided'))}</pre></details><details><summary>${t('nutritionRaw')}</summary><pre>${esc(p.nutrition_raw||t('notProvided'))}</pre></details><details><summary>${t('snapshotRaw')}</summary><pre>${esc(p.snapshot_text||t('noSnapshot'))}</pre></details>`;}
function compareHtml(data){return `<h2>${t('compareTitle')}</h2><p class="small-note">${t('compareNote')}</p><div class="compare-grid" style="--columns:${data.items.length}">${data.items.map(p=>card(p,false)).join('')}</div>`;}
// modalView 保存已取回的数据，切换语言时只重绘、不重新请求。
function showModalView(view){state.modalView=view;modal(view.render(view.data));}
async function detail(id){state.modalView=null;modal(`<p>${t('loadingDetails')}</p>`);try{showModalView({render:detailHtml,data:await api('/api/products/'+encodeURIComponent(id))});}catch(e){modal(`<p>${esc(e.message)}</p>`);}}
async function compare(){state.modalView=null;modal(`<p>${t('loadingCompare')}</p>`);try{showModalView({render:compareHtml,data:await api('/api/compare?'+new URLSearchParams({ids:[...state.selection.keys()].join(',')}))});}catch(e){modal(`<p>${esc(e.message)}</p>`);}}
function bandsForDisplay(d){if(d.bands)return d.bands.map(b=>({...b,name:tx(b.name)}));return d.normal_range;}
function methodology(){const c=state.cfg;$('#methodology').innerHTML=`<p class="eyebrow">METHOD / TRANSPARENCY</p><h1>${t('methodTitle')}</h1><p class="notice">${esc(tx(c.verification_note))}</p><article><h2>${t('methodQuestion')}</h2><p>${t('methodP1')}</p><p>${t('methodP2')}</p><p>${t('methodP3')}</p></article>${Object.entries(c.dimensions).map(([k,d])=>`<article id="${k}"><h2>${esc(tx(d.name))}</h2><p>${esc(tx(d.basis))}</p><p class="small-note">${esc(t('authorityLabel',{v:tx(d.authority)}))}</p><pre>${esc(JSON.stringify(bandsForDisplay(d),null,2))}</pre></article>`).join('')}<article id="verdicts"><h2>${t('verdictsTitle')}</h2><p>${t('verdictsIntro')}</p>${Object.values(c.verdict_levels).map(l=>`<p><span class="pill verdict-${esc(l.color)}">${esc(tx(l.name))}</span> ${esc(tx(l.explain))}</p>`).join('')}<p class="small-note">${esc(tx(c.energy.me_method))}</p></article><article id="warnings"><h2>${t('warningsTitle')}</h2>${c.context_rules.map(r=>`<p class="note-${esc(r.level)}">${esc(tx(r.text))}</p>`).join('')}</article><article><h2>${t('stagesTitle')}</h2>${Object.entries(c.life_stage_groups).map(([k,v])=>`<p>${esc(stageName(k)||t('unknownStage'))}: ${esc(tx(v.note))}</p>`).join('')}<p>${t('lensNote')}</p></article><p class="small-note">${esc(t('methodSource',{source:tx(c.source),reviewed:c.last_reviewed||t('pendingVerify')}))}</p>`;}
function renderReview(){const d=state.reviewData,el=$('#review');el.innerHTML=`<p class="eyebrow">DATA / QUALITY</p><h1>${t('reviewTitle')}</h1><p class="intro">${t('reviewIntro')}</p><article><h2>${t('needsReviewTitle')} · ${d.needs_review.length}</h2>${d.needs_review.length?`<table><tbody>${d.needs_review.map(p=>`<tr><td>${esc(p.name)}</td><td>${p.review_reasons.map(r=>esc(tx(r))).join('<br>')}</td><td><button data-detail="${esc(p.id)}">${t('viewRaw')}</button></td></tr>`).join('')}</tbody></table>`:`<p>${t('noReview')}</p>`}</article><article><h2>${t('excludedTitle')} · ${d.excluded.length}</h2><table><thead><tr><th>${t('colName')}</th><th>${t('colReason')}</th><th>${t('colSource')}</th></tr></thead><tbody>${d.excluded.map(p=>`<tr><td>${esc(p.name)}</td><td>${esc(translations.CN['ex_'+p.reason]?t('ex_'+p.reason):p.reason)}</td><td>${esc(p.source)}</td></tr>`).join('')}</tbody></table></article><article><h2>${t('quarantineTitle')} · ${d.quarantine.length}</h2>${d.quarantine.map(r=>`<pre>${esc(JSON.stringify(r,null,2))}</pre>`).join('')||`<p>${t('noQuarantine')}</p>`}</article><article><h2>${t('recentRuns')}</h2>${d.runs.map(r=>`<details><summary>${esc(r.created_at)} · ${esc(r.source)} · ${esc(t('runRecords',{n:r.summary.records}))}</summary><pre>${esc(JSON.stringify(r.summary,null,2))}</pre></details>`).join('')}</article>`;}
async function review(){const el=$('#review');el.innerHTML=`<p>${t('loadingReview')}</p>`;try{state.reviewData=await api('/api/review');renderReview();}catch(e){el.textContent=e.message;}}
function setLanguage(lang){
  if (!LANGS.includes(lang)) return;
  state.lang = lang;
  try { localStorage.setItem(LANG_KEY, lang); } catch {}
  applyTranslations();
  tray();
  if (state.route === '/methodology' && state.cfg) methodology();
  else if (state.route === '/review' && state.reviewData) renderReview();
  else render();
  if ($('#modal')?.open && state.modalView) modal(state.modalView.render(state.modalView.data));
}
const productsEl = $('#products');
if (productsEl) {
  productsEl.addEventListener('click', e => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const id = btn.closest('[data-id]').dataset.id;
    if (btn.dataset.action === 'detail') return detail(id);
    if (state.selection.has(id)) state.selection.delete(id);
    else {
      if (state.selection.size === 3) {
        status(t('compareMax'));
        return;
      }
      state.selection.set(id, true);
    }
    status();
    tray();
    render();
  });
}

const reviewEl = $('#review');
if (reviewEl) {
  reviewEl.addEventListener('click', e => {
    const b = e.target.closest('[data-detail]');
    if (b) detail(b.dataset.detail);
  });
}

const closeModalBtn = $('#close-modal');
if (closeModalBtn) closeModalBtn.onclick = () => $('#modal')?.close();
const modalEl = $('#modal');
if (modalEl) {
  modalEl.addEventListener('click', e => {
    if (e.target === modalEl) modalEl.close();
  });
}

const compareBtn = $('#compare');
if (compareBtn) compareBtn.onclick = compare;
const clearCompareBtn = $('#clear-compare');
if (clearCompareBtn) clearCompareBtn.onclick = () => { state.selection.clear(); tray(); render(); };
const languageSelect = $('#language-select');
if (languageSelect) languageSelect.onchange = () => setLanguage(languageSelect.value);

let timer;
const searchEl = $('#search');
if (searchEl) {
  searchEl.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.page = 1; load(); }, 220);
  };
}

['brand', 'type', 'stage', 'grain', 'prescription'].forEach(id => {
  const el = $('#' + id);
  if (el) {
    el.onchange = () => { state.page = 1; load(); };
  }
});

const resetBtn = $('#reset');
if (resetBtn) {
  resetBtn.onclick = () => {
    ['search', 'brand', 'type', 'stage'].forEach(id => {
      const el = $('#' + id);
      if (el) el.value = '';
    });
    ['grain', 'prescription'].forEach(id => {
      const el = $('#' + id);
      if (el) el.checked = false;
    });
    state.page = 1;
    load();
  };
}

const prevBtn = $('#prev');
if (prevBtn) prevBtn.onclick = () => { state.page--; load(); };
const nextBtn = $('#next');
if (nextBtn) nextBtn.onclick = () => { state.page++; load(); };

document.querySelectorAll('[name=lens]').forEach(el => {
  el.onchange = () => {
    state.lenses = new Set([...document.querySelectorAll('[name=lens]:checked')].map(x => x.value));
    render();
  };
});

applyTranslations();

(async () => {
  try {
    const [health, cfg, stats] = await Promise.all([api('/api/health'), api('/api/benchmarks'), api('/api/stats')]);
    state.cfg = cfg;
    state.stats = stats.cohorts;
    state.mode = health.mode;

    const route = ['/methodology', '/review'].includes(location.pathname) ? location.pathname : '/';
    const activeNav = document.querySelector(`[data-nav="${route}"]`);
    if (activeNav) activeNav.classList.add('active');
    state.route = route;

    const guideEl = $('#guide');
    const methodologyEl = $('#methodology');
    const reviewSectionEl = $('#review');
    if (guideEl) guideEl.hidden = route !== '/';
    if (methodologyEl) methodologyEl.hidden = route !== '/methodology';
    if (reviewSectionEl) reviewSectionEl.hidden = route !== '/review';

    applyTranslations();
    if (route === '/methodology') methodology();
    else if (route === '/review') await review();
    else await load();
  } catch (e) {
    status(t('backendError') + e.message);
  }
})();
