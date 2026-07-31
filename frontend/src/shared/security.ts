/** security.ts — 本地脱敏引擎，嵌入 ChatInput 的 onPaste 和 ChatMessages 的渲染 */
const STORAGE_KEY = "zenith_secure_map";

// ===== 检测规则（与 backend/tools/shield.py 保持同步）=====
interface Rule {
  name: string;
  pattern: RegExp;
}

const RULES: Rule[] = [
  { name: "GITHUB_CLASSIC", pattern: /ghp_[A-Za-z0-9]{36}/g },
  { name: "GITHUB_FINE",    pattern: /github_pat_[A-Za-z0-9_]{22,82}/g },
  { name: "GITLAB_PAT",     pattern: /glpat-[A-Za-z0-9_\-]{20,}/g },
  { name: "OPENAI_KEY",     pattern: /sk-(?:proj-)?[A-Za-z0-9]{32,}/g },
  { name: "ANTHROPIC_KEY",  pattern: /sk-ant-(?:api03-)?[A-Za-z0-9_\-]{32,}/g },
  { name: "DEEPSEEK_KEY",   pattern: /sk-[A-Za-z0-9]{32}/g },
  { name: "SILICONFLOW_KEY",pattern: /sk-[A-Za-z0-9]{40,}/g },
  { name: "JWT_TOKEN",      pattern: /eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}/g },
  { name: "API_ASSIGN",     pattern: /(?:API[_-]?KEY|api[_-]?key|apikey|token|secret|password|pwd|pass)\s*[=:]\s*["']?([^\s"'<>]{16,})["']?/gi },
  { name: "BEARER_TOKEN",   pattern: /(?:Bearer|bearer)\s+([A-Za-z0-9_\-.]{20,})/g },
  { name: "ENV_VAR",        pattern: /(?:KEY|TOKEN|SECRET|PASSWORD|API_KEY)\s*=\s*([A-Za-z0-9+/=_-]{32,})/gi },
];

export interface SecretMatch {
  placeholder: string;   // "SEC_001"
  maskedValue: string;   // "{{SEC_001}}"
  raw: string;           // 原始值
  name: string;          // 规则名称
}

/** 从 localStorage 加载映射表 */
function loadMap(): Record<string, string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

/** 保存映射表到 localStorage */
function saveMap(map: Record<string, string>) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
}

/** 查找 raw 值是否已��映射 */
function findExisting(raw: string, map: Record<string, string>): string | null {
  for (const [k, v] of Object.entries(map)) {
    if (v === raw) return k;
  }
  return null;
}

/** 扫描文本，返回找到的所有敏感匹配 */
export function scanSecrets(text: string): SecretMatch[] {
  const map = loadMap();
  const matches: SecretMatch[] = [];
  const seen = new Set<string>();

  let nextId = Object.keys(map).length + 1;
  let changed = false;

  for (const rule of RULES) {
    // Reset lastIndex for global regex
    rule.pattern.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = rule.pattern.exec(text)) !== null) {
      // API_ASSIGN and ENV_VAR are capture-group patterns; the sensitive part is in group 1
      const raw = (rule.name === "API_ASSIGN" || rule.name === "ENV_VAR") ? m[1] : m[0];
      if (!raw || raw.length < 8) continue;
      if (seen.has(raw)) continue;
      seen.add(raw);

      // 检查是否已有映射
      let placeholder = findExisting(raw, map);
      if (!placeholder) {
        placeholder = `SEC_${String(nextId).padStart(3, "0")}`;
        map[placeholder] = raw;
        nextId++;
        changed = true;
      }
      matches.push({ placeholder, maskedValue: `{{${placeholder}}}`, raw, name: rule.name });
    }
  }

  if (changed) saveMap(map);
  return matches;
}

/** 脱敏文本：将所有敏感匹配替换为占位符 */
export function maskSecrets(text: string): { masked: string; count: number } {
  const map = loadMap();
  let result = text;
  let count = 0;

  for (const rule of RULES) {
    rule.pattern.lastIndex = 0;
    let m: RegExpExecArray | null;
    const replacements: Array<{ start: number; end: number; placeholder: string }> = [];

    while ((m = rule.pattern.exec(text)) !== null) {
      const raw = (rule.name === "API_ASSIGN" || rule.name === "ENV_VAR") ? m[1] : m[0];
      if (!raw || raw.length < 8) continue;

      let placeholder = findExisting(raw, map);
      if (!placeholder) {
        const nextId = Object.keys(map).length + 1;
        placeholder = `SEC_${String(nextId).padStart(3, "0")}`;
        map[placeholder] = raw;
      }

      // For capture-group patterns, we need to replace only the captured group
      const fullMatch = m[0];
      const matchStart = m.index;
      if (rule.name === "API_ASSIGN" || rule.name === "ENV_VAR") {
        const prefix = fullMatch.slice(0, fullMatch.indexOf(raw));
        const suffix = fullMatch.slice(fullMatch.indexOf(raw) + raw.length);
        replacements.push({
          start: matchStart + prefix.length,
          end: matchStart + prefix.length + raw.length,
          placeholder: `{{${placeholder}}}`,
        });
      } else {
        replacements.push({
          start: matchStart,
          end: matchStart + fullMatch.length,
          placeholder: `{{${placeholder}}}`,
        });
      }
    }

    // Apply replacements from end to start to preserve indices
    replacements.sort((a, b) => b.start - a.start);
    for (const r of replacements) {
      result = result.slice(0, r.start) + r.placeholder + result.slice(r.end);
      count++;
    }
  }

  saveMap(map);
  return { masked: result, count };
}

/** 从剪贴板事件获取粘贴文本 */
export function getPastedText(e: React.ClipboardEvent): string {
  return e.clipboardData?.getData("text/plain") || "";
}

/** 还原占位符 */
export function unmaskPlaceholders(text: string): { unmasked: string; count: number } {
  const map = loadMap();
  let count = 0;
  const result = text.replace(/\{\{([A-Za-z0-9_]+)\}\}/g, (_, key) => {
    const val = map[key];
    if (val) {
      count++;
      return val;
    }
    return `{{${key}}}`;
  });
  return { unmasked: result, count };
}

/** 查找单个占位符对应的原始值 */
export function lookupPlaceholder(key: string): string | null {
  const map = loadMap();
  return map[key] || null;
}

/** 获取映射表大小 */
export function getMapSize(): number {
  return Object.keys(loadMap()).length;
}
