import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import PTPBoxDashboard from "../app/page";
import "../app/globals.css";

const root = document.getElementById("root");

if (!root) throw new Error("PTPBox application root was not found");

createRoot(root).render(
  <StrictMode>
    <PTPBoxDashboard />
  </StrictMode>,
);
