// ===== 実務1：仕入れ =====
function score(c){return Math.max(c.profit_jpy,0)*c.match_score*Math.min(1,(c.comps_count||0)/5)}
function sorted(list){
  const k={score:score,profit:c=>c.profit_jpy,roi:c=>c.roi}[sortBy];
  return [...list].sort((a,b)=>(b.is_new-a.is_new)||(k(b)-k(a)));
}
// 見送り・仕入れ済みは一覧から除く
function active(){
  const hide=new Set([...S.dismissed.map(x=>x.item_key),...S.ops.skus.map(x=>x.item_key)]);
  return S.candidates.filter(c=>!hide.has(c.item_key));
}
const passing=()=>active().filter(c=>!c.near_miss);
function filtered(){
  const q=$("#q").value.trim().toLowerCase();
  const base=active().filter(c=>showNear||!c.near_miss);
  const list=base.filter(c=>(!genre||c.genre===genre)&&(!q||(c.mercari_title+" "+c.genre+" "+(c.category||"")).toLowerCase().includes(q)));
  return [...sorted(list.filter(c=>!c.near_miss)),...sorted(list.filter(c=>c.near_miss))];
}
const inQueue=k=>S.queue.some(x=>x.item_key===k);
async function addQ(k){const r=await post("/api/queue",{item_key:k});toast(r.ok?"仕入れキューに追加しました":"追加できませんでした");load()}
async function delQ(k){await fetch("/api/queue?key="+encodeURIComponent(k),{method:"DELETE"});load()}
async function dismiss(k,reason){menuFor=null;const r=await post("/api/dismiss",{item_key:k,reason});toast(r.ok?"見送りました（見送りリストから戻せます）":"見送りできませんでした");load()}
async function undo(kind,k){await fetch(`/api/${kind}?key=`+encodeURIComponent(k),{method:"DELETE"});toast("元に戻しました");load()}
async function bought(k,price){const r=await (await post("/api/purchased",{item_key:k,actual_buy_jpy:price})).json();toast(r.ok?`SKU ${r.sku} を作成しました → 入荷・検品で到着を記録`:"記録できませんでした");load()}

