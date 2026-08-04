
/** The pure parts of the monthly-reading form.
 *
 *  Two defects live here, both invisible until someone actually edits.
 *
 *  1. The form seeded its fields with `useState(existing ?? prefill)`. React
 *     runs a useState initializer once, on mount, and the form is mounted
 *     while the details element is still closed. Clicking Edit later changed
 *     the `existing` prop but not the fields, so the selected reading did
 *     not load.
 *
 *  2. Every field went through `Number(value)`, and `Number("")` is 0. So
 *     clearing Cash and saving wrote cash = 0 over a real balance without
 *     ever saying so. A blank is not a zero; a zero is a zero.
 */

export interface ReadingFields {
  month: string;
  cash: string;
  investments: string;
  otherAssets: string;
  liabilities: string;
  note: string;
}

export interface ReadingSource {
  month: string;
  cash: number;
  investments: number;
  other_assets: number;
  liabilities: number;
  note?: string;
}

/** Fields for a reading being corrected, or for a fresh one from the prefill. */
export function fieldsFor(
  existing: ReadingSource | undefined, prefill: ReadingSource,
): ReadingFields {
  const seed = existing ?? prefill;
  return {
    month: seed.month,
    cash: String(seed.cash ?? ""),
    investments: String(seed.investments ?? ""),
    otherAssets: String(seed.other_assets ?? ""),
    liabilities: String(seed.liabilities ?? ""),
    note: existing?.note ?? "",
  };
}

/** Which reading the fields should be showing, as a stable string.
 *
 *  Used as the effect key that reloads the form. It changes when the user
 *  picks a different month to edit, and when they leave edit mode, but not
 *  on an unrelated parent refresh — so a refresh cannot wipe an edit in
 *  progress.
 */
export function formIdentity(
  existing: ReadingSource | undefined, prefillMonth: string,
): string {
  return existing ? `edit:${existing.month}` : `new:${prefillMonth}`;
}

export type ParsedAmount =
  | { ok: true; value: number }
  | { ok: false; reason: string };

/** A required money field. Blank is refused; zero is accepted. */
export function parseAmount(raw: string, label: string): ParsedAmount {
  const text = String(raw ?? "").trim();
  if (text === "") {
    return {
      ok: false,
      reason: `${label} is blank. Enter 0 if it really is zero.`,
    };
  }
  const value = Number(text);
  if (!Number.isFinite(value)) {
    return { ok: false, reason: `${label} is not a number.` };
  }
  if (value < 0) {
    return {
      ok: false,
      reason: `${label} cannot be negative. An overdraft belongs in Debts.`,
    };
  }
  return { ok: true, value };
}

export interface ParsedReading {
  month: string;
  cash: number;
  investments: number;
  otherAssets: number;
  liabilities: number;
  note: string;
}

export type ReadingResult =
  | { ok: true; reading: ParsedReading }
  | { ok: false; reason: string };

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

/** Validate the whole form. Refuses rather than coercing anything. */
export function parseReading(fields: ReadingFields): ReadingResult {
  if (!MONTH.test(String(fields.month ?? "").trim())) {
    return { ok: false, reason: "Choose the month this reading is for." };
  }
  const parts: [keyof ReadingFields, string, keyof ParsedReading][] = [
    ["cash", "Cash", "cash"],
    ["investments", "Investments", "investments"],
    ["otherAssets", "Other assets", "otherAssets"],
    ["liabilities", "Debts", "liabilities"],
  ];
  const reading: ParsedReading = {
    month: fields.month.trim(), cash: 0, investments: 0,
    otherAssets: 0, liabilities: 0, note: fields.note ?? "",
  };
  for (const [field, label, key] of parts) {
    const parsed = parseAmount(fields[field], label);
    if (!parsed.ok) return { ok: false, reason: parsed.reason };
    (reading[key] as number) = parsed.value;
  }
  return { ok: true, reading };
}

/** The running total under the form, or null while anything is unusable. */
export function runningTotal(fields: ReadingFields): number | null {
  const parsed = parseReading(fields);
  if (!parsed.ok) return null;
  const { cash, investments, otherAssets, liabilities } = parsed.reading;
  return Math.round((cash + investments + otherAssets - liabilities) * 100) / 100;
}
