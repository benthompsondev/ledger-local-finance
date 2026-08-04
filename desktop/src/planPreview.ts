// Request sequencing for Plan's live preview.
//
// Plan previews the equation as the user types. Two things went wrong with
// that, and both are the kind that only show up under load:
//
//   1. Loading an unsaved plan hydrated the form from a preview, and setting
//      the form then triggered the debounced preview effect, so opening Plan
//      fired two identical requests. Each request is its own sidecar process,
//      so the duplicate was a second database connection for no result.
//
//   2. Responses were applied in arrival order, not issue order. A slow
//      failure from an old keystroke could land after a fast success from a
//      newer one and replace a correct equation with an error message.
//
// Kept free of React and Tauri imports so it can be run directly by
// `node --test`.

/** The inputs a preview actually depends on. */
export interface PreviewInputs {
  mode: string;
  income: string;
  fixed: string;
  savings: string;
  buffer: string;
}

/**
 * A stable string for one set of preview inputs.
 *
 * Used to tell "the form changed" from "the form was just hydrated with the
 * values we already previewed", which is the duplicate this removes.
 */
export function previewSignature(inputs: PreviewInputs): string {
  return [
    inputs.mode, inputs.income, inputs.fixed, inputs.savings, inputs.buffer,
  ]
    .map((value) => String(value ?? "").trim())
    .join("|");
}

export interface RequestGate {
  /** Claim the next token. Call once per request, before awaiting. */
  begin(): number;
  /** Whether a response for `token` is still the one worth applying. */
  isLatest(token: number): boolean;
  /** Tokens issued so far. Test and diagnostic use. */
  issued(): number;
}

/**
 * Latest-request-wins.
 *
 * Only the most recently issued request may apply its result, whether that
 * result is a success or a failure. Anything older is discarded, so responses
 * finishing out of order cannot walk the screen backwards and a stale error
 * cannot overwrite a newer correct answer.
 */
export function createRequestGate(): RequestGate {
  let issued = 0;
  return {
    begin(): number {
      issued += 1;
      return issued;
    },
    isLatest(token: number): boolean {
      // Tokens start at 1, so 0 is "no request was ever made" rather than a
      // request that happens to match a gate nothing has been issued from.
      return token > 0 && token === issued;
    },
    issued(): number {
      return issued;
    },
  };
}