function chips(){
  const s=S.summary; if(!s.finished_at) return "";
  const st=s.stats||{};
  return `<div class="chips">
    <span class="chip">取得 <b>${S.settings?.mercari_source==="api"?"メルカリAPI":"メルカリ検索ページ"}</b></span>
    <span class="chip">走査 <b>${st.scanned??0}</b> → 除外${st.excluded??0}・上限超${st.over_budget??0}・需要不足${st.low_demand??0}・相場なし${st.no_comp??0}・付属品疑い${st.accessory??0}・基準未満${st.below_threshold??0}</span>
    <span class="chip ${s.data_source==="sample"?"sample":""}">データ <b>${s.data_source==="sample"?"サンプル":"実サイト"}</b></span>
    <span class="chip">走査 Mercari <b>${s.mercari_count}</b> / eBay <b>${s.ebay_count}</b></span>
    <span class="chip">為替 <b>¥${s.jpy_per_usd}</b>/USD</span>
    <span class="chip">下限 <b>計画シートのカテゴリ別貢献利益</b>・仕入ROI <b>${pct(s.min_roi)}</b></span>
    <span class="chip">最終スキャン <b>${dtfmt(s.finished_at)}</b></span>
    ${S.next_scan_at?`<span class="chip">次の自動スキャン <b>${dtfmt(new Date(S.next_scan_at*1000).toISOString())}</b></span>`:""}</div>`;
}
function candRows(list,{compact=false}={}){
  if(!list.length) return `<div class="empty">${S.summary.finished_at?"条件を満たす候補はありません（「惜しい候補も表示」で基準未満も見られます）":"まだスキャン結果がありません。「再スキャン」を押してください"}</div>`;
  return `<div class="wide"><table><thead><tr><th>#</th><th>商品</th><th class="num">貢献利益</th><th class="num">仕入ROI</th><th class="num">仕入</th><th class="num">eBay相場・需要</th><th>照合</th>${compact?"":'<th class="num">損益分岐</th>'}<th></th></tr></thead><tbody>`+
  list.map((c,i)=>{
    const k=c.item_key, isOpen=open.has(k);
    return `<tr>
      <td class="meta">${i+1}</td>
      <td class="item"><div class="itemrow">${c.image_url?`<img class="thumb" src="${esc(c.image_url)}" alt="" loading="lazy">`:""}<div>
        <div>${c.is_master?'<span class="tag new">★マスター</span>':""}${c.is_new?'<span class="tag new">NEW</span>':""}${c.near_miss?'<span class="tag warn">基準未満</span>':""}${c.is_auction?'<span class="tag warn">オークション</span>':""}${c.condition?`<span class="tag">${esc(c.condition)}</span>`:""}${c.is_shop?'<span class="tag">ショップ</span>':""}${rotRisk(c)?'<span class="tag warn">回転リスク</span>':""}${restricted(c.category)?'<span class="tag warn">仕入れ抑制中</span>':""}<span class="tag">${esc(c.category||c.genre)}</span></div>
        <a class="title" href="${esc(c.mercari_url)}" target="_blank" rel="noopener">${esc(c.mercari_title)}</a>
        <div class="meta">${esc(c.genre)} ・ ${c.shipping_included?"送料込み":"送料別"} ・ 初回検知 ${dtfmt(c.first_seen)}</div>
        <div class="links"><a href="${esc(c.mercari_url)}" target="_blank" rel="noopener">${isSample()?"メルカリで検索（サンプル）":"メルカリ商品ページ"} ↗</a>
          ${c.ebay_sold_search_url?`<a href="${esc(c.ebay_sold_search_url)}" target="_blank" rel="noopener">eBay落札相場 ↗</a>`:""}
          ${c.ebay_research_url?`<a href="${esc(c.ebay_research_url)}" target="_blank" rel="noopener">Product Research ↗</a>`:""}</div></div></div></td>
      <td class="num ${c.profit_jpy>=(c.min_profit_jpy||0)?"pos":"neg"} big">${yen(c.profit_jpy)}<div class="meta">下限 ${yen(c.min_profit_jpy)}</div>${c.limit_price_jpy!=null?`<div class="meta" title="この価格以下で買えれば下限利益を確保（値下げ交渉の目安）">指値 <b>${yen(c.limit_price_jpy)}</b></div>`:""}</td>
      <td class="num"><b>${pct(c.roi)}</b></td>
      <td class="num neg">${yen(c.buy_price_jpy)}</td>
      <td class="num">${usd(c.market_usd)}<div class="meta"><a href="#" data-o="${esc(k)}">根拠 sold ${c.comps_count}件</a>${c.comps_count<3?' <span class="tag warn">需要薄</span>':""}</div>${demandText(c)}</td>
      <td class="num"><span class="bar"><i style="width:${c.match_score*100}%"></i></span>${(c.match_score*100).toFixed(0)}</td>
      ${compact?"":`<td class="num" style="color:var(--new)">${yen(c.breakeven_buy_jpy)}</td>`}
      <td class="num"><a class="btn buy sm" href="${esc(c.mercari_url)}" target="_blank" rel="noopener">仕入れる</a>
        <button class="btn sm" data-q="${esc(k)}" ${inQueue(k)?"disabled":""}>${inQueue(k)?"キュー済":"キューへ"}</button>
        <button class="btn sm" data-o="${esc(k)}">${isOpen?"閉じる":"内訳"}</button>
        <button class="btn sm ghost" data-master="${esc(k)}">マスター登録</button>
        <span class="menuwrap"><button class="btn sm ghost" data-m="${esc(k)}">仕入れない</button>${menuFor===k?`<div class="menu">${REASONS.map(r=>`<button data-dk="${esc(k)}" data-r="${esc(r)}">${esc(r)}</button>`).join("")}</div>`:""}</span></td></tr>`+
    (isOpen?`<tr class="detail"><td></td><td colspan="${compact?7:8}">${breakdown(c)}</td></tr>`:"")+
    (masterFor===k?`<tr class="detail"><td></td><td colspan="${compact?7:8}">${masterForm(masterDefaults(c))}</td></tr>`:"");
  }).join("")+"</tbody></table></div>";
}
function breakdown(c){
  const row=(l,v)=>`<dt>${l}</dt><dd>${v}</dd>`;
  return `<div class="bd"><dl>
    ${row("eBay想定売価（Sold中央値）",`$${c.sell_price_usd.toFixed(2)}（${yen(c.sell_price_jpy)}）`)}
    ${row("eBay手数料（FVF＋固定＋海外1.35%）",`−$${c.ebay_fee_usd.toFixed(2)}`)}
    ${row("広告費（Promoted）","−"+yen(c.ad_fee_jpy))}
    ${row("米国関税見込み（DDP）","−"+yen(c.duty_jpy))}
    ${row("換金スプレッド","−"+yen(c.fx_spread_jpy))}
    ${row("<b>受取（円）</b>",`<b>${yen(c.received_jpy)}</b>`)}
    ${row("返品・値下げ・為替引当","−"+yen(c.reserve_jpy))}
    ${row("仕入（メルカリ）","−"+yen(c.buy_price_jpy))}
    ${c.mercari_fee_jpy?row("購入手数料","−"+yen(c.mercari_fee_jpy)):""}
    ${row("国際送料","−"+yen(c.shipping_cost_jpy))}
    ${row("梱包資材","−"+yen(c.packing_jpy))}
    ${row("作業費（検品・撮影・出品・発送）","−"+yen(c.labor_jpy))}
    <dt class="tot">貢献利益 / 仕入ROI</dt><dd class="tot">${yen(c.profit_jpy)} / ${pct(c.roi)}</dd>
    ${row("売上高貢献利益率",pct(c.margin,1))}
    ${row("損益分岐の仕入れ上限",yen(c.breakeven_buy_jpy))}</dl>
    <div class="comps"><div class="meta" style="margin-bottom:4px">相場の根拠：eBayで実際に売れた商品（照合上位${c.comps.length}件／全${c.comps_count}件の中央値）${c.comp_note?`・${esc(c.comp_note)}`:""}</div>
      <div class="meta" style="margin-bottom:4px">同じ商品なら ✓、別物なら ✗ を押してください（照合精度の実測と学習に使います。✗は次から比較対象を外します）</div>${c.comps.map(x=>{const lk=c.mercari_title+"\t"+x.url, lb=(S.labels||{})[lk];
      return `<div class="pv" style="margin:2px 0"><a href="${esc(x.url)}" target="_blank" rel="noopener" style="flex:1">$${x.total_usd.toFixed(0)} — ${esc(x.title)}（一致${(x.score*100).toFixed(0)}）↗</a>
        <button class="btn sm ${lb===1?"dark":"ghost"}" data-lab="1" data-ck="${esc(c.item_key)}" data-ct="${esc(x.title)}" data-cu="${esc(x.url)}">✓</button><button class="btn sm ${lb===0?"buy":"ghost"}" data-lab="0" data-ck="${esc(c.item_key)}" data-ct="${esc(x.title)}" data-cu="${esc(x.url)}">✗</button></div>`}).join("")}
      ${demandBlock(c)}
      <div class="meta" style="margin:10px 0 4px">自分の目で確かめる</div>
      <a href="${esc(c.mercari_url)}" target="_blank" rel="noopener">${isSample()?"メルカリで検索（サンプルのため商品ページなし）":"このメルカリ商品ページ"} ↗</a>
      ${c.ebay_sold_search_url?`<a href="${esc(c.ebay_sold_search_url)}" target="_blank" rel="noopener">eBayの落札（Sold）検索結果 ↗</a>`:""}
      ${c.ebay_research_url?`<a href="${esc(c.ebay_research_url)}" target="_blank" rel="noopener">Seller Hub Product Research（旧Terapeak）↗</a>`:""}
      ${c.mercari_similar_url?`<a href="${esc(c.mercari_similar_url)}" target="_blank" rel="noopener">メルカリの同ジャンル出品一覧 ↗</a>`:""}
      <div class="note">関税は「米国の買い手比率 ${pct(S.costs?.us_buyer_share)} × 関税率 ${pct(S.costs?.us_duty_rate)}」の見込み（外部環境ページで要確認）。</div></div></div>`;
}
// ---- マスター（継続仕入れ）登録フォーム
let masterFor=null;
function genreOf(c){return (S.rules.config_genres||[]).find(g=>g.id===c.target_id)||(S.rules.genres||[]).find(g=>g.id===c.target_id)||{}}
function masterDefaults(c){
  const g=genreOf(c);
  // 指値の初期値: 「損益分岐 − カテゴリ下限利益」と「今回の仕入れ値×1.1」の小さい方（この値以下なら下限利益を確保）
  const cap=Math.max(0,Math.min((c.breakeven_buy_jpy||0)-(c.min_profit_jpy||0),Math.round(c.buy_price_jpy*1.1/100)*100));
  return {label:c.mercari_title.slice(0,40),category:c.category||g.category||"",mercari_query:g.mercari_query||c.mercari_title.slice(0,30),
    ebay_query:g.ebay_query||"",max_buy_jpy:cap||c.buy_price_jpy,shipping_jpy:c.shipping_cost_jpy,source_ref:c.item_key}
}
function masterForm(d,{id=null,submit="マスターに登録"}={}){
  const f=(n,l,v,cls="")=>`<label>${l}<input name="${n}" class="${cls}" value="${esc(v??"")}"></label>`;
  return `<div class="form-inline" data-mf ${id?`data-id="${id}"`:""}>${f("label","名前",d.label,"w2")}${f("mercari_query","メルカリ検索語",d.mercari_query,"w2")}${f("ebay_query","eBay検索語（相場）",d.ebay_query,"w2")}
    ${f("max_buy_jpy","指値（仕入れ上限 円）",d.max_buy_jpy)}${f("shipping_jpy","実送料（円）",d.shipping_jpy)}${f("min_profit_jpy","下限利益（空=カテゴリ基準）",d.min_profit_jpy)}
    ${f("exclude","除外語（空白区切り）",d.exclude,"w2")}<label>カテゴリ<select name="category">${(S.plan.categories||[]).map(c=>`<option ${c["カテゴリ"]===d.category?"selected":""}>${esc(c["カテゴリ"])}</option>`).join("")}</select></label>
    <input type="hidden" name="source_ref" value="${esc(d.source_ref||"")}"><button class="btn dark sm" data-msave>${submit}</button></div>
    <div class="meta" style="margin-top:4px">マスター＝「当たり」と分かった商品を、指値と実送料つきで継続監視します。指値以下の新しい出品が出たら候補の先頭に★付きで出し、通知します。</div>`;
}
async function saveMaster(btn){
  const box=btn.closest("[data-mf]"), o={}; box.querySelectorAll("[name]").forEach(i=>o[i.name]=i.value); if(box.dataset.id) o.id=box.dataset.id;
  const r=await (await post("/api/watch",o)).json(); toast(r.message); if(r.ok){masterFor=null; editing=null; load()}
}
function rotRisk(c){const d=c.demand||{};return d.sell60!=null&&c.plan_sell60&&d.sell60<c.plan_sell60}
function restricted(cat){return !!((S.ops.restrictions||{}).categories||{})[cat]}
function demandText(c){const d=c.demand||{};if(!d.sold_30d&&d.sold_30d!==0)return "";
  return `<div class="meta" title="成約の日付と出品中の件数から推定">月${d.sold_30d}件成約${d.est_days!=null?` ・ 約${d.est_days}日で売れる`:""}</div>`}
