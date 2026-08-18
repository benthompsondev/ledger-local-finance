import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { migrateLegacyPreferences } from "./preferences";
import "./styles.css";

// Before the first render, not inside a component. App's useState initializers
// read the landing page and density synchronously on mount, so a migration
// running in an effect would arrive one render too late and the first launch
// after the rename would open on the wrong screen at the wrong density.
migrateLegacyPreferences();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
