/** Round a positive chart peak to a readable ceiling without wasting space. */
export function niceMax(value: number): number {
  if (value <= 0) return 100;
  const power = 10 ** Math.floor(Math.log10(value));
  const normalized = value / power;
  const ceilings = [1, 2, 2.5, 3, 4, 5, 7.5, 10];
  const ceiling = ceilings.find((candidate) => candidate >= normalized) ?? 10;
  return ceiling * power;
}

export interface SignedBarGeometry {
  bottom: number;
  height: number;
}

/** Position a signed interval inside a chart whose domain includes zero. */
