// ===== 上流：戦略・計画 =====
const planMonth=()=>S.ops.plan_month||{};
const actualM=()=>S.ops.actual||{};
function planBanner(){
  const p=S.plan||{};
  if(p.error&&!p.monthly) return `<div class="note">計画シート未接続：${esc(p.error)}</div>`;
  return `<div class="chips">
    <span class="chip">計画 <b>${esc(S.ops.month_label||"")}</b>（${S.ops.month_index}か月目）</span>
    <span class="chip">正本 <a href="${esc(p.sheet_url||"#")}" target="_blank" rel="noopener">${esc(p.title||"事業計画シート")} ↗</a></span>
    <span class="chip">同期 <b>${esc(p._synced_at||"—")}</b> <a href="#" data-sync>今すぐ同期</a></span>
    <span class="chip">為替 <b>¥${num(S.fx?.rate)?S.fx.rate.toFixed(2):"—"}</b>/USD（${esc(S.fx?.source||"")}）</span>
    <span class="chip">自動スキャン <b>${S.next_scan_at?"次回 "+dtfmt(new Date(S.next_scan_at*1000).toISOString()):"停止中"}</b></span>
    ${p.error?`<span class="chip sample">${esc(p.error)}</span>`:""}</div>`;
}
function monthProgress(){const d=new Date();const days=new Date(d.getFullYear(),d.getMonth()+1,0).getDate();return d.getDate()/days}

