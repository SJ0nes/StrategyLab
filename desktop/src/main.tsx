import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./app.css";
import "./shell.css";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("index.html has no #root element to draw the app in");
}
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
