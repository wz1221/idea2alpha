// Cloudflare Worker：金鑰、限流、快取、驗證集中處理。AI 只讀事實表，輸出須通過數字核對與禁語過濾。
const DICT = {rev:[-50,500,"近3月營收年增率%"],inst:[0,20,"投信外資連買日數"],var5:[0,1,"下檔風險排名"],pe:[0,100,"本益比"],roe:[-50,60,"ROE%"],
  mom:[0,1,"12-1月動能百分位"],ma:[0,1,"均線多頭排列(1是)"],mg:[-50,50,"10日融資變動%"],nh:[0,100,"距52週高點%"],yld:[0,20,"現金殖利率%"]};
const OPS = [">", ">=", "<", "<=", "=="];
const BAN = /必漲|保證|建議買進|目標價|穩賺|一定會|買進價|停損價/;
const cors = e => ({"access-control-allow-origin": e.ALLOW_ORIGIN || "*", "access-control-allow-headers": "content-type", "access-control-allow-methods": "POST,OPTIONS"});
const json = (e, o, s = 200) => new Response(JSON.stringify(o), {status: s, headers: {"content-type": "application/json", ...cors(e)}});

async function llm(e, system, user) {
  const r = await fetch("https://api.groq.com/openai/v1/chat/completions", {method: "POST",
    headers: {"authorization": `Bearer ${e.GROQ_API_KEY}`, "content-type": "application/json"},
    body: JSON.stringify({model: e.MODEL, temperature: 0.2, max_tokens: 900,
      response_format: {type: "json_object"},
      messages: [{role: "system", content: system}, {role: "user", content: user}]})});
  const j = await r.json();
  return JSON.parse(j.choices?.[0]?.message?.content || "{}");
}
const twice = async fn => { for (let i = 0; i < 2; i++) { try { const r = await fn(); if (r) return r; } catch (_) {} } return null; };

function validRules(rs) {  // 欄位在字典內、運算子在白名單、數值在合理範圍
  if (!Array.isArray(rs)) return null;
  const out = [];
  for (const r of rs) {
    const d = DICT[r?.f];
    if (!d || !OPS.includes(r.op) || typeof r.v !== "number" || r.v < d[0] || r.v > d[1]) return null;
    out.push({f: r.f, op: r.op, v: r.v});
  }
  return out;
}
function numsOK(text, facts) {  // 文字中的每個數字（扣掉〔Fxx〕）都必須出現在事實表，容許四捨五入
  const allowed = facts.flatMap(f => (String(f.value).match(/-?\d+(\.\d+)?/g) || []).map(Number));
  const nums = (text.replace(/〔[^〕]*〕/g, "").match(/-?\d+(\.\d+)?/g) || []).map(Number);
  return nums.every(n => allowed.some(a => Math.abs(n - a) <= Math.max(0.051, Math.abs(a) * 0.01)));
}
function cleanPoints(ps, facts) {  // 引用必填、編號須存在、無禁語、數字核對通過
  const ids = new Set(facts.map(f => f.id));
  return (Array.isArray(ps) ? ps : []).filter(p => typeof p?.text === "string" && Array.isArray(p.refs) && p.refs.length &&
    p.refs.every(x => ids.has(x)) && !BAN.test(p.text) && numsOK(p.text, facts)).slice(0, 3);
}
const factText = f => f.map(x => `${x.id} ${x.name}: ${x.value}`).join("\n");
const RULE = "只能使用事實表中的數字，不得自行計算或回憶任何財務數字；每點必須在 refs 填事實編號；不得出現必漲、保證、建議買進、目標價等字眼。只輸出 JSON。";

const H = {
  async translate(e, b) {
    const dict = Object.fromEntries(Object.entries(DICT).map(([k, v]) => [k, `${v[2]} 範圍${v[0]}~${v[1]}`]));
    const sys = `你是選股條件翻譯員。把使用者一句話轉成 {"rules":[{"f":因子代號,"op":">|>=|<|<=|==","v":數字}]}。因子字典：${JSON.stringify(dict)}。字典外的概念不要硬翻。只輸出 JSON。`;
    const r = await twice(async () => validRules((await llm(e, sys, String(b.text).slice(0, 200))).rules));
    return r ? {rules: r} : {rules: [], error: "無法轉成合法條件"};
  },
  async explain(e, b) {
    const sys = `你是解說員。用三句白話解釋這檔股票目前的狀態。輸出 {"sentences":[{"text":"","refs":["F01"]}]}。${RULE}`;
    const r = await twice(async () => { const s = cleanPoints((await llm(e, sys, factText(b.facts))).sentences, b.facts); return s.length ? s : null; });
    return r ? {sentences: r} : {error: "解說未通過核對"};
  },
  async debate(e, b) {
    const side = (name, ask) => twice(async () => {
      const j = await llm(e, `你是${name}辯手。${ask}輸出 {"points":[{"text":"","refs":["F01"]}]}，最多三點。${RULE}`, factText(b.facts));
      const p = cleanPoints(j.points, b.facts); return p.length ? p : null; });
    const [bull, bear] = await Promise.all([side("多方", "只從有利面論述。"), side("空方", "只從不利面論述。")]);
    if (!bull && !bear) return {error: "辯論未通過核對"};
    const judge = await twice(async () => {
      const j = await llm(e, `你是裁判。整理多空關鍵分歧，並把失效條件寫成條件 JSON。輸出 {"disputes":[{"text":"","refs":["F01"]}],"invalid_if":[{"f":"","op":"","v":0}]}。${RULE}`,
        factText(b.facts) + "\n多方:" + JSON.stringify(bull) + "\n空方:" + JSON.stringify(bear));
      const inv = validRules(j.invalid_if); return inv ? {disputes: cleanPoints(j.disputes, b.facts), invalid_if: inv} : null; });
    return {bull: bull || [], bear: bear || [], judge};
  },
};

export default {
  async fetch(req, e) {
    if (req.method === "OPTIONS") return new Response(null, {headers: cors(e)});
    const path = new URL(req.url).pathname.slice(1);
    if (req.method !== "POST" || !H[path]) return json(e, {error: "not found"}, 404);
    const body = await req.json().catch(() => null);
    if (!body || (path === "translate" ? !body.text : !Array.isArray(body.facts) || !body.facts.length)) return json(e, {error: "bad request"}, 400);
    if (e.CACHE) {  // 限流：每 IP 每分鐘 20 次
      const ip = req.headers.get("cf-connecting-ip") || "x", k = `rl:${ip}:${Math.floor(Date.now() / 60000)}`;
      const n = +(await e.CACHE.get(k) || 0); if (n >= 20) return json(e, {error: "rate limited"}, 429);
      await e.CACHE.put(k, String(n + 1), {expirationTtl: 120});
    }
    const hex = [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(body))))].map(x => x.toString(16).padStart(2, "0")).join("");
    const key = `c:${path}:${hex}`, hit = e.CACHE && await e.CACHE.get(key);
    if (hit) return new Response(hit, {headers: {"content-type": "application/json", ...cors(e)}});
    const out = await H[path](e, body);
    if (e.CACHE && !out.error) await e.CACHE.put(key, JSON.stringify(out), {expirationTtl: 86400});
    return json(e, out, out.error && path !== "translate" ? 502 : 200);
  },
};