// ---------- 経営ダッシュボード
VIEWS.dash=["上流 / 経営ダッシュボード",()=>{
  const pm=planMonth(), a=actualM(), o=S.ops, c=o.counts||{}, ind=o.indicators||{};
  const fixed=pm["固定費(万円)"]||0, opProfit=num(a.contribution_man)?a.contribution_man-fixed:null;
  const sc=(S.plan.scenarios||[]);
  const risks=(o.risks||[]).slice().sort((x,y)=>(y.status==="alert")-(x.status==="alert"));
  const nextGate=(o.gates||[]).find(g=>!g.reached)||(o.gates||[])[0];
  const alerts=risks.filter(r=>r.status==="alert");
  const notes=(S.summary.notes||[]).concat(S.last_error?[{target:"スキャン",level:"error",message:S.last_error}]:[]);
  const needLogin=notes.some(n=>/ログイン|拒否|captcha/.test(n.message));
  const prog=monthProgress();
  const flow=[
    ["cands","仕入れ候補",passing().length,"件（基準クリア）"],
    ["queue","仕入れキュー",S.queue.length,"件"],
    ["inbound","入荷・検品",(c.ordered||0)+(c.arrived||0),`到着待ち${c.ordered||0}・検品${c.arrived||0}`],
    ["listing","出品待ち",c.ready||0,"件"],
    ["stock","出品中",c.listed||0,o.reprice_n?`値下げ対象 ${o.reprice_n}`:"値下げ対象なし",o.reprice_n?"hot":""],
    ["ship","発送待ち",c.sold||0,o.ship_overdue_n?`期限超過 ${o.ship_overdue_n}`:"期限内",o.ship_overdue_n?"bad":""],
    ["settle","着金待ち",c.shipped||0,"件"],
  ];
  return h1(`${esc(o.month_label||"今月")}の現在地と、今日やること。`,
    `計画値は事業計画シート（数値の正本）から、実績はツールに記録した仕入れ・SKUから自動で集計しています。月の経過 ${pct(prog)}。`)
  + planBanner()
  + h2("· 01","今日やること","工程ごとの件数。押すとその工程へ移動します")
  + `<div class="flow">${flow.map(([v,l,n,s,cls])=>`<div class="st ${cls||""}" data-go="${v}"><div class="l">${l}</div><div class="v">${n}</div><div class="s">${s}</div></div>`).join("")}</div>`
  + h2("· 02","今月の計画対実績",`計画 = シート「12ヶ月計画」${o.month_index}か月目（月間値）。実績は月初からの累計`)
  + `<div class="kpis">
      ${kpiTile("販売数",a.sales,pm["販売数"],{fmt:v=>v+"件",tag:"SALES",sub:`経過按分の目安 ${num(pm["販売数"])?Math.round(pm["販売数"]*prog)+"件":"—"}`})}
      ${kpiTile("貢献利益/件",a.profit_per_unit,pm["貢献利益/件(円)"],{fmt:yen,tag:"UNIT"})}
      ${kpiTile("購入成功数",a.purchases,(S.plan.kpi_plan||[]).find(k=>k["月"]==o.month_index)?.["仕入成功数_計画"],{fmt:v=>v+"件",tag:"SUPPLY"})}
      ${kpiTile("営業利益",opProfit,pm["営業利益(万円)"],{fmt:manFmt,tag:"OP",sub:`貢献利益${manFmt(a.contribution_man)} − 固定費${manFmt(fixed)}`})}
    </div>
    <div class="kpis" style="margin-top:10px">
      ${kpiTile("売上",a.revenue_man,pm["売上(万円)"],{fmt:manFmt,tag:"REV"})}
      ${kpiTile("在庫日数",a.inventory_days,pm["在庫日数"],{fmt:v=>v+"日",better:"low",tag:"DAYS"})}
      ${kpiTile("予測利益誤差",ind.forecast_error,0.2,{fmt:v=>pct(v,1),better:"low",tag:"ACCURACY",sub:`着金済 ${ind.forecast_n||0}件で計測`})}
      ${kpiTile("返品率",ind.return_rate,0.07,{fmt:v=>pct(v,1),better:"low",tag:"QUALITY"})}
    </div>`
  + `<div class="grid2" style="margin-top:4px"><div>`
  + h2("· 03","リスク監視",`シート「リスク管理」の発動閾値で自動判定（発動 ${alerts.length}件）`)
  + `<div class="card"><div class="wide"><table><thead><tr><th>リスク</th><th>先行指標</th><th>閾値</th><th class="num">現在値</th><th>判定</th><th>自動対応</th></tr></thead><tbody>
      ${risks.map(r=>`<tr><td><b>${esc(r["リスク"])}</b></td><td class="mini">${esc(r["先行指標"])}</td><td class="mini">${esc(r["発動閾値"])}</td>
        <td class="num">${riskVal(r)}</td><td>${statusBadge(r.status)}</td><td class="wrap">${esc(r["自動対応"])}</td></tr>`).join("")||'<tr><td colspan="6" class="empty">計画シート未同期</td></tr>'}
    </tbody></table></div></div></div><div>`
  + h2("· 04","次のステージゲート",nextGate?esc(nextGate["時点"]):"")
  + (nextGate?`<div class="card" style="padding:14px 18px">${nextGate.checks.map(ch=>`<div style="margin:6px 0"><div class="pv"><b>${esc(ch.label)}</b><span class="plan">基準 ${ch.target.toLocaleString()}${esc(ch.unit||"")}</span>
        <span style="margin-left:auto">${ch.value==null?'<span class="badge na">未計測</span>':`${(+ch.value).toLocaleString()}${esc(ch.unit||"")} <span class="badge ${ch.ok?"ok":"alert"}">${ch.ok?"達成":"未達"}</span>`}</span></div></div>`).join("")}
      <div class="note"><b>達成時：</b>${esc(nextGate["達成時"])}<br><b>未達時：</b>${esc(nextGate["未達時"])}</div></div>`:'<div class="card empty">計画シート未同期</div>')
  + h2("· 05","経営判断ルール","12か月目の月間営業利益で判断")
  + `<div class="card rules">${(S.plan.decision_rules||[]).map(r=>`<div class="r"><div>${esc(r["経営判断"])}</div><span class="mini" style="max-width:60%;text-align:right">${esc(r["ルール"])}</span></div>`).join("")}</div>`
  + `</div></div>`
  + h2("· 06","アラート（データ取得）")
  + `<div class="card notes">${needLogin||S.logging_in?loginBox():""}${notes.length?dedupNotes(notes).map(n=>`<div class="n ${n.level}">${esc(n.target)}：${esc(n.message)}</div>`).join(""):'<div class="n">問題はありません</div>'}</div>`
  + (sc.length?h2("· 07","12か月目の目標シナリオ")+`<div class="card"><div class="wide"><table><thead><tr><th>シナリオ</th><th class="num">販売数</th><th class="num">平均単価</th><th class="num">貢献利益/件</th><th class="num">営業利益</th><th class="num">在庫日数</th><th class="num">必要資金</th><th>位置づけ</th></tr></thead><tbody>
      ${sc.map(x=>`<tr><td><b>${esc(x["シナリオ"])}</b></td><td class="num">${x["販売数"]}件</td><td class="num">${yen(x["平均販売単価"])}</td><td class="num">${yen(x["貢献利益/件"])}</td><td class="num">${manFmt(x["営業利益(万円)"])}</td><td class="num">${x["在庫日数"]}日</td><td class="num">${manFmt(x["推奨利用可能資金(万円)"])}</td><td>${esc(x["位置づけ"])}</td></tr>`).join("")}</tbody></table></div></div>`:"");
}];
function riskVal(r){
  const v=r.value; if(v==null) return "—";
  if(r.metric==="free_cash") return yen(v);
  if(r.metric==="purchase_vs_plan") return "計画比 "+pct(v);
  if(r.metric==="profit_vs_plan") return (v>=0?"+":"")+pct(v,1);
  return pct(v,1);
}
function loginBox(){
  return `<div class="n info"><b>eBayの相場を取るには、ツール用ブラウザで一度だけeBayにログインが必要です。</b><br>
    <button class="btn dark sm" data-login style="margin-top:6px" ${S.logging_in||S.scanning?"disabled":""}>${S.logging_in?'<span class="spin"></span>ブラウザでログインしてください…':"eBayにログインする"}</button>
    <div class="meta">ボタンを押すとChromeが開きます。eBayにログインし、検索結果が表示されたら自動で閉じて再スキャンします。</div></div>`
    +(S.login_result&&S.login_result!=="ok"?`<div class="n warn">eBayログイン: ${S.login_result==="timeout"?"時間切れになりました":S.login_result==="closed"?"ブラウザが閉じられました":esc(S.login_result)}</div>`:"");
}
function dedupNotes(notes){
  const m=new Map();
  for(const n of notes){const k=n.level+n.message; m.has(k)?m.get(k).targets.push(n.target):m.set(k,{...n,targets:[n.target]})}
  return [...m.values()].map(n=>({...n,target:n.targets.length>2?`${n.targets[0]} ほか${n.targets.length-1}ジャンル`:n.targets.join("・")}));
}

