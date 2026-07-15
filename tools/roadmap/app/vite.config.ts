import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 相対パスでビルド（file://での確認や任意サブパス配信に強くする）
export default defineConfig({
  base: "./",
  plugins: [react()],
});
