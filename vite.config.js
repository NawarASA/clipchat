import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./", // the production build is loaded from file://
  server: { port: 5173, strictPort: true },
});
