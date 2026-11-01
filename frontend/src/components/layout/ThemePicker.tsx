import { useEffect, useState } from "react";
import {
  THEMES,
  applyTheme,
  readStoredTheme,
  watchSystemTheme,
  type Theme,
} from "../../lib/theme";

export default function ThemePicker() {
  const [theme, setTheme] = useState<Theme>(() => readStoredTheme());

  useEffect(() => {
    applyTheme(theme);
    watchSystemTheme();
  }, [theme]);

  return (
    <label className="flex items-center gap-2 px-2 py-1.5 rounded-lg border border-dark-600 bg-dark-800 text-xs text-gray-400">
      <span className="h-2.5 w-2.5 rounded-full bg-accent-blue" aria-hidden="true" />
      <span className="sr-only">Color theme</span>
      <select
        aria-label="Color theme"
        value={theme}
        onChange={(event) => setTheme(event.target.value as Theme)}
        className="bg-transparent text-xs text-gray-300 outline-none"
      >
        {THEMES.map((item) => (
          <option key={item.id} value={item.id}>
            {item.label}
          </option>
        ))}
      </select>
    </label>
  );
}