function demandBlock(c){const d=c.demand||{};if(d.sold_30d==null)return "";
  return `<div class="meta" style="margin:10px 0 4px">回転（eBay）</div><div class="mini">月の成約 <b>${d.sold_30d}件</b>${d.dated?"":"（日付が読めないため90日で按分）"} ・ 出品中 <b>${d.active_count??"—"}件</b> ・ 消化率 <b>${pct(d.str)}</b> ・ 売れるまで <b>${d.est_days!=null?"約"+d.est_days+"日":"—"}</b> ・ 60日以内に売れる見込み <b>${pct(d.sell60)}</b>${c.plan_sell60?`（計画 ${pct(c.plan_sell60)}）`:""}</div>`}
async function label(b){
  const c=[...S.candidates,...(evalRes?.candidate?[evalRes.candidate]:[])].find(x=>x.item_key===b.dataset.ck); if(!c) return;
  const key=c.mercari_title+"\t"+b.dataset.cu, cur=(S.labels||{})[key], lab=+b.dataset.lab;
  const r=await (await post("/api/label",{target_id:c.target_id,mercari_title:c.mercari_title,ebay_title:b.dataset.ct,ebay_url:b.dataset.cu,label:cur===lab?null:lab})).json();
  toast(r.message); load();
}
function bindRows(){
  $$("[data-lab]").forEach(b=>b.onclick=()=>label(b));
  $$("[data-master]").forEach(b=>b.onclick=()=>{masterFor=masterFor===b.dataset.master?null:b.dataset.master;render()});
  $$("[data-msave]").forEach(b=>b.onclick=()=>saveMaster(b));
  $$("[data-q]").forEach(b=>b.onclick=()=>addQ(b.dataset.q));
  $$("[data-o]").forEach(b=>b.onclick=e=>{e.preventDefault();const k=b.dataset.o;open.has(k)?open.delete(k):open.add(k);render()});
  $$("[data-m]").forEach(b=>b.onclick=e=>{e.stopPropagation();menuFor=menuFor===b.dataset.m?null:b.dataset.m;render()});
  $$("[data-dk]").forEach(b=>b.onclick=e=>{e.stopPropagation();dismiss(b.dataset.dk,b.dataset.r)});
  $$("[data-undo]").forEach(b=>b.onclick=()=>undo(b.dataset.kind,b.dataset.undo));
  $$("[data-buy]").forEach(b=>b.onclick=()=>{const inp=document.querySelector(`[data-price="${CSS.escape(b.dataset.buy)}"]`);bought(b.dataset.buy,inp?inp.value:null)});
  const nm=$("#nearChk"); if(nm) nm.onchange=()=>{showNear=nm.checked;localSet("near",showNear?"1":"0");render()};
  $$("[data-sort]").forEach(b=>b.onclick=()=>{sortBy=b.dataset.sort;render()});
  const g=$("#genreSel"); if(g) g.onchange=()=>{genre=g.value;render()};
}
function sortSeg(){
  return `<div class="seg">${[["score","総合"],["profit","貢献利益"],["roi","仕入ROI"]].map(([k,l])=>`<button data-sort="${k}" class="${sortBy===k?"on":""}">${l}</button>`).join("")}</div>`;
}

