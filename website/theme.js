(() => {
  "use strict";
  const root = document.documentElement;
  function readPreference(key) {
    try {
      return localStorage.getItem(key);
    } catch (error) {
      console.warn(`Cocoa County could not read ${key}; preferences will apply to this page only.`, error);
      return null;
    }
  }
  function savePreference(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (error) {
      console.warn(`Cocoa County could not save ${key}; preferences will apply to this page only.`, error);
    }
  }
  const savedTheme = readPreference("cocoa-theme");
  let theme = ["light", "dark"].includes(savedTheme) ? savedTheme : "system";
  function applyTheme() {
    if (theme === "system") root.removeAttribute("data-theme");
    else root.dataset.theme = theme;
    const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = dark ? "#241913" : "#fcf6ed";
  }
  applyTheme();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

  document.addEventListener("DOMContentLoaded", () => {
    const background = document.createElement("div");
    background.className = "cocoa-background";
    background.setAttribute("aria-hidden", "true");
    const positions = [
      [3, 16, 44, 14, -4], [91, 8, 54, 19, -7], [7, 73, 36, 17, -10],
      [94, 66, 42, 21, -3], [47, 92, 32, 16, -6], [64, 37, 26, 23, -12],
    ];
    for (const [x, y, size, duration, delay] of positions) {
      const marshmallow = document.createElement("span");
      marshmallow.className = "marshmallow";
      marshmallow.style.cssText = `--x:${x}%;--y:${y}%;--size:${size}px;--duration:${duration}s;--delay:${delay}s`;
      background.append(marshmallow);
    }
    document.body.prepend(background);
    const controls = document.createElement("div");
    controls.className = "appearance-controls";
    const label = document.createElement("label");
    label.htmlFor = "cocoa-theme";
    label.textContent = "Theme";
    const select = document.createElement("select");
    select.id = "cocoa-theme";
    for (const value of ["system", "light", "dark"]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value[0].toUpperCase() + value.slice(1);
      select.append(option);
    }
    select.value = theme;
    select.addEventListener("change", () => {
      theme = select.value;
      applyTheme();
      savePreference("cocoa-theme", theme);
    });
    let paused = readPreference("cocoa-motion") === "paused";
    const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
    const motion = document.createElement("button");
    motion.type = "button";
    function updateMotion() {
      background.dataset.paused = String(paused || reducedMotion.matches);
      motion.disabled = reducedMotion.matches;
      motion.textContent = reducedMotion.matches ? "Motion reduced" : paused ? "Resume marshmallows" : "Pause marshmallows";
      motion.setAttribute("aria-label", reducedMotion.matches ? "Animation disabled by your reduced-motion preference" : motion.textContent);
    }
    motion.addEventListener("click", () => {
      paused = !paused;
      updateMotion();
      savePreference("cocoa-motion", paused ? "paused" : "running");
    });
    reducedMotion.addEventListener("change", updateMotion);
    updateMotion();
    controls.append(label, select, motion);
    document.querySelector(".site-header").append(controls);
  });
})();
