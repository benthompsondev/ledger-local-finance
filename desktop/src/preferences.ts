export type ChartPeriod = 3 | 6 | 12;
export type AnalysisPeriod = 30 | 90;

const CHART_PERIOD_KEY = "northstar.chartPeriod";
const ANALYSIS_PERIOD_KEY = "northstar.analysisPeriodDays";
const DENSITY_KEY = "northstar.density";
const HOME_SECONDARY_KEY = "northstar.homeSecondary";
const LANDING_PAGE_KEY = "northstar.landingPage";
const SHOW_NET_WORTH_KEY = "northstar.showNetWorth";
const SAVINGS_STYLE_KEY = "northstar.savingsStyle";
const SAVINGS_VALUE_KEY = "northstar.savingsValue";
const COMPARE_BASELINE_KEY = "northstar.compareBaseline";
const CATEGORY_EXPANDED_KEY = "northstar.categoryExpanded";
const WEEK_START_KEY = "northstar.weekStart";
export type WeekStart = "monday" | "sunday";
export type Density = "comfortable" | "compact";
// Goals left primary navigation when Money Focus replaced it, but this list
// still offered it as a startup screen, which reopened the retired
// self-reported goal system. Anyone who had chosen it is moved to Home; the
// goal records and schema are untouched.
export type LandingPage = "home"|"plan"|"insights"|"transactions";
export type SavingsStyle = "percentage"|"amount";
export type CompareBaseline = "last"|"average";

export function readChartPeriod(): ChartPeriod {
  const stored = Number(window.localStorage.getItem(CHART_PERIOD_KEY));
  return stored === 3 || stored === 6 || stored === 12 ? stored : 6;
}

export function saveChartPeriod(value: ChartPeriod): void {
  window.localStorage.setItem(CHART_PERIOD_KEY, String(value));
}

export function readAnalysisPeriod(): AnalysisPeriod {
  const stored = Number(window.localStorage.getItem(ANALYSIS_PERIOD_KEY));
  return stored === 30 || stored === 90 ? stored : 30;
}

export function saveAnalysisPeriod(value: AnalysisPeriod): void {
  window.localStorage.setItem(ANALYSIS_PERIOD_KEY, String(value));
}

export function readDensity(): Density {
  return window.localStorage.getItem(DENSITY_KEY) === "compact" ? "compact" : "comfortable";
}

export function saveDensity(value: Density): void {
  window.localStorage.setItem(DENSITY_KEY, value);
  window.dispatchEvent(new Event("northstar-preferences-changed"));
}

export function readHomeSecondary(): boolean {
  return window.localStorage.getItem(HOME_SECONDARY_KEY) !== "hidden";
}

export function saveHomeSecondary(show: boolean): void {
  window.localStorage.setItem(HOME_SECONDARY_KEY, show ? "shown" : "hidden");
}

export function readLandingPage(): LandingPage {
  const value=window.localStorage.getItem(LANDING_PAGE_KEY);
  if(value==="goals"){
    // Migrate rather than silently ignore, so the stored value stops
    // pointing at a screen that is no longer in the product.
    window.localStorage.setItem(LANDING_PAGE_KEY,"home");
    return "home";
  }
  return ["home","plan","insights","transactions"].includes(value??"")
    ? value as LandingPage : "home";
}
export function saveLandingPage(value:LandingPage):void {window.localStorage.setItem(LANDING_PAGE_KEY,value);}
export function readShowNetWorth():boolean {return window.localStorage.getItem(SHOW_NET_WORTH_KEY)!=="hidden";}
export function saveShowNetWorth(show:boolean):void {window.localStorage.setItem(SHOW_NET_WORTH_KEY,show?"shown":"hidden");window.dispatchEvent(new Event("northstar-preferences-changed"));}
export function readSavingsPreference():{style:SavingsStyle;value:number}{
  const style=window.localStorage.getItem(SAVINGS_STYLE_KEY)==="amount"?"amount":"percentage";
  const raw=window.localStorage.getItem(SAVINGS_VALUE_KEY);
  const parsed=raw===null?Number.NaN:Number(raw);
  return {style,value:Number.isFinite(parsed)&&parsed>=0?parsed:15};
}
export function saveSavingsPreference(style:SavingsStyle,value:number):void{
  window.localStorage.setItem(SAVINGS_STYLE_KEY,style);window.localStorage.setItem(SAVINGS_VALUE_KEY,String(Math.max(0,value)));
  window.dispatchEvent(new Event("northstar-preferences-changed"));
}

export function readCompareBaseline(): CompareBaseline {
  return window.localStorage.getItem(COMPARE_BASELINE_KEY) === "average" ? "average" : "last";
}
export function saveCompareBaseline(value: CompareBaseline): void {
  window.localStorage.setItem(COMPARE_BASELINE_KEY, value);
}
/** Monday is the Canadian and European default; much of the US reads Sunday
 * first, and a calendar whose columns are wrong is quietly unreadable. */
export function readWeekStart(): WeekStart {
  return window.localStorage.getItem(WEEK_START_KEY) === "sunday"
    ? "sunday" : "monday";
}
export function saveWeekStart(value: WeekStart): void {
  window.localStorage.setItem(WEEK_START_KEY, value);
  window.dispatchEvent(new Event("northstar-preferences-changed"));
}

export function readCategoryExpanded(): boolean {
  return window.localStorage.getItem(CATEGORY_EXPANDED_KEY) === "expanded";
}
export function saveCategoryExpanded(expanded: boolean): void {
  window.localStorage.setItem(CATEGORY_EXPANDED_KEY, expanded ? "expanded" : "collapsed");
}

export function clearNorthstarPreferences(): void {
  window.localStorage.removeItem(COMPARE_BASELINE_KEY);
  window.localStorage.removeItem(CATEGORY_EXPANDED_KEY);
  window.localStorage.removeItem(CHART_PERIOD_KEY);
  window.localStorage.removeItem(ANALYSIS_PERIOD_KEY);
  window.localStorage.removeItem(DENSITY_KEY);
  window.localStorage.removeItem(HOME_SECONDARY_KEY);
  window.localStorage.removeItem(LANDING_PAGE_KEY);
  window.localStorage.removeItem(SHOW_NET_WORTH_KEY);
  window.localStorage.removeItem(SAVINGS_STYLE_KEY);
  window.localStorage.removeItem(SAVINGS_VALUE_KEY);
  window.localStorage.removeItem(WEEK_START_KEY);
}
