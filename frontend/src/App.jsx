import { useState, useCallback, useRef, useEffect } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, RadarChart, Radar, PolarGrid,
  PolarAngleAxis, PolarRadiusAxis,
} from "recharts";

// -- Config --------------------------------------------------------------------
// __API_BASE__ is injected by Vite at build time (see vite.config.js).
// Falls back to empty string so proxy rules in nginx / vite dev server handle routing.
const API_BASE = (typeof __API_BASE__ !== "undefined" && __API_BASE__) ? __API_BASE__ : "";

const COLORS = ["#58a6ff","#3fb950","#f0b429","#ff7b72","#d2a8ff","#56d364","#79c0ff","#ffa657"];
const SLOW_COLOR = "#ff7b72";

// -- API helpers ---------------------------------------------------------------
async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// -- Fallback client-side parser (when backend unavailable) -----------------
function parseLogLocal(rawLog, staticTags = ["service-abc"]) {
  const lines = rawLog.split("\n");
  const stages = [], methodTimings = {}, methodTags = {}, callTree = [];
  let currentStage = null, stack = [];
  const tagPats = staticTags.map(t => ({
    tag: t, re: new RegExp(String.raw`${t.replace(/-/g,"\\-")}:\s*([\w_]+)\s*$`)
  }));
  const genericRe = /^([\w][\w-]*[\w]):\s+([\w_]+)\s*$/;
  const timingRe = /^([\w_]+):time-elapsed-seconds:([\d.]+)/;
  const stageRe = /StageName:\s*(.+)/i;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i], trimmed = line.trim();
    if (!trimmed) continue;
    const indent = line.length - line.trimStart().length;

    const sm = trimmed.match(stageRe);
    if (sm) {
      if (currentStage) stages.push(currentStage);
      currentStage = { name: sm[1].trim(), methods: [], totalTime: 0 };
      stack = []; continue;
    }

    const tm = trimmed.match(timingRe);
    if (tm) {
      const [, name, sec] = tm, el = parseFloat(sec);
      (methodTimings[name] = methodTimings[name] || []).push(el);
      const node = [...stack].reverse().find(n => n.name === name);
      if (node) node.elapsed = el;
      if (currentStage) { currentStage.methods.push({ name, elapsed: el }); currentStage.totalTime += el; }
      continue;
    }

    let mTag = null, mMethod = null;
    for (const { tag, re } of tagPats) {
      const m = trimmed.match(re);
      if (m) { mTag = tag; mMethod = m[1]; break; }
    }
    if (!mTag) {
      const gm = trimmed.match(genericRe);
      if (gm && !["method","stage","log","info","error","warn"].includes(gm[1])) {
        mTag = gm[1]; mMethod = gm[2];
      }
    }
    if (mTag && mMethod) {
      (methodTags[mMethod] = methodTags[mMethod] || new Set()).add(mTag);
      const node = { name: mMethod, service_tag: mTag, elapsed: null, indent, children: [] };
      while (stack.length && stack[stack.length-1].indent >= indent) {
        const p = stack.pop();
        if (stack.length) stack[stack.length-1].children.push(p); else callTree.push(p);
      }
      stack.push(node);
    }
  }
  while (stack.length) {
    const n = stack.pop();
    if (stack.length) stack[stack.length-1].children.push(n); else callTree.push(n);
  }
  if (currentStage) stages.push(currentStage);

  const timingStats = Object.entries(methodTimings).map(([name, vals]) => ({
    name, service_tags: [...(methodTags[name] || [])],
    total: +vals.reduce((a,b)=>a+b,0).toFixed(3),
    avg: +(vals.reduce((a,b)=>a+b,0)/vals.length).toFixed(3),
    calls: vals.length,
    max: +Math.max(...vals).toFixed(3),
    min: +Math.min(...vals).toFixed(3),
    p95: +([...vals].sort((a,b)=>a-b)[Math.floor(vals.length*0.95)]).toFixed(3),
    is_slow: false,
  })).sort((a,b) => b.total - a.total);

  return { stages, timing_stats: timingStats, call_tree: callTree,
    detected_tags: [...new Set(timingStats.flatMap(s=>s.service_tags))],
    total_duration: stages.reduce((a,s)=>a+s.totalTime,0),
    log_lines: lines.length, warnings: [], source_methods_matched: 0, ai_report: "" };
}

// -- Subcomponents -------------------------------------------------------------

function Btn({ children, onClick, loading, disabled, variant = "primary", small, style = {} }) {
  const vs = {
    primary:   { background: "#238636", color: "#fff", border: "1px solid #2ea043" },
    secondary: { background: "#21262d", color: "#e6edf3", border: "1px solid #30363d" },
    ghost:     { background: "transparent", color: "#8b949e", border: "1px solid #21262d" },
    danger:    { background: "#b62324", color: "#fff", border: "1px solid #da3633" },
    info:      { background: "#1158a7", color: "#fff", border: "1px solid #388bfd" },
  };
  return (
    <button onClick={onClick} disabled={disabled || loading} style={{
      ...vs[variant], padding: small ? "4px 10px" : "7px 14px", borderRadius: 6,
      cursor: disabled || loading ? "not-allowed" : "pointer",
      fontSize: small ? 11 : 12, fontFamily: "inherit", fontWeight: 700,
      opacity: disabled || loading ? 0.5 : 1, whiteSpace: "nowrap",
      transition: "opacity 0.15s", ...style,
    }}>{loading ? "... ..." : children}</button>
  );
}

function Chip({ label, color }) {
  return <div style={{ background:`${color}18`, border:`1px solid ${color}40`, color,
    borderRadius:20, padding:"3px 10px", fontSize:11, fontWeight:700 }}>{label}</div>;
}

function Badge({ text, ok }) {
  return <span style={{ padding:"2px 8px", borderRadius:20, fontSize:10, fontWeight:700,
    background: ok ? "#0f2" + "2" : "#f022", color: ok ? "#3fb950" : "#ff7b72",
    border: `1px solid ${ok?"#3fb95040":"#ff7b7240"}` }}>{text}</span>;
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background:"#0d1117", border:"1px solid #30363d", borderRadius:8,
      padding:"10px 14px", fontFamily:"monospace", fontSize:12, color:"#e6edf3" }}>
      <div style={{ fontWeight:700, color:"#58a6ff", marginBottom:4 }}>{label}</div>
      {payload.map((p,i) => <div key={i} style={{ color:p.color }}>
        {p.name}: <strong>{typeof p.value === "number" ? p.value.toFixed(2)+"s" : p.value}</strong>
      </div>)}
    </div>
  );
}

function CallNode({ node, depth=0 }) {
  const [open, setOpen] = useState(depth < 2);
  const has = node.children?.length > 0;
  const heat = node.elapsed ? Math.min(1, node.elapsed/10) : 0;
  const heatColor = node.elapsed
    ? `rgba(${Math.round(255*heat)},${Math.round(255*(1-heat)*0.6)},60,0.85)` : "#2a3040";
  return (
    <div style={{ marginLeft: depth*18, marginBottom:2 }}>
      <div onClick={() => has && setOpen(!open)} style={{
        display:"flex", alignItems:"center", gap:8, padding:"5px 10px", borderRadius:4,
        cursor: has?"pointer":"default", background:"rgba(255,255,255,0.04)",
        borderLeft:`3px solid ${heatColor}`, fontFamily:"monospace", fontSize:12,
      }}>
        <span style={{ color:"#4a9eff", minWidth:14 }}>{has?(open?"":""):"*"}</span>
        <span style={{ color:"#7ecfff" }}>{node.service_tag}:</span>
        <span style={{ color:"#e2e8f0", fontWeight:600 }}>{node.name}</span>
        {node.elapsed != null && (
          <span style={{ marginLeft:"auto", background:heatColor, color:"#fff",
            padding:"1px 8px", borderRadius:20, fontSize:11, fontWeight:700 }}>
            {node.elapsed}s
          </span>
        )}
      </div>
      {open && has && <div style={{ marginTop:2 }}>
        {node.children.map((c,i) => <CallNode key={i} node={c} depth={depth+1} />)}
      </div>}
    </div>
  );
}

