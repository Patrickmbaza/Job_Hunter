"use client";

import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import {
  Activity, ArrowRight, ArrowUpRight, Bell, Bookmark, BriefcaseBusiness,
  Check, ChevronDown, CircleHelp, FileText, Gauge, Globe2, GraduationCap,
  LayoutDashboard, LoaderCircle, MapPin, Menu, MessageSquareText, Search, Settings2,
  ShieldCheck, Sparkles, Target, UsersRound, X,
} from "lucide-react";
import PhaseTwo from "./phase-two";

const API = "";
const nav = [
  { label: "Overview", icon: LayoutDashboard, group: "WORKSPACE" },
  { label: "Find jobs", icon: Search, group: "WORKSPACE" },
  { label: "Matched jobs", icon: Target, group: "WORKSPACE", badge: "0" },
  { label: "Applications", icon: BriefcaseBusiness, group: "WORKSPACE" },
  { label: "CV studio", icon: FileText, group: "PREPARE" },
  { label: "Interview prep", icon: MessageSquareText, group: "PREPARE" },
  { label: "Company research", icon: UsersRound, group: "PREPARE" },
  { label: "Skills roadmap", icon: GraduationCap, group: "INSIGHTS" },
  { label: "Notifications", icon: Bell, group: "INSIGHTS" },
  { label: "REA automation", icon: Activity, group: "SYSTEM" },
  { label: "Settings", icon: Settings2, group: "SYSTEM" },
];

type Profile = {
  headline: string; experience_summary: string; location: string; skills: string[];
  projects: string[]; target_titles: string[]; preferences: Record<string, boolean | string>;
  verification_status: string; source_note: string;
};
type Dashboard = { jobs_discovered: number; high_match_jobs: number; applications_prepared: number; applications_submitted: number; interviews: number; offers: number; rejections: number; pending_follow_ups: number; recent_jobs: Array<Record<string, unknown>>; profile_verification: string };
type AuthState = "checking" | "unconfigured" | "login" | "authenticated" | "offline";
type ReaTarget = { target_id: string; title: string; url: string; origin: string };

const emptyDashboard: Dashboard = { jobs_discovered: 0, high_match_jobs: 0, applications_prepared: 0, applications_submitted: 0, interviews: 0, offers: 0, rejections: 0, pending_follow_ups: 0, recent_jobs: [], profile_verification: "unverified" };