// ---------- 外部環境
VIEWS.env=["上流 / 外部環境",()=>{
  const e=S.env||{}, today=new Date().toISOString().slice(0,10);
  if(e.error) return h1("外部環境")+`<div class="note">environment.yaml を読めません：${esc(e.error)}</div>`;
  const all=(e.areas||[]).flatMap(a=>a.items||[]);
  const due=all.filter(i=>i.review_by&&i.review_by<=today);
  const cnt=lv=>all.filter(i=>i.level===lv).length;
  return h1("メルカリ・eBay・物流・為替の、いま。",
    "販売先（eBay）・仕入れ先（メルカリ）・物流と関税・為替について、事実（いつ・何が）と、自社への影響、ツールでの扱いを並べています。内容は <code>flipbot/knowledge/environment.yaml</code> で、GitHub上からも編集できます。")
  + `<div class="chips"><span class="chip">最終確認 <b>${esc(e.last_reviewed||"—")}</b></span>
      <span class="chip"><span class="lv 追い風">追い風</span> ${cnt("追い風")}</span><span class="chip"><span class="lv 逆風">逆風</span> ${cnt("逆風")}</span>
      <span class="chip"><span class="lv 要注意">要注意・要確認</span> ${cnt("要注意")+cnt("要確認")}</span>
      ${due.length?`<span class="chip sample">再確認の期限切れ ${due.length}件</span>`:""}
      <span class="chip">為替（自動） <b>¥${num(S.fx?.rate)?S.fx.rate.toFixed(2):"—"}</b></span>
      <span class="chip">関税の前提 <b>${pct(S.costs?.us_duty_rate)}</b> × 米国比率 <b>${pct(S.costs?.us_buyer_share)}</b></span></div>`
  + (e.implications?.length?h2("· 00","戦略への示唆")+`<div class="card" style="padding:14px 20px"><ol style="margin:0;padding-left:18px">${e.implications.map(x=>`<li style="margin:4px 0">${esc(x)}</li>`).join("")}</ol></div>`:"")
  + (e.areas||[]).map((a,i)=>h2("· "+String(i+1).padStart(2,"0"),esc(a.name))+`<div class="envgrid">${(a.items||[]).map(it=>`<div class="card envcard">
      <div class="pv"><span class="lv ${esc(it.level)}">${esc(it.level)}</span><span class="meta">確認 ${esc(it.checked||"")}${it.review_by?` ・ 次回確認 ${esc(it.review_by)}${it.review_by<=today?' <b class="overdue">（期限切れ）</b>':""}`:""}</span></div>
      <h3>${esc(it.title)}</h3>
      <div class="cols"><div><b>事実</b>${esc(it.fact)}</div><div><b>自社への影響</b>${esc(it.impact)}</div><div><b>ツールでの扱い</b>${esc(it.tool)}</div></div>
      <div class="src">${(it.sources||[]).map(s=>s.url?`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)} ↗</a>`:`<span class="meta">出典：${esc(s.title)}</span>`).join("")}</div></div>`).join("")}</div>`).join("");
}];

