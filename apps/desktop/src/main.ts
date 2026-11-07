import "./style.css";
import { renderProject } from "./views/project";
import { renderStart } from "./views/start";

const root = document.getElementById("app")!;
const showStart = () => renderStart(root, (p) => renderProject(root, p, showStart));
showStart();