// ---- URLを貼って判定
let evalRes=null, evalBusy=false;
function evalBox(){
  const g=[...(S.rules.config_genres||[]),...(S.watch||[]).filter(w=>w.active).map(w=>({id:"m"+w.id,name:"★"+w.label}))];
  const r=evalRes, c=r?.candidate;
  const vcls={"仕入れ候補":"ok","条件付き":"warn","見送り":"alert","相場なし":"na"}[r?.verdict]||"na";
  return `<div class="card" style="padding:14px 18px;margin:12px 0">
    <div class="form-inline"><label style="flex:1">メルカリの商品URLを貼って判定（アプリの新着通知で気になった出品など）<input id="evalUrl" class="w2" style="width:100%" placeholder="https://jp.mercari.com/item/m..." value="${esc(r?.url||"")}"></label>
      <label>ジャンル<select id="evalTarget"><option value="">自動で判定</option>${g.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</select></label>
      <button class="btn dark" id="evalBtn" ${evalBusy?"disabled":""}>${evalBusy?'<span class="spin"></span>判定中…':"判定する"}</button></div>
    ${r?(r.ok===false?`<div class="note">${esc(r.message)}</div>`:`<div style="margin-top:10px"><span class="badge ${vcls}">${esc(r.verdict)}</span> <b>${esc(r.target||"")}</b>
      ${r.ebay_at?`<span class="meta">（相場は ${dtfmt(r.ebay_at)} 取得の成約データ）</span>`:""}
      ${(r.reasons||[]).length?`<ul style="margin:6px 0 0;padding-left:18px">${r.reasons.map(x=>`<li class="mini">${esc(x)}</li>`).join("")}</ul>`:""}
      ${r.message?`<div class="note">${esc(r.message)}</div>`:""}</div>
      ${c?`<div style="margin-top:8px">${candRows([c],{compact:false})}</div>`:""}`):""}</div>`;
}
async function runEval(){
  const url=$("#evalUrl").value.trim(); if(!url){toast("URLを貼ってください");return}
  evalBusy=true; evalRes=null; render();
  try{evalRes=await (await post("/api/evaluate",{url,target_id:$("#evalTarget").value})).json(); evalRes.url=url}catch(e){evalRes={ok:false,message:"判定できませんでした"}}
  evalBusy=false; if(evalRes.candidate) open.add(evalRes.candidate.item_key); load();
}