// ---------- 戦略・カテゴリ
VIEWS.strategy=["上流 / 戦略・カテゴリ",()=>{
  const cats=S.plan.categories||[], act=S.ops.categories||{}, genres=S.rules.config_genres||S.rules.genres||[];
  const cands=passing(), inv=S.ops.inventory?.cost_jpy||0;
  const rules=S.plan.allocation_rules||[];
  const maxShare=Math.max(0,...Object.values(act).map(x=>x.share||0));
  const genreInv={}; skus(["ordered","arrived","ready","listed"]).forEach(s=>{genreInv[s.genre]=(genreInv[s.genre]||0)+s.buy_price_jpy});
  const maxGenre=inv?Math.max(0,...Object.values(genreInv))/inv:0;
  const unverified=new Set(cats.filter(c=>/未検証|検証枠/.test(c["検証状況"]||"")).map(c=>c["カテゴリ"]));
  const unvShare=inv?Object.entries(act).filter(([k])=>unverified.has(k)).reduce((s,[,v])=>s+v.inventory_jpy,0)/inv:0;
  const ruleState=[["1カテゴリ上限",maxShare,.40],["1型番上限",maxGenre,.05],["検証枠",unvShare,.15]];
  const db=S.plan.genre_db||[], covered=new Set(genres.flatMap(g=>g.plan_ids||[]));
  const decisions=(S.plan.decisions||[]).filter(d=>d["ステータス"]==="有効");
  return h1("どのカテゴリに、いくら張るか。","シート「カテゴリ別採算」の計画と、ツールの実績（在庫・販売・貢献利益）を並べています。下限利益はこのカテゴリ基準で候補の足切りに使われます。")
  + planBanner()
  + h2("· 01","カテゴリ別：計画と実績")
  + `<div class="card"><div class="wide"><table><thead><tr><th>カテゴリ</th><th class="num">構成比(計画)</th><th class="num">在庫比率(実績)</th><th class="num">貢献利益 下限/基準</th><th class="num">平均貢献利益(実績)</th><th class="num">60日販売率(計画)</th><th class="num">返品率上限</th><th class="num">監視ジャンル</th><th class="num">今の候補</th><th>主なリスク</th><th>検証状況</th></tr></thead><tbody>
    ${cats.map(c=>{const a=act[c["カテゴリ"]]||{};const g=genres.filter(x=>x.category===c["カテゴリ"]).length;const n=cands.filter(x=>x.category===c["カテゴリ"]).length;
      return `<tr><td style="white-space:nowrap"><b>${esc(c["カテゴリ"])}</b></td><td class="num">${pct(c["販売数構成"])}</td>
      <td class="num ${a.share>.4?"neg":""}">${inv?pct(a.share||0):"—"}${bar(a.share||0,1,a.share>.4?"bad":"")}</td>
      <td class="num">${yen(c["貢献利益下限"])} / ${yen(c["貢献利益基準"])}</td>
      <td class="num">${a.avg_profit!=null?`<span class="${a.avg_profit>=c["貢献利益下限"]?"pos":"neg"}">${yen(a.avg_profit)}</span>`:"—"}</td>
      <td class="num">${pct(c["60日以内販売率"])}</td><td class="num">${pct(c["返品率上限"])}</td>
      <td class="num">${g}</td><td class="num">${n}</td><td class="wrap">${esc(c["主なリスク"])}</td><td>${esc(c["検証状況"])}</td></tr>`}).join("")||'<tr><td colspan="11" class="empty">計画シート未同期</td></tr>'}
    </tbody></table></div></div>`
  + h2("· 02","資金配分ルールの遵守状況",`在庫原価 ${yen(inv)} に対する比率`)
  + `<div class="grid3" style="margin-top:0">${ruleState.map(([n,v,lim])=>{const r=rules.find(x=>x["ルール"]===n);return `<div class="card qc"><div class="l">${esc(n)}（${esc(r?.["基準"]||"上限"+pct(lim))}）</div>
      <div class="v ${inv&&v>lim?"neg":""}">${inv?pct(v,1):"—"}</div>${bar(v,lim*1.5,v>lim?"bad":"ok")}<p>${inv?(v>lim?"上限超過：新規仕入れを他カテゴリへ":"範囲内"):"在庫なし"}</p></div>`}).join("")}</div>`
  + h2("· 03","当たりジャンル仮説の検証カバー率",`シート「当たりジャンル商品DB」${db.length}件中、ツールで監視中 ${db.filter(d=>covered.has(d.ID)).length}件`)
  + `<div class="card"><div class="wide"><table><thead><tr><th>ID</th><th>優先</th><th>ジャンル / 商品</th><th>狙う価格帯</th><th>当たり仮説</th><th>物流/検品/真贋</th><th>シートの状態</th><th>ツール</th></tr></thead><tbody>
    ${db.map(d=>{const gs=genres.filter(g=>(g.plan_ids||[]).includes(d.ID));return `<tr><td class="meta">${esc(d.ID)}</td><td><b>${esc(d["優先度"])}</b></td>
      <td class="wrap"><b>${esc(d["商品/テーマ"])}</b><div class="meta">${esc(d["ジャンル"])}</div></td><td class="mini">${esc(d["狙う価格帯"])}</td>
      <td class="wrap">${esc(d["当たり仮説"])}</td><td class="mini">${esc(d["物流難度"])} / ${esc(d["検品難度"])} / ${esc(d["真贋/IPリスク"])}</td><td class="mini">${esc(d["ステータス"])}</td>
      <td>${gs.length?`<span class="badge ok">監視中</span><div class="meta">${gs.map(g=>esc(g.name)).join("・")}</div>`:'<span class="badge na">未監視</span>'}</td></tr>`}).join("")||'<tr><td colspan="8" class="empty">計画シート未同期</td></tr>'}
    </tbody></table></div><div class="foot"><span class="meta">監視ジャンルは config.yaml の genres（plan_ids でこの表のIDと紐づけ）。トレカ（P006〜P009）はカード番号での照合が必要なため未対応です。</span></div></div>`
  + h2("· 04","有効な意思決定",`シート「意思決定ログ」のステータス=有効（${decisions.length}件）`)
  + `<div class="card"><div class="wide"><table><thead><tr><th>日付</th><th>テーマ</th><th>決定内容</th><th>理由</th><th>見直し条件</th><th>ツールへの反映</th></tr></thead><tbody>
    ${decisions.map(d=>`<tr><td class="meta">${esc(d["日付"])}</td><td><b>${esc(d["テーマ"])}</b></td><td class="wrap">${esc(d["決定内容"])}</td><td class="wrap">${esc(d["理由"])}</td><td class="wrap">${esc(d["見直し条件"])}</td><td class="wrap mini">${esc(DECISION_MAP[d["テーマ"]]||"")}</td></tr>`).join("")}
    </tbody></table></div></div>`;
}];
const DECISION_MAP={
  "商品戦略":"監視ジャンルをカメラ・レンズ中心に再構成し、低単価ゲーム機を外した",
  "仕入れ判断":"並び順の既定を「総合＝貢献利益×照合度×成約件数」に変更（ROI単独で並べない）",
  "購入自動化":"自動購入はしない。候補→キュー→人が購入→「仕入れ済み」",
  "運用原則":"計画値はシートから読み込み、ツールは実績だけを持つ（二重管理しない）",
  "供給量KPI":"購入成功数を「KPI・ファネル」とダッシュボードで計画対比",
  "自動停止":"リスク監視で閾値超過を「発動」として表示（自動での仕入れ停止は未実装）",
  "利益目標再定義":"ダッシュボードに3シナリオと経営判断ルールを表示",
  "資金計画":"在庫原価と工程別の内訳（未到着・検品出品待ち・販売可能）を表示",
};

