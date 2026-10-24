// ===== 共通: 状態・メニュー・操作・部品 =====
// メニューは「上流（戦略・計画）→ 実務（仕入れ → 入荷・出品・在庫 → 販売・発送 → 着金・精算）」の工程順
const NAV=[
  {sec:"上流｜戦略・計画",lane:"up",items:[
    ["dash","経営ダッシュボード"],["env","外部環境"],["strategy","戦略・カテゴリ"],["kpi","KPI・ファネル"],["tasks","計画タスク","tasks"],["sim","資本シミュレーター"]]},
  {sec:"実務 1｜仕入れ",lane:"ops",items:[
    ["ebayimp","eBay相場の取り込み","ebaystale"],["cands","仕入れ候補","cands"],["map","収益機会マップ"],["queue","仕入れキュー","queue"],["master","マスター（継続仕入れ）","master"],["dismissed","見送りリスト","dismissed"]]},
  {sec:"実務 2｜入荷・出品・在庫",lane:"ops",items:[
    ["inbound","入荷・検品","inbound"],["listing","出品","ready"],["stock","在庫・値付け","listed"]]},
  {sec:"実務 3｜販売・発送",lane:"ops",items:[["ship","発送","sold"]]},
  {sec:"実務 4｜着金・精算",lane:"ops",items:[["settle","着金・精算","shipped"],["ledger","SKU台帳"]]},
  {sec:"設定",items:[["rules","ルール・前提"]]},
];
let S={candidates:[],queue:[],dismissed:[],purchases:[],summary:{},history:[],rules:{},ops:{skus:[],counts:{},months:[],funnel:[],risks:[],gates:[]},plan:{},env:{}};
let view=localGet("view")||"dash", sortBy="score", genre="", open=new Set(), sim=null, showNear=localGet("near")==="1", menuFor=null, editing=null;
const REASONS=["価格が高い","状態が悪い・リスク","商品違い（マッチ誤り）","相場が怪しい","カテゴリ上限・資金","その他"];
const DEFECTS=["説明と違う状態","動作不良","カビ・曇り・傷","付属品欠品","商品違い（型番・版）","偽物の疑い","その他"];
const RETURNS=["説明と違う（状態）","動作不良","商品違い","輸送破損","買い手都合","関税・受取拒否","その他"];
const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const num=v=>typeof v==="number"&&isFinite(v);
const yen=n=>num(n)?"¥"+Math.round(n).toLocaleString("ja-JP"):"—";
const usd=n=>num(n)?"$"+(+n).toFixed(0):"—";
const manFmt=n=>num(n)?(+n).toLocaleString("ja-JP",{maximumFractionDigits:1})+"万":"—";
const man=n=>n>=1e8?(n/1e8).toFixed(1)+"億":(n/1e4).toFixed(0)+"万";
const pct=(r,d=0)=>num(r)?(r*100).toFixed(d)+"%":"—";
const dfmt=s=>s?new Date(s).toLocaleDateString("ja-JP",{month:"numeric",day:"numeric"}):"—";
const dtfmt=s=>s?new Date(s).toLocaleString("ja-JP",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}):"—";
function localGet(k){try{return localStorage.getItem(k)}catch(e){return null}}
function localSet(k,v){try{localStorage.setItem(k,v)}catch(e){}}
function toast(m){const t=$("#toast");t.textContent=m;t.classList.add("show");setTimeout(()=>t.classList.remove("show"),2200)}
async function post(url,body){return fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})}
const skus=st=>S.ops.skus.filter(x=>!st||(Array.isArray(st)?st.includes(x.status):x.status===st));
const isSample=()=>S.summary.data_source==="sample";
function go(v){view=v;localSet("view",v);render();scrollTo(0,0)}

// ---- 読み込み
async function load(){
  try{const r=await fetch("/api/state"); S=await r.json();}catch(e){toast("サーバーに接続できません");return}
  const b=$("#scanBtn"), busy=S.scanning||S.logging_in; b.disabled=busy;
  b.innerHTML=S.scanning?'<span class="spin"></span>スキャン中…':S.logging_in?'<span class="spin"></span>eBayログイン待ち…':"再スキャン";
  render();
  if(busy) setTimeout(load,2500);
}
async function scan(){await fetch("/api/scan",{method:"POST"});toast("スキャンを開始しました");setTimeout(load,300)}
async function ebayLogin(){const r=await (await post("/api/ebay-login",{})).json();toast(r.started?"ツール用ブラウザが開きます。eBayにログインしてください":"スキャン中のため、終わってから押してください");setTimeout(load,500)}
async function planSync(){toast("計画シートを読み込み中…");const r=await (await post("/api/plan-sync",{})).json();toast(r.message);load()}
async function skuAct(sku,action,fields={}){
  const r=await (await post("/api/sku",{sku,action,fields})).json();
  toast(r.ok?"更新しました":r.message||"更新できませんでした"); editing=null; load();
}

// ---- メニュー
function navCounts(){
  const c=S.ops.counts||{};
  return {cands:passing().length,queue:S.queue.length,dismissed:S.dismissed.length,
    inbound:(c.ordered||0)+(c.arrived||0),ready:c.ready||0,listed:c.listed||0,sold:c.sold||0,shipped:c.shipped||0,
    tasks:dueTasks().length,ebaystale:(S.ebay_status||[]).filter(x=>!x.fresh).length,master:(S.watch||[]).filter(w=>w.active).length};
}
function renderNav(){
  const cnt=navCounts(), o=S.ops||{};
  const flag={stock:o.reprice_n?`<span class="tag warn" style="margin:0">値下げ${o.reprice_n}</span>`:"",ship:o.ship_overdue_n?`<span class="tag warn" style="margin:0">遅延${o.ship_overdue_n}</span>`:""};
  let step=0;
  $("#nav").innerHTML=NAV.map(g=>`<div class="navsec">${g.lane?`<span class="lane ${g.lane}">${g.lane==="up"?"上流":"実務"}</span>`:""}${esc(g.sec.split("｜")[1]||g.sec)}</div>`+
    g.items.map(([id,label,c])=>`<button class="${view===id?"on":""}" data-v="${id}"><span>${label}</span><span class="cnt">${flag[id]||(c&&cnt[c]?cnt[c]:"")}</span></button>`).join("")).join("");
  $$("#nav button").forEach(b=>b.onclick=()=>go(b.dataset.v));
  void step;
}

