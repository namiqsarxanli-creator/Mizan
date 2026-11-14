// Shared by every page: dates written the Azerbaijani way, and the access token kept out of sight.
(() => {
  // The intro plays on the first page of a session, or whenever the address has ?intro.
  try {
    const forced = new URLSearchParams(location.search).has("intro");
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!reduced && (forced || !sessionStorage.getItem("mizan-intro"))) {
      const root = document.documentElement;
      root.classList.add("intro-on");
      sessionStorage.setItem("mizan-intro", "1");
      setTimeout(() => root.classList.add("intro-done"), 2800);
    }
  } catch (_) { /* no storage: no intro */ }

  // A deployment with auth gets the token once, as ?token=..., and keeps it for the session.
  function token() {
    try {
      const fromUrl = new URLSearchParams(location.search).get("token");
      if (fromUrl) {
        sessionStorage.setItem("mizan-token", fromUrl);
        const url = new URL(location.href);
        url.searchParams.delete("token");
        history.replaceState(null, "", url);
      }
      return sessionStorage.getItem("mizan-token") || "";
    } catch (_) {
      return "";
    }
  }

  function headers(extra) {
    const h = Object.assign({}, extra || {});
    const t = token();
    if (t) h["Authorization"] = "Bearer " + t;
    return h;
  }

  // "15.03.2020" -> "2020-03-15"; null when it is not a real date.
  function parseDate(text) {
    const m = /^\s*(\d{1,2})[.\/-](\d{1,2})[.\/-](\d{4})\s*$/.exec(text || "");
    if (!m) return null;
    const [d, mo, y] = [Number(m[1]), Number(m[2]), Number(m[3])];
    const date = new Date(Date.UTC(y, mo - 1, d));
    if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d) return null;
    return `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  }

  function formatDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    return m ? `${m[3]}.${m[2]}.${m[1]}` : iso;
  }

  // Typing "15032020" becomes "15.03.2020".
  function maskDate(input) {
    input.addEventListener("input", () => {
      const digits = input.value.replace(/\D/g, "").slice(0, 8);
      const parts = [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4)].filter(Boolean);
      input.value = parts.join(".");
    });
  }

  // <div class="when">: "Bu gün" / "Başqa tarix" with a dd.mm.yyyy field behind the second.
  function whenField(root) {
    const input = root.querySelector("input");
    const buttons = [...root.querySelectorAll(".seg button")];
    maskDate(input);
    let mode = "today";
    const set = (next) => {
      mode = next;
      buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
      input.hidden = mode !== "past";
      if (mode === "past") input.focus();
    };
    buttons.forEach((b) => b.addEventListener("click", () => set(b.dataset.mode)));
    set("today");
    return {
      // {iso: null} means today; {error} when the date cannot be read.
      read() {
        if (mode === "today") return { iso: null };
        const iso = parseDate(input.value);
        if (!iso) return { error: "Tarixi gün.ay.il şəklində yazın, məsələn 15.03.2020." };
        if (iso < "1999-02-01") return { error: "Məcəllə 1 fevral 1999-cu ildə qüvvəyə minib; daha sonrakı tarix seçin." };
        return { iso };
      },
    };
  }

  window.Mizan = { token, headers, parseDate, formatDate, maskDate, whenField };
})();