// ---------- KPI・ファネル
VIEWS.kpi=["上流 / KPI・ファネル",()=>{
  const months=S.plan.monthly||[], act=S.ops.months||[], kp=S.plan.kpi_plan||[], f=S.ops.funnel||[], ind=S.ops.indicators||{};
  const row=i=>act.find(a=>a.month===i);
  return h1("計画と実績の差を、毎週シートに戻す。","実績はツールが自動で集計します。下のボタンで、シートの「KPI実績」「仕入れファネル」にそのまま貼り付けられる形でコピーできます（運用ルール：毎週更新）。")
  + planBanner()
  + h2("· 01","仕入れファネル（当月）","計画は12か月目の基準値を当月の販売計画で按分。経過按分＝月の経過割合ぶんの目安")
  + `<div class="card"><div class="wide"><table><thead><tr><th>工程</th><th class="num">当月目標</th><th class="num">経過按分</th><th class="num">実績</th><th>進捗</th><th class="num">転換率 計画</th><th class="num">転換率 実績</th><th>定義</th></tr></thead><tbody>
    ${f.map(x=>`<tr><td><b>${esc(x.stage)}</b></td><td class="num">${x.target_month??"—"}</td><td class="num">${x.target_to_date??"—"}</td><td class="num"><b>${x.actual??"—"}</b></td>
      <td style="min-width:120px">${bar(x.actual,x.target_to_date||1,num(x.actual)&&x.target_to_date&&x.actual>=x.target_to_date?"ok":"bad")}</td>
      <td class="num">${num(x.conv_plan)?pct(x.conv_plan,1):"—"}</td><td class="num">${pct(x.conv_actual,1)}</td><td class="wrap mini">${esc(x.note||"")}</td></tr>`).join("")}
    </tbody></table></div><div class="foot"><span class="meta">取得商品データ＝初めて見たメルカリ出品数 / AI一次候補＝基準を満たした候補（実データのみ）/ 人間確認＝キュー追加＋見送り</span><button class="btn" id="copyFunnel">実績列をコピー</button></div></div>`
  + h2("· 02","品質・在庫の指標",`シート「KPI実績」の右側の列`)
  + `<div class="kpis">
      ${kpiTile("返品率",ind.return_rate,0.04,{fmt:v=>pct(v,1),better:"low",tag:"上限4%"})}
      ${kpiTile("照合精度（誤仕入れなし率）",num(ind.mispurchase_rate)?1-ind.mispurchase_rate:null,0.95,{fmt:v=>pct(v,1),tag:"P0目標95%"})}
      ${kpiTile("予測利益誤差",ind.forecast_error,0.10,{fmt:v=>pct(v,1),better:"low",tag:"基準10%"})}
      ${kpiTile("90日超在庫率",ind.over90_rate,0.10,{fmt:v=>pct(v,1),better:"low",tag:"上限10%"})}
    </div>`
  + h2("· 03","12か月計画と実績","実績は経過した月のみ")
  + `<div class="card"><div class="wide"><table><thead><tr><th>月</th><th class="num">販売数 計画/実績</th><th class="num">貢献利益/件 計画/実績</th><th class="num">売上 計画/実績</th><th class="num">営業利益 計画/実績</th><th class="num">在庫日数 計画/実績</th><th class="num">仕入成功 計画/実績</th><th>マイルストーン</th></tr></thead><tbody>
    ${months.map(m=>{const i=m["月"],a=row(i),k=kp.find(x=>x["月"]==i)||{};const op=a&&num(a.contribution_man)?a.contribution_man-(m["固定費(万円)"]||0):null;
      const pv=(p,v,fmt,better="high")=>`${fmt(p)} / ${a?`<b class="${num(v)&&num(p)?((better==="high"?v>=p:v<=p)?"pos":"neg"):""}">${fmt(v)}</b>`:'<span class="meta">—</span>'}`;
      return `<tr style="${i==S.ops.month_index?"background:var(--accent-bg)":""}"><td><b>${i}</b><div class="meta">${esc((a&&a.label)||"")}</div></td>
      <td class="num">${pv(m["販売数"],a?.sales,v=>num(v)?v+"件":"—")}</td><td class="num">${pv(m["貢献利益/件(円)"],a?.profit_per_unit,yen)}</td>
      <td class="num">${pv(m["売上(万円)"],a?.revenue_man,manFmt)}</td><td class="num">${pv(m["営業利益(万円)"],op,manFmt)}</td>
      <td class="num">${pv(m["在庫日数"],a?.inventory_days,v=>num(v)?v+"日":"—","low")}</td><td class="num">${pv(k["仕入成功数_計画"],a?.purchases,v=>num(v)?v+"件":"—")}</td>
      <td class="mini">${esc(m["主要マイルストーン"]||"")}</td></tr>`}).join("")||'<tr><td colspan="8" class="empty">計画シート未同期</td></tr>'}
    </tbody></table></div><div class="foot"><span class="meta">「KPI実績」シートの当月行（月〜90日超在庫率の20列）をタブ区切りでコピーします。A列（月）を選んで貼り付けてください。</span>
      <span><select id="kpiMonth">${months.map(m=>`<option value="${m["月"]}" ${m["月"]==S.ops.month_index?"selected":""}>${m["月"]}か月目</option>`).join("")}</select> <button class="btn dark" id="copyKpi">KPI実績の行をコピー</button></span></div></div>`;
},()=>{
  const cp=async(t,msg)=>{try{await navigator.clipboard.writeText(t);toast(msg)}catch(e){prompt("コピーできなかったので、ここから手動でコピーしてください",t)}};
  const k=$("#copyKpi"); if(k) k.onclick=async()=>{const t=await (await fetch("/api/kpi.tsv?month="+$("#kpiMonth").value)).text();cp(t,"コピーしました。シート「KPI実績」の該当月のA列に貼り付けてください")};
  const fb=$("#copyFunnel"); if(fb) fb.onclick=()=>cp((S.ops.funnel||[]).map(x=>x.actual??"").join("\n"),"コピーしました。シート「仕入れファネル」の実績列（D列）の先頭に貼り付けてください");
}];