// ---- 部品
function h1(title,lead){return `<h1>${title}</h1>${lead?`<p class="lead">${lead}</p>`:""}`}
function h2(no,title,sub){return `<h2><span class="no">${no}</span>${title}${sub?`<span class="sub">${sub}</span>`:""}</h2>`}
// 計画対実績のタイル。better="high"なら実績≧計画で緑
function kpiTile(label,actual,plan,{fmt=v=>v,better="high",tag="",sub=""}={}){
  let cls="", gap="";
  if(num(actual)&&num(plan)&&plan!==0){
    const ok=better==="high"?actual>=plan:actual<=plan; cls=ok?"pos":"neg";
    gap=`<span class="badge ${ok?"ok":"alert"}">${better==="high"?(actual>=plan?"達成":"未達"):(actual<=plan?"良好":"超過")}</span>`;
  }
  return `<div class="kpi"><div class="l">${label}<span>${tag}</span></div>
    <div class="v ${cls}" style="font-size:26px">${num(actual)?fmt(actual):"—"}</div>
    <div class="s">計画 ${num(plan)?fmt(plan):"—"} ${gap}</div>${sub?`<div class="s">${sub}</div>`:""}</div>`;
}
function statusBadge(st){return `<span class="badge ${st==="ok"?"ok":st==="alert"?"alert":"na"}">${st==="ok"?"正常":st==="alert"?"発動":"データ不足"}</span>`}
function bar(v,max,cls=""){const w=num(v)&&max?Math.max(0,Math.min(100,v/max*100)):0;return `<div class="prog"><i class="${cls}" style="width:${w}%"></i></div>`}
function skuCell(x){
  return `<td class="item"><div class="itemrow">${x.image_url?`<img class="thumb" src="${esc(x.image_url)}" alt="" loading="lazy">`:""}<div>
    <div><span class="tag">${esc(x.sku)}</span><span class="tag">${esc(x.category||x.genre||"")}</span></div>
    <a class="title" href="${esc(x.mercari_url)}" target="_blank" rel="noopener">${esc(x.title)}</a>
    <div class="meta">仕入 ${dfmt(x.purchased_at)} ・ 在庫${x.days_in_stock??0}日${x.ebay_url?` ・ <a href="${esc(x.ebay_url)}" target="_blank" rel="noopener">eBay出品 ↗</a>`:""}</div></div></div></td>`;
}
// 工程フォーム: data-f コンテナ内の input[name] を集めて送信
function collect(btn){const f=btn.closest("[data-f]");const o={};f&&f.querySelectorAll("[name]").forEach(i=>{o[i.name]=i.value});return o}
function bindCommon(){
  $$("[data-login]").forEach(b=>b.onclick=ebayLogin);
  $$("[data-sync]").forEach(b=>b.onclick=planSync);
  $$("[data-go]").forEach(b=>b.onclick=e=>{e.preventDefault();go(b.dataset.go)});
  $$("[data-act]").forEach(b=>b.onclick=()=>{
    const a=b.dataset.act, sku=b.dataset.sku;
    if(a==="delete"&&!confirm(`${sku} を台帳から削除しますか？（元に戻せません）`)) return;
    skuAct(sku,a,collect(b));
  });
  $$("[data-edit]").forEach(b=>b.onclick=()=>{editing=editing===b.dataset.edit?null:b.dataset.edit;render()});
}
const VIEWS={};
function render(){
  if(!VIEWS[view]) view="dash";
  renderNav();
  const [crumb,fn,bind]=VIEWS[view];
  $("#crumb").textContent="ARBITER / "+crumb;
  $("#view").innerHTML=fn();
  bindCommon(); bindRows(); bind&&bind();
}
// 普段のChrome（eBayにログイン済み）のブックマークから送られた「売れた商品」ページを受け取る
let ebayRecv=new Set(), ebayLast=null;
window.addEventListener("message",async e=>{
  if(!/^https:\/\/([a-z0-9-]+\.)*ebay\.(com|co\.uk|de|com\.au)$/.test(e.origin)) return;
  const d=e.data; if(!d||d.type!=="arbiter-ebay-page") return;
  try{e.source.postMessage({type:"arbiter-ack"},e.origin)}catch(_){}
  const key=d.url+":"+(d.html||"").length; if(ebayRecv.has(key)) return; ebayRecv.add(key);
  toast("eBayのページを受け取りました。取り込み中…");
  ebayLast=await (await post("/api/ebay-page",{html:d.html,url:d.url})).json(); ebayLast.html=d.html; ebayLast.url=d.url;
  toast(ebayLast.message); go("ebayimp"); load();
});
function boot(){
  $("#scanBtn").onclick=scan;
  if(location.hash==="#ebay") view="ebayimp";
  document.addEventListener("click",()=>{if(menuFor){menuFor=null;render()}});
  $("#q").oninput=()=>{if(!["cands","ledger"].includes(view)) view="cands"; render()};
  load();
  setInterval(()=>{if(!S.scanning&&!S.logging_in&&!editing&&document.visibilityState==="visible"&&!document.activeElement.matches("input,select,textarea")) load()},60000);
}
