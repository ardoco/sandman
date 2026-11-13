import "./style.css";
import { cmd, loadAppSettings } from "./api";
import { applySettings, DEFAULT_SETTINGS } from "./logic";
import { renderProject } from "./views/project";
import { renderStart } from "./views/start";

const root = document.getElementById("app")!;
const showStart = () => renderStart(root, (p) => { void cmd("save_project", { project: p }).catch(() => undefined); return renderProject(root, p, showStart); });
void loadAppSettings().catch(() => DEFAULT_SETTINGS).then((s) => { applySettings(s); showStart(); });