// ---------- 計画タスク
function dueTasks(){
  const t=S.plan.tasks||[], lim=new Date(Date.now()+7*864e5).toISOString().slice(0,10);
  return t.filter(x=>!/完了|中止/.test(x["ステータス"]||"")&&x["期限"]&&x["期限"]<=lim);
}
VIEWS.tasks=["上流 / 計画タスク",()=>{
  const all=S.plan.tasks||[], today=new Date().toISOString().slice(0,10);
  const open_=all.filter(x=>!/完了|中止/.test(x["ステータス"]||""));
  const overdue=open_.filter(x=>x["期限"]&&x["期限"]<today), soon=dueTasks().filter(x=>!(x["期限"]<today));
  const tool=all.filter(x=>TOOL_TASK[x.ID]);
  const tbl=list=>`<div class="card"><div class="wide"><table><thead><tr><th>ID</th><th>タスク</th><th>完了条件</th><th>期限</th><th>優先度</th><th>担当</th><th>ステータス</th><th>KPI</th></tr></thead><tbody>
    ${list.map(x=>`<tr><td class="meta">${esc(x.ID)}</td><td class="wrap"><b>${esc(x["タスク"])}</b><div class="meta">${esc(x["フェーズ"])}・${esc(x["カテゴリ"])}</div></td><td class="wrap">${esc(x["成果物・完了条件"])}</td>
      <td class="${x["期限"]<today?"overdue":""}">${esc(x["期限"])}</td><td>${esc(x["優先度"])}</td><td class="mini">${esc(x["担当区分"])}${x["担当者"]?"・"+esc(x["担当者"]):""}</td><td>${esc(x["ステータス"])}</td><td class="mini">${esc(x.KPI)}</td></tr>`).join("")||'<tr><td colspan="8" class="empty">該当なし</td></tr>'}</tbody></table></div></div>`;
  return h1("計画のアクションと、ツールの進み具合。","シート「タスク管理」を読み込んで、期限切れと今週の期限を出しています。ステータスの更新はシートで行ってください（ツールは読み取りのみ）。")
  + planBanner()
  + `<div class="chips"><span class="chip">全 <b>${all.length}</b></span><span class="chip">未完了 <b>${open_.length}</b></span><span class="chip ${overdue.length?"sample":""}">期限切れ <b>${overdue.length}</b></span><span class="chip">7日以内 <b>${soon.length}</b></span></div>`
  + h2("· 01","期限切れ",`${overdue.length}件`)+tbl(overdue.slice(0,40))
  + h2("· 02","7日以内に期限",`${soon.length}件`)+tbl(soon)
  + h2("· 03","ツールで実装・自動化したタスク","シートのステータス更新の参考にしてください")
  + `<div class="card"><div class="wide"><table><thead><tr><th>ID</th><th>タスク</th><th>シートの状態</th><th>ツールでの対応</th></tr></thead><tbody>
    ${tool.map(x=>`<tr><td class="meta">${esc(x.ID)}</td><td class="wrap"><b>${esc(x["タスク"])}</b></td><td>${esc(x["ステータス"])}</td><td class="wrap">${esc(TOOL_TASK[x.ID])}</td></tr>`).join("")}</tbody></table></div></div>`;
}];
const TOOL_TASK={
  P10:"利益計算をユニットエコノミクスの費用構成（手数料・広告・送料・梱包・作業・返品為替引当）に改修。さらに海外手数料1.35%・米国関税（DDP）・換金スプレッドを追加",
  P11:"照合で焦点距離・F値・型式末尾（III/IIIx, T2/T3）・箱有無の食い違いを判定。仕入れない理由・検品不合格理由を記録",
  P12:"SKU採番（YYMM-連番）と、購入→到着→検品→出品→販売→発送→着金のステータス管理",
  P13:"KPI実績・仕入れファネルの実績を自動集計し、シートに貼れる形でコピー",
  S04:"仕入れ確定時に予測利益・予測売価を自動保存（実際の仕入れ値で補正）",
  S12:"発送時に送料・関税の実額、着金時に入金額を入力",
  S13:"返品理由・検品不合格理由をコード化して記録",
  S14:"予測利益と実現利益の差（予測利益誤差）をSKU別・全体で自動計算",
  S15:"見送り理由「商品違い」、検品不合格「商品違い」を誤照合データとして蓄積",
  S17:"在庫・値付け画面で30/60/90日の経過と値下げ提案を表示",
  O06:"候補に成約件数（需要）を表示し、総合スコアに反映",
  O08:"30/60/90日ルールで値下げ価格を提案（損益分岐を下回らない）",
  O11:"発送時の実送料を記録し、予測との差を集計",
};