function MdRender({ text }) {
  const lines = text.split("\n");
  return <div>{lines.map((line, i) => {
    if (line.startsWith("## ")) return <h2 key={i} style={{ color:"#58a6ff", fontSize:15, marginTop:20, marginBottom:6, borderBottom:"1px solid #21262d", paddingBottom:4 }}>{line.slice(3)}</h2>;
    if (line.startsWith("# "))  return <h1 key={i} style={{ color:"#79c0ff", fontSize:18, marginTop:16, marginBottom:8 }}>{line.slice(2)}</h1>;
    if (line.startsWith("### ")) return <h3 key={i} style={{ color:"#d2a8ff", fontSize:13, marginTop:14, marginBottom:4 }}>{line.slice(4)}</h3>;
    if (line.startsWith("- ")) return <div key={i} style={{ margin:"3px 0 3px 12px", color:"#8b949e", display:"flex", gap:6 }}>
      <span style={{ color:"#30363d" }}>></span>
      <span dangerouslySetInnerHTML={{ __html: line.slice(2).replace(/\*\*(.+?)\*\*/g,'<strong style="color:#e6edf3">$1</strong>').replace(/`(.+?)`/g,'<code style="background:#161b22;color:#79c0ff;padding:1px 5px;border-radius:3px;font-size:11px">$1</code>') }} />
    </div>;
    if (line.match(/^\d+\. /)) {
      const m = line.match(/^(\d+)\. \*\*(.+?)\*\*(.*)/);
      if (m) return <div key={i} style={{ margin:"6px 0", display:"flex", gap:8 }}>
        <span style={{ color:"#58a6ff", fontWeight:700, minWidth:20 }}>{m[1]}.</span>
        <span><strong style={{ color:"#79c0ff" }}>{m[2]}</strong><span style={{ color:"#8b949e" }}>{m[3]}</span></span>
      </div>;
    }
    if (line.startsWith("```")) return <div key={i} style={{ height:4 }} />;
    if (line.trim()) return <p key={i} style={{ color:"#8b949e", margin:"4px 0", lineHeight:1.6, fontSize:13 }}
      dangerouslySetInnerHTML={{ __html: line.replace(/\*\*(.+?)\*\*/g,'<strong style="color:#e6edf3">$1</strong>').replace(/`(.+?)`/g,'<code style="background:#161b22;color:#79c0ff;padding:1px 5px;border-radius:3px;font-size:11px">$1</code>') }} />;
    return <div key={i} style={{ height:6 }} />;
  })}</div>;
}

// -- Config Panel --------------------------------------------------------------

