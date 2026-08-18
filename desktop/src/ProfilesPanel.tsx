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
    return <p className="guidance">{error || "Loading profiles…"}</p>;
  }

  return (
    <div className="profiles-panel">
      <p className="guidance">
        Each profile keeps its own transactions, accounts, plans, net worth
        and imports in its own database. Nothing is shared between them, and
        only the profile you are in is open.
      </p>

      {error && <div className="inline-error">{error}</div>}

      <div className="settings-list">
        {data.profiles.map((profile) => (
          <div className={`setting-row profile-row${profile.active ? " active" : ""}`}
            key={profile.id}>
            <div className="profile-identity">
              {renaming === profile.id ? (
                <input
                  autoFocus
                  value={draftName}
                  maxLength={60}
                  onChange={(event) => setDraftName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void commitRename(profile);
                    if (event.key === "Escape") setRenaming("");
                  }}
                  onBlur={() => void commitRename(profile)}
                />
              ) : (
                <strong>{profile.name}</strong>
              )}
              <small>
                {profile.active ? "Open now" : profile.has_data
                  ? `${profile.transaction_count.toLocaleString()} transactions`
                  : "Empty"}
                {profile.is_default && " · first profile"}
              </small>
            </div>

            <div className="button-row compact-actions">
              {!profile.active && (
                <button type="button" className="ghost-button"
                  disabled={!!busy}
                  onClick={() => void change(profile)}>
                  {busy === `switch-${profile.id}` ? "Switching…" : "Switch to"}
                </button>
              )}
              <button type="button" className="ghost-button" disabled={!!busy}
                onClick={() => { setRenaming(profile.id); setDraftName(profile.name); }}>
                Rename
              </button>
              {/* The first profile's directory is the data root, so removing
                  it would take every other profile with it. The open one is
                  refused because the running app is reading from it. */}
              {!profile.is_default && !profile.active && (
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

      <div className="profile-add">
        <label>
          New profile
          <input
            value={newName}
            maxLength={60}
            placeholder="Tori's Finances"
            onChange={(event) => setNewName(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") void add(); }}
          />
        </label>
        <button type="button" className="ghost-button"
          disabled={!newName.trim() || !!busy}
          onClick={() => void add()}>
          {busy === "create" ? "Creating…" : "Create"}
        </button>
      </div>
      <p className="guidance">
        A new profile starts empty. Import statements into it after switching.
      </p>
    </div>
  );
}