// ---------- 資本シミュレーター
VIEWS.sim=["上流 / 資本シミュレーター",()=>{
  const c=passing(), avg=c.length?c.reduce((a,x)=>a+x.roi,0)/c.length:.3;
  const ue=(S.plan.unit_economics||[]).find(x=>x["項目"]==="仕入ROI");
  sim=sim||{init:8000000,months:12,roi:20,turns:1,reinvest:80,util:60};
  const f=sim, rows=[]; let cap=f.init, cum=0;
  for(let m=1;m<=f.months;m++){const deployed=cap*f.util/100, profit=deployed*(f.roi/100)*f.turns, re=profit*f.reinvest/100; cum+=profit; cap+=re; rows.push({m,cap,profit,cum,withdraw:profit-re})}
  const W=900,H=260,L=70,B=30,T=16,R=16,max=Math.max(...rows.map(r=>r.cap),f.init);
  const X=i=>L+i/(rows.length)*(W-L-R), Y=v=>H-B-v/max*(H-B-T);
  const line=[`${X(0)},${Y(f.init)}`,...rows.map((r,i)=>`${X(i+1)},${Y(r.cap)}`)].join(" ");
  const inp=(k,l,step)=>`<div><label>${l}<input type="number" step="${step}" data-k="${k}" value="${f[k]}"></label></div>`;
  return h1("この資金で、何か月でどこまで行けるか。",`1回転あたりROI・月の回転数・再投資率から資本の推移を試算します。参考：計画シートの仕入ROI（基準）は <b>${ue?pct(ue["基準"]):"—"}</b>、現在の候補の平均ROIは <b>${pct(avg,1)}</b>。在庫日数50日なら回転は月0.6回程度です。初期資本の既定値は、計画の自己資金300万円＋初回融資500万円です。`)
  + `<div class="card"><div class="form">${inp("init","初期資本（円）",100000)}${inp("months","期間（月）",1)}${inp("roi","1回転あたりROI（%）",.5)}${inp("turns","月の回転数",.1)}${inp("reinvest","再投資率（%）",5)}</div>
    <div class="form" style="padding-top:0">${inp("util","資本の稼働率（%）",5)}</div></div>
  <div class="kpis sim" style="margin-top:16px">
    <div class="kpi"><div class="l">${f.months}か月後の資本</div><div class="v">${yen(rows.at(-1)?.cap??f.init)}</div><div class="s">初期比 ×${((rows.at(-1)?.cap??f.init)/f.init).toFixed(2)}</div></div>
    <div class="kpi"><div class="l">累計利益</div><div class="v">${yen(cum)}</div><div class="s">月平均 ${yen(cum/Math.max(f.months,1))}</div></div>
    <div class="kpi"><div class="l">引き出し可能額</div><div class="v">${yen(rows.reduce((a,r)=>a+r.withdraw,0))}</div><div class="s">再投資に回さない分</div></div>
    <div class="kpi"><div class="l">月の仕入れ額（初月）</div><div class="v">${yen(f.init*f.util/100*f.turns)}</div><div class="s">稼働資本 × 回転数</div></div></div>
  <div class="card" style="padding:16px;margin-top:16px"><svg viewBox="0 0 ${W} ${H}" width="100%">
    ${[0,.25,.5,.75,1].map(t=>`<line x1="${L}" x2="${W-R}" y1="${Y(max*t)}" y2="${Y(max*t)}" stroke="var(--line)"/><text x="${L-8}" y="${Y(max*t)+3}" text-anchor="end">${man(max*t)}</text>`).join("")}
    ${rows.map((r,i)=>`<text x="${X(i+1)}" y="${H-10}" text-anchor="middle">${r.m}</text>`).join("")}
    <polyline points="${line}" fill="none" stroke="var(--profit)" stroke-width="2.5"/></svg></div>`;
},()=>{$$("[data-k]").forEach(i=>i.onchange=()=>{sim[i.dataset.k]=parseFloat(i.value)||0;render()})}];