export default function Home() {
  const [active, setActive] = useState("Overview");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard>(emptyDashboard);
  const [reaState, setReaState] = useState("checking");
  const [reaTargets, setReaTargets] = useState<ReaTarget[]>([]);
  const [reaPage, setReaPage] = useState<Record<string, unknown> | null>(null);
  const [reaBusy, setReaBusy] = useState(false);
  const [reaMessage, setReaMessage] = useState("");
  const [apiState, setApiState] = useState("checking");
  const [authState, setAuthState] = useState<AuthState>("checking");
  const [csrfToken, setCsrfToken] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [mobileNav, setMobileNav] = useState(false);
  const [draft, setDraft] = useState<Profile | null>(null);

  useEffect(() => {
    let mounted = true;
    fetch(`${API}/api/auth/session`, { cache: "no-store" }).then(async r => {
      if (!r.ok) throw new Error("Authentication service unavailable");
      return r.json();
    }).then(session => {
      if (!mounted) return;
      setApiState("connected");
      setCsrfToken(session.csrf_token ?? "");
      setAuthState(!session.configured ? "unconfigured" : session.authenticated ? "authenticated" : "login");
    }).catch(() => { if (mounted) { setApiState("offline"); setAuthState("offline"); } });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    if (authState !== "authenticated") return;
    let mounted = true;
    Promise.all([
      fetch(`${API}/api/health`, { cache: "no-store" }).then(async r => { if (!r.ok) throw new Error(); return r.json(); }),
      fetch(`${API}/api/profile`, { cache: "no-store" }).then(async r => { if (!r.ok) throw new Error(); return r.json(); }),
      fetch(`${API}/api/dashboard`, { cache: "no-store" }).then(async r => { if (!r.ok) throw new Error(); return r.json(); }),
      fetch(`${API}/api/rea/status`, { cache: "no-store" }).then(async r => { if (!r.ok) throw new Error(); return r.json(); }),
    ]).then(([_, p, d, rea]) => {
      if (!mounted) return;
      setApiState("connected"); setProfile(p); setDashboard(d); setReaState(rea.browser?.state === "connected" ? "browser_connected" : rea.state);
    }).catch(() => { if (mounted) { setApiState("offline"); setReaState("unavailable"); } });
    return () => { mounted = false; };
  }, [authState]);

  const initials = useMemo(() => profile ? "U" : "?", [profile]);
  const saveProfile = async () => {
    if (!draft) return;
    setSaving(true); setNotice("");
    try {
      const response = await fetch(`${API}/api/profile`, { method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken }, body: JSON.stringify(draft) });
      if (!response.ok) throw new Error("Profile save failed");
      setProfile(await response.json()); setEditing(false); setNotice("Profile saved locally.");
    } catch { setNotice("Could not save. Check the local API and try again."); }
    finally { setSaving(false); }
  };

  const signIn = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setLoginBusy(true); setLoginError("");
    try {
      const response = await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }) });
      if (!response.ok) throw new Error(response.status === 429 ? "Too many attempts. Wait a few minutes and try again." : "Sign-in failed. Check the password and try again.");
      const result = await response.json(); setCsrfToken(result.csrf_token); setPassword(""); setAuthState("authenticated");
    } catch (error) { setLoginError(error instanceof Error ? error.message : "Could not sign in."); }
    finally { setLoginBusy(false); }
  };

  const signOut = async () => {
    try { await fetch(`${API}/api/auth/logout`, { method: "POST", headers: { "X-CSRF-Token": csrfToken } }); }
    finally { setProfile(null); setCsrfToken(""); setAuthState("login"); }
  };

  const discoverReaTargets = async () => {
    setReaBusy(true); setReaMessage(""); setReaPage(null);
    try {
      const response = await fetch(`${API}/api/rea/targets`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Target discovery failed (${response.status}). Check REA and the configured browser.`);
      const result = await response.json();
      const found = result?.structuredContent?.normalized_result?.targets;
      setReaTargets(Array.isArray(found) ? found : []);
      setReaState(Array.isArray(found) ? "browser_connected" : "connected");
      if (!Array.isArray(found) || found.length === 0) setReaMessage("REA responded, but no page target matched the configured origins.");
    } catch (error) { setReaMessage(error instanceof Error ? error.message : "REA target discovery failed."); setReaState("unavailable"); }
    finally { setReaBusy(false); }
  };

  const inspectReaTarget = async (target: ReaTarget) => {
    setReaBusy(true); setReaMessage(""); setReaPage(null);
    try {
      const response = await fetch(`${API}/api/rea/inspect`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken }, body: JSON.stringify({ target_id: target.target_id, origin: target.origin }) });
      if (!response.ok) throw new Error(`Page inspection failed (${response.status}). Discover the target again and retry.`);
      const result = await response.json();
      setReaPage(result?.structuredContent?.normalized_result ?? null);
      if (!result?.structuredContent?.normalized_result) setReaMessage("REA returned a result without structured page details.");
    } catch (error) { setReaMessage(error instanceof Error ? error.message : "Page inspection failed."); }
    finally { setReaBusy(false); }
  };

  if (authState !== "authenticated") return <AuthScreen state={authState} password={password} setPassword={setPassword} signIn={signIn} busy={loginBusy} error={loginError}/>;

  return <div className="app-shell">
    <aside className={`sidebar ${mobileNav ? "sidebar-open" : ""}`}>
      <div className="brand"><div className="brand-mark"><Sparkles size={18} strokeWidth={2.4}/></div><div><b>northstar</b><span>JOB SEARCH OS</span></div><button className="mobile-close icon-button" onClick={() => setMobileNav(false)} aria-label="Close menu"><X size={18}/></button></div>
      <div className="workspace-switch"><div className="workspace-avatar">U</div><div className="workspace-copy"><strong>Personal workspace</strong><span>Private · Local only</span></div><ChevronDown size={15}/></div>
      <nav className="primary-nav" aria-label="Main navigation">
        {(["WORKSPACE", "PREPARE", "INSIGHTS", "SYSTEM"] as const).map(group => <div className="nav-group" key={group}><p>{group}</p>{nav.filter(item => item.group === group).map(item => <button key={item.label} className={`nav-item ${active === item.label ? "nav-active" : ""}`} onClick={() => { setActive(item.label); setMobileNav(false); }}><item.icon size={17} strokeWidth={1.8}/><span>{item.label}</span>{item.badge && <i>{item.badge}</i>}{item.label === "REA automation" && <span className={`nav-dot ${reaState === "not_bridged" ? "dot-muted" : ""}`}/>}</button>)}</div>)}
      </nav>
      <div className="sidebar-bottom"><div className="privacy-card"><div><ShieldCheck size={17}/><strong>Private by default</strong></div><p>Your profile and application data stay on this device.</p><span><span className="status-dot"/> Local workspace</span></div><button className="help-link"><CircleHelp size={16}/> Help &amp; feedback</button><div className="user-card"><div className="user-avatar">{initials}</div><div className="user-copy"><strong>Your profile</strong><span>Job seeker</span></div><button className="icon-button" aria-label="User menu"><MoreDots/></button></div></div>
    </aside>
    {mobileNav && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setMobileNav(false)}/>}

    <main className="main-area">
      <header className="topbar"><div className="crumb"><button className="menu-trigger icon-button" onClick={() => setMobileNav(true)} aria-label="Open menu"><Menu size={19}/></button><span>Workspace</span><b>/</b><strong>{active}</strong></div><div className="top-actions"><div className="connection-label"><span className={`status-dot ${apiState === "offline" ? "dot-red" : apiState === "checking" ? "dot-amber" : ""}`}/>{apiState === "connected" ? "Local system ready" : apiState === "checking" ? "Connecting…" : "API unavailable"}</div><button className="icon-button notification-button" aria-label="Notifications"><Bell size={18}/><i/></button><button className="signout-button" onClick={signOut}>Sign out</button><div className="top-avatar">{initials}</div></div></header>
      <div className="content-wrap">
        {active === "Overview" ? <>
          <section className="welcome-row"><div><div className="eyebrow"><span className="eyebrow-line"/> YOUR NEXT CHAPTER STARTS HERE</div><h1>Good morning <span className="wave">✳</span></h1><p className="welcome-subtitle">A clear view of your search, from first lead to signed offer.</p></div><div className="welcome-actions"><button className="button button-light" onClick={() => { setDraft(profile); setEditing(true); }}><Settings2 size={16}/> Edit profile</button><button className="button button-primary" onClick={() => setActive("Find jobs")}><Search size={16}/> Find opportunities</button></div></section>
          <div className={`verification-banner ${profile?.verification_status === "verified" ? "verified-banner" : ""}`}><div className="verify-icon"><ShieldCheck size={18}/></div><div className="verify-copy"><strong>{profile?.verification_status === "verified" ? "Profile verified" : "Your profile needs a quick fact-check"}</strong><span>{profile?.verification_status === "verified" ? "Verified details can be used for job matching." : "Your starting profile is unverified. Review it against your original CV before matching or preparing applications."}</span></div><button onClick={() => { setDraft(profile); setEditing(true); }}>{profile?.verification_status === "verified" ? "Review profile" : "Review profile"}<ArrowRight size={15}/></button></div>
          <section className="section-heading"><div><h2>Your search at a glance</h2><p>Progress across your job search</p></div><button className="period-select">All time <ChevronDown size={14}/></button></section>
          <div className="stats-grid">
            <StatCard icon={<Search size={17}/>} label="Jobs discovered" value={dashboard.jobs_discovered} tone="blue" foot="Across your saved searches" />
            <StatCard icon={<Target size={17}/>} label="High-match jobs" value={dashboard.high_match_jobs} tone="violet" foot="85% match or higher" />
            <StatCard icon={<FileText size={17}/>} label="Applications ready" value={dashboard.applications_prepared} tone="orange" foot="Prepared for your review" />
            <StatCard icon={<BriefcaseBusiness size={17}/>} label="Applications sent" value={dashboard.applications_submitted} tone="green" foot="Confirmed submissions only" />
          </div>
          <div className="overview-grid">
            <section className="panel pipeline-panel"><div className="panel-heading"><div><h2>Application pipeline</h2><p>Keep every opportunity moving</p></div><button className="subtle-link" onClick={() => setActive("Applications")}>View pipeline <ArrowRight size={14}/></button></div><div className="pipeline-flow">{[
              {name:"Matched", count:dashboard.high_match_jobs, tint:"p-blue", icon:<Target size={16}/>},
              {name:"In preparation", count:dashboard.applications_prepared, tint:"p-orange", icon:<FileText size={16}/>},
              {name:"Applied", count:dashboard.applications_submitted, tint:"p-violet", icon:<SendIcon/>},
              {name:"Interview", count:dashboard.interviews, tint:"p-green", icon:<MessageSquareText size={16}/>},
              {name:"Offers", count:dashboard.offers, tint:"p-gold", icon:<Sparkles size={16}/>},
            ].map((step, i) => <div className="pipeline-step" key={step.name}><div className={`pipeline-icon ${step.tint}`}>{step.icon}</div><strong>{step.count}</strong><span>{step.name}</span>{i < 4 && <div className="pipeline-connector"/>}</div>)}</div><div className="pipeline-note"><span className="note-mark"><Activity size={14}/></span><span>Once your first role is saved, your pipeline activity will appear here.</span></div></section>
            <section className="panel profile-panel"><div className="panel-heading"><div><h2>Professional profile</h2><p>Your matching foundation</p></div><button className="icon-button" onClick={() => { setDraft(profile); setEditing(true); }} aria-label="Edit professional profile"><Settings2 size={17}/></button></div><div className="profile-headline">{profile?.headline ?? "DevOps Engineer · Cloud Engineer · Platform Engineer"}</div><div className="profile-location"><MapPin size={14}/>{profile?.location ?? "Port Harcourt, Nigeria"}<span>·</span><Globe2 size={14}/> Open to remote</div><div className="skill-heading"><span>CORE SKILLS</span><span>{profile?.skills.length ?? 0} skills</span></div><div className="skill-chips">{(profile?.skills ?? []).slice(0, 8).map(skill => <span key={skill}>{skill}</span>)}<button onClick={() => { setDraft(profile); setEditing(true); }}>+{Math.max(0, (profile?.skills.length ?? 8) - 8)} more</button></div><div className="profile-foot"><span><span className="status-dot dot-amber"/>{profile?.verification_status === "verified" ? "Verified" : "Needs verification"}</span><button onClick={() => { setDraft(profile); setEditing(true); }}>Edit profile <ArrowRight size={14}/></button></div></section>
          </div>
          <div className="lower-grid"><section className="panel opportunities-panel"><div className="panel-heading"><div><h2>Recent opportunities</h2><p>New roles that fit your direction</p></div><button className="subtle-link" onClick={() => setActive("Matched jobs")}>See all <ArrowRight size={14}/></button></div><div className="empty-opportunities"><div className="empty-illustration"><div className="empty-orbit orbit-one"/><div className="empty-orbit orbit-two"/><div className="empty-core"><Bookmark size={22}/></div><span className="float-star star-a">✳</span><span className="float-star star-b">✦</span><span className="float-star star-c">·</span></div><strong>Your next great role is out there</strong><p>Start with a search. We’ll help you find and evaluate roles that fit your experience and goals.</p><button className="button button-primary" onClick={() => setActive("Find jobs")}><Search size={15}/> Start your first search</button></div></section>
            <section className="panel automation-panel"><div className="panel-heading"><div><h2>Research assistant</h2><p>Browser research via REA</p></div><span className={`connection-pill ${reaState !== "browser_connected" ? "pill-muted" : ""}`}><span className="status-dot"/>{reaState === "browser_connected" ? "Browser tested" : reaState === "connected" ? "MCP connected" : reaState === "checking" ? "Checking" : "Unavailable"}</span></div><div className="rea-card"><div className="rea-logo"><Sparkles size={18}/></div><div><strong>REA browser research</strong><p>{reaState === "browser_connected" ? "The app verified the configured browser and tool catalog." : reaState === "connected" ? "REA MCP is connected. Browser target discovery has not yet succeeded." : "REA status has not been confirmed."}</p></div></div><div className="automation-meta"><span><span className="status-dot dot-gray"/>Status is based on live checks</span><button onClick={() => setActive("REA automation")}>View automation <ArrowRight size={14}/></button></div></section></div>
          <footer className="page-footer"><span>Made for a thoughtful job search.</span><div><span><ShieldCheck size={13}/> Your data stays local</span><span className="footer-sep">·</span><span>v0.1.0 · Foundation</span></div></footer>
        </> : active === "REA automation" ? <section className="rea-workspace"><div className="eyebrow"><span className="eyebrow-line"/> LOCAL BROWSER RESEARCH</div><h1>REA automation</h1><p>Read-only research against the configured browser. Targets are filtered to the origins in your local settings.</p><div className="panel rea-workspace-panel"><div className="panel-heading"><div><h2>Connection status</h2><p>MCP and browser checks are reported separately</p></div><span className={`connection-pill ${reaState !== "browser_connected" ? "pill-muted" : ""}`}><span className="status-dot"/>{reaState === "browser_connected" ? "Browser tested" : reaState === "connected" ? "MCP connected" : reaState === "checking" ? "Checking" : "Unavailable"}</span></div><button className="button button-primary" onClick={discoverReaTargets} disabled={reaBusy}>{reaBusy && <LoaderCircle className="spin" size={15}/>} Discover allowed browser tabs</button>{reaMessage && <p role="status" className="rea-feedback">{reaMessage}</p>}{reaTargets.map(target=><article className="rea-target" key={target.target_id}><div><strong>{target.title || "Untitled page"}</strong><a href={target.url} target="_blank" rel="noreferrer">{target.url}</a><span>Target {target.target_id} · {target.origin}</span></div><button className="button button-light" onClick={()=>inspectReaTarget(target)} disabled={reaBusy}>Inspect page</button></article>)}{reaPage && <section className="rea-inspection"><h2>{String((reaPage.target as Record<string,unknown> | undefined)?.title ?? "Page inspection")}</h2><p>{String((reaPage.target as Record<string,unknown> | undefined)?.url ?? "")}</p><div className="rea-counts"><span>DOM nodes: {String((reaPage.dom as Record<string,unknown> | undefined)?.total_nodes ?? "—")}</span><span>Accessibility nodes: {String((reaPage.accessibility as Record<string,unknown> | undefined)?.total_nodes ?? "—")}</span></div><h3>Accessible text</h3><div className="rea-text">{(((reaPage.accessibility as Record<string,unknown> | undefined)?.nodes ?? []) as Array<Record<string,unknown>>).filter(node=>node.role === "StaticText").map((node,index)=><p key={index}>{String(node.name ?? "")}</p>)}</div><p className="rea-feedback">Inspection is passive. REA did not navigate, click, or modify the page.</p></section>}</div><p className="rea-footnote">Only allow-listed origins are queried. No form, click, upload, or submission actions are available here.</p></section> : <PhaseTwo active={active} csrf={csrfToken} notify={setNotice}/>}
      </div>
    </main>
    {notice && <div role="status" className="toast"><Check size={16}/>{notice}<button onClick={() => setNotice("")} aria-label="Dismiss"><X size={14}/></button></div>}
    {editing && draft && <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) setEditing(false); }}><section className="profile-modal" role="dialog" aria-modal="true" aria-labelledby="profile-modal-title"><header><div><span className="eyebrow"><span className="eyebrow-line"/> PROFILE SETTINGS</span><h2 id="profile-modal-title">Your professional profile</h2><p>Changes are saved on this device. Verify all claims against your original CV.</p></div><button className="icon-button" onClick={() => setEditing(false)} aria-label="Close"><X size={18}/></button></header><div className="verification-callout"><ShieldCheck size={17}/><span><strong>Verification status:</strong> {draft.verification_status.replace("_", " ")}. The system will not generate application documents from unverified claims.</span></div><label>Professional headline<input value={draft.headline} onChange={e => setDraft({...draft, headline:e.target.value})}/></label><label>Experience summary<input value={draft.experience_summary} onChange={e => setDraft({...draft, experience_summary:e.target.value})}/></label><label>Location<input value={draft.location} onChange={e => setDraft({...draft, location:e.target.value})}/></label><label>Skills <span className="field-hint">Separate entries with commas</span><textarea rows={3} value={draft.skills.join(", ")} onChange={e => setDraft({...draft, skills:e.target.value.split(",").map(x=>x.trim()).filter(Boolean)})}/></label><label>Projects <span className="field-hint">Names only until verified</span><textarea rows={2} value={draft.projects.join(", ")} onChange={e => setDraft({...draft, projects:e.target.value.split(",").map(x=>x.trim()).filter(Boolean)})}/></label><label>Target roles<textarea rows={2} value={draft.target_titles.join(", ")} onChange={e => setDraft({...draft, target_titles:e.target.value.split(",").map(x=>x.trim()).filter(Boolean)})}/></label><label>Verification<select value={draft.verification_status} onChange={e => setDraft({...draft, verification_status:e.target.value})}><option value="unverified">Unverified</option><option value="in_review">In review</option><option value="verified">Verified against original CV</option></select></label><div className="modal-actions"><button className="button button-light" onClick={() => setEditing(false)}>Cancel</button><button className="button button-primary" onClick={saveProfile} disabled={saving}>{saving && <LoaderCircle className="spin" size={15}/>} Save profile</button></div></section></div>}
  </div>;
}

function StatCard({icon,label,value,tone,foot}:{icon:ReactNode;label:string;value:number;tone:string;foot:string}) { return <article className="stat-card"><div className="stat-top"><div className={`stat-icon ${tone}`}>{icon}</div><span className="stat-period">ALL TIME</span></div><strong className="stat-value">{value}</strong><span className="stat-label">{label}</span><div className="stat-foot"><span>{foot}</span><span className="stat-trend"><ArrowUpRight size={13}/></span></div></article>; }

function AuthScreen({state,password,setPassword,signIn,busy,error}:{state:AuthState;password:string;setPassword:(value:string)=>void;signIn:(event:FormEvent<HTMLFormElement>)=>void;busy:boolean;error:string}) {
  return <main className="auth-screen"><section className="auth-card"><div className="brand-mark auth-mark"><Sparkles size={19}/></div><span className="eyebrow"><span className="eyebrow-line"/> PRIVATE WORKSPACE</span><h1>Welcome back</h1>
    {state === "checking" && <p>Checking the local authentication service…</p>}
    {state === "offline" && <p role="alert">The local API is unavailable. Start the backend on 127.0.0.1:8000 and reload.</p>}
    {state === "unconfigured" && <div className="auth-message"><p>Local sign-in is not configured yet. Set a password hash from the backend folder, then restart the API:</p><code>python -m app.auth_cli</code><p>Put the printed verifier in the ignored project <code>.env</code> as <code>APP_PASSWORD_HASH</code>. Never put your password itself in that file.</p></div>}
    {state === "login" && <form onSubmit={signIn} className="auth-form"><p>Sign in to your local job search workspace.</p><label htmlFor="dashboard-password">Password</label><input id="dashboard-password" type="password" autoComplete="current-password" minLength={14} maxLength={1024} value={password} onChange={event=>setPassword(event.target.value)} required/><span className="auth-hint">At least 14 characters</span>{error && <p role="alert" className="auth-error">{error}</p>}<button className="button button-primary" disabled={busy}>{busy && <LoaderCircle className="spin" size={15}/>} Sign in</button></form>}
    <div className="auth-local"><ShieldCheck size={15}/> Localhost only · session protected</div>
  </section></main>;
}
function SendIcon(){return <ArrowUpRight size={16}/>}
function MoreDots(){return <span className="more-dots">•••</span>}
function IconFor({label}:{label:string}) { const item=nav.find(x=>x.label===label); const Icon=item?.icon??Gauge; return <Icon size={22}/>; }