function ConfigPanel({ serverConfig, onSaved }) {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState({});
  const [testResults, setTestResults] = useState({});
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (serverConfig) setCfg(JSON.parse(JSON.stringify(serverConfig)));
  }, [serverConfig]);

  if (!cfg) return <div style={{ color:"#8b949e", padding:40, textAlign:"center" }}>Loading config...</div>;

  const set = (path, val) => {
    const keys = path.split(".");
    setCfg(prev => {
      const next = JSON.parse(JSON.stringify(prev));
      let obj = next;
      for (let k of keys.slice(0,-1)) obj = obj[k];
      obj[keys[keys.length-1]] = val;
      return next;
    });
  };

  const save = async () => {
    setSaving(true); setMsg("");
    try {
      await apiFetch("/api/config", { method:"PUT", body: JSON.stringify({ config: cfg }) });
      setMsg("[OK] Config saved");
      onSaved?.();
    } catch(e) { setMsg("[x] " + e.message); }
    setSaving(false);
  };

  const testAI = async (provider) => {
    setTesting(t => ({...t, [provider]:true}));
    try {
      const r = await apiFetch("/api/config/test-ai", { method:"POST", body: JSON.stringify({ provider }) });
      setTestResults(t => ({...t, [provider]: r}));
    } catch(e) { setTestResults(t => ({...t, [provider]: { status:"error", error:e.message }})); }
    setTesting(t => ({...t, [provider]:false}));
  };

  const testGH = async (repo) => {
    setTesting(t => ({...t, gh:true}));
    try {
      const r = await apiFetch("/api/config/test-github", { method:"POST", body: JSON.stringify({ url: repo.url, branch: repo.branch, paths: repo.paths, extensions: repo.extensions }) });
      setTestResults(t => ({...t, gh: r}));
    } catch(e) { setTestResults(t => ({...t, gh: { status:"error", error:e.message }})); }
    setTesting(t => ({...t, gh:false}));
  };

  const addRepo = () => set("github.repos", [...cfg.github.repos, { url:"", branch:"main", paths:["src/"], extensions:[".groovy",".java"], enabled:true }]);
  const removeRepo = (i) => set("github.repos", cfg.github.repos.filter((_,j) => j!==i));
  const addTag = () => set("pipeline.static_tags", [...cfg.pipeline.static_tags, ""]);
  const removeTag = (i) => set("pipeline.static_tags", cfg.pipeline.static_tags.filter((_,j)=>j!==i));

  const S = { // styles
    section: { background:"#161b22", border:"1px solid #21262d", borderRadius:8, padding:18, marginBottom:16 },
    label: { fontSize:11, color:"#8b949e", textTransform:"uppercase", letterSpacing:1, marginBottom:6, display:"block" },
    input: { width:"100%", background:"#0d1117", border:"1px solid #30363d", borderRadius:6, padding:"7px 10px", color:"#e6edf3", fontFamily:"inherit", fontSize:12, boxSizing:"border-box" },
    row: { display:"flex", gap:10, alignItems:"center", marginBottom:10 },
    h: { fontSize:13, color:"#58a6ff", marginBottom:12, fontWeight:700, borderBottom:"1px solid #21262d", paddingBottom:6 },
  };

  return (
    <div style={{ maxWidth:800 }}>
      {/* AI Backend */}
      <div style={S.section}>
        <div style={S.h}>[AI] AI Backend</div>
        <div style={S.row}>
          <div style={{ flex:1 }}>
            <label style={S.label}>Provider</label>
            <select value={cfg.ai.provider} onChange={e=>set("ai.provider",e.target.value)} style={S.input}>
              <option value="anthropic">Anthropic Claude (cloud)</option>
              <option value="ollama">Ollama (local GPU)</option>
              <option value="private">Private / Enterprise endpoint</option>
            </select>
          </div>
          <div style={{ flex:1 }}>
            <label style={S.label}>GPU Acceleration</label>
            <div style={{ display:"flex", alignItems:"center", gap:10 }}>
              <label style={{ display:"flex", alignItems:"center", gap:6, cursor:"pointer", fontSize:13, color:"#e6edf3" }}>
                <input type="checkbox" checked={cfg.ai.gpu_enabled} onChange={e=>set("ai.gpu_enabled",e.target.checked)} />
                Enable GPU
              </label>
              {cfg.ai.gpu_enabled && (
                <input type="number" value={cfg.ai.gpu_layers} onChange={e=>set("ai.gpu_layers",+e.target.value)}
                  placeholder="GPU layers" style={{...S.input, width:100}} />
              )}
            </div>
          </div>
        </div>

        {cfg.ai.provider === "anthropic" && (
          <div>
            <label style={S.label}>Anthropic API Key</label>
            <div style={S.row}>
              <input type="password" value={cfg.ai.anthropic.api_key} onChange={e=>set("ai.anthropic.api_key",e.target.value)} placeholder="sk-ant-... or env:ANTHROPIC_API_KEY" style={S.input} />
              <Btn small onClick={() => testAI("anthropic")} loading={testing.anthropic} variant="info">Test</Btn>
            </div>
            <label style={S.label}>Model</label>
            <input value={cfg.ai.anthropic.model} onChange={e=>set("ai.anthropic.model",e.target.value)} style={S.input} />
          </div>
        )}

        {cfg.ai.provider === "ollama" && (
          <div>
            <div style={S.row}>
              <div style={{ flex:2 }}>
                <label style={S.label}>Ollama URL</label>
                <input value={cfg.ai.ollama.base_url} onChange={e=>set("ai.ollama.base_url",e.target.value)} style={S.input} />
              </div>
              <div style={{ flex:1 }}>
                <label style={S.label}>Model</label>
                <input value={cfg.ai.ollama.model} onChange={e=>set("ai.ollama.model",e.target.value)} style={S.input} placeholder="codellama:13b" />
              </div>
              <Btn small onClick={() => testAI("ollama")} loading={testing.ollama} variant="info">Test</Btn>
            </div>
          </div>
        )}

        {cfg.ai.provider === "private" && (
          <div>
            <div style={S.row}>
              <div style={{ flex:2 }}>
                <label style={S.label}>API Base URL (OpenAI-compatible)</label>
                <input value={cfg.ai.private.base_url} onChange={e=>set("ai.private.base_url",e.target.value)} style={S.input} placeholder="http://localhost:8080/v1" />
              </div>
              <div style={{ flex:1 }}>
                <label style={S.label}>Model name</label>
                <input value={cfg.ai.private.model} onChange={e=>set("ai.private.model",e.target.value)} style={S.input} />
              </div>
            </div>
            <div style={S.row}>
              <div style={{ flex:1 }}>
                <label style={S.label}>API Key (optional)</label>
                <input type="password" value={cfg.ai.private.api_key} onChange={e=>set("ai.private.api_key",e.target.value)} style={S.input} placeholder="leave empty if not required" />
              </div>
              <div>
                <label style={S.label}>Verify SSL</label>
                <label style={{ display:"flex",alignItems:"center",gap:6,cursor:"pointer",fontSize:13,color:"#e6edf3" }}>
                  <input type="checkbox" checked={cfg.ai.private.verify_ssl} onChange={e=>set("ai.private.verify_ssl",e.target.checked)} />
                  Verify
                </label>
              </div>
              <Btn small onClick={() => testAI("private")} loading={testing.private} variant="info">Test</Btn>
            </div>
          </div>
        )}

        {cfg.network?.private_only_mode && cfg.ai.provider === "anthropic" && (
          <div style={{ marginTop:6, padding:"6px 10px", borderRadius:6, background:"#3d1a1a", border:"1px solid #ff7b7240", color:"#ffa198", fontSize:12 }}>
            [!] PRIVATE-ONLY MODE: Anthropic (cloud) is blocked. Use ollama or private provider.
          </div>
        )}
        {testResults[cfg.ai.provider] && (
          <div style={{ marginTop:8, padding:"6px 12px", borderRadius:6, fontSize:12,
            background: testResults[cfg.ai.provider].status==="ok" ? "#0f22" : "#f022",
            border: `1px solid ${testResults[cfg.ai.provider].status==="ok" ? "#3fb95040":"#ff7b7240"}`,
            color: testResults[cfg.ai.provider].status==="ok" ? "#3fb950":"#ff7b72" }}>
            {testResults[cfg.ai.provider].status==="ok"
              ? `[OK] Connected: ${testResults[cfg.ai.provider].response}`
              : `[x] ${testResults[cfg.ai.provider].error}`}
          </div>
        )}
      </div>

      {/* GitHub */}
      <div style={S.section}>
        <div style={S.h}>[octo] GitHub Integration</div>
        <div style={S.row}>
          <div style={{ flex:1 }}>
            <label style={S.label}>Repository Type</label>
            <select value={cfg.github.type} onChange={e=>set("github.type",e.target.value)} style={S.input}>
              <option value="public">Public</option>
              <option value="private">Private (token required)</option>
            </select>
          </div>
          {cfg.github.type === "private" && (
            <div style={{ flex:2 }}>
              <label style={S.label}>GitHub Token</label>
              <input type="password" value={cfg.github.token} onChange={e=>set("github.token",e.target.value)} placeholder="ghp_... or env:GITHUB_TOKEN" style={S.input} />
            </div>
          )}
        </div>

        <div style={{ marginTop:12 }}>
          <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:8 }}>
            <label style={{...S.label, marginBottom:0 }}>Source Repos for Code Correlation</label>
            <Btn small onClick={addRepo} variant="secondary">+ Add Repo</Btn>
          </div>
          {cfg.github.repos.map((repo, i) => (
            <div key={i} style={{ background:"#0d1117", borderRadius:6, padding:12, marginBottom:8, border:"1px solid #21262d" }}>
              <div style={S.row}>
                <input value={repo.url} onChange={e=>{const r=[...cfg.github.repos];r[i]={...r[i],url:e.target.value};set("github.repos",r);}}
                  placeholder="https://github.com/org/repo" style={{...S.input, flex:3}} />
                <input value={repo.branch} onChange={e=>{const r=[...cfg.github.repos];r[i]={...r[i],branch:e.target.value};set("github.repos",r);}}
                  placeholder="branch" style={{...S.input, flex:1, width:80}} />
                <label style={{ display:"flex",alignItems:"center",gap:4,fontSize:12,color:"#e6edf3",cursor:"pointer",whiteSpace:"nowrap" }}>
                  <input type="checkbox" checked={repo.enabled} onChange={e=>{const r=[...cfg.github.repos];r[i]={...r[i],enabled:e.target.checked};set("github.repos",r);}} />
                  Enabled
                </label>
                <Btn small onClick={() => testGH(repo)} loading={testing.gh} variant="info">Test</Btn>
                <Btn small onClick={() => removeRepo(i)} variant="danger">[x]</Btn>
              </div>
              <div style={S.row}>
                <div style={{ flex:1 }}>
                  <label style={S.label}>Scan paths (comma-sep)</label>
                  <input value={repo.paths.join(",")} onChange={e=>{const r=[...cfg.github.repos];r[i]={...r[i],paths:e.target.value.split(",").map(s=>s.trim())};set("github.repos",r);}}
                    style={S.input} placeholder="src/,vars/" />
                </div>
                <div style={{ flex:1 }}>
                  <label style={S.label}>File extensions (comma-sep)</label>
                  <input value={repo.extensions.join(",")} onChange={e=>{const r=[...cfg.github.repos];r[i]={...r[i],extensions:e.target.value.split(",").map(s=>s.trim())};set("github.repos",r);}}
                    style={S.input} placeholder=".groovy,.java" />
                </div>
              </div>
            </div>
          ))}
          {cfg.network?.private_only_mode && cfg.github?.type === "public" && (
            <div style={{ padding:"6px 10px", borderRadius:6, background:"#3d1a1a", border:"1px solid #ff7b7240", color:"#ffa198", fontSize:12, marginTop:4 }}>
              [!] PRIVATE-ONLY MODE: public github.com blocked. GitHub Enterprise (type: private) is allowed.
            </div>
          )}
          {testResults.gh && (
            <div style={{ padding:"6px 12px", borderRadius:6, fontSize:12, marginTop:4,
              background: testResults.gh.status==="ok"?"#0f22":"#f022",
              border:`1px solid ${testResults.gh.status==="ok"?"#3fb95040":"#ff7b7240"}`,
              color: testResults.gh.status==="ok"?"#3fb950":"#ff7b72" }}>
              {testResults.gh.status==="ok"
                ? `[OK] ${testResults.gh.total_files} files found, ${testResults.gh.matching_files} matching. Sample: ${testResults.gh.sample?.slice(0,3).join(", ")}`
                : `[x] ${testResults.gh.error}`}
            </div>
          )}
        </div>
      </div>

      {/* Pipeline Tags */}
      <div style={S.section}>
        <div style={S.h}>[tag] Pipeline Tags</div>
        <p style={{ fontSize:12, color:"#8b949e", marginBottom:12 }}>
          Static pipeline prefixes used in log lines like <code style={{ color:"#79c0ff" }}>service-abc: method_name</code>.
          Add all tags used across your pipelines.
        </p>
        {cfg.pipeline.static_tags.map((tag, i) => (
          <div key={i} style={{ ...S.row, marginBottom:6 }}>
            <input value={tag} onChange={e=>{const t=[...cfg.pipeline.static_tags];t[i]=e.target.value;set("pipeline.static_tags",t);}}
              placeholder="service-abc" style={{...S.input, flex:1}} />
            <Btn small onClick={() => removeTag(i)} variant="danger">[x]</Btn>
          </div>
        ))}
        <Btn small onClick={addTag} variant="secondary">+ Add Tag</Btn>
        <div style={{ marginTop:16 }}>
          <label style={S.label}>Method Start Pattern (use {"{tag}"} placeholder)</label>
          <input value={cfg.pipeline.method_start_pattern} onChange={e=>set("pipeline.method_start_pattern",e.target.value)} style={S.input} />
          <label style={{...S.label, marginTop:8}}>Timing Pattern</label>
          <input value={cfg.pipeline.timing_pattern} onChange={e=>set("pipeline.timing_pattern",e.target.value)} style={S.input} />
        </div>
      </div>

      {/* Analysis */}
      <div style={S.section}>
        <div style={S.h}>[cfg] Analysis Settings</div>
        <div style={S.row}>
          <div style={{ flex:1 }}>
            <label style={S.label}>Slow method percentile threshold</label>
            <input type="number" value={cfg.analysis.slow_method_percentile} onChange={e=>set("analysis.slow_method_percentile",+e.target.value)}
              min={50} max={99} style={S.input} />
          </div>
          <div style={{ flex:1 }}>
            <label style={S.label}>Max log chars sent to AI</label>
            <input type="number" value={cfg.analysis.max_log_chars_for_ai} onChange={e=>set("analysis.max_log_chars_for_ai",+e.target.value)} style={S.input} />
          </div>
          <div style={{ flex:1 }}>
            <label style={S.label}>Max source chars sent to AI</label>
            <input type="number" value={cfg.analysis.max_source_chars_for_ai} onChange={e=>set("analysis.max_source_chars_for_ai",+e.target.value)} style={S.input} />
          </div>
        </div>
      </div>

      <div style={{ display:"flex", gap:10, alignItems:"center" }}>
        <Btn onClick={save} loading={saving}>[save] Save Config</Btn>
        {msg && <span style={{ fontSize:12, color: msg.startsWith("[OK]")?"#3fb950":"#ff7b72" }}>{msg}</span>}
      </div>
    </div>
  );
}

