import "@brokaid/brand-tokens/tokens.css";
import "./index.css";
import "./styles/harbour-phase6.css";
import "./styles/ledger-phase7.css";
import "./styles/pending-quote.css";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