VIEWS.cands=["実務1 仕入れ / 仕入れ候補",()=>{
  const genres=[...new Set(active().map(c=>c.genre))], nearN=active().filter(c=>c.near_miss).length;
  const notes=(S.summary.notes||[]);
  return h1("貢献利益順に並んだ、今日ポチる候補。",
    "総合＝貢献利益×照合度×需要（成約件数）。意思決定ログ「ROIだけでなく利益額・売却確率・在庫日数を重視」に合わせています。下限利益は計画シートのカテゴリ別基準です。")
  + (S.settings?.ebay_fetch==="manual"&&(S.ebay_status||[]).some(x=>!x.fresh)?`<div class="note">eBayの相場が未取り込み・期限切れのジャンルが <b>${(S.ebay_status||[]).filter(x=>!x.fresh).length}件</b> あります。そのジャンルの候補は出ません → <a href="#" data-go="ebayimp">eBay相場の取り込み</a></div>`:"")
  + evalBox()
  + chips()
  + (S.mercari_pause?.active?`<div class="note"><b>メルカリの取得を一時停止中</b>（${dtfmt(S.mercari_pause.until)} まで）：${esc(S.mercari_pause.reason||"")}。<a href="#" data-go="rules">ルール・前提</a>から再開できます。</div>`:"")
  + (notes.some(n=>/ログイン|拒否|captcha/.test(n.message))||S.logging_in?`<div class="card notes" style="margin:10px 0">${loginBox()}</div>`:"")
  + `<div class="toolbar" style="margin-top:14px">${sortSeg()}
    <select id="genreSel"><option value="">すべてのジャンル</option>${genres.map(g=>`<option ${g===genre?"selected":""}>${esc(g)}</option>`).join("")}</select>
    <label class="chk"><input type="checkbox" id="nearChk" ${showNear?"checked":""}> 惜しい候補も表示（基準未満だが黒字：${nearN}件）</label></div>
  <div class="card">${candRows(filtered())}</div>`;
},()=>{const b=$("#evalBtn"); if(b) b.onclick=runEval; const u=$("#evalUrl"); if(u) u.onkeydown=e=>{if(e.key==="Enter")runEval()}}];

const PALETTE=["#3b4a8c","#2f7a4f","#b07a12","#b3412f","#6b5ca5","#2c7f86"];
VIEWS.map=["実務1 仕入れ / 収益機会マップ",()=>{
  const c=passing(), groups=[...new Set(c.map(x=>x.category||x.genre))];
  const W=900,H=420,L=56,B=40,T=20,R=20;
  const maxX=Math.max(...c.map(x=>x.buy_price_jpy),10000)*1.1, maxY=Math.max(...c.map(x=>x.roi),.5)*1.15, maxP=Math.max(...c.map(x=>x.profit_jpy),1);
  const X=v=>L+v/maxX*(W-L-R), Y=v=>H-B-v/maxY*(H-B-T);
  let grid="";
  for(let i=0;i<=5;i++){const y=maxY*i/5;grid+=`<line x1="${L}" x2="${W-R}" y1="${Y(y)}" y2="${Y(y)}" stroke="var(--line)"/><text x="${L-8}" y="${Y(y)+3}" text-anchor="end">${(y*100).toFixed(0)}%</text>`;
    const x=maxX*i/5;grid+=`<line y1="${T}" y2="${H-B}" x1="${X(x)}" x2="${X(x)}" stroke="var(--line)"/><text x="${X(x)}" y="${H-B+16}" text-anchor="middle">${(x/1000).toFixed(0)}K</text>`}
  const dots=c.map((x,i)=>{const r=6+Math.sqrt(Math.max(x.profit_jpy,0)/maxP)*26, col=PALETTE[groups.indexOf(x.category||x.genre)%PALETTE.length];
    return `<circle data-i="${i}" cx="${X(x.buy_price_jpy)}" cy="${Y(Math.max(x.roi,0))}" r="${r}" fill="${col}" fill-opacity=".22" stroke="${col}" stroke-width="1.2" style="cursor:pointer"/>`}).join("");
  return h1("資金 × 利益率 × 利益額で見る、狙い所。","横軸に仕入価格、縦軸に仕入ROI、円の大きさが貢献利益。色はカテゴリ。円をクリックすると候補一覧で内訳を開きます。")
  + `<div class="card" style="padding:16px"><svg viewBox="0 0 ${W} ${H}" width="100%" id="mapSvg">${grid}
    <text x="${W-R}" y="${H-6}" text-anchor="end">仕入価格（¥）→</text><text x="${L}" y="${T-6}">↑ 仕入ROI</text>${dots}</svg>
    <div class="chips" style="margin-top:8px">${groups.map((g,i)=>`<span class="chip"><span style="color:${PALETTE[i%PALETTE.length]}">●</span> ${esc(g)}</span>`).join("")}</div></div>`;
},()=>{
  const tip=$("#tip");
  $$("#mapSvg circle").forEach(el=>{
    const c=passing()[+el.dataset.i];
    el.onmousemove=e=>{tip.style.display="block";tip.style.left=e.clientX+14+"px";tip.style.top=e.clientY+14+"px";
      tip.innerHTML=`<b>${esc(c.mercari_title)}</b><br>仕入 ${yen(c.buy_price_jpy)} ・ 貢献利益 ${yen(c.profit_jpy)} ・ ROI ${pct(c.roi)}`};
    el.onmouseleave=()=>tip.style.display="none";
    el.onclick=()=>{tip.style.display="none";open.add(c.item_key);go("cands")};
  });
}];