// -- Sample log ----------------------------------------------------------------
const SAMPLE_LOG = `[2024-01-15T10:00:15.123z] StageName: Build
Starting build process...
service-abc: method_1
  Initializing dependencies
  service-abc: method_2
    Loading configuration
    service-abc: method_3
    Processing core modules
    method_3:time-elapsed-seconds:2
    service-abc: method_4
    Compiling assets
    method_4:time-elapsed-seconds:1
  method_2:time-elapsed-seconds:5
  service-abc: method_5
  Validating output
  method_5:time-elapsed-seconds:2
method_1:time-elapsed-seconds:10

[2024-01-15T10:00:26.456z] StageName: Test
service-test: test_runner
  service-test: unit_tests
    service-test: auth_tests
    auth_tests:time-elapsed-seconds:3
    service-test: api_tests
    api_tests:time-elapsed-seconds:4
  unit_tests:time-elapsed-seconds:8
  service-test: integration_tests
  integration_tests:time-elapsed-seconds:12
test_runner:time-elapsed-seconds:22

[2024-01-15T10:00:49.789z] StageName: Deploy
service-deploy: deploy_main
  service-deploy: docker_build
  docker_build:time-elapsed-seconds:15
  service-deploy: push_registry
  push_registry:time-elapsed-seconds:5
  service-deploy: k8s_apply
  k8s_apply:time-elapsed-seconds:8
  service-deploy: health_check
  health_check:time-elapsed-seconds:3
deploy_main:time-elapsed-seconds:32`;

// -- Main App ------------------------------------------------------------------

