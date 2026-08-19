import { useCallback, useEffect, useState } from "react";
import {
  createProfile, deleteProfile, loadProfiles, renameProfile, switchProfile,
} from "./api";
import type { Profile, ProfileList } from "./types";

/**
 * Managing separate sets of finances in one installation.
 *
 * Profiles isolate by directory, so switching is not a filter being applied,
 * it is a different database being opened. That is why switching reloads the
 * whole app: every figure on every screen comes from the profile that was
 * open when the request was made, and showing one profile's Home above
 * another profile's Insights would be worse than a moment's reload.
 */
export function ProfilesPanel({ onSwitched }: { onSwitched: () => void }) {
  const [data, setData] = useState<ProfileList | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [newName, setNewName] = useState("");
  const [renaming, setRenaming] = useState("");
  const [draftName, setDraftName] = useState("");
  const [confirmDelete, setConfirmDelete] = useState("");

  const refresh = useCallback(async () => {
    try {
      setData(await loadProfiles());
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const run = async (key: string, work: () => Promise<ProfileList>) => {
    setBusy(key);
    setError("");
    try {
      setData(await work());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const add = async () => {
    if (!newName.trim()) return;
    await run("create", () => createProfile(newName.trim()));
    setNewName("");
  };

  const commitRename = async (profile: Profile) => {
    if (!draftName.trim() || draftName.trim() === profile.name) {
      setRenaming("");
      return;
    }
    await run(`rename-${profile.id}`,
      () => renameProfile(profile.id, draftName.trim()));
    setRenaming("");
  };

  const change = async (profile: Profile) => {
    if (profile.active) return;
    await run(`switch-${profile.id}`, () => switchProfile(profile.id));
    // Everything on screen belongs to the profile that was open when it was
    // loaded, so the only honest thing to do is load it all again.
    onSwitched();
  };

  const remove = async (profile: Profile) => {
    await run(`delete-${profile.id}`, () => deleteProfile(profile.id));
    setConfirmDelete("");
  };

  if (!data) {
    return <article className="chart-card">
      <p className="guidance">{error || "Loading profiles…"}</p>
    </article>;
  }

  const active = data.profiles.find((profile) => profile.active);
  const others = data.profiles.filter((profile) => !profile.active);

  const identity = (profile: Profile) => renaming === profile.id ? (
    <input
      autoFocus
      value={draftName}
      maxLength={60}
      aria-label={`Rename ${profile.name}`}
      onChange={(event) => setDraftName(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") void commitRename(profile);
        if (event.key === "Escape") setRenaming("");
      }}
      onBlur={() => void commitRename(profile)}
    />
  ) : <strong>{profile.name}</strong>;

  const holdings = (profile: Profile) => profile.has_data
    ? `${profile.transaction_count.toLocaleString()} transaction${profile.transaction_count === 1 ? "" : "s"}`
    : "No transactions yet";

  return (
    <div className="profiles-panel">
      {/* A profile is the least self-explanatory thing in the app, so this
          says what one is before listing any. */}
      <p className="profiles-lede">
        A profile is one complete set of finances. Each keeps its own
        transactions, accounts, imports, plans, net worth and saved rules in
        its own database, so a second person or a test import never mixes
        with yours. Only the profile you are in is open. How the app looks
        stays shared across the installation.
      </p>

      {error && <div className="inline-error">{error}</div>}

      {/* Which one is open is the question this screen exists to answer, so
          it is a card of its own rather than a highlighted row in a list. */}
      {active && <article className="chart-card profile-active-card">
        <div>
          <span className="eyebrow">Open now</span>
          <div className="profile-identity">
            {identity(active)}
            <small>
              {holdings(active)}
              {active.is_default && " · the first profile"}
            </small>
          </div>
        </div>
        <button type="button" className="ghost-button" disabled={!!busy}
          onClick={() => { setRenaming(active.id); setDraftName(active.name); }}>
          Rename
        </button>
      </article>}

      {others.length > 0 && <article className="chart-card">
        <h3>Not open</h3>
        <div className="settings-list">
          {others.map((profile) => (
            <div className="setting-row profile-row" key={profile.id}>
              <div className="profile-identity">
                {identity(profile)}
                <small>
                  {holdings(profile)}
                  {profile.is_default && " · the first profile"}
                </small>
              </div>

              <div className="button-row compact-actions">
                <button type="button" disabled={!!busy}
                  onClick={() => void change(profile)}>
                  {busy === `switch-${profile.id}` ? "Switching…" : "Switch to this"}
                </button>
                <button type="button" className="ghost-button" disabled={!!busy}
                  onClick={() => { setRenaming(profile.id); setDraftName(profile.name); }}>
                  Rename
                </button>
                {/* The first profile's directory is the data root, so removing
                    it would take every other profile with it. The open one is
                    refused because the running app is reading from it, and it
                    is not in this list at all. */}
                {!profile.is_default && (
                  confirmDelete === profile.id ? (
                    <>
                      <span className="nw-confirm">
                        Delete {profile.name} and its data?
                      </span>
                      <button type="button" className="danger-button"
                        disabled={!!busy}
                        onClick={() => void remove(profile)}>Delete</button>
                      <button type="button" className="ghost-button"
                        onClick={() => setConfirmDelete("")}>Keep</button>
                    </>
                  ) : (
                    <button type="button" className="ghost-button"
                      disabled={!!busy}
                      onClick={() => setConfirmDelete(profile.id)}>Delete</button>
                  )
                )}
              </div>
            </div>
          ))}
        </div>
      </article>}

      <article className="chart-card profile-add-card">
        <h3>Add a profile</h3>
        <p className="guidance">
          It starts empty. Switch to it, then import statements as usual;
          nothing imported there can reach the profiles beside it.
        </p>
        <div className="profile-add">
          <label>
            Name it
            <input
              value={newName}
              maxLength={60}
              placeholder="Tori's Finances"
              onChange={(event) => setNewName(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") void add(); }}
            />
          </label>
          <button type="button"
            disabled={!newName.trim() || !!busy}
            onClick={() => void add()}>
            {busy === "create" ? "Creating…" : "Create profile"}
          </button>
        </div>
      </article>
    </div>
  );
}
