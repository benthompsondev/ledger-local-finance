import { saveGettingStartedDismissed } from "./preferences";

/**
 * What to do with an empty profile.
 *
 * Deliberately a card and not a wizard. Somebody who has just installed a
 * finance app does not need five modal screens before they are allowed to
 * look at it; they need to know where their data lives, what file to bring,
 * and what happens after that. Everything here is reachable from the app
 * anyway, so this is signposting rather than a gate.
 *
 * It renders only for a profile holding no transactions, so an established
 * set of finances never sees it. Dismissing it is remembered per profile,
 * and Settings can bring it back.
 */
export function GettingStarted({
  profileId, profileName, showProfileName, onAddData, onNavigate, onDismiss,
}: {
  profileId: string;
  profileName: string;
  showProfileName: boolean;
  onAddData: () => void;
  onNavigate: (screen: string) => void;
  onDismiss: () => void;
}) {
  const dismiss = () => {
    saveGettingStartedDismissed(profileId, true);
    onDismiss();
  };

  return (
    <section className="getting-started" aria-labelledby="getting-started-title">
      <div className="gs-head">
        <div>
          <span className="eyebrow">Getting started</span>
          <h2 id="getting-started-title">
            {showProfileName
              ? `${profileName} has no transactions yet`
              : "Welcome to SignalSpace Finance"}
          </h2>
          <p>
            Four steps, and only the second one needs anything from you.
          </p>
        </div>
        <button type="button" className="ghost-button" onClick={dismiss}>
          Dismiss
        </button>
      </div>

      <ol className="gs-steps">
        <li>
          <strong>Your finances stay on this computer</strong>
          <p>
            There is no account to create and nothing to sign in to.
            SignalSpace keeps a database file on this machine and does the
            calculations here. You can back it up or export everything to CSV
            at any time from Settings.
          </p>
        </li>

        <li>
          <strong>Import a statement</strong>
          <p>
            Download a <b>CSV</b> export from your bank or credit-card website
            and open it here. CSV is the format to use: SignalSpace reads the
            common ones and works out the date and amount conventions per
            file. If a file is ambiguous it refuses it rather than guessing at
            your numbers, so some exports will need you to map the columns and
            a few will not work at all.
          </p>
          <p className="gs-note">
            PDF statements are Beta. They work for a small number of layouts
            and fail on many banks. Use CSV if your bank offers one.
          </p>
          <div className="button-row">
            <button type="button" onClick={onAddData}>
              Import a statement
            </button>
          </div>
        </li>

        <li>
          <strong>Check what came in</strong>
          <p>
            SignalSpace sorts the rows itself and flags only the ones worth a
            look. Transactions is where you correct a category or split a
            shared cost, and the tab shows a count when something is waiting.
          </p>
        </li>

        <li>
          <strong>Read Home, then Insights</strong>
          <p>
            Home is the current check-in: what came in, what went out, what is
            left. Insights is what changed and which patterns are worth
            knowing about. Both fill in as soon as the first import lands.
          </p>
        </li>
      </ol>

      <p className="gs-later">
        <strong>Plan comes later.</strong> It works out a month once there are
        a few complete months behind it, so there is nothing useful to set up
        on day one. Import what you have and it becomes worth opening on its
        own.{" "}
        <button type="button" className="text-button gs-link"
          onClick={() => onNavigate("settings")}>
          Settings can show this again
        </button>{" "}
        whenever you want it.
      </p>
    </section>
  );
}