export default function App() {
  const [logText, setLogText] = useState("");
  const [fileUrl, setFileUrl] = useState("");
  const [parsed, setParsed] = useState(null);
  const [activeTab, setActiveTab] = useState("input");
  const [loading, setLoading] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [chartType, setChartType] = useState("total");
  const [customTags, setCustomTags] = useState("");   // per-request tag override
  const [backendStatus, setBackendStatus] = useState(null); // null=unknown, true=up, false=down
  const [healthData, setHealthData] = useState(null);       // raw /health response
  const [serverConfig, setServerConfig] = useState(null);
  const [batchProgress, setBatchProgress] = useState(null); // {batch,total,label,reports:[]}
  const fileInputRef = useRef();

  // Check backend health on mount
  useEffect(() => {
    apiFetch("/health")
      .then(h => { setBackendStatus(true); setHealthData(h); loadConfig(); })
      .catch(() => { setBackendStatus(false); setHealthData(null); });
  }, []);

  const loadConfig = async () => {
    try {
      const [cfg, h] = await Promise.all([
        apiFetch("/api/config"),
        apiFetch("/health"),
      ]);
      setServerConfig(cfg);
      setHealthData(h);
      setBackendStatus(true);
    } catch { setBackendStatus(false); }
  };

  const isBatchMode = (text) => {
    if (!healthData) return false;
    const mode = healthData.batch_mode ?? "auto";
    if (mode === "always") return true;
    if (mode === "never")  return false;
    return (text.match(/\n/g) || []).length >= (healthData.batch_threshold_lines ?? 500);
  };

  const analyzeBatch = async (text) => {
    const tags = customTags.trim() ? customTags.split(",").map(s=>s.trim()).filter(Boolean) : null;
    const body = JSON.stringify({ log_text: text, pipeline_tags: tags, include_source: true });

    try {
      // First do a synchronous parse to get structured data
      const parseResult = await apiFetch("/api/parse", { method:"POST", body });
      setParsed({ ...parseResult, ai_report:"", source_methods_matched:0 });
      setActiveTab("analysis");

      // Then stream the batch AI analysis
      const resp = await fetch(`${API_BASE}/api/analyze/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || resp.statusText);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (evt.type === "start") {
              setBatchProgress({ batch:0, total:evt.total_batches, label:"Starting...", reports:[], logLines:evt.log_lines });
            } else if (evt.type === "progress") {
              setBatchProgress(p => ({ ...p, batch:evt.batch, total:evt.total, label:evt.label }));
              setActiveTab("ai");
            } else if (evt.type === "batch_done") {
              setBatchProgress(p => ({ ...p, reports:[...(p?.reports||[]), evt.partial_report] }));
            } else if (evt.type === "synthesis") {
              setBatchProgress(p => ({ ...p, label:evt.message, synthesising:true }));
            } else if (evt.type === "done") {
              setParsed(p => ({ ...p, ai_report:evt.final_report, source_methods_matched:evt.source_matched }));
              setBatchProgress(p => ({ ...p, done:true, label:"Complete" }));
            } else if (evt.type === "error") {
              setParsed(p => ({ ...p, ai_report:`**Batch error:** ${evt.message}` }));
              setBatchProgress(p => ({ ...p, error:true, label:evt.message }));
            }
          } catch {}
        }
      }
    } catch (e) {
      setParsed(p => p ? { ...p, ai_report:`**Error:** ${e.message}` } : null);
    } finally {
      setLoading(false);
      setAiLoading(false);
    }
  };

  const analyze = async (text) => {
    setLoading(true);
    setAiLoading(true);
    setBatchProgress(null);

    if (backendStatus && isBatchMode(text)) {
      await analyzeBatch(text);
      return;
    }

    try {
      const tags = customTags.trim() ? customTags.split(",").map(s=>s.trim()).filter(Boolean) : null;

      if (backendStatus) {
        // Full backend analysis (parse + source correlation + AI)
        const result = await apiFetch("/api/analyze", {
          method: "POST",
          body: JSON.stringify({ log_text: text, pipeline_tags: tags, include_source: true }),
        });
        setParsed(result);
      } else {
        // Client-side fallback
        const result = parseLogLocal(text, tags || ["service-abc","service-test","service-deploy"]);
        result.ai_report = "[!] Backend not available -- running client-side parse only. Start the Python API for AI analysis.";
        setParsed(result);
      }
      setActiveTab("parse");
    } catch(e) {
      alert("Analysis error: " + e.message);
    }
    setLoading(false);
    setAiLoading(false);
  };

  const regenerateAI = async () => {
    if (!backendStatus || !logText.trim()) return;
    setAiLoading(true);
    const tags = customTags.trim() ? customTags.split(",").map(s=>s.trim()).filter(Boolean) : null;
    try {
      const result = await apiFetch("/api/analyze", {
        method: "POST",
        body: JSON.stringify({ log_text: logText, pipeline_tags: tags, include_source: true }),
      });
      setParsed(result);
      setActiveTab("report");
    } catch(e) { alert("AI error: " + e.message); }
    setAiLoading(false);
  };

  const fetchUrl = async () => {
    setLoading(true);
    try {
      const res = await fetch(fileUrl);
      const text = await res.text();
      setLogText(text);
    } catch { alert("Failed to fetch URL (check CORS)."); }
    setLoading(false);
  };

  const handleFile = (e) => {
    const file = e.target.files[0]; if (!file) return;
    const reader = new FileReader();
    reader.onload = ev => setLogText(ev.target.result);
    reader.readAsText(file);
  };

  const tabs = [
    { id:"input",  label:"[kbd] Input" },
    { id:"parse",  label:"[chart] Analysis",  disabled: !parsed },
    { id:"tree",   label:" Call Tree", disabled: !parsed },
    { id:"report", label:"[AI] AI Report", disabled: !parsed },
    { id:"config", label:"[cfg] Config",    badge: backendStatus ? "API [v]" : "offline" },
  ];

  const chartData = parsed?.timing_stats?.slice(0,15) ?? [];

  return (
    <div style={{ minHeight:"100vh", background:"#0d1117", color:"#e6edf3", fontFamily:"'JetBrains Mono','Fira Code',monospace", display:"flex", flexDirection:"column" }}>
      {/* Header */}
      <div style={{ background:"linear-gradient(90deg,#161b22 0%,#0d1117 100%)", borderBottom:"1px solid #21262d", padding:"14px 28px", display:"flex", alignItems:"center", gap:14 }}>
        <div style={{ width:36, height:36, borderRadius:8, background:"linear-gradient(135deg,#58a6ff,#3fb950)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:18, fontWeight:900, color:"#0d1117" }}>J</div>
        <div>
          <div style={{ fontSize:16, fontWeight:700, color:"#e6edf3" }}>Jenkins Performance Analyzer</div>
          <div style={{ fontSize:11, color:"#8b949e" }}>AI-powered * Source-correlated * Multi-provider</div>
        </div>
        <div style={{ marginLeft:"auto", display:"flex", gap:8, alignItems:"center", flexWrap:"wrap" }}>
          <Badge text={backendStatus ? "API online" : "API offline"} ok={backendStatus} />
          {backendStatus && healthData && (() => {
            const provider  = healthData.ai_provider ?? "unknown";
            const gpuOn     = healthData.gpu_enabled;
            const privateOnly = healthData.private_only_mode;
            const isCloud   = provider === "anthropic";
            const isLocal   = provider === "ollama";
            const aiLabel   = isCloud ? "AI: cloud" : isLocal ? (gpuOn ? "AI: local+GPU" : "AI: local") : "AI: private";
            const aiColor   = (privateOnly && isCloud) ? "#484f58" : isCloud ? "#58a6ff" : isLocal ? "#3fb950" : "#f0b429";
            return <>
              {privateOnly && <Chip label="PRIVATE-ONLY" color="#ff7b72" />}
              <Chip label={aiLabel} color={aiColor} />
            </>;
          })()}
          {backendStatus && healthData && healthData.batch_mode !== "never" && (() => {
            const mode = healthData.batch_mode ?? "auto";
            const thresh = healthData.batch_threshold_lines ?? 500;
            const label = mode === "always" ? "batch: always" : `batch: >${thresh} lines`;
            return <Chip label={label} color="#6e7681" />;
          })()}
          {backendStatus && serverConfig && (() => {
            const ghType = serverConfig.github?.type ?? "public";
            const repos  = (serverConfig.github?.repos ?? []).filter(r => r.enabled).length;
            const label  = ghType === "private" ? "GitHub: enterprise" : "GitHub: public";
            const color  = ghType === "private" ? "#d2a8ff" : "#8b949e";
            return <Chip label={repos > 0 ? `${label} (${repos} repo${repos!==1?"s":""})` : label} color={color} />;
          })()}
          {parsed && <>
            <Chip label={`${parsed.timing_stats?.length ?? 0} methods`} color="#58a6ff" />
            <Chip label={`${parsed.stages?.length ?? 0} stages`} color="#3fb950" />
            <Chip label={`${parsed.total_duration?.toFixed(1)}s`} color="#f0b429" />
            {parsed.source_methods_matched > 0 && <Chip label={`${parsed.source_methods_matched} src matched`} color="#d2a8ff" />}
          </>}
        </div>
      </div>

      {/* Private-only mode banner */}
      {backendStatus && healthData?.private_only_mode && (
        <div style={{ background:"#1e1012", borderBottom:"1px solid #ff7b7240", padding:"7px 28px", display:"flex", alignItems:"center", gap:10 }}>
          <span style={{ color:"#ff7b72", fontWeight:700, fontSize:12 }}>[PRIVATE-ONLY MODE]</span>
          <span style={{ color:"#ffa198", fontSize:12 }}>
            Public cloud blocked (Anthropic, github.com).
            GitHub Enterprise, private AI, and all on-prem resources remain accessible.
          </span>
        </div>
      )}

      {/* Tabs */}
      <div style={{ display:"flex", borderBottom:"1px solid #21262d", background:"#161b22", padding:"0 28px" }}>
        {tabs.map(t => {
          const accentColor = t.failedBadge ? "#ff7b72" : t.errorBadge ? "#d29922" : "#58a6ff";
          return (
            <button key={t.id} disabled={t.disabled} onClick={() => !t.disabled && setActiveTab(t.id)} style={{
              background:"none", border:"none", padding:"12px 18px",
              color: activeTab===t.id ? accentColor : t.disabled ? "#484f58" : "#8b949e",
              borderBottom: activeTab===t.id ? `2px solid ${accentColor}` : "2px solid transparent",
              cursor: t.disabled ? "not-allowed" : "pointer", fontSize:13, fontFamily:"inherit", whiteSpace:"nowrap",
            }}>
              {t.label}
              {t.badge && <span style={{ marginLeft:6, fontSize:10, padding:"1px 6px", borderRadius:10, background: backendStatus?"#0f22":"#f022", color: backendStatus?"#3fb950":"#ff7b72", border:`1px solid ${backendStatus?"#3fb95040":"#ff7b7240"}` }}>{t.badge}</span>}
            </button>
          );
        })}
      </div>

      {/* Content */}
      <div style={{ flex:1, padding:"24px 28px", maxWidth:1200, width:"100%", margin:"0 auto" }}>

        {/* INPUT */}
        {activeTab === "input" && (
          <div style={{ display:"flex", flexDirection:"column", gap:16 }}>
            {/* Tag override */}
            <div style={{ background:"#161b22", border:"1px solid #30363d", borderRadius:8, padding:14 }}>
              <div style={{ fontSize:11, color:"#8b949e", marginBottom:6, textTransform:"uppercase", letterSpacing:1 }}>[tag] Pipeline Tags Override (comma-separated, empty = use config)</div>
              <input value={customTags} onChange={e=>setCustomTags(e.target.value)}
                placeholder="service-abc, service-deploy, service-test  (leave empty to use config defaults)"
                style={{ width:"100%", background:"#0d1117", border:"1px solid #21262d", borderRadius:6, padding:"7px 12px", color:"#79c0ff", fontFamily:"inherit", fontSize:12, boxSizing:"border-box" }} />
            </div>

            <div style={{ background:"#161b22", border:"1px solid #30363d", borderRadius:8, padding:14 }}>
              <div style={{ fontSize:11, color:"#8b949e", marginBottom:8, textTransform:"uppercase", letterSpacing:1 }}>[folder] Load from URL or File</div>
              <div style={{ display:"flex", gap:8, marginBottom:8 }}>
                <input value={fileUrl} onChange={e=>setFileUrl(e.target.value)}
                  placeholder="https://jenkins.example.com/job/build/123/consoleText"
                  style={{ flex:1, background:"#0d1117", border:"1px solid #30363d", borderRadius:6, padding:"7px 12px", color:"#e6edf3", fontFamily:"inherit", fontSize:12 }} />
                <Btn onClick={fetchUrl} loading={loading}>Fetch</Btn>
              </div>
              <div style={{ display:"flex", gap:8 }}>
                <Btn onClick={() => fileInputRef.current.click()} variant="secondary">[folder] Upload .txt</Btn>
                <Btn onClick={() => setLogText(SAMPLE_LOG)} variant="ghost">Load Sample</Btn>
                <input ref={fileInputRef} type="file" accept=".txt,.log" style={{ display:"none" }} onChange={handleFile} />
              </div>
            </div>

            <div style={{ background:"#161b22", border:"1px solid #30363d", borderRadius:8, padding:14 }}>
              <div style={{ fontSize:11, color:"#8b949e", marginBottom:8, textTransform:"uppercase", letterSpacing:1 }}>[paste] Paste Log</div>
              <textarea value={logText} onChange={e=>setLogText(e.target.value)}
                placeholder="Paste Jenkins console output here..."
                style={{ width:"100%", minHeight:280, background:"#0d1117", border:"1px solid #21262d", borderRadius:6, padding:12, color:"#79c0ff", fontFamily:"inherit", fontSize:12, lineHeight:1.6, resize:"vertical", boxSizing:"border-box" }} />
              <div style={{ display:"flex", gap:8, marginTop:10 }}>
                <Btn onClick={() => analyze(logText)} loading={loading} disabled={!logText.trim()}>[!] Analyze</Btn>
                <Btn onClick={() => setLogText("")} variant="ghost">Clear</Btn>
                <span style={{ marginLeft:"auto", fontSize:11, color:"#484f58", alignSelf:"center" }}>
                  {logText.split("\n").length} lines * {(logText.length/1024).toFixed(1)} KB
                  {backendStatus && " * backend analysis enabled"}
                </span>
              </div>
            </div>
          </div>
        )}

        {/* ANALYSIS */}
        {activeTab === "parse" && parsed && (
          <div style={{ display:"flex", flexDirection:"column", gap:16 }}>
            {parsed.warnings?.length > 0 && (
              <div style={{ background:"#2d1f0e", border:"1px solid #f0b42940", borderRadius:8, padding:"10px 14px" }}>
                {parsed.warnings.map((w,i) => <div key={i} style={{ color:"#f0b429", fontSize:12 }}>[!] {w}</div>)}
              </div>
            )}

            {/* Stage cards */}
            <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(180px,1fr))", gap:10 }}>
              {parsed.stages.map((s,i) => (
                <div key={i} style={{ background:"#161b22", border:"1px solid #21262d", borderRadius:8, padding:12, borderTop:`3px solid ${COLORS[i%COLORS.length]}` }}>
                  <div style={{ fontSize:10, color:"#8b949e", textTransform:"uppercase" }}>Stage</div>
                  <div style={{ fontSize:14, fontWeight:700, color:"#e6edf3", margin:"4px 0" }}>{s.name}</div>
                  <div style={{ fontSize:24, fontWeight:900, color:COLORS[i%COLORS.length] }}>{(s.total_time??0).toFixed(1)}s</div>
                  <div style={{ fontSize:11, color:"#8b949e" }}>{s.methods?.length} calls</div>
                </div>
              ))}
            </div>

            {/* Chart type selector */}
            <div style={{ display:"flex", gap:8, alignItems:"center" }}>
              <span style={{ fontSize:12, color:"#8b949e" }}>Metric:</span>
              {["total","avg","calls","max","p95"].map(c => (
                <button key={c} onClick={() => setChartType(c)} style={{
                  background: chartType===c?"#58a6ff":"#21262d", color: chartType===c?"#0d1117":"#8b949e",
                  border:"none", borderRadius:4, padding:"4px 10px", fontSize:11, cursor:"pointer",
                  fontFamily:"inherit", fontWeight:700, textTransform:"uppercase",
                }}>{c}</button>
              ))}
              <Btn onClick={() => { setActiveTab("report"); if (!parsed.ai_report) regenerateAI(); }} loading={aiLoading} style={{ marginLeft:"auto" }}>
                [AI] AI Report
              </Btn>
            </div>

            {/* Bar chart */}
            <div style={{ background:"#161b22", border:"1px solid #21262d", borderRadius:8, padding:16 }}>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={chartData} margin={{ top:0, right:20, left:0, bottom:60 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262d" />
                  <XAxis dataKey="name" tick={{ fontSize:10, fill:"#8b949e" }} angle={-35} textAnchor="end" interval={0} />
                  <YAxis tick={{ fontSize:10, fill:"#8b949e" }} />
                  <Tooltip content={<CustomTooltip />} />
                  <Bar dataKey={chartType} radius={[4,4,0,0]}>
                    {chartData.map((row,i) => <Cell key={i} fill={row.is_slow ? SLOW_COLOR : COLORS[i%COLORS.length]} fillOpacity={0.85} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <div style={{ fontSize:10, color:"#484f58", marginTop:4 }}>[red] Red bars = above slow threshold percentile</div>
            </div>

            {/* Table */}
            <div style={{ background:"#161b22", border:"1px solid #21262d", borderRadius:8, overflow:"hidden" }}>
              <div style={{ padding:"10px 16px", borderBottom:"1px solid #21262d", fontSize:12, color:"#8b949e" }}>Method Timing Details</div>
              <div style={{ overflowX:"auto" }}>
                <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
                  <thead><tr style={{ background:"#0d1117" }}>
                    {["Method","Tags","Total","Avg","P95","Max","Min","Calls","Slow"].map(h=>(
                      <th key={h} style={{ padding:"7px 14px", textAlign:"left", color:"#58a6ff", fontSize:10, textTransform:"uppercase", letterSpacing:0.5, whiteSpace:"nowrap" }}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {parsed.timing_stats.map((row,i) => (
                      <tr key={i} style={{ borderTop:"1px solid #21262d", background: row.is_slow?"rgba(255,123,114,0.06)":"transparent" }}>
                        <td style={{ padding:"7px 14px", color:row.is_slow?"#ff7b72":"#79c0ff", fontWeight:600 }}>{row.name}</td>
                        <td style={{ padding:"7px 14px", color:"#8b949e", fontSize:10 }}>{row.service_tags?.join(", ")}</td>
                        <td style={{ padding:"7px 14px", color:"#f0b429" }}>{row.total}s</td>
                        <td style={{ padding:"7px 14px", color:"#e6edf3" }}>{row.avg}s</td>
                        <td style={{ padding:"7px 14px", color:"#d2a8ff" }}>{row.p95}s</td>
                        <td style={{ padding:"7px 14px", color:"#ff7b72" }}>{row.max}s</td>
                        <td style={{ padding:"7px 14px", color:"#3fb950" }}>{row.min}s</td>
                        <td style={{ padding:"7px 14px", color:"#8b949e" }}>{row.calls}</td>
                        <td style={{ padding:"7px 14px" }}>{row.is_slow && <span style={{ color:"#ff7b72", fontSize:10, fontWeight:700 }}>SLOW</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* CALL TREE */}
        {activeTab === "tree" && parsed && (
          <div style={{ display:"flex", flexDirection:"column", gap:12 }}>
            <div style={{ display:"flex", gap:8, alignItems:"center" }}>
              <div style={{ fontSize:12, color:"#8b949e" }}>Nested call hierarchy. Color = elapsed time heat (green->red). Click to expand.</div>
              <Btn onClick={() => { setActiveTab("report"); if (!parsed.ai_report) regenerateAI(); }} loading={aiLoading} style={{ marginLeft:"auto" }}>[AI] AI Report</Btn>
            </div>
            <div style={{ background:"#161b22", border:"1px solid #21262d", borderRadius:8, padding:16 }}>
              {parsed.call_tree?.length > 0
                ? parsed.call_tree.map((n,i) => <CallNode key={i} node={n} depth={0} />)
                : <div style={{ color:"#484f58", textAlign:"center", padding:40 }}>No nested call tree detected.</div>}
            </div>
          </div>
        )}

        {/* AI REPORT */}
        {activeTab === "errors" && (
        <div style={{ padding:"24px 28px" }}>
          {!parsed ? (
            <div style={{ color:"#8b949e", textAlign:"center", paddingTop:60 }}>Analyze a log file to see errors.</div>
          ) : errorCount === 0 ? (
            <div style={{ color:"#3fb950", textAlign:"center", paddingTop:60, fontSize:15 }}>&#10003; No errors detected in this build.</div>
          ) : (
            <>
              {parsed.build_failed && (
                <div style={{ marginBottom:20, padding:"12px 16px", background:"#1e1012", border:"1px solid #ff7b7240", borderRadius:8, display:"flex", alignItems:"center", gap:12 }}>
                  <span style={{ color:"#ff7b72", fontWeight:700, fontSize:14 }}>BUILD FAILED</span>
                  <span style={{ color:"#ffa198", fontSize:12 }}>
                    {errorCount} error{errorCount!==1?"s":""} detected
                    {parsed.failed_methods?.length > 0 && ` | Implicated: ${parsed.failed_methods.slice(0,6).join(", ")}${parsed.failed_methods.length>6?` +${parsed.failed_methods.length-6} more`:""}`}
                  </span>
                </div>
              )}
              {parsed.failed_methods?.length > 0 && (
                <div style={{ marginBottom:16, padding:"10px 14px", background:"#161b22", border:"1px solid #30363d", borderRadius:8 }}>
                  <div style={{ color:"#8b949e", fontSize:11, marginBottom:6, textTransform:"uppercase", letterSpacing:1 }}>Methods Implicated in Failure</div>
                  <div style={{ display:"flex", flexWrap:"wrap", gap:6 }}>
                    {parsed.failed_methods.map(m => (
                      <span key={m} style={{ background:"#2d1b1b", color:"#ff7b72", padding:"3px 10px", borderRadius:12, fontSize:12, fontFamily:"monospace", border:"1px solid #ff7b7240" }}>{m}</span>
                    ))}
                  </div>
                </div>
              )}
              <div style={{ display:"flex", flexDirection:"column", gap:12, marginBottom:24 }}>
                {parsed.errors.map((err, i) => {
                  const typeColor = err.error_type==="BUILD_FAILED"?"#ff7b72":err.error_type==="EXCEPTION"?"#d29922":err.error_type==="TIMEOUT"?"#79c0ff":"#e6edf3";
                  return (
                    <div key={i} style={{ background:"#161b22", border:"1px solid #30363d", borderRadius:8, overflow:"hidden" }}>
                      <div style={{ padding:"9px 14px", borderBottom:"1px solid #21262d", display:"flex", alignItems:"center", gap:8, flexWrap:"wrap" }}>
                        <span style={{ padding:"2px 8px", borderRadius:4, fontSize:11, fontWeight:700, background:"#0d1117", color:typeColor, border:`1px solid ${typeColor}40` }}>{err.error_type}</span>
                        <span style={{ color:"#484f58", fontSize:11 }}>line {err.line_number}</span>
                        {err.stage && <span style={{ color:"#8b949e", fontSize:11 }}>&#183; <span style={{ color:"#e6edf3" }}>{err.stage}</span></span>}
                        {err.exit_code != null && <span style={{ color:"#ff7b72", fontSize:11, fontFamily:"monospace", marginLeft:"auto" }}>exit {err.exit_code}</span>}
                      </div>
                      <div style={{ padding:"12px 14px" }}>
                        <div style={{ color:"#e6edf3", fontSize:12, fontFamily:"monospace", wordBreak:"break-all", marginBottom:8 }}>{err.message}</div>
                        {err.stack_trace?.length > 0 && (
                          <details style={{ marginBottom:6 }}>
                            <summary style={{ color:"#58a6ff", fontSize:12, cursor:"pointer", userSelect:"none" }}>Stack trace ({err.stack_trace.length} frames)</summary>
                            <pre style={{ marginTop:6, padding:"8px 10px", background:"#0d1117", borderRadius:4, fontSize:11, color:"#ffa657", overflow:"auto", maxHeight:180, margin:0 }}>{err.stack_trace.join("\n")}</pre>
                          </details>
                        )}
                        {err.context_lines?.length > 0 && (
                          <details>
                            <summary style={{ color:"#8b949e", fontSize:12, cursor:"pointer", userSelect:"none" }}>Log context (+-5 lines)</summary>
                            <pre style={{ marginTop:6, padding:"8px 10px", background:"#0d1117", borderRadius:4, fontSize:11, color:"#e6edf3", overflow:"auto", maxHeight:160, margin:0 }}>{err.context_lines.join("\n")}</pre>
                          </details>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
              {parsed.failure_report ? (
                <div>
                  <div style={{ color:"#e6edf3", fontWeight:600, fontSize:14, marginBottom:12 }}>
                    AI Failure Analysis
                    {parsed.failed_methods?.length > 0 && <span style={{ fontSize:11, color:"#8b949e", fontWeight:400, marginLeft:8 }}>root cause + source-enriched</span>}
                  </div>
                  <div style={{ padding:18, background:"#161b22", borderRadius:8, border:"1px solid #ff7b7220", fontSize:13, lineHeight:1.65 }}>
                    <ReactMarkdown>{parsed.failure_report}</ReactMarkdown>
                  </div>
                </div>
              ) : (
                <div style={{ color:"#484f58", fontSize:12, textAlign:"center", paddingTop:8 }}>
                  Enable an AI provider in Config to get root cause analysis with source code.
                </div>
              )}
            </>
          )}
        </div>
      )}

      {activeTab === "report" && (
          <div style={{ display:"flex", flexDirection:"column", gap:12 }}>
            <div style={{ display:"flex", gap:8, alignItems:"center" }}>
              {batchProgress && (
                <div style={{ marginBottom:12, padding:"10px 14px", borderRadius:8, background:"#161b22", border:"1px solid #21262d" }}>
                  <div style={{ display:"flex", alignItems:"center", gap:10, marginBottom:6 }}>
                    <span style={{ fontSize:12, color:"#8b949e" }}>
                      {batchProgress.done ? "Batch analysis complete" :
                       batchProgress.error ? "Batch analysis failed" :
                       batchProgress.synthesising ? "Synthesising..." :
                       `Analysing batch ${batchProgress.batch} of ${batchProgress.total}`}
                    </span>
                    {batchProgress.logLines && (
                      <span style={{ fontSize:11, color:"#484f58" }}>{batchProgress.logLines.toLocaleString()} lines</span>
                    )}
                    {batchProgress.done && <span style={{ fontSize:11, color:"#3fb950" }}>[v] done</span>}
                    {batchProgress.error && <span style={{ fontSize:11, color:"#ff7b72" }}>[x] error</span>}
                  </div>
                  <div style={{ height:6, borderRadius:3, background:"#21262d", overflow:"hidden" }}>
                    <div style={{
                      height:"100%", borderRadius:3, transition:"width 0.4s ease",
                      background: batchProgress.error ? "#ff7b72" : batchProgress.done ? "#3fb950" : "#58a6ff",
                      width: batchProgress.total > 0
                        ? `${Math.round(((batchProgress.synthesising ? batchProgress.total : batchProgress.batch) / (batchProgress.total + 1)) * 100)}%`
                        : "5%"
                    }} />
                  </div>
                  {batchProgress.label && !batchProgress.done && !batchProgress.error && (
                    <div style={{ fontSize:11, color:"#58a6ff", marginTop:5, fontFamily:"monospace" }}>
                      {batchProgress.label}
                    </div>
                  )}
                </div>
              )}
              <Btn onClick={regenerateAI} loading={aiLoading} disabled={!backendStatus}>
                [AI] {parsed?.ai_report ? "Regenerate" : "Generate AI Report"}
              </Btn>
              {!backendStatus && <span style={{ fontSize:11, color:"#ff7b72" }}>Backend required for AI reports -- start the Python API server</span>}
              {parsed?.ai_report && <Btn onClick={() => navigator.clipboard.writeText(parsed.ai_report)} variant="ghost">[paste] Copy</Btn>}
            </div>

            {aiLoading && (
              <div style={{ background:"#161b22", border:"1px solid #21262d", borderRadius:8, padding:32, textAlign:"center" }}>
                <div style={{ color:"#58a6ff", fontSize:13 }}>[!] Analyzing with AI...</div>
                <div style={{ color:"#484f58", fontSize:11, marginTop:4 }}>Correlating source code and timing data</div>
              </div>
            )}

            {parsed?.ai_report && !aiLoading && (
              <div style={{ background:"#161b22", border:"1px solid #21262d", borderRadius:8, padding:24 }}>
                <div style={{ marginBottom:14, paddingBottom:10, borderBottom:"1px solid #21262d", display:"flex", alignItems:"center", gap:8 }}>
                  <div style={{ width:8, height:8, borderRadius:"50%", background:"#3fb950" }} />
                  <span style={{ fontSize:11, color:"#8b949e" }}>AI Performance Report</span>
                  {parsed.source_methods_matched > 0 && <span style={{ fontSize:10, color:"#d2a8ff", marginLeft:8 }}> {parsed.source_methods_matched} methods correlated from source</span>}
                </div>
                <MdRender text={parsed.ai_report} />
              </div>
            )}

            {!aiLoading && !parsed?.ai_report && (
              <div style={{ background:"#161b22", border:"1px dashed #30363d", borderRadius:8, padding:48, textAlign:"center" }}>
                <div style={{ fontSize:36, marginBottom:12 }}>[AI]</div>
                <div style={{ color:"#8b949e", fontSize:14 }}>No AI report yet</div>
              </div>
            )}
          </div>
        )}

        {/* CONFIG */}
        {activeTab === "config" && (
          <div>
            {!backendStatus && (
              <div style={{ background:"#1e1208", border:"1px solid #f0b42940", borderRadius:8, padding:"10px 16px", marginBottom:16, fontSize:12, color:"#f0b429" }}>
                [!] Backend API is offline. Config changes require the Python server running at <code style={{ color:"#79c0ff" }}>{API_BASE}</code>.
                <br />Start with: <code style={{ color:"#3fb950" }}>cd backend && uvicorn main:app --reload</code>
              </div>
            )}
            <ConfigPanel serverConfig={serverConfig} onSaved={loadConfig} />
          </div>
        )}

      </div>

      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap');
        * { box-sizing: border-box; }
        textarea:focus, input:focus, select:focus { outline: 1px solid #58a6ff; }
        select option { background: #161b22; }
      `}</style>
    </div>
  );
}
