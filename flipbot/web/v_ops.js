// ===== 実務2〜4：入荷・検品 → 出品 → 在庫・値付け → 発送 → 着金・精算 =====
const fx=()=>S.fx?.rate||S.summary.jpy_per_usd||155;
const catPlan=c=>(S.plan.categories||[]).find(x=>x["カテゴリ"]===c)||{};
const opt=(list,sel)=>list.map(v=>`<option ${v===sel?"selected":""}>${esc(v)}</option>`).join("");
function emptyCard(msg){return `<div class="card"><div class="empty">${msg}</div></div>`}
function stepHead(no,title,lead,next){
  return h1(title,lead)+(next?`<div class="note">この工程が終わったら → <a href="#" data-go="${next[0]}">${next[1]}</a></div>`:"");
}
function opsTable(head,rows){return `<div class="card"><div class="wide"><table><thead><tr>${head.map(h=>`<th class="${h.startsWith("#")?"num":""}">${h.replace(/^#/,"")}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div></div>`}
// eBay受取の見込み（手数料・広告・換金差引後。関税は別）
function payoutEstimate(usdAmt){
  const e=S.rules.fees?.ebay||{};
  return Math.round((usdAmt*(1-(e.final_value_fee_rate||.1325)-(e.international_fee_rate||.0135)-(e.ad_rate||.02))-(e.payment_fixed_fee_usd||.3))*fx()*(1-(e.payout_fx_spread_rate||.02)));
}

// ---------- 入荷・検品
VIEWS.inbound=["実務2 入荷・出品・在庫 / 入荷・検品",()=>{
  const ord=skus("ordered"), arr=skus("arrived");
  return stepHead("2-1","届いたら記録し、検品する。","メルカリで買った商品の到着を記録し、検品結果を入れます。不合格の理由は誤仕入れ率（照合精度）の計算に使われます（計画：誤仕入れ2%以下）。",["listing","出品へ"])
  + h2("· 01","到着待ち",`${ord.length}件`)
  + (ord.length?opsTable(["商品","#仕入","#予測利益",""],ord.map(x=>`<tr>${skuCell(x)}<td class="num">${yen(x.buy_price_jpy)}</td><td class="num">${yen(x.predicted_profit_jpy)}</td>
      <td class="num"><button class="btn dark sm" data-act="arrive" data-sku="${esc(x.sku)}">到着した</button>
      <button class="btn sm ghost" data-act="delete" data-sku="${esc(x.sku)}">取消</button></td></tr>`)):emptyCard("到着待ちの商品はありません"))
  + h2("· 02","検品待ち",`${arr.length}件。カテゴリ別のチェック項目は計画シート「カテゴリ別採算」の主なリスクから表示`)
  + (arr.length?opsTable(["商品","チェック項目","検品結果",""],arr.map(x=>{const cp=catPlan(x.category);return `<tr>${skuCell(x)}
      <td class="wrap"><b>${esc(cp["主なリスク"]||"状態・付属品・動作")}</b><div class="meta">目標 ${cp["検品目標分"]||"—"}分/件</div></td>
      <td><div class="form-inline" data-f><label>重量(g)<input name="weight_g" type="number" placeholder="梱包後"></label><label>メモ<input name="notes" class="w2" placeholder="状態・付属品"></label></div></td>
      <td class="num"><div data-f><button class="btn dark sm" data-act="inspect_pass" data-sku="${esc(x.sku)}">合格→出品待ち</button></div>
        <div class="form-inline" data-f style="margin-top:6px;justify-content:flex-end"><select name="defect_reason">${opt(DEFECTS)}</select><button class="btn sm" data-act="inspect_fail" data-sku="${esc(x.sku)}">不合格</button></div></td></tr>`})):emptyCard("検品待ちの商品はありません"));
},()=>{
  // 合格ボタンは重量・メモの入力欄（同じ行の左の data-f）も送る
  $$('[data-act="inspect_pass"]').forEach(b=>b.onclick=()=>{const tr=b.closest("tr");const o={};tr.querySelectorAll("[name=weight_g],[name=notes]").forEach(i=>o[i.name]=i.value);skuAct(b.dataset.sku,"inspect_pass",o)});
}];

// ---------- 出品
VIEWS.listing=["実務2 入荷・出品・在庫 / 出品",()=>{
  const r=skus("ready");
  return stepHead("2-2","検品に合格した商品を、eBayに出す。","推奨価格は、相場（Sold中央値）の5%上を初期値にしています（30日売れなければ値下げ提案が出ます）。損益分岐の売価を下回る価格にはしないでください。英語タイトルは、相場の根拠にした成約品のタイトルを参考に表示しています。",["stock","在庫・値付けへ"])
  + (r.length?opsTable(["商品","相場・損益分岐","英語タイトルの参考（成約品）","出品"],r.map(x=>{const rec=x.predicted_sell_usd?Math.round(x.predicted_sell_usd*1.05):"";return `<tr>${skuCell(x)}
      <td class="num">相場 ${usd(x.predicted_sell_usd)}<div class="meta">損益分岐 ${usd(x.floor_usd)}</div><div class="meta">推奨 <b>${usd(rec)}</b></div></td>
      <td class="wrap">${(x.comps||[]).map(c=>`<div class="mini"><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a> <span class="meta">$${(+c.total_usd).toFixed(0)}</span></div>`).join("")||'<span class="meta">—</span>'}
        ${x.ebay_sold_search_url?`<div class="mini"><a href="${esc(x.ebay_sold_search_url)}" target="_blank" rel="noopener">落札相場を開く ↗</a></div>`:""}</td>
      <td><div class="form-inline" data-f><label>出品価格(USD)<input name="list_price_usd" type="number" step="1" value="${rec}"></label><label>eBay出品URL<input name="ebay_url" class="w2" placeholder="https://www.ebay.com/itm/..."></label>
        <button class="btn dark sm" data-act="list" data-sku="${esc(x.sku)}">出品した</button></div>
        <div style="margin-top:6px"><button class="btn sm" data-draft="${esc(x.sku)}">${draftFor===x.sku?"下書きを閉じる":"英語の出品下書きを作る"}</button>
        <span class="meta">eBayの「カスタムラベル（SKU）」に <b>${esc(x.sku)}</b> を入れてください（CSV取り込みで自動反映するため）</span></div></td></tr>`
      +(draftFor===x.sku?`<tr class="detail"><td colspan="4">${draftView(drafts[x.sku])}</td></tr>`:"")})):emptyCard("出品待ちの商品はありません（検品合格すると、ここに出ます）"));
},()=>{
  $$("[data-draft]").forEach(b=>b.onclick=async()=>{const k=b.dataset.draft; if(draftFor===k){draftFor=null;render();return}
    drafts[k]=drafts[k]||await (await fetch("/api/listing-draft?sku="+encodeURIComponent(k))).json(); draftFor=k; render()});
  $$("[data-copy]").forEach(b=>b.onclick=async()=>{const t=document.getElementById(b.dataset.copy).value;try{await navigator.clipboard.writeText(t);toast("コピーしました")}catch(e){prompt("コピーしてください",t)}});
}];
let draftFor=null; const drafts={};
function draftView(d){
  if(!d) return '<span class="meta">作成中…</span>';
  if(d.error) return `<div class="note">${esc(d.error)}</div>`;
  const ta=(id,v,rows)=>`<textarea id="${id}" rows="${rows}" style="width:100%;font:inherit;font-size:12px;border:1px solid var(--line);border-radius:6px;padding:6px;background:var(--bg);color:var(--ink)">${esc(v)}</textarea>`;
  return `<div class="grid2" style="gap:12px"><div><div class="pv"><b>タイトル</b><span class="meta">${d.title_len}/80文字</span><button class="btn sm" data-copy="dt-${esc(d.custom_label)}">コピー</button></div>${ta("dt-"+d.custom_label,d.title,2)}
    <div class="pv" style="margin-top:8px"><b>説明文</b><button class="btn sm" data-copy="dd-${esc(d.custom_label)}">コピー</button></div>${ta("dd-"+d.custom_label,d.description,14)}</div>
    <div><b>Item specifics</b><div class="card rules" style="margin-top:4px">${Object.entries(d.specifics).map(([k,v])=>`<div class="r"><div>${esc(k)}</div><b>${esc(v)}</b></div>`).join("")}
    <div class="r"><div>Custom label (SKU)</div><b>${esc(d.custom_label)}</b></div></div>
    <div class="meta" style="margin-top:6px">ベースにした成約品：${esc(d.base_from||"—")}<br>状態ランクはメルカリの状態から仮置き。検品メモを反映して、写真と合っているか確認してから使ってください。</div></div></div>`;
}

// ---------- 在庫・値付け
VIEWS.stock=["実務2 入荷・出品・在庫 / 在庫・値付け",()=>{
  const l=skus("listed"), q=$("#q").value.trim().toLowerCase();
  const list=l.filter(x=>!q||(x.title+x.sku+(x.category||"")).toLowerCase().includes(q)).sort((a,b)=>(b.days_listed||0)-(a.days_listed||0));
  const age=[[0,29],[30,59],[60,89],[90,1e9]].map(([a,b])=>{const xs=l.filter(x=>(x.days_listed||0)>=a&&(x.days_listed||0)<=b);return {label:b>1e8?"90日超":`${a}〜${b}日`,n:xs.length,cost:xs.reduce((s,x)=>s+x.buy_price_jpy,0)}});
  const inv=S.ops.inventory||{};
  return stepHead("2-3","寝ている在庫を、30/60/90日ルールで動かす。","出品からの日数で値下げを提案します（30日＝相場の3%下、60日＝8%下、90日＝損益分岐の少し上まで。損益分岐は割りません）。計画：90日超在庫率10%以下。",["ship","発送へ"])
  + `<div class="kpis">${age.map((a,i)=>`<div class="kpi"><div class="l">出品から${a.label}</div><div class="v ${i===3&&a.n?"neg":""}">${a.n}<small>件</small></div><div class="s">在庫原価 ${yen(a.cost)}</div></div>`).join("")}</div>`
  + `<div class="chips" style="margin-top:10px">${Object.entries(inv.buckets||{}).map(([k,v])=>`<span class="chip">${esc(k)} <b>${yen(v)}</b></span>`).join("")}<span class="chip">90日超（購入から） <b>${yen(inv.over90_jpy)}</b></span></div>`
  + h2("· 01","出品中",`${l.length}件（出品日数の長い順）`)
  + (list.length?opsTable(["商品","#出品価格","#相場 / 損益分岐","#出品日数","値下げ提案","売れたら"],list.map(x=>`<tr>${skuCell(x)}
      <td class="num"><b>${usd(x.list_price_usd)}</b><div class="form-inline" data-f style="justify-content:flex-end;margin-top:4px"><input name="list_price_usd" type="number" style="width:70px" placeholder="新価格"><button class="btn sm" data-act="reprice" data-sku="${esc(x.sku)}">変更</button></div></td>
      <td class="num">${usd(x.predicted_sell_usd)}<div class="meta">${usd(x.floor_usd)}</div></td>
      <td class="num ${(x.days_listed||0)>=90?"neg":(x.days_listed||0)>=60?"":""}"><b>${x.days_listed??0}日</b></td>
      <td class="wrap">${x.reprice?`<div><b>${usd(x.reprice.suggested_usd)}</b> <span class="meta">${esc(x.reprice.reason)}</span></div><div data-f><input type="hidden" name="list_price_usd" value="${x.reprice.suggested_usd}"><button class="btn dark sm" data-act="reprice" data-sku="${esc(x.sku)}">提案価格に変更</button></div><div class="meta">※eBay側の価格も同じに変更してください</div>`:'<span class="meta">不要</span>'}</td>
      <td><div class="form-inline" data-f><label>売価(USD・送料込)<input name="sold_price_usd" type="number" value="${x.list_price_usd||""}"></label><label>買い手の国<select name="buyer_country">${opt(["US","GB","AU","DE","CA","FR","その他"])}</select></label>
        <button class="btn dark sm" data-act="sell" data-sku="${esc(x.sku)}">売れた</button></div></td></tr>`)):emptyCard("出品中の商品はありません"));
}];

// ---------- 発送
VIEWS.ship=["実務3 販売・発送 / 発送",()=>{
  const s=skus("sold").sort((a,b)=>(a.ship_due||"").localeCompare(b.ship_due||""));
  const duty=S.costs?.us_duty_rate||0;
  return stepHead("3-1","売れたら、期限内に発送する。",`売れた日から${S.settings?.handling_days||3}日以内を社内期限にしています（config.yaml の app.handling_days）。`+"米国宛てはDDP必須。送料と関税の実額を入れると、実現利益と予測誤差に反映されます。eBayの注文レポートCSVを取り込めば、売約・発送は自動で反映されます（着金・精算の画面）。",["settle","着金・精算へ"])
  + (s.length?opsTable(["商品","#売価","宛先・期限","発送記録"],s.map(x=>{const isUS=x.buyer_country==="US";const d=isUS?Math.round((x.sold_price_usd||0)*fx()*duty):0;return `<tr>${skuCell(x)}
      <td class="num"><b>${usd(x.sold_price_usd)}</b><div class="meta">${dfmt(x.sold_at)} 売約</div></td>
      <td><b>${esc(x.buyer_country||"—")}</b>${isUS?' <span class="tag warn">DDP必須</span>':""}<div class="${x.ship_overdue?"overdue":"meta"}">期限 ${dtfmt(x.ship_due)}${x.ship_overdue?"（超過）":""}</div></td>
      <td><div class="form-inline" data-f><label>配送<select name="carrier">${opt(["SpeedPAK","日本郵便","FedEx","DHL","eLogi","その他"])}</select></label>
        <label>追跡番号<input name="tracking" class="w2"></label><label>送料実費(円)<input name="shipping_cost_jpy" type="number"></label>
        <label>関税(円)${isUS?`<input name="duty_jpy" type="number" value="${d}">`:`<input name="duty_jpy" type="number" value="0">`}</label>
        <button class="btn dark sm" data-act="ship" data-sku="${esc(x.sku)}">発送した</button></div></td></tr>`})):emptyCard("発送待ちの商品はありません"));
}];

// ---------- 着金・精算
VIEWS.settle=["実務4 着金・精算 / 着金・精算",()=>{
  const sh=skus("shipped"), paid=skus("paid").slice(0,30), ret=skus("returned");
  const errs=paid.filter(x=>num(x.realized_jpy)&&x.predicted_profit_jpy);
  return stepHead("4-1","届いたら完了にして、予測と実績の差を見る。","Payoneer/Wiseの入金は複数の取引のまとめ払いなので、SKUごとの入金額は不要です。商品が届いて取引が終わったら「完了」にします。実現利益は「売価 − eBay手数料（CSVで取り込めば実額）− 換金差 − 仕入 − 実送料 − 関税 − 梱包 − 作業費」で計算します。月ごとの入金総額は「KPI・ファネル」の月次実績に入れて照合します。")
  + h2("· 01","eBayのCSVを取り込む","Seller Hubの注文レポート（売約・発送・追跡番号）／取引レポート（手数料の実額）。カスタムラベル＝SKU で照合")
  + `<div class="card" style="padding:14px 18px"><div class="form-inline"><input type="file" id="ebayCsv" accept=".csv,text/csv"><button class="btn" id="ebayPreview">内容を確認</button><button class="btn dark" id="ebayApply" ${importPlan?.changes?.length?"":"disabled"}>反映する</button></div>
    ${importPlan?`<div class="note">${esc(importPlan.message)}</div>${(importPlan.changes||[]).length?`<div class="mini">${importPlan.changes.slice(0,30).map(c=>`<div>${esc(c.sku)}：${esc(c.label)}</div>`).join("")}</div>`:""}${(importPlan.errors||[]).map(e=>`<div class="mini neg">${esc(e)}</div>`).join("")}`:""}</div>`
  + h2("· 02","配送中（完了待ち）",`${sh.length}件`)
  + (sh.length?opsTable(["商品","#売価","発送","#実現利益（計算）","完了・返品"],sh.map(x=>`<tr>${skuCell(x)}<td class="num"><b>${usd(x.sold_price_usd)}</b>${x.fees_usd!=null?`<div class="meta">手数料 $${x.fees_usd}</div>`:""}</td>
      <td class="mini">${esc(x.carrier||"")} ${esc(x.tracking||"")}<div class="meta">${dfmt(x.shipped_at)} 発送・送料${yen(x.shipping_cost_jpy)}・関税${yen(x.duty_jpy)}</div></td>
      <td class="num">${yen(x.estimated_jpy)}<div class="meta">予測 ${yen(x.predicted_profit_jpy)}</div></td>
      <td><div class="form-inline" data-f><button class="btn dark sm" data-act="pay" data-sku="${esc(x.sku)}">完了にする</button><label>入金額(任意・円)<input name="payout_jpy" type="number" placeholder="分かる場合のみ"></label></div>
        <div class="form-inline" data-f style="margin-top:6px"><select name="return_reason">${opt(RETURNS)}</select><label>返金・返送の損失(円)<input name="return_loss_jpy" type="number"></label><button class="btn sm" data-act="return" data-sku="${esc(x.sku)}">返品になった</button></div></td></tr>`)):emptyCard("配送中の商品はありません"))
  + (sh.length>1?`<div style="margin:8px 0"><button class="btn" id="payAll">配送中の${sh.length}件をまとめて完了にする</button></div>`:"")
  + h2("· 03","予測と実績（直近の完了）",`予測利益誤差（平均・絶対値）${pct(S.ops.indicators?.forecast_error,1)}`)
  + (paid.length?opsTable(["商品","#売価","#受取（計算）","#予測利益","#実現利益","#誤差",""],paid.map(x=>{const e=num(x.realized_jpy)&&x.predicted_profit_jpy?(x.realized_jpy-x.predicted_profit_jpy)/Math.abs(x.predicted_profit_jpy):null;return `<tr>${skuCell(x)}
      <td class="num">${usd(x.sold_price_usd)}</td><td class="num">${yen(x.received_jpy)}${x.payout_jpy?'<div class="meta">入金額で計算</div>':""}</td><td class="num">${yen(x.predicted_profit_jpy)}</td>
      <td class="num ${x.realized_jpy>=0?"pos":"neg"}"><b>${yen(x.realized_jpy)}</b></td><td class="num ${num(e)&&Math.abs(e)>.2?"neg":""}">${num(e)?(e>=0?"+":"")+pct(e,1):"—"}</td>
      <td class="num">${x.realized_jpy>0?`<button class="btn sm ghost" data-edit="m${esc(x.sku)}">マスター登録</button>`:""}</td></tr>`
      +(editing==="m"+x.sku?`<tr class="detail"><td colspan="7">${masterForm(skuMasterDefaults(x))}</td></tr>`:"")})):emptyCard("完了した商品はまだありません"))
  + (errs.length>=3?`<div class="note">誤差が大きい商品は、送料（実費 vs 見込み）・関税・売価（相場 vs 実売）のどれがずれたかを確認してください。実送料はジャンルごとに3件たまると自動で見込みに反映されます。</div>`:"")
  + h2("· 04","返品",`${ret.length}件・返品率 ${pct(S.ops.indicators?.return_rate,1)}（計画上限4%・停止7%）`)
  + (ret.length?opsTable(["商品","理由","#損失","返品日","その後"],ret.map(x=>`<tr>${skuCell(x)}<td>${esc(x.return_reason)}</td><td class="num">${yen(x.return_loss_jpy)}</td><td>${dfmt(x.returned_at)}</td>
      <td><div class="form-inline" data-f><button class="btn sm dark" data-act="restock" data-sku="${esc(x.sku)}">手元に戻った→再検品</button></div>
        <div class="form-inline" data-f style="margin-top:6px"><label>損失(円)<input name="return_loss_jpy" type="number" value="${x.return_loss_jpy??x.buy_price_jpy}"></label><button class="btn sm" data-act="writeoff" data-sku="${esc(x.sku)}">戻らない→損失処理</button></div></td></tr>`)):emptyCard("返品はありません"));
},()=>{
  const read=()=>new Promise(res=>{const f=$("#ebayCsv").files[0]; if(!f){toast("CSVファイルを選んでください");return res(null)} const r=new FileReader(); r.onload=()=>res(r.result); r.readAsText(f)});
  const pv=$("#ebayPreview"); if(pv) pv.onclick=async()=>{const t=await read(); if(!t) return; importText=t; importPlan=await (await post("/api/ebay-import",{text:t,apply:false})).json(); render()};
  const ap=$("#ebayApply"); if(ap) ap.onclick=async()=>{if(!importText) return; importPlan=await (await post("/api/ebay-import",{text:importText,apply:true})).json(); toast(importPlan.message); importText=null; load()};
  const pa=$("#payAll"); if(pa) pa.onclick=async()=>{if(!confirm("配送中の商品をすべて完了にしますか？"))return; for(const x of skus("shipped")) await post("/api/sku",{sku:x.sku,action:"pay",fields:{}}); toast("まとめて完了にしました"); load()};
}];
let importPlan=null, importText=null;

// 着金済みSKU → マスター：実送料と、実績から逆算した指値を初期値にする
function skuMasterDefaults(x){
  const g=(S.rules.config_genres||[]).find(g=>g.name===x.genre||g.genre===x.genre)||{};
  const ship=x.shipping_cost_jpy||g.shipping_cost_jpy||"";
  const minp=(catPlan(x.category)["貢献利益下限"])||8000;
  // 実現利益が下限を上回った分だけ高く買っても下限は確保できる
  const cap=Math.max(0,Math.round((x.buy_price_jpy+(x.realized_jpy||0)-minp)/100)*100);
  return {label:x.title.slice(0,40),category:x.category,mercari_query:g.mercari_query||x.title.slice(0,30),ebay_query:g.ebay_query||"",max_buy_jpy:cap,shipping_jpy:ship,source_ref:x.sku};
}

// ---------- SKU台帳
const STATUS_NAMES={ordered:"到着待ち",arrived:"検品待ち",ready:"出品待ち",listed:"出品中",sold:"発送待ち",shipped:"配送中",paid:"完了",rejected:"不合格・損失",returned:"返品"};
function ledgerRow(x){
  return `<tr>${skuCell(x)}
    <td><span class="badge ${x.status==="paid"?"ok":["rejected","returned"].includes(x.status)?"alert":"warn"}">${esc(STATUS_NAMES[x.status]||x.status_label)}</span></td>
    <td class="num">${yen(x.buy_price_jpy)}</td><td class="num">${yen(x.predicted_profit_jpy)}</td><td class="num">${usd(x.sold_price_usd||x.list_price_usd)}</td>
    <td class="num">${num(x.realized_jpy)?yen(x.realized_jpy):num(x.estimated_jpy)?`<span class="meta">見込 ${yen(x.estimated_jpy)}</span>`:"—"}</td>
    <td class="mini">買 ${dfmt(x.purchased_at)}${x.listed_at?`<br>出 ${dfmt(x.listed_at)}`:""}${x.sold_at?`<br>売 ${dfmt(x.sold_at)}`:""}${x.paid_at?`<br>完 ${dfmt(x.paid_at)}`:""}</td>
    <td class="num"><button class="btn sm ghost" data-edit="${esc(x.sku)}">${editing===x.sku?"閉じる":"修正"}</button></td></tr>`
    +(editing===x.sku?`<tr class="detail"><td colspan="8">${editForm(x)}</td></tr>`:"");
}
// 入力ミスの修正（状態は変えない）と、一つ前の工程に戻す
function editForm(x){
  const d=v=>v?String(v).slice(0,10):"";
  const f=(n,l,v,t="text")=>`<label>${l}<input name="${n}" type="${t}" value="${esc(v??"")}"></label>`;
  return `<div class="form-inline" data-f>
    ${f("title","商品名",x.title)}${f("buy_price_jpy","仕入(円)",x.buy_price_jpy,"number")}${f("purchased_at","仕入日",d(x.purchased_at),"date")}
    ${f("list_price_usd","出品価格(USD)",x.list_price_usd,"number")}${f("ebay_url","eBay URL",x.ebay_url)}
    ${f("sold_price_usd","売価(USD)",x.sold_price_usd,"number")}${f("buyer_country","国",x.buyer_country)}${f("sold_at","売約日",d(x.sold_at),"date")}
    ${f("shipping_cost_jpy","送料(円)",x.shipping_cost_jpy,"number")}${f("duty_jpy","関税(円)",x.duty_jpy,"number")}${f("tracking","追跡番号",x.tracking)}
    ${f("fees_usd","eBay手数料(USD)",x.fees_usd,"number")}${f("payout_jpy","入金額(円・任意)",x.payout_jpy,"number")}${f("return_loss_jpy","返品損失(円)",x.return_loss_jpy,"number")}
    <label>カテゴリ<select name="category">${(S.plan.categories||[]).map(c=>`<option ${c["カテゴリ"]===x.category?"selected":""}>${esc(c["カテゴリ"])}</option>`).join("")}</select></label>
    ${f("notes","メモ",x.notes)}
    <button class="btn dark sm" data-act="edit" data-sku="${esc(x.sku)}">保存</button></div>
    <div style="margin-top:8px" class="pv"><span class="meta">工程を間違えて進めた場合：</span>
      <button class="btn sm" data-act="back" data-sku="${esc(x.sku)}">一つ前の工程に戻す（今：${esc(STATUS_NAMES[x.status]||"")}）</button>
      <button class="btn sm ghost" data-act="delete" data-sku="${esc(x.sku)}">台帳から削除</button></div>`;
}
VIEWS.ledger=["実務4 着金・精算 / SKU台帳",()=>{
  const q=$("#q").value.trim().toLowerCase(), st=localGet("ledgerSt")||"";
  const list=S.ops.skus.filter(x=>(!st||x.status===st)&&(!q||(x.title+x.sku+(x.category||"")+(x.tracking||"")).toLowerCase().includes(q)));
  const c=S.ops.counts||{};
  return h1("すべてのSKUを、1行ずつ。","購入から完了までの全記録です。「修正」で入力ミスを直したり、一つ前の工程に戻したりできます。CSVで書き出して会計（証憑突合）やシートの更新に使えます。")
  + `<div class="toolbar"><select id="ledgerSt"><option value="">すべて（${S.ops.skus.length}）</option>${Object.entries(STATUS_NAMES).map(([k,l])=>`<option value="${k}" ${st===k?"selected":""}>${l}（${c[k]||0}）</option>`).join("")}</select>
     <a class="btn" href="/api/skus.csv">CSV 書き出し</a></div>`
  + (list.length?opsTable(["SKU・商品","状態","#仕入","#予測利益","#売価","#実現利益","日付",""],list.map(ledgerRow)):emptyCard("該当するSKUはありません"));
},()=>{const s=$("#ledgerSt"); if(s) s.onchange=()=>{localSet("ledgerSt",s.value);render()}}];

// ---------- ルール・前提
VIEWS.rules=["設定 / ルール・前提",()=>{
  const r=S.rules, f=r.fees||{}, th=r.thresholds||{}, c=S.costs||{};
  const row=(l,s,v)=>`<div class="r"><div>${l}<span>${s}</span></div><b>${v}</b></div>`;
  return h1("利益計算と判断の前提を、すべて見える場所に。","値は config.yaml（GitHubで編集可）で変更し、「再スキャン」で全候補の並びに反映されます。計画値（下限利益・リスク閾値など）は計画シートが正本です。")
  + planBanner()
  + h2("§","資金（画面から入力）","リスク「資金不足（自由現金300万円未満）」の判定に使います")
  + `<div class="card rules"><div class="r"><div>現在の現金残高（自由現金）<span>銀行残高 − 税金準備金。月次締めのたびに更新</span></div>
      <div class="form-inline"><input id="cashIn" type="number" value="${S.ops.cash_jpy??""}" placeholder="円"><button class="btn sm" id="cashSave">保存</button></div></div></div>`
  + h2("§","手数料・為替・費用（1件あたり）","計画シート「ユニットエコノミクス」と同じ構成＋外部環境で判明した費用")
  + `<div class="card rules">
    ${row("eBay Final Value Fee","カテゴリ平均（ジャンル別に上書き可）",pct(f.ebay?.final_value_fee_rate,2))}
    ${row("eBay 固定手数料","取引ごと",(f.ebay?.payment_fixed_fee_usd??0).toFixed(2)+" USD")}
    ${row("海外手数料","日本登録セラー（eBay日本FAQ）",pct(f.ebay?.international_fee_rate,2))}
    ${row("広告費","Promoted Listings（売価比）",pct(f.ebay?.ad_rate,1))}
    ${row("Payoneer / Wise 換金","USD→JPY のスプレッド",pct(f.ebay?.payout_fx_spread_rate,1))}
    ${row("米国関税（DDP）","米国の買い手比率 × 関税率（外部環境で要確認）",`${pct(c.us_buyer_share)} × ${pct(c.us_duty_rate)}`)}
    ${row("返品・値下げ・為替引当","売価比",pct(c.reserve_rate,1))}
    ${row("梱包資材","1件",yen(c.packing_material_jpy))}
    ${row("作業費","検品・撮影・出品・発送（外注換算）",yen(c.labor_jpy))}
    ${row("為替レート",esc(S.fx?.source||""),num(S.fx?.rate)?S.fx.rate.toFixed(2)+" 円/USD":"—")}</div>`
  + h2("§","候補に載せる基準")
  + `<div class="card rules">
    ${row("貢献利益の下限","計画シート「カテゴリ別採算」の貢献利益下限を優先（なければ右の値）",yen(th.min_profit_jpy))}
    ${row("仕入ROIの下限","貢献利益 ÷ 仕入価格",pct(th.min_roi))}
    ${row("照合しきい値","メルカリ品 ↔ eBay成約品の一致スコア",(th.match_threshold??0).toFixed(2))}
    ${row("相場に必要な成約件数","これ未満は「相場なし」",(th.min_comps??0)+" 件")}
    ${row("仕入れ価格の上限","資金規模（ジャンル・マスターで個別に設定可）",th.max_buy_jpy?yen(th.max_buy_jpy):"なし")}
    ${row("需要の下限","eBayで検索語が直近に売れた件数（0001_sedori）",(th.min_sold_count??0)+" 件")}
    ${row("付属品・別物よけ","相場(円)に対する仕入れ価格の下限比",pct(th.min_price_ratio))}
    ${row("データソース","live / sample / auto",esc(r.data_source))}
    ${row("メルカリの取得方法","browser＝公開の検索ページ / api＝内部検索API（非公式・要判断）",esc(S.settings?.mercari_source||""))}
    ${row("自動スキャン","画面版の起動中（間隔は毎回±20%ずらす）",S.settings?.auto_scan_minutes?`${S.settings.auto_scan_minutes}分おき・1回${S.settings.genres_per_scan||"全"}ジャンルを順番に`:"停止")}
    <div class="r"><div>メルカリ取得の自動停止<span>403/429・人間確認・急な0件を検知すると一時停止（連続するたびに長く）</span></div>
      <div>${S.mercari_pause?.active?`<b class="neg">停止中（${dtfmt(S.mercari_pause.until)} まで）</b> <button class="btn sm" id="mResume">今すぐ再開</button><div class="meta">${esc(S.mercari_pause.reason||"")}</div>`:`<b>正常</b>${S.mercari_pause?.strikes?`<span class="meta">（直近の停止 ${S.mercari_pause.strikes}回）</span>`:""}`}</div></div></div>
  <h2><span class="no">§</span>除外語（3階層：全体 ＋ カテゴリ ＋ ジャンル）</h2><div class="card rules">
    ${row("全体","全ジャンル共通",esc((r.exclude_keywords||[]).join("・")))}
    ${Object.entries(S.settings?.category_excludes||{}).map(([k,v])=>row(esc(k),"カテゴリ別",esc(v.join("・")))).join("")}</div>
  <h2><span class="no">§</span>通知</h2><div class="card rules">
    ${row("Slack","config.local.yaml の notify.slack_webhook_url（GitHubに上げない）",S.settings?.notify_configured?"設定済み":"未設定")}
    ${row("通知の対象","基準を満たした新着 / マスターのヒット（同じ出品は1回だけ）",`${S.settings?.notify?.on_new_candidates?"新着":""}${S.settings?.notify?.on_master_hits?"・マスター":""}`)}
    <div class="r"><div>テスト送信<span>設定後に押して届くか確認</span></div><button class="btn sm" id="notifyTest">テスト通知を送る</button></div></div>`
  + h2("§","運用（バックアップ・発送期限・前提の出どころ）")
  + `<div class="card rules">
    ${row("監視範囲","all＝設定ジャンル＋マスター / masters_only＝マスターだけ（メルカリへのアクセス最小）",esc(S.settings?.scan_mode||"all"))}
    ${row("DBのバックアップ","1日1回 data/backups に14日分（app.backup_dir を指定するとそこにもコピー）",S.settings?.backup_dir?"＋ "+esc(S.settings.backup_dir):"data/backups のみ")}
    ${row("発送期限","売れてから（app.handling_days）",(S.settings?.handling_days||3)+" 日")}
    ${row("計画シートから読んだ前提","前提条件タブに「米国の買い手比率」「米国関税率」の行があれば優先",(c.from_sheet||[]).length?esc(c.from_sheet.join("・")):"なし（config.yaml の値を使用）")}
    ${row("実送料の学習","発送済みが3件以上のジャンルは実送料の中央値を見込みに使う",Object.entries(S.ops.ship_learn||{}).filter(([,v])=>v.n>=3).map(([g,v])=>`${esc(g)} ${yen(v.median)}`).join("・")||"まだなし")}</div>`
  + h2("§","監視ジャンル",`有効 ${(r.all_genres||[]).filter(g=>g.active).length} / 全 ${(r.all_genres||[]).length}件。停止・追加は画面から（config.yaml は書き換えません）`)
  + `<div class="card"><div class="wide"><table><thead><tr><th>ジャンル</th><th>カテゴリ</th><th>仮説ID</th><th>メルカリ検索</th><th>eBay検索</th><th class="num">国際送料</th><th class="num">下限</th><th></th></tr></thead><tbody>
    ${(r.all_genres||[]).map(g=>`<tr style="${g.active?"":"opacity:.5"}"><td><b>${esc(g.name)}</b>${g.custom?' <span class="tag">画面で追加</span>':""}</td><td class="mini">${esc(g.category||"")}</td><td class="mini">${esc((g.plan_ids||[]).join(","))}</td><td class="mini">${esc(g.mercari_query)}</td><td class="mini">${esc(g.ebay_query)}</td>
      <td class="num">${yen(g.shipping_cost_jpy)}</td><td class="num">${g.min_profit_jpy!=null?yen(g.min_profit_jpy):yen((catPlan(g.category)["貢献利益下限"])??th.min_profit_jpy)}</td>
      <td class="num"><button class="btn sm" data-gtoggle="${esc(g.id)}" data-on="${g.active?0:1}">${g.active?"停止":"再開"}</button>${g.custom?` <button class="btn sm ghost" data-gdel="${esc(g.id)}">削除</button>`:""}</td></tr>`).join("")}
  </tbody></table></div>
  <div class="foot" style="display:block"><b>ジャンルを追加</b><div class="form-inline" id="gadd" style="margin-top:6px">
    <label>名前<input name="name" class="w2"></label><label>メルカリ検索語<input name="mercari_query" class="w2"></label><label>eBay検索語（英語）<input name="ebay_query" class="w2"></label>
    <label>カテゴリ<select name="category">${(S.plan.categories||[]).map(c=>`<option>${esc(c["カテゴリ"])}</option>`).join("")}</select></label>
    <label>国際送料(円)<input name="shipping_cost_jpy" type="number" value="3500"></label><label>仮説ID<input name="plan_ids" placeholder="P013"></label>
    <button class="btn dark sm" id="gaddBtn">追加</button></div></div></div>`;
},()=>{const b=$("#cashSave"); if(b) b.onclick=async()=>{await post("/api/settings",{cash_jpy:$("#cashIn").value});toast("保存しました");load()};
  const t=$("#notifyTest"); if(t) t.onclick=async()=>{const r=await (await post("/api/notify-test",{})).json();toast(r.message)};
  const m=$("#mResume"); if(m) m.onclick=async()=>{if(!confirm("一時停止を解除して、次のスキャンからメルカリの取得を再開しますか？"))return;const r=await (await post("/api/mercari-resume",{})).json();toast(r.message);load()};
  $$("[data-gtoggle]").forEach(b=>b.onclick=async()=>{const r=await (await post("/api/genre",{id:b.dataset.gtoggle,active:b.dataset.on==="1"})).json();toast(r.message);load()});
  $$("[data-gdel]").forEach(b=>b.onclick=async()=>{if(!confirm("このジャンルを削除しますか？"))return;const r=await (await post("/api/genre",{id:b.dataset.gdel,delete:true})).json();toast(r.message);load()});
  const ga=$("#gaddBtn"); if(ga) ga.onclick=async()=>{const o={};$$("#gadd [name]").forEach(i=>o[i.name]=i.value);
    if(!o.name||!o.mercari_query||!o.ebay_query){toast("名前・メルカリ検索語・eBay検索語は必須です");return}
    o.plan_ids=o.plan_ids?o.plan_ids.split(/[ ,、]+/).filter(Boolean):[];o.genre=o.name;
    const r=await (await post("/api/genre",{custom:o})).json();toast(r.message);load()};
}];
