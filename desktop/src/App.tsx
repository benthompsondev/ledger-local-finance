import { useEffect, useState } from "react";
import AddDataView from "./AddDataView";
import AiView from "./AiView";
import HomeView from "./HomeView";
import InsightsView from "./InsightsView";
import PlanView from "./PlanView";
import SettingsView from "./SettingsView";
import TransactionsView from "./TransactionsView";
import { loadReviewSummary } from "./api";
import type { TxPrefill } from "./types";
import brandIcon from "./assets/spendshape-icon.png";
import { loadProfiles } from "./api";
import { readDensity, readLandingPage } from "./preferences";

type Screen = "home" | "add-data" | "plan" | "insights" | "transactions" | "ai" | "settings";
// Goals is gone. Its progress was self-reported, which made a whole tab out of
// a number the app could not check.
//
// Until 2.5.4 the component was still imported and still had a screen branch,
// so it shipped in the bundle while being unreachable — and worse, the goal
// rows behind it went on reserving money out of Safe to Spend with no screen
// left to see or stop them. A one-time migration in utils/database.py switches
// that influence off. Every goal row is preserved.
const SCREENS: {id:Screen;label:string}[]=[
  {id:"home",label:"Home"},{id:"add-data",label:"Add Data"},{id:"plan",label:"Plan"},
  {id:"insights",label:"Insights"},{id:"transactions",label:"Transactions"},
  {id:"ai",label:"Coach"},
];

export default function App(){
  const[screen,setScreen]=useState<Screen>(readLandingPage);
  const[addDataMounted,setAddDataMounted]=useState(false);
  const[dataVersion,setDataVersion]=useState(0);
  const[txPrefill,setTxPrefill]=useState<TxPrefill|null>(null);
  const[focusAnchor,setFocusAnchor]=useState("");
  const[focusToken,setFocusToken]=useState(0);
  const[density,setDensity]=useState(readDensity);
  const[reviewCount,setReviewCount]=useState(0);
  const[profile,setProfile]=useState<{name:string;count:number}|null>(null);
  useEffect(()=>{const update=()=>setDensity(readDensity());window.addEventListener("spendshape-preferences-changed",update);return()=>window.removeEventListener("spendshape-preferences-changed",update);},[]);
  // Home owns first-run database initialization. Delay this small secondary
  // badge request so two new sidecars never race while a schema is migrating.
  // Also the first call that writes the profile registry, so an
  // installation that never opens Settings still gets one.
  useEffect(()=>{void loadProfiles().then(p=>{const active=p.profiles.find(x=>x.active);setProfile(active?{name:active.name,count:p.profiles.length}:null);}).catch(()=>setProfile(null));},[dataVersion]);
  useEffect(()=>{const timer=window.setTimeout(()=>{void loadReviewSummary().then(v=>setReviewCount(v.count)).catch(()=>setReviewCount(0));},dataVersion===0?1500:0);return()=>window.clearTimeout(timer);},[dataVersion]);
  const changed=()=>setDataVersion(v=>v+1);
  const goTo=(target:string)=>{const[base,anchor=""]=target.split("#");const[next,queryString=""]=base.split("?") as [Screen,string?];if(next==="add-data")setAddDataMounted(true);if(next==="transactions"&&queryString){const params=new URLSearchParams(queryString);setTxPrefill({quickReview:params.get("quickReview")==="1",flaggedOnly:params.get("flaggedOnly")==="1",suggestedOnly:params.get("suggestedOnly")==="1"});}setFocusAnchor(anchor);setFocusToken(v=>v+1);setScreen(next);};
  const drill=(p:TxPrefill)=>{setTxPrefill(p);setScreen("transactions");};
  return <main className={`app-shell density-${density}`}>
    <header className="app-header"><button className="brand-button" type="button" onClick={()=>setScreen("home")} aria-label="SignalSpace Home"><img src={brandIcon} alt=""/>{/* "Signal" white, the rest green. The span already carried that split
        for the previous two-word name, so the lockup is the same mechanism
        with different words, not a new mark. */}
    <div><h1><span>Signal</span>Space Finance</h1><p>Private, local-first personal finance.</p></div></button><div className="header-actions">{profile&&profile.count>1&&<button type="button" className="profile-chip" onClick={()=>{setScreen("settings");setFocusAnchor("profiles");setFocusToken(v=>v+1);}} title="Switch profile">{profile.name}</button>}<span className="native-badge">Native · local-first</span><button className={screen==="settings"?"icon-button nav-active":"icon-button"} onClick={()=>setScreen("settings")} aria-label="Settings">⚙</button></div></header>
    <nav className="app-nav" aria-label="Main">{SCREENS.map(s=><button key={s.id} type="button" className={screen===s.id?"nav-button nav-active":"nav-button"} onClick={()=>goTo(s.id)}>{s.label}{s.id==="transactions"&&reviewCount>0&&<span className="nav-count">{reviewCount>99?"99+":reviewCount}</span>}</button>)}</nav>
    {screen==="home"&&<HomeView onAddData={()=>goTo("add-data")} onNavigate={goTo} onDrill={drill} refreshToken={dataVersion}/>}
    {addDataMounted&&<div hidden={screen!=="add-data"}><AddDataView onGoHome={()=>setScreen("home")} onGoTransactions={()=>setScreen("transactions")} onDataChanged={changed}/></div>}
    {screen==="plan"&&<PlanView refreshToken={dataVersion} onDataChanged={changed} onNavigate={goTo}/>}
    {screen==="insights"&&<InsightsView refreshToken={dataVersion} onDrill={drill} onNavigate={goTo} onDataChanged={changed}/>}
    {screen==="transactions"&&<TransactionsView refreshToken={dataVersion} onDataChanged={changed} prefill={txPrefill} onPrefillApplied={()=>setTxPrefill(null)}/>}
    {screen==="ai"&&<AiView onOpenSettings={()=>goTo("settings#ai-assist")} onDrill={d=>drill({category:d.category,search:d.merchant,startDate:d.start_date,endDate:d.end_date})}/>}
    {screen==="settings"&&<SettingsView onDataChanged={changed} focusAnchor={focusAnchor} focusToken={focusToken}/>}
  </main>;
}