VIEWS.queue=["実務1 仕入れ / 仕入れキュー",()=>{
  const q=S.queue, cost=q.reduce((a,x)=>a+x.buy_price_jpy,0), prof=q.reduce((a,x)=>a+x.profit_jpy,0);
  // 資金配分ルール：キュー分を足したときのカテゴリ比率
  const inv=S.ops.inventory?.cost_jpy||0, byCat={};
  Object.entries(S.ops.categories||{}).forEach(([k,v])=>byCat[k]=v.inventory_jpy);
  q.forEach(x=>byCat[x.category||"未分類"]=(byCat[x.category||"未分類"]||0)+x.buy_price_jpy);
  const total=inv+cost, over=Object.entries(byCat).filter(([,v])=>total&&v/total>.4);
  const rs=S.ops.restrictions||{}, rq=q.filter(x=>restricted(x.category));
  return h1(`今、ポチる${q.length}件。`,"メルカリで購入できたら（購入はスマホのモバイル回線で）、実際の価格を入れて「仕入れ済みにする」を押してください。SKUが採番され、「入荷・検品」に移ります。")
  + ((rs.global||[]).length?`<div class="note"><b>全体の仕入れ制限（リスク管理の自動対応）</b><br>${rs.global.map(esc).join("<br>")}</div>`:"")
  + (rq.length?`<div class="note"><b>仕入れ抑制中のカテゴリの商品が${rq.length}件あります</b><br>${Object.entries(rs.categories||{}).map(([k,v])=>`${esc(k)}：${v.map(esc).join("・")}`).join("<br>")}</div>`:"")
  + (over.length&&total>200000?`<div class="note">資金配分ルール：キューを全部買うと「${over.map(([k,v])=>`${esc(k)} ${pct(v/total)}`).join("・")}」となり、1カテゴリ上限40%を超えます。</div>`:"")
  + `<div class="card">${q.length?`<div class="wide"><table><thead><tr><th>#</th><th>アイテム</th><th class="num">仕入</th><th class="num">貢献利益</th><th class="num">ROI</th><th class="num">損益分岐</th><th></th></tr></thead><tbody>${q.map((x,i)=>`<tr>
      <td class="meta">·${String(i+1).padStart(2,"0")}</td><td class="item"><span class="tag">${esc(x.category||x.genre)}</span><a class="title" href="${esc(x.mercari_url)}" target="_blank" rel="noopener">${esc(x.mercari_title)}</a></td>
      <td class="num">${yen(x.buy_price_jpy)}</td><td class="num ${x.profit_jpy>=0?"pos":"neg"}">${yen(x.profit_jpy)}</td><td class="num">${pct(x.roi)}</td><td class="num">${yen(x.breakeven_buy_jpy)}</td>
      <td class="num"><a class="btn buy sm" href="${esc(x.mercari_url)}" target="_blank" rel="noopener">メルカリで買う</a>
        <span class="meta">実際の価格</span><input class="price" type="number" data-price="${esc(x.item_key)}" value="${x.buy_price_jpy}">
        <button class="btn dark sm" data-buy="${esc(x.item_key)}">仕入れ済みにする</button>
        <button class="btn sm" data-d="${esc(x.item_key)}">外す</button></td></tr>`).join("")}</tbody></table></div>
    <div class="foot"><div><span class="meta">TOTAL</span><div class="sum">${yen(cost)} <span class="meta">→ 貢献利益 ${yen(prof)}</span></div></div><a class="btn" href="/api/queue.csv">CSV 書き出し</a></div>`
    :'<div class="empty">仕入れキューは空です。「仕入れ候補」から追加してください。</div>'}</div>`;
},()=>{$$("[data-d]").forEach(b=>b.onclick=()=>delQ(b.dataset.d))}];

VIEWS.dismissed=["実務1 仕入れ / 見送りリスト",()=>{
  const d=S.dismissed, by={};
  d.forEach(x=>by[x.reason]=(by[x.reason]||0)+1);
  return h1(`見送った${d.length}件。`,"「仕入れない」を押した出品は、次回以降のスキャンでも候補に出ません。「商品違い（マッチ誤り）」は照合精度の改善データになります（タスクS15）。")
  + `<div class="chips">${Object.entries(by).map(([r,n])=>`<span class="chip">${esc(r)} <b>${n}</b></span>`).join("")}</div>
  <div class="card" style="margin-top:10px">${d.length?`<div class="wide"><table><thead><tr><th>見送り日</th><th>アイテム</th><th>理由</th><th class="num">仕入</th><th class="num">貢献利益</th><th></th></tr></thead><tbody>${d.map(x=>`<tr>
      <td class="meta">${dfmt(x.dismissed_at)}</td>
      <td class="item"><span class="tag">${esc(x.category||x.genre)}</span><a class="title" href="${esc(x.mercari_url)}" target="_blank" rel="noopener">${esc(x.mercari_title)}</a></td>
      <td>${esc(x.reason)}</td><td class="num">${yen(x.buy_price_jpy)}</td><td class="num">${yen(x.profit_jpy)}</td>
      <td class="num"><button class="btn sm" data-undo="${esc(x.item_key)}" data-kind="dismiss">候補に戻す</button></td></tr>`).join("")}</tbody></table></div>`
    :'<div class="empty">見送った出品はありません。</div>'}</div>`;
}];

// メルカリの検索URL（販売中・指値以下・新着順）。スマホで開いて「検索条件を保存」すると公式の新着通知が届く
function savedSearchUrl(x){
  const q=new URLSearchParams({keyword:x.mercari_query,status:"on_sale",sort:"created_time",order:"desc"});
  if(x.max_buy_jpy) q.set("price_max",x.max_buy_jpy);
  if(x.exclude) q.set("exclude_keyword",x.exclude);
  return "https://jp.mercari.com/search?"+q.toString();
}
VIEWS.master=["実務1 仕入れ / マスター（継続仕入れ）",()=>{
  const w=S.watch||[], hits=id=>S.candidates.filter(c=>c.target_id==="m"+id&&!c.near_miss);
  return h1("当たり商品を、指値で待ち伏せする。","一度仕入れて利益が出た商品（または確実に利益が出る型番）を、指値と実送料つきで登録します。自動スキャンのたびに監視し、指値以下の新着が出たら候補の先頭に★付きで出して通知します（0001_sedoriの「マスター登録→定点監視」を統合）。")
  + `<div class="chips"><span class="chip">登録 <b>${w.length}</b></span><span class="chip">監視中 <b>${w.filter(x=>x.active).length}</b></span>
     <span class="chip">通知 <b>${S.settings?.notify_configured?"Slack設定済み":"未設定（ルール・前提で設定）"}</b></span></div>`
  + h2("· 01","登録済みマスター")
  + (w.length?`<div class="card"><div class="wide"><table><thead><tr><th>名前・検索語</th><th class="num">指値</th><th class="num">実送料</th><th class="num">下限利益</th><th class="num">今のヒット</th><th>状態</th><th></th></tr></thead><tbody>
    ${w.map(x=>`<tr><td class="wrap"><b>${esc(x.label)}</b><div class="meta">${esc(x.category||"")} ・ メルカリ「${esc(x.mercari_query)}」 ・ eBay「${esc(x.ebay_query)}」${x.exclude?` ・ 除外「${esc(x.exclude)}」`:""}</div>
      <div class="mini"><a href="${esc(savedSearchUrl(x))}" target="_blank" rel="noopener">メルカリで同じ条件を開く ↗</a> <span class="meta">→ アプリなら検索結果の「検索条件を保存」で新着通知</span></div></td>
      <td class="num">${yen(x.max_buy_jpy)}</td><td class="num">${yen(x.shipping_jpy)}</td><td class="num">${x.min_profit_jpy!=null?yen(x.min_profit_jpy):"カテゴリ基準"}</td>
      <td class="num">${hits(x.id).length?`<a href="#" data-go="cands"><b>${hits(x.id).length}件</b></a>`:"0"}</td>
      <td>${x.active?'<span class="badge ok">監視中</span>':'<span class="badge na">停止</span>'}</td>
      <td class="num"><button class="btn sm" data-mtoggle="${x.id}" data-on="${x.active?0:1}">${x.active?"停止":"再開"}</button> <button class="btn sm ghost" data-edit="w${x.id}">編集</button> <button class="btn sm ghost" data-mdel="${x.id}">削除</button></td></tr>`
      +(editing==="w"+x.id?`<tr class="detail"><td colspan="7">${masterForm(x,{id:x.id,submit:"保存"})}</td></tr>`:"")).join("")}
    </tbody></table></div></div>`:`<div class="card"><div class="empty">まだ登録がありません。仕入れ候補の「マスター登録」か、着金済みSKUの「マスター登録」から追加できます。</div></div>`)
  + h2("· 02","手動で登録")+`<div class="card" style="padding:14px 18px">${masterForm({label:"",mercari_query:"",ebay_query:"",max_buy_jpy:"",shipping_jpy:"",category:"カメラ・交換レンズ"})}</div>`;
},()=>{
  $$("[data-mtoggle]").forEach(b=>b.onclick=async()=>{await post("/api/watch",{id:b.dataset.mtoggle,active:b.dataset.on});load()});
  $$("[data-mdel]").forEach(b=>b.onclick=async()=>{if(!confirm("このマスターを削除しますか？"))return;await fetch("/api/watch?id="+b.dataset.mdel,{method:"DELETE"});load()});
}];


// ---- eBay相場の取り込み（普段のChromeから送る）
const BOOKMARKLET="javascript:(()=>{const O='http://127.0.0.1:8000';if(!/LH_Sold=1/.test(location.search)){alert('eBayの「売れた商品」の検索結果ページで押してください');return}const w=window.open(O+'/#ebay','arbiter');if(!w){alert('ポップアップを許可してください');return}const d={type:'arbiter-ebay-page',html:document.documentElement.outerHTML,url:location.href};let n=0;const iv=setInterval(()=>{n++;try{w.postMessage(d,O)}catch(e){}if(n>50)clearInterval(iv)},300);window.addEventListener('message',e=>{if(e.data&&e.data.type==='arbiter-ack')clearInterval(iv)})})()";
VIEWS.ebayimp=["実務1 仕入れ / eBay相場の取り込み",()=>{
  const st=S.ebay_status||[], stale=st.filter(x=>!x.fresh), r=ebayLast;
  return h1("eBayの相場は、普段のChromeから送る。",`ツールはeBayに自動でアクセスしません（自動操作として拒否され、セラーアカウントにリスクがあるため）。ログイン済みの普段のChromeで「売れた商品」のページを開き、ブックマークの「Arbiterへ送る」を押すと、そのジャンルの相場として取り込みます。相場は${S.settings?.ebay_manual_days||7}日間使い、期限が切れたら更新します。`)
  + `<div class="card" style="padding:14px 18px"><b>最初に1回だけ：ブックマークを登録</b>
      <div class="pv" style="margin-top:8px"><a class="btn dark" href="${esc(BOOKMARKLET)}" onclick="event.preventDefault();toast('このボタンをブックマークバーまでドラッグしてください')">Arbiterへ送る</a>
      <span class="meta">← このボタンを、Chromeのブックマークバーへドラッグして登録します（ブックマークバーが出ていなければ ⌘⇧B）。</span></div>
      <div class="meta" style="margin-top:6px">使い方：下の「売れた商品を開く」→ 普段のChromeでページが開く → 一覧が表示されたらブックマークの「Arbiterへ送る」を押す → この画面に結果が出ます。</div></div>`
  + (r?`<div class="note"><b>${r.ok?"取り込みました":"取り込めませんでした"}</b>：${esc(r.message)}
      ${r.need_target?`<div class="form-inline" style="margin-top:6px"><select id="impTarget">${(r.genres||[]).map(g=>`<option value="${esc(g.id)}">${esc(g.name)}</option>`).join("")}</select><button class="btn dark sm" id="impRetry">このジャンルとして取り込む</button></div>`:""}</div>`:"")
  + h2("· 01","ジャンルごとの取り込み状況",`期限切れ・未取り込み ${stale.length} / ${st.length}件`)
  + `<div class="card"><div class="wide"><table><thead><tr><th>ジャンル</th><th>eBay検索語</th><th class="num">件数</th><th>最終取り込み</th><th>状態</th><th></th></tr></thead><tbody>
    ${st.map(x=>`<tr><td><b>${esc(x.name)}</b><div class="meta">${esc(x.category||"")}</div></td><td class="mini">${esc(x.ebay_query)}</td><td class="num">${x.count||"—"}</td>
      <td class="mini">${x.captured_at?dtfmt(x.captured_at)+`（${x.age_days}日前）`:"—"}</td>
      <td>${x.fresh?'<span class="badge ok">使用中</span>':x.captured_at?'<span class="badge warn">期限切れ</span>':'<span class="badge alert">未取り込み</span>'}</td>
      <td class="num"><a class="btn sm ${x.fresh?"":"dark"}" href="${esc(x.sold_url)}" target="_blank" rel="noopener">売れた商品を開く ↗</a></td></tr>`).join("")}
    </tbody></table></div></div>`
  + h2("· 02","ブックマークが使えないとき","ページを保存（⌘S・「ウェブページ、完全」ではなく「HTMLのみ」）してアップロード")
  + `<div class="card" style="padding:14px 18px"><div class="form-inline"><input type="file" id="impFile" accept=".html,.htm"><label>ジャンル<select id="impFileTarget"><option value="">検索語から自動判定</option>${st.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</select></label><label>ページのURL（自動判定用・任意）<input id="impUrl" class="w2" placeholder="https://www.ebay.com/sch/i.html?_nkw=...&LH_Sold=1"></label><button class="btn" id="impUpload">取り込む</button></div></div>`;
},()=>{
  const rt=$("#impRetry"); if(rt) rt.onclick=async()=>{const t=$("#impTarget").value; const r=await (await post("/api/ebay-page",{html:ebayLast.html,url:ebayLast.url,target_id:t})).json(); ebayLast={...r,html:ebayLast.html,url:ebayLast.url}; toast(r.message); load()};
  const up=$("#impUpload"); if(up) up.onclick=()=>{const f=$("#impFile").files[0]; if(!f){toast("HTMLファイルを選んでください");return}
    const rd=new FileReader(); rd.onload=async()=>{const t=$("#impFileTarget").value; const url=$("#impUrl").value||((S.ebay_status||[]).find(x=>x.id===t)||{}).sold_url||"LH_Sold=1";
      const r=await (await post("/api/ebay-page",{html:rd.result,url,target_id:t})).json(); ebayLast={...r,html:rd.result,url}; toast(r.message); load()}; rd.readAsText(f)};
}];
