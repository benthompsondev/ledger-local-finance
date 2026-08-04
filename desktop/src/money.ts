const currency = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  maximumFractionDigits: 0,
});

const currencyCents = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function money(value: number | null | undefined): string {
  return currency.format(Number(value || 0));
}

export function moneyCents(value: number | null | undefined): string {
  return currencyCents.format(Number(value || 0));
}

export function signedMoneyCents(value: number | null | undefined): string {
  const amount = Number(value || 0);
  return amount > 0 ? `+${currencyCents.format(amount)}` : currencyCents.format(amount);
}
