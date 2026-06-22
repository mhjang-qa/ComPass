(function attachTimeUtility(global) {
  function formatKstDateTime(value, includeSeconds = false) {
    if (!value) return "-";
    if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return String(value);
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: global.COMPASS_CONFIG?.timeZone || "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: includeSeconds ? "2-digit" : undefined,
      hourCycle: "h23",
    }).formatToParts(date);
    const get = (type) => parts.find((part) => part.type === type)?.value || "";
    const time = `${get("hour")}:${get("minute")}${includeSeconds ? `:${get("second")}` : ""}`;
    return `${get("year")}-${get("month")}-${get("day")} ${time} KST`;
  }

  global.ComPassTime = Object.freeze({ formatKstDateTime });
}(window));
