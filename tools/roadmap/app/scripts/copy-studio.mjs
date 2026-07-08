// 個人ゴールマップ(tools/goalmap/goalmap-studio.html)を、このアプリの public/ にコピーする。
// ソースは tools/goalmap/ のまま単一の正とし、二重管理を避ける。
// Level3（個人）ノードのクリック遷移先 (/goalmap-studio.html?member=<名前>) として同一オリジンで配信する。
import { copyFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "..", "goalmap", "goalmap-studio.html");
const destDir = join(here, "..", "public");
const dest = join(destDir, "goalmap-studio.html");

mkdirSync(destDir, { recursive: true });
copyFileSync(src, dest);
console.log(`copied ${src} -> ${dest}`);
