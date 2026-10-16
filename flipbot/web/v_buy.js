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
  return `<div class="chips">
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
        <div>${c.is_new?'<span class="tag new">NEW</span>':""}${c.near_miss?'<span class="tag warn">基準未満</span>':""}${c.is_auction?'<span class="tag warn">オークション</span>':""}<span class="tag">${esc(c.category||c.genre)}</span></div>
        <a class="title" href="${esc(c.mercari_url)}" target="_blank" rel="noopener">${esc(c.mercari_title)}</a>
        <div class="meta">${esc(c.genre)} ・ ${c.shipping_included?"送料込み":"送料別"} ・ 初回検知 ${dtfmt(c.first_seen)}</div>
        <div class="links"><a href="${esc(c.mercari_url)}" target="_blank" rel="noopener">${isSample()?"メルカリで検索（サンプル）":"メルカリ商品ページ"} ↗</a>
          ${c.ebay_sold_search_url?`<a href="${esc(c.ebay_sold_search_url)}" target="_blank" rel="noopener">eBay落札相場 ↗</a>`:""}
          ${c.ebay_research_url?`<a href="${esc(c.ebay_research_url)}" target="_blank" rel="noopener">Product Research ↗</a>`:""}</div></div></div></td>
      <td class="num ${c.profit_jpy>=(c.min_profit_jpy||0)?"pos":"neg"} big">${yen(c.profit_jpy)}<div class="meta">下限 ${yen(c.min_profit_jpy)}</div></td>
      <td class="num"><b>${pct(c.roi)}</b></td>
      <td class="num neg">${yen(c.buy_price_jpy)}</td>
      <td class="num">${usd(c.market_usd)}<div class="meta"><a href="#" data-o="${esc(k)}">根拠 sold ${c.comps_count}件</a>${c.comps_count<3?' <span class="tag warn">需要薄</span>':""}</div></td>
      <td class="num"><span class="bar"><i style="width:${c.match_score*100}%"></i></span>${(c.match_score*100).toFixed(0)}</td>
      ${compact?"":`<td class="num" style="color:var(--new)">${yen(c.breakeven_buy_jpy)}</td>`}
      <td class="num"><a class="btn buy sm" href="${esc(c.mercari_url)}" target="_blank" rel="noopener">仕入れる</a>
        <button class="btn sm" data-q="${esc(k)}" ${inQueue(k)?"disabled":""}>${inQueue(k)?"キュー済":"キューへ"}</button>
        <button class="btn sm" data-o="${esc(k)}">${isOpen?"閉じる":"内訳"}</button>
        <span class="menuwrap"><button class="btn sm ghost" data-m="${esc(k)}">仕入れない</button>${menuFor===k?`<div class="menu">${REASONS.map(r=>`<button data-dk="${esc(k)}" data-r="${esc(r)}">${esc(r)}</button>`).join("")}</div>`:""}</span></td></tr>`+
    (isOpen?`<tr class="detail"><td></td><td colspan="${compact?7:8}">${breakdown(c)}</td></tr>`:"");
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
    <div class="comps"><div class="meta" style="margin-bottom:4px">相場の根拠：eBayで実際に売れた商品（照合上位${c.comps.length}件／全${c.comps_count}件の中央値）</div>${c.comps.map(x=>
      `<a href="${esc(x.url)}" target="_blank" rel="noopener">$${x.total_usd.toFixed(0)} — ${esc(x.title)}（一致${(x.score*100).toFixed(0)}）↗</a>`).join("")}
      <div class="meta" style="margin:10px 0 4px">自分の目で確かめる</div>
      <a href="${esc(c.mercari_url)}" target="_blank" rel="noopener">${isSample()?"メルカリで検索（サンプルのため商品ページなし）":"このメルカリ商品ページ"} ↗</a>
      ${c.ebay_sold_search_url?`<a href="${esc(c.ebay_sold_search_url)}" target="_blank" rel="noopener">eBayの落札（Sold）検索結果 ↗</a>`:""}
      ${c.ebay_research_url?`<a href="${esc(c.ebay_research_url)}" target="_blank" rel="noopener">Seller Hub Product Research（旧Terapeak）↗</a>`:""}
      ${c.mercari_similar_url?`<a href="${esc(c.mercari_similar_url)}" target="_blank" rel="noopener">メルカリの同ジャンル出品一覧 ↗</a>`:""}
      <div class="note">関税は「米国の買い手比率 ${pct(S.costs?.us_buyer_share)} × 関税率 ${pct(S.costs?.us_duty_rate)}」の見込み（外部環境ページで要確認）。</div></div></div>`;
}
function bindRows(){
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

VIEWS.cands=["実務1 仕入れ / 仕入れ候補",()=>{
  const genres=[...new Set(active().map(c=>c.genre))], nearN=active().filter(c=>c.near_miss).length;
  const notes=(S.summary.notes||[]);
  return h1("貢献利益順に並んだ、今日ポチる候補。",
    "総合＝貢献利益×照合度×需要（成約件数）。意思決定ログ「ROIだけでなく利益額・売却確率・在庫日数を重視」に合わせています。下限利益は計画シートのカテゴリ別基準です。")
  + chips()
  + (notes.some(n=>/ログイン|拒否|captcha/.test(n.message))||S.logging_in?`<div class="card notes" style="margin:10px 0">${loginBox()}</div>`:"")
  + `<div class="toolbar" style="margin-top:14px">${sortSeg()}
    <select id="genreSel"><option value="">すべてのジャンル</option>${genres.map(g=>`<option ${g===genre?"selected":""}>${esc(g)}</option>`).join("")}</select>
    <label class="chk"><input type="checkbox" id="nearChk" ${showNear?"checked":""}> 惜しい候補も表示（基準未満だが黒字：${nearN}件）</label></div>
  <div class="card">${candRows(filtered())}</div>`;
}];

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
  return h1(`今、ポチる${q.length}件。`,"メルカリで購入できたら、実際の価格を入れて「仕入れ済みにする」を押してください。SKUが採番され、「入荷・検品」に移ります。")
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
