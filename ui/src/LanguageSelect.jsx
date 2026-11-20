import { useEffect, useState } from "react";
import { api } from "./api.js";

// Whether the agent's final report also comes in Vietnamese (the investigation stays in English); saved at once.
export default function LanguageSelect({ onChange }) {
  const [language, setLanguage] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { api.settings().then((s) => { setLanguage(s.language); onChange && onChange(s.language); }).catch(() => {}); }, []);
  if (language === null) return null;
  async function change(e) {
    const value = e.target.value;
    setLanguage(value);
    setSaving(true);
    try { await api.saveSettings({ language: value }); onChange && onChange(value); } finally { setSaving(false); }
  }
  return (
    <label className="lang-label" title="Agent điều tra bằng tiếng Anh. Khi chọn thêm tiếng Việt, báo cáo cuối có thêm bản tiếng Việt.">
      Báo cáo cuối
      <select className="input lang-select" value={language} onChange={change} disabled={saving}>
        <option value="en">English</option>
        <option value="vi">English + bản tiếng Việt</option>
      </select>
    </label>
  );
}
